"""文档助手仓储：文档 CRUD、统计与素材来源索引。"""

from __future__ import annotations

import builtins
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import DocRow, KnowledgeDocumentRow, KnowledgeLibraryRow, NoteRow


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _apply_scope(stmt, column, user_id: UserScope):
    if user_id is None:
        return stmt.where(column.is_(None))
    if user_id is not ANY_USER:
        return stmt.where(column == user_id)
    return stmt


def _excerpt(value: str, limit: int = 150) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def doc_to_dict(row: DocRow, *, include_content: bool = False) -> dict:
    content = row.content or ""
    payload = {
        "id": row.id,
        "title": row.title,
        "format": row.format,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "excerpt": _excerpt(content),
        "characters": len(content),
        "lines": content.count("\n") + 1 if content else 1,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_content:
        payload["content"] = content
    return payload


class DocRepository:
    """文档表的读写入口；所有公开查询都显式按用户作用域过滤。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list(
        self,
        *,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
        user_id: UserScope = ANY_USER,
    ) -> list[DocRow]:
        stmt = select(DocRow).order_by(DocRow.updated_at.desc(), DocRow.created_at.desc())
        stmt = _apply_scope(stmt, DocRow.user_id, user_id)
        if q:
            term = q.strip()
            if term:
                stmt = stmt.where(DocRow.title.contains(term) | DocRow.content.contains(term))
        stmt = stmt.offset(max(0, offset)).limit(max(1, min(limit, 500)))
        return list(self.session.scalars(stmt))

    def get(self, doc_id: str, *, user_id: UserScope = ANY_USER) -> DocRow | None:
        stmt = select(DocRow).where(DocRow.id == doc_id)
        stmt = _apply_scope(stmt, DocRow.user_id, user_id)
        return self.session.scalars(stmt).first()

    def create(
        self,
        *,
        title: str,
        content: str = "",
        format: str = "markdown",
        source_type: str = "blank",
        source_id: str | None = None,
        user_id: str | None = None,
    ) -> DocRow:
        # 保证“最新创建”严格晚于当前作用域内的最后更新时间。系统时钟回拨或同一
        # 微秒内连续创建时，仅按时间排序仍可能返回不稳定顺序。
        latest_stmt = (
            select(DocRow.updated_at)
            .order_by(DocRow.updated_at.desc())
            .limit(1)
        )
        latest_stmt = _apply_scope(latest_stmt, DocRow.user_id, user_id)
        latest = self.session.scalar(latest_stmt)
        if latest is not None and latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        timestamp = datetime.now(UTC)
        if latest is not None and timestamp <= latest:
            timestamp = latest + timedelta(microseconds=1)
        row = DocRow(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            content=content,
            format=format,
            source_type=source_type,
            source_id=source_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update(
        self, doc_id: str, *, user_id: UserScope = ANY_USER, **fields
    ) -> DocRow | None:
        row = self.get(doc_id, user_id=user_id)
        if row is None:
            return None
        for key in ("title", "content", "format"):
            if key in fields and fields[key] is not None:
                setattr(row, key, fields[key])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete(self, doc_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get(doc_id, user_id=user_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def stats(self, *, user_id: UserScope = ANY_USER) -> dict[str, int]:
        stmt = select(DocRow)
        stmt = _apply_scope(stmt, DocRow.user_id, user_id)
        rows = list(self.session.scalars(stmt))
        today = datetime.now(UTC).date()
        return {
            "documents": len(rows),
            "characters": sum(len(row.content or "") for row in rows),
            "updated_today": sum(
                1
                for row in rows
                if row.updated_at and row.updated_at.date() == today
            ),
            "sources": sum(1 for row in rows if row.source_type != "blank"),
        }

    def source_options(self, *, user_id: UserScope = ANY_USER) -> builtins.list[dict]:
        """返回可转成文档的笔记与知识库资料，按最近更新排序。"""

        notes_stmt = select(NoteRow)
        notes_stmt = _apply_scope(notes_stmt, NoteRow.user_id, user_id)
        documents_stmt = (
            select(KnowledgeDocumentRow, KnowledgeLibraryRow)
            .join(
                KnowledgeLibraryRow,
                KnowledgeLibraryRow.id == KnowledgeDocumentRow.library_id,
            )
            .order_by(
                KnowledgeDocumentRow.updated_at.desc(),
                KnowledgeDocumentRow.created_at.desc(),
            )
        )
        documents_stmt = _apply_scope(
            documents_stmt, KnowledgeDocumentRow.user_id, user_id
        )
        documents_stmt = _apply_scope(
            documents_stmt, KnowledgeLibraryRow.user_id, user_id
        )

        items: list[dict] = []
        for note in self.session.scalars(notes_stmt):
            items.append(
                {
                    "type": "note",
                    "id": note.id,
                    "title": note.title,
                    "excerpt": _excerpt(note.content),
                    "label": "笔记",
                    "characters": len(note.content or ""),
                    "updated_at": _iso(note.updated_at),
                }
            )
        for document, library in self.session.execute(documents_stmt):
            source_text = document.content_markdown or document.content_text
            items.append(
                {
                    "type": "knowledge",
                    "id": document.id,
                    "title": document.title,
                    "excerpt": _excerpt(source_text),
                    "label": library.name,
                    "characters": len(document.content_text or ""),
                    "updated_at": _iso(document.updated_at),
                }
            )
        items.sort(key=lambda item: item["updated_at"] or "", reverse=True)
        return items