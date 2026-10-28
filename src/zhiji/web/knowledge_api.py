"""知识库 API：资料库、资料导入、混合检索与流式问答。"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
import threading
from collections.abc import Callable
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from zhiji.config import ConfigManager
from zhiji.knowledge import (
    TEXT_MIME_TYPES,
    KnowledgeImportError,
    extract_document_text,
    split_text,
)
from zhiji.llm.client import LLMClient
from zhiji.storage import Database
from zhiji.storage.knowledge_repository import (
    KnowledgeRepository,
    document_to_dict,
    library_to_dict,
)

_QUEUE_EMPTY = object()


class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    color: str = Field(default="accent", max_length=16)


class LibraryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, max_length=16)


class DocumentImport(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=240)
    content: str
    mime_type: str | None = Field(default=None, max_length=120)


class DocumentRename(BaseModel):
    title: str = Field(min_length=1, max_length=240)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    library_id: str | None = None
    model_name: str | None = None
    top_k: int = Field(default=6, ge=1, le=12)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _queue_get(target: queue.Queue, timeout: float):
    try:
        return target.get(timeout=timeout)
    except queue.Empty:
        return _QUEUE_EMPTY


def _clean_text(value: str, *, fallback: str = "") -> str:
    return " ".join(str(value or "").split()) or fallback


def _library_payload(row, stats: dict[str, int] | None = None) -> dict:
    payload = library_to_dict(row)
    payload.update(stats or {"document_count": 0, "char_count": 0})
    return payload


def _document_payload(row, stats: dict[str, int] | None = None, *, content: bool = False) -> dict:
    values = stats or {}
    return document_to_dict(
        row,
        chunk_count=values.get("chunk_count", 0),
        char_count=values.get("char_count", 0),
        include_content=content,
    )


def _build_ask_messages(question: str, sources: list[dict]) -> list[dict]:
    context = []
    for index, source in enumerate(sources, start=1):
        context.append(
            f"[{index}] 资料《{source['document']['title']}》"
            f"（资料库：{source['library']['name']}，片段 {source['chunk']['ordinal'] + 1}）\n"
            f"{source['chunk']['text']}"
        )
    system = (
        "你是知记的知识库问答助手。只依据给出的资料片段回答，不要编造资料中没有的事实。\n"
        "回答使用中文，结构清晰。引用资料时必须在句末标注对应的 [编号]，可同时引用多个编号。\n"
        "如果资料不足，明确说明“现有资料不足以回答”，并指出还需要补充什么。\n\n"
        "可用资料：\n\n"
        + "\n\n".join(context)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def create_knowledge_router(
    database: Database,
    scope_user: Callable[[Request], str | None],
    *,
    config: ConfigManager,
    resolve_model: Callable[[ConfigManager, str | None], Any],
) -> APIRouter:
    """构建工作区③的知识库路由。"""

    router = APIRouter()

    @router.get("/api/knowledge/stats")
    def knowledge_stats(request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            return {"stats": KnowledgeRepository(session).stats(user_id=scope)}

    # ---- Libraries ----

    @router.get("/api/knowledge/libraries")
    def list_libraries(request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            rows = repo.list_libraries(user_id=scope)
            stats = repo.library_stats([row.id for row in rows], user_id=scope)
            return {
                "libraries": [
                    _library_payload(row, stats.get(row.id)) for row in rows
                ]
            }

    @router.post("/api/knowledge/libraries")
    def create_library(payload: LibraryCreate, request: Request) -> dict:
        scope = scope_user(request)
        name = _clean_text(payload.name)
        if not name:
            raise HTTPException(status_code=400, detail="资料库名称不能为空")
        with database.session_scope() as session:
            row = KnowledgeRepository(session).create_library(
                name=name,
                description=payload.description.strip(),
                color=payload.color.strip() or "accent",
                user_id=scope,
            )
            return {"library": _library_payload(row)}

    @router.get("/api/knowledge/libraries/{library_id}")
    def get_library(library_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            row = repo.get_library(library_id, user_id=scope)
            if row is None:
                raise HTTPException(status_code=404, detail="资料库不存在")
            stats = repo.library_stats([row.id], user_id=scope).get(row.id)
            return {"library": _library_payload(row, stats)}

    @router.patch("/api/knowledge/libraries/{library_id}")
    def update_library(library_id: str, payload: LibraryUpdate, request: Request) -> dict:
        scope = scope_user(request)
        fields = payload.model_dump(exclude_unset=True)
        if "name" in fields:
            fields["name"] = _clean_text(fields["name"])
            if not fields["name"]:
                raise HTTPException(status_code=400, detail="资料库名称不能为空")
        if "description" in fields and fields["description"] is not None:
            fields["description"] = fields["description"].strip()
        if "color" in fields and fields["color"] is not None:
            fields["color"] = fields["color"].strip() or "accent"
        with database.session_scope() as session:
            row = KnowledgeRepository(session).update_library(
                library_id, user_id=scope, **fields
            )
            if row is None:
                raise HTTPException(status_code=404, detail="资料库不存在")
            return {"library": _library_payload(row)}

    @router.delete("/api/knowledge/libraries/{library_id}")
    def delete_library(library_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            if not KnowledgeRepository(session).delete_library(library_id, user_id=scope):
                raise HTTPException(status_code=404, detail="资料库不存在")
            return {"ok": True}

    # ---- Documents ----

    @router.get("/api/knowledge/documents")
    def list_documents(request: Request, library_id: str | None = None) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            rows = repo.list_documents(library_id=library_id, user_id=scope)
            stats = repo.document_stats([row.id for row in rows], user_id=scope)
            return {
                "documents": [
                    _document_payload(row, stats.get(row.id)) for row in rows
                ]
            }

    @router.post("/api/knowledge/libraries/{library_id}/documents")
    def import_document(
        library_id: str, payload: DocumentImport, request: Request
    ) -> dict:
        scope = scope_user(request)
        filename = PurePath(payload.filename).name
        if not filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")
        try:
            text, extension = extract_document_text(filename, payload.content)
        except KnowledgeImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        chunks = split_text(text)
        if not chunks:
            raise HTTPException(status_code=400, detail="资料未能切分为可检索片段")
        title = _clean_text(payload.title or "", fallback=PurePath(filename).stem)
        mime_type = (
            payload.mime_type
            or TEXT_MIME_TYPES.get(extension)
            or mimetypes.guess_type(filename)[0]
            or "text/plain"
        )
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            if repo.get_library(library_id, user_id=scope) is None:
                raise HTTPException(status_code=404, detail="资料库不存在")
            row = repo.create_document(
                library_id=library_id,
                title=title[:240],
                filename=filename,
                mime_type=mime_type,
                content_text=text,
                content_markdown=text,
                chunks=chunks,
                user_id=scope,
            )
            return {
                "document": _document_payload(
                    row,
                    {"chunk_count": len(chunks), "char_count": len(text)},
                )
            }

    @router.get("/api/knowledge/documents/{document_id}")
    def get_document(document_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            row = repo.get_document(document_id, user_id=scope)
            if row is None:
                raise HTTPException(status_code=404, detail="资料不存在")
            stats = repo.document_stats([row.id], user_id=scope).get(row.id)
            return {"document": _document_payload(row, stats, content=True)}

    @router.patch("/api/knowledge/documents/{document_id}")
    def rename_document(
        document_id: str, payload: DocumentRename, request: Request
    ) -> dict:
        scope = scope_user(request)
        title = _clean_text(payload.title)
        if not title:
            raise HTTPException(status_code=400, detail="资料标题不能为空")
        with database.session_scope() as session:
            row = KnowledgeRepository(session).update_document(
                document_id, user_id=scope, title=title[:240]
            )
            if row is None:
                raise HTTPException(status_code=404, detail="资料不存在")
            return {"document": _document_payload(row)}

    @router.delete("/api/knowledge/documents/{document_id}")
    def delete_document(document_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            if not KnowledgeRepository(session).delete_document(
                document_id, user_id=scope
            ):
                raise HTTPException(status_code=404, detail="资料不存在")
            return {"ok": True}

    # ---- Search / Ask ----

    @router.get("/api/knowledge/search")
    def search(
        request: Request,
        q: str,
        library_id: str | None = None,
        limit: int = 12,
    ) -> dict:
        query = q.strip()
        if not query:
            raise HTTPException(status_code=400, detail="检索词不能为空")
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            if library_id and repo.get_library(library_id, user_id=scope) is None:
                raise HTTPException(status_code=404, detail="资料库不存在")
            results = repo.search(
                query,
                library_id=library_id,
                limit=limit,
                user_id=scope,
            )
            return {"query": query, "results": results, "count": len(results)}

    @router.post("/api/knowledge/ask/stream")
    def ask_stream(payload: AskRequest, request: Request) -> StreamingResponse:
        scope = scope_user(request)
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="问题不能为空")
        with database.session_scope() as session:
            repo = KnowledgeRepository(session)
            if payload.library_id and repo.get_library(
                payload.library_id, user_id=scope
            ) is None:
                raise HTTPException(status_code=404, detail="资料库不存在")
            sources = repo.search(
                question,
                library_id=payload.library_id,
                limit=payload.top_k,
                user_id=scope,
            )
        if not sources:
            raise HTTPException(status_code=404, detail="没有检索到可引用的资料")
        model = resolve_model(config, payload.model_name)
        messages = _build_ask_messages(question, sources)

        async def event_source():
            events: queue.Queue = queue.Queue()
            cancelled = threading.Event()
            loop = asyncio.get_running_loop()

            def work() -> None:
                try:
                    events.put({"meta": {"model": model.name, "sources": sources}})
                    for chunk in LLMClient(model).stream_chunks(messages):
                        if cancelled.is_set():
                            break
                        events.put({"delta": chunk})
                    events.put({"done": True})
                except Exception as exc:  # noqa: BLE001
                    events.put({"error": {"message": str(exc)}})
                    events.put({"done": True})
                finally:
                    events.put(None)

            threading.Thread(target=work, daemon=True).start()
            try:
                while True:
                    if await request.is_disconnected():
                        cancelled.set()
                        break
                    item = await loop.run_in_executor(None, _queue_get, events, 0.5)
                    if item is _QUEUE_EMPTY:
                        continue
                    if item is None:
                        break
                    yield _sse(item)
            finally:
                cancelled.set()

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
