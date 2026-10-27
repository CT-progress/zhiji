"""笔记与待办 API 路由（阶段 C · 工作区②）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zhiji.storage import Database
from zhiji.storage.note_repository import (
    NoteRepository,
    TodoRepository,
    note_to_dict,
    todo_to_dict,
)


class NoteCreate(BaseModel):
    title: str = "无标题"
    content: str = ""
    tags: str = ""
    pinned: bool = False


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: str | None = None
    pinned: bool | None = None


class TodoCreate(BaseModel):
    title: str
    priority: int = 0
    due_date: str | None = None
    note_id: str | None = None


class TodoUpdate(BaseModel):
    title: str | None = None
    done: bool | None = None
    priority: int | None = None
    due_date: str | None = None
    note_id: str | None = None


def _parse_due_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip())
    except ValueError:
        return None


def create_notes_router(
    database: Database,
    scope_user: Callable[[Request], str | None],
) -> APIRouter:
    """构建笔记 + 待办的 CRUD 路由。"""

    router = APIRouter()

    # ---- Notes ----

    @router.get("/api/notes")
    def list_notes(request: Request, q: str | None = None, tag: str | None = None) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = NoteRepository(session)
            notes = repo.list(q=q, tag=tag, user_id=scope)
            return {"notes": [note_to_dict(n) for n in notes]}

    @router.post("/api/notes")
    def create_note(payload: NoteCreate, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = NoteRepository(session)
            row = repo.create(
                title=payload.title,
                content=payload.content,
                tags=payload.tags,
                pinned=payload.pinned,
                user_id=scope,
            )
            return {"note": note_to_dict(row)}

    @router.get("/api/notes/{note_id}")
    def get_note(note_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = NoteRepository(session)
            row = repo.get(note_id, user_id=scope)
            if row is None:
                raise HTTPException(status_code=404, detail="笔记不存在")
            return {"note": note_to_dict(row)}

    @router.put("/api/notes/{note_id}")
    def update_note(note_id: str, payload: NoteUpdate, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = NoteRepository(session)
            row = repo.update(note_id, user_id=scope, **payload.model_dump(exclude_unset=True))
            if row is None:
                raise HTTPException(status_code=404, detail="笔记不存在")
            return {"note": note_to_dict(row)}

    @router.delete("/api/notes/{note_id}")
    def delete_note(note_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = NoteRepository(session)
            ok = repo.delete(note_id, user_id=scope)
            if not ok:
                raise HTTPException(status_code=404, detail="笔记不存在")
            return {"ok": True}

    # ---- Todos ----

    @router.get("/api/todos")
    def list_todos(
        request: Request,
        done: bool | None = None,
        note_id: str | None = None,
    ) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = TodoRepository(session)
            todos = repo.list(done=done, note_id=note_id, user_id=scope)
            return {"todos": [todo_to_dict(t) for t in todos]}

    @router.post("/api/todos")
    def create_todo(payload: TodoCreate, request: Request) -> dict:
        scope = scope_user(request)
        note_id = payload.note_id or None
        with database.session_scope() as session:
            if note_id is not None:
                note = NoteRepository(session).get(note_id, user_id=scope)
                if note is None:
                    raise HTTPException(status_code=404, detail="关联笔记不存在")
            repo = TodoRepository(session)
            row = repo.create(
                title=payload.title,
                priority=payload.priority,
                due_date=_parse_due_date(payload.due_date),
                note_id=note_id,
                user_id=scope,
            )
            return {"todo": todo_to_dict(row)}

    @router.put("/api/todos/{todo_id}")
    def update_todo(todo_id: str, payload: TodoUpdate, request: Request) -> dict:
        scope = scope_user(request)
        fields = payload.model_dump(exclude_unset=True)
        if "due_date" in fields:
            fields["due_date"] = _parse_due_date(fields["due_date"])
        if fields.get("note_id") == "":
            fields["note_id"] = None
        with database.session_scope() as session:
            if fields.get("note_id") is not None:
                note = NoteRepository(session).get(fields["note_id"], user_id=scope)
                if note is None:
                    raise HTTPException(status_code=404, detail="关联笔记不存在")
            repo = TodoRepository(session)
            row = repo.update(todo_id, user_id=scope, **fields)
            if row is None:
                raise HTTPException(status_code=404, detail="待办不存在")
            return {"todo": todo_to_dict(row)}

    @router.delete("/api/todos/{todo_id}")
    def delete_todo(todo_id: str, request: Request) -> dict:
        scope = scope_user(request)
        with database.session_scope() as session:
            repo = TodoRepository(session)
            ok = repo.delete(todo_id, user_id=scope)
            if not ok:
                raise HTTPException(status_code=404, detail="待办不存在")
            return {"ok": True}

    return router

