"""笔记与待办仓储：对 notes / todos 表的 CRUD 封装。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import NoteRow, TodoRow


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def note_to_dict(row: NoteRow) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "tags": row.tags,
        "pinned": row.pinned,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def todo_to_dict(row: TodoRow) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "done": row.done,
        "priority": row.priority,
        "due_date": _iso(row.due_date),
        "note_id": row.note_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }



class NoteRepository:
    """笔记表读写入口。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list(
        self,
        *,
        q: str | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: UserScope = ANY_USER,
    ) -> list[NoteRow]:
        stmt = select(NoteRow).order_by(
            NoteRow.pinned.desc(), NoteRow.updated_at.desc()
        )
        if user_id is None:
            stmt = stmt.where(NoteRow.user_id.is_(None))
        elif user_id is not ANY_USER:
            stmt = stmt.where(NoteRow.user_id == user_id)
        if q:
            stmt = stmt.where(NoteRow.title.contains(q) | NoteRow.content.contains(q))
        if tag:
            stmt = stmt.where(NoteRow.tags.contains(tag))
        stmt = stmt.offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def get(self, note_id: str, *, user_id: UserScope = ANY_USER) -> NoteRow | None:
        stmt = select(NoteRow).where(NoteRow.id == note_id)
        if user_id is None:
            stmt = stmt.where(NoteRow.user_id.is_(None))
        elif user_id is not ANY_USER:
            stmt = stmt.where(NoteRow.user_id == user_id)
        return self.session.scalars(stmt).first()

    def create(
        self,
        *,
        title: str = "无标题",
        content: str = "",
        tags: str = "",
        pinned: bool = False,
        user_id: str | None = None,
    ) -> NoteRow:
        row = NoteRow(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            content=content,
            tags=tags,
            pinned=pinned,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update(
        self, note_id: str, *, user_id: UserScope = ANY_USER, **fields
    ) -> NoteRow | None:
        row = self.get(note_id, user_id=user_id)
        if row is None:
            return None
        for key in ("title", "content", "tags", "pinned"):
            if key in fields and fields[key] is not None:
                setattr(row, key, fields[key])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete(self, note_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get(note_id, user_id=user_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True


class TodoRepository:
    """待办表读写入口。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list(
        self,
        *,
        done: bool | None = None,
        note_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
        user_id: UserScope = ANY_USER,
    ) -> list[TodoRow]:
        stmt = select(TodoRow).order_by(
            TodoRow.done.asc(), TodoRow.priority.desc(), TodoRow.created_at.desc()
        )
        if user_id is None:
            stmt = stmt.where(TodoRow.user_id.is_(None))
        elif user_id is not ANY_USER:
            stmt = stmt.where(TodoRow.user_id == user_id)
        if done is not None:
            stmt = stmt.where(TodoRow.done == done)
        if note_id is not None:
            stmt = stmt.where(TodoRow.note_id == note_id)
        stmt = stmt.offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def get(self, todo_id: str, *, user_id: UserScope = ANY_USER) -> TodoRow | None:
        stmt = select(TodoRow).where(TodoRow.id == todo_id)
        if user_id is None:
            stmt = stmt.where(TodoRow.user_id.is_(None))
        elif user_id is not ANY_USER:
            stmt = stmt.where(TodoRow.user_id == user_id)
        return self.session.scalars(stmt).first()

    def create(
        self,
        *,
        title: str,
        priority: int = 0,
        due_date: datetime | None = None,
        note_id: str | None = None,
        user_id: str | None = None,
    ) -> TodoRow:
        row = TodoRow(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            done=False,
            priority=priority,
            due_date=due_date,
            note_id=note_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update(
        self, todo_id: str, *, user_id: UserScope = ANY_USER, **fields
    ) -> TodoRow | None:
        row = self.get(todo_id, user_id=user_id)
        if row is None:
            return None
        for key in ("title", "done", "priority"):
            if key in fields and fields[key] is not None:
                setattr(row, key, fields[key])
        # 截止日期与关联笔记允许显式传 null 清空。
        for key in ("due_date", "note_id"):
            if key in fields:
                setattr(row, key, fields[key])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete(self, todo_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get(todo_id, user_id=user_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True
