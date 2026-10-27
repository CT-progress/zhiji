"""文档助手 API：文档 CRUD、素材取材、Markdown 导出与流式 AI 协作。"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from zhiji.config import ConfigManager
from zhiji.llm.client import LLMClient
from zhiji.storage import Database
from zhiji.storage.doc_repository import DocRepository, doc_to_dict
from zhiji.storage.knowledge_repository import KnowledgeRepository
from zhiji.storage.note_repository import NoteRepository

DocAction = Literal["summarize", "ask", "continue", "polish"]
DocFormat = Literal["markdown", "text"]
SourceType = Literal["blank", "note", "knowledge"]
_QUEUE_EMPTY = object()


class DocCreate(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    content: str = Field(default="", max_length=1_000_000)
    format: DocFormat = "markdown"
    source_type: SourceType = "blank"
    source_id: str | None = Field(default=None, max_length=36)


class DocUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    content: str | None = Field(default=None, max_length=1_000_000)
    format: DocFormat | None = None


class AssistRequest(BaseModel):
    action: DocAction
    instruction: str = Field(default="", max_length=4_000)
    content: str | None = Field(default=None, max_length=1_000_000)
    selection: str | None = Field(default=None, max_length=200_000)
    model_name: str | None = Field(default=None, max_length=120)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _queue_get(target: queue.Queue, timeout: float):
    try:
        return target.get(timeout=timeout)
    except queue.Empty:
        return _QUEUE_EMPTY


def _clean_title(value: str) -> str:
    return " ".join(str(value or "").split())[:240]


def _clip(value: str, limit: int = 120_000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[正文过长，已在当前请求中截断]"


def _build_assist_messages(
    *,
    action: DocAction,
    title: str,
    content: str,
    instruction: str,
    selection: str,
) -> list[dict]:
    system = (
        "你是知记的文档协作助手。你帮助用户阅读、总结、续写和润色中文文档。\n"
        "严格遵守：只输出用户要求的结果本身，不要解释你的工作流程，不要添加“以下是”之类的前言。\n"
        "保留原文事实与语气，不虚构文档中没有的信息；不确定时直接指出信息不足。\n"
        "输出使用 Markdown；除非任务要求，否则不要重复整篇原文。"
    )
    context = f"文档标题：{title}\n\n<document>\n{_clip(content)}\n</document>"
    if action == "summarize":
        task = (
            "请把文档整理成一份可快速阅读的摘要：先用一句话给出核心结论，再列出 3-6 个要点，"
            "最后补充需要进一步确认的问题（如果存在）。"
        )
    elif action == "ask":
        task = f"请只依据文档回答下面的问题，并明确区分文档事实与你的推断：\n{instruction.strip()}"
    elif action == "continue":
        task = (
            "请顺着文档当前的思路和语气自然续写 2-4 段。不要重复已有内容，不要写总结，"
            "直接从续写正文开始。"
        )
        if instruction.strip():
            task += f"\n续写方向：{instruction.strip()}"
    else:
        target = selection.strip() or content.strip()
        task = (
            "请润色下面的文本：保持事实、观点和段落结构不变，改善表达、衔接和节奏。"
            "只输出润色后的文本。\n\n<selection>\n"
            f"{_clip(target, 80_000)}\n</selection>"
        )
        if instruction.strip():
            task += f"\n额外要求：{instruction.strip()}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{context}\n\n任务：\n{task}"},
    ]


def create_docs_router(
    database: Database,
    scope_user: Callable[[Request], str | None],
    *,
    config: ConfigManager,
    resolve_model: Callable[[ConfigManager, str | None], Any],
) -> APIRouter:
    """构建工作区④的文档助手路由。"""

    router = APIRouter()

    @router.get("/api/docs/stats")
    def docs_stats(request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            return {"stats": DocRepository(session).stats(user_id=scope)}

    @router.get("/api/docs/sources")
    def list_sources(request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            return {"sources": DocRepository(session).source_options(user_id=scope)}

    @router.get("/api/docs")
    def list_docs(request: Request, q: str | None = None) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            rows = DocRepository(session).list(q=q, user_id=scope)
            return {"documents": [doc_to_dict(row) for row in rows]}

    @router.post("/api/docs")
    def create_doc(payload: DocCreate, request: Request) -> dict:
        scope = scope_user(request)
        title = _clean_title(payload.title or "") or "无标题文档"
        content = payload.content
        source_id = None
        with database.session_scope() as session:
            if payload.source_type == "note":
                if not payload.source_id:
                    raise HTTPException(status_code=400, detail="请选择来源笔记")
                source = NoteRepository(session).get(payload.source_id, user_id=scope)
                if source is None:
                    raise HTTPException(status_code=404, detail="来源笔记不存在")
                title = _clean_title(payload.title or "") or _clean_title(source.title) or "无标题文档"
                content = source.content or ""
                source_id = source.id
            elif payload.source_type == "knowledge":
                if not payload.source_id:
                    raise HTTPException(status_code=400, detail="请选择知识库资料")
                source_doc = KnowledgeRepository(session).get_document(
                    payload.source_id, user_id=scope
                )
                if source_doc is None:
                    raise HTTPException(status_code=404, detail="来源资料不存在")
                title = (
                    _clean_title(payload.title or "")
                    or _clean_title(source_doc.title)
                    or "无标题文档"
                )
                content = source_doc.content_markdown or source_doc.content_text or ""
                source_id = source_doc.id
            row = DocRepository(session).create(
                title=title,
                content=content,
                format=payload.format,
                source_type=payload.source_type,
                source_id=source_id,
                user_id=scope,
            )
            return {"document": doc_to_dict(row, include_content=True)}

    @router.get("/api/docs/{doc_id}")
    def get_doc(doc_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            row = DocRepository(session).get(doc_id, user_id=scope)
            if row is None:
                raise HTTPException(status_code=404, detail="文档不存在")
            return {"document": doc_to_dict(row, include_content=True)}

    @router.put("/api/docs/{doc_id}")
    def update_doc(doc_id: str, payload: DocUpdate, request: Request) -> dict:
        scope = scope_user(request)
        fields = payload.model_dump(exclude_unset=True)
        if "title" in fields:
            fields["title"] = _clean_title(fields["title"] or "") or "无标题文档"
        with database.session_scope() as session:
            row = DocRepository(session).update(doc_id, user_id=scope, **fields)
            if row is None:
                raise HTTPException(status_code=404, detail="文档不存在")
            return {"document": doc_to_dict(row, include_content=True)}

    @router.delete("/api/docs/{doc_id}")
    def delete_doc(doc_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            if not DocRepository(session).delete(doc_id, user_id=scope):
                raise HTTPException(status_code=404, detail="文档不存在")
            return {"ok": True}

    @router.get("/api/docs/{doc_id}/export")
    def export_doc(doc_id: str, request: Request) -> Response:
        scope = scope_user(request)
        with database.session_scope() as session:
            row = DocRepository(session).get(doc_id, user_id=scope)
            if row is None:
                raise HTTPException(status_code=404, detail="文档不存在")
            title = row.title or "文档"
            content = row.content or ""
            doc_format = row.format
        safe = re.sub(r'[\\/:*?"<>|\s]+', "-", title).strip(".-") or "document"
        filename = safe + (".md" if doc_format == "markdown" else ".txt")
        media_type = "text/markdown" if doc_format == "markdown" else "text/plain"
        disposition = (
            'attachment; filename="document.md"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
        return Response(
            content=content,
            media_type=f"{media_type}; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )

    @router.post("/api/docs/{doc_id}/assist/stream")
    def assist_stream(
        doc_id: str, payload: AssistRequest, request: Request
    ) -> StreamingResponse:
        scope = scope_user(request)
        with database.session_scope() as session:
            row = DocRepository(session).get(doc_id, user_id=scope)
            if row is None:
                raise HTTPException(status_code=404, detail="文档不存在")
            title = row.title
            stored_content = row.content or ""
        content = payload.content if payload.content is not None else stored_content
        if not content.strip():
            raise HTTPException(status_code=400, detail="文档还是空的，请先写一点内容")
        if payload.action == "ask" and not payload.instruction.strip():
            raise HTTPException(status_code=400, detail="请输入要问文档的问题")
        if payload.action == "polish" and not (payload.selection or "").strip() and not content.strip():
            raise HTTPException(status_code=400, detail="没有可润色的文本")

        model = resolve_model(config, payload.model_name)
        messages = _build_assist_messages(
            action=payload.action,
            title=title,
            content=content,
            instruction=payload.instruction,
            selection=payload.selection or "",
        )

        async def event_source():
            events: queue.Queue = queue.Queue()
            cancelled = threading.Event()
            loop = asyncio.get_running_loop()

            def work() -> None:
                try:
                    events.put({"meta": {"model": model.name, "action": payload.action}})
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