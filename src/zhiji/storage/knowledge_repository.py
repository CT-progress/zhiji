"""知识库仓储：资料库、资料、切片与本地检索。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from zhiji.knowledge import embedding_for, parse_embedding, score_chunk, snippet_for
from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import (
    KnowledgeChunkRow,
    KnowledgeDocumentRow,
    KnowledgeLibraryRow,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _apply_scope(stmt, column, user_id: UserScope):
    if user_id is None:
        return stmt.where(column.is_(None))
    if user_id is not ANY_USER:
        return stmt.where(column == user_id)
    return stmt


def library_to_dict(row: KnowledgeLibraryRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "color": row.color,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def document_to_dict(
    row: KnowledgeDocumentRow,
    *,
    chunk_count: int = 0,
    char_count: int = 0,
    include_content: bool = False,
) -> dict:
    payload = {
        "id": row.id,
        "library_id": row.library_id,
        "title": row.title,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size": row.size,
        "status": row.status,
        "error": row.error,
        "chunk_count": chunk_count,
        "char_count": char_count or len(row.content_text),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_content:
        payload["content"] = row.content_markdown or row.content_text
        payload["text"] = row.content_text
    return payload


class KnowledgeRepository:
    """所有读写都显式接收用户作用域，跨用户资料一律视为不存在。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_libraries(self, *, user_id: UserScope = ANY_USER) -> list[KnowledgeLibraryRow]:
        stmt = select(KnowledgeLibraryRow).order_by(
            KnowledgeLibraryRow.updated_at.desc(), KnowledgeLibraryRow.created_at.desc()
        )
        stmt = _apply_scope(stmt, KnowledgeLibraryRow.user_id, user_id)
        return list(self.session.scalars(stmt))

    def get_library(
        self, library_id: str, *, user_id: UserScope = ANY_USER
    ) -> KnowledgeLibraryRow | None:
        stmt = select(KnowledgeLibraryRow).where(KnowledgeLibraryRow.id == library_id)
        stmt = _apply_scope(stmt, KnowledgeLibraryRow.user_id, user_id)
        return self.session.scalars(stmt).first()

    def create_library(
        self,
        *,
        name: str,
        description: str = "",
        color: str = "accent",
        user_id: str | None = None,
    ) -> KnowledgeLibraryRow:
        row = KnowledgeLibraryRow(
            id=str(uuid4()),
            user_id=user_id,
            name=name,
            description=description,
            color=color,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update_library(
        self, library_id: str, *, user_id: UserScope = ANY_USER, **fields
    ) -> KnowledgeLibraryRow | None:
        row = self.get_library(library_id, user_id=user_id)
        if row is None:
            return None
        for key in ("name", "description", "color"):
            if key in fields and fields[key] is not None:
                setattr(row, key, fields[key])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete_library(self, library_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get_library(library_id, user_id=user_id)
        if row is None:
            return False
        document_ids = select(KnowledgeDocumentRow.id).where(
            KnowledgeDocumentRow.library_id == row.id
        )
        self.session.execute(
            delete(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id.in_(document_ids))
        )
        self.session.execute(
            delete(KnowledgeDocumentRow).where(KnowledgeDocumentRow.library_id == row.id)
        )
        self.session.delete(row)
        self.session.flush()
        return True

    def list_documents(
        self,
        *,
        library_id: str | None = None,
        user_id: UserScope = ANY_USER,
    ) -> list[KnowledgeDocumentRow]:
        stmt = select(KnowledgeDocumentRow).order_by(
            KnowledgeDocumentRow.updated_at.desc(), KnowledgeDocumentRow.created_at.desc()
        )
        stmt = _apply_scope(stmt, KnowledgeDocumentRow.user_id, user_id)
        if library_id:
            stmt = stmt.where(KnowledgeDocumentRow.library_id == library_id)
        return list(self.session.scalars(stmt))

    def get_document(
        self, document_id: str, *, user_id: UserScope = ANY_USER
    ) -> KnowledgeDocumentRow | None:
        stmt = select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.id == document_id)
        stmt = _apply_scope(stmt, KnowledgeDocumentRow.user_id, user_id)
        return self.session.scalars(stmt).first()

    def create_document(
        self,
        *,
        library_id: str,
        title: str,
        filename: str,
        mime_type: str,
        content_text: str,
        content_markdown: str,
        chunks: list[dict],
        user_id: str | None = None,
    ) -> KnowledgeDocumentRow:
        row = KnowledgeDocumentRow(
            id=str(uuid4()),
            library_id=library_id,
            user_id=user_id,
            title=title,
            filename=filename,
            mime_type=mime_type,
            size=len(content_text.encode("utf-8")),
            content_text=content_text,
            content_markdown=content_markdown,
            status="indexed",
            error="",
        )
        self.session.add(row)
        self.session.flush()
        for chunk in chunks:
            self.session.add(
                KnowledgeChunkRow(
                    document_id=row.id,
                    user_id=user_id,
                    ordinal=chunk["ordinal"],
                    text=chunk["text"],
                    embedding_json=json.dumps(embedding_for(chunk["text"])),
                    char_count=chunk["char_count"],
                    start_offset=chunk["start_offset"],
                    end_offset=chunk["end_offset"],
                )
            )
        self.session.flush()
        return row

    def update_document(
        self, document_id: str, *, user_id: UserScope = ANY_USER, title: str | None = None
    ) -> KnowledgeDocumentRow | None:
        row = self.get_document(document_id, user_id=user_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete_document(self, document_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get_document(document_id, user_id=user_id)
        if row is None:
            return False
        self.session.execute(
            delete(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id == row.id)
        )
        self.session.delete(row)
        self.session.flush()
        return True

    def document_stats(
        self, document_ids: list[str], *, user_id: UserScope = ANY_USER
    ) -> dict[str, dict[str, int]]:
        if not document_ids:
            return {}
        stmt = (
            select(
                KnowledgeChunkRow.document_id,
                func.count(KnowledgeChunkRow.id),
                func.coalesce(func.sum(KnowledgeChunkRow.char_count), 0),
            )
            .where(KnowledgeChunkRow.document_id.in_(document_ids))
            .group_by(KnowledgeChunkRow.document_id)
        )
        stmt = _apply_scope(stmt, KnowledgeChunkRow.user_id, user_id)
        return {
            document_id: {"chunk_count": int(count), "char_count": int(chars)}
            for document_id, count, chars in self.session.execute(stmt)
        }

    def library_stats(
        self, library_ids: list[str], *, user_id: UserScope = ANY_USER
    ) -> dict[str, dict[str, int]]:
        if not library_ids:
            return {}
        stmt = (
            select(
                KnowledgeDocumentRow.library_id,
                func.count(func.distinct(KnowledgeDocumentRow.id)),
                func.coalesce(func.sum(KnowledgeChunkRow.char_count), 0),
            )
            .outerjoin(
                KnowledgeChunkRow,
                KnowledgeChunkRow.document_id == KnowledgeDocumentRow.id,
            )
            .where(KnowledgeDocumentRow.library_id.in_(library_ids))
            .group_by(KnowledgeDocumentRow.library_id)
        )
        stmt = _apply_scope(stmt, KnowledgeDocumentRow.user_id, user_id)
        return {
            library_id: {"document_count": int(docs), "char_count": int(chars)}
            for library_id, docs, chars in self.session.execute(stmt)
        }

    def stats(self, *, user_id: UserScope = ANY_USER) -> dict[str, int]:
        library_stmt = select(func.count(KnowledgeLibraryRow.id))
        library_stmt = _apply_scope(library_stmt, KnowledgeLibraryRow.user_id, user_id)
        document_stmt = select(func.count(KnowledgeDocumentRow.id))
        document_stmt = _apply_scope(document_stmt, KnowledgeDocumentRow.user_id, user_id)
        chunk_stmt = select(
            func.count(KnowledgeChunkRow.id),
            func.coalesce(func.sum(KnowledgeChunkRow.char_count), 0),
        )
        chunk_stmt = _apply_scope(chunk_stmt, KnowledgeChunkRow.user_id, user_id)
        chunk_count, char_count = self.session.execute(chunk_stmt).one()
        return {
            "libraries": int(self.session.scalar(library_stmt) or 0),
            "documents": int(self.session.scalar(document_stmt) or 0),
            "chunks": int(chunk_count or 0),
            "characters": int(char_count or 0),
        }

    def search(
        self,
        query: str,
        *,
        library_id: str | None = None,
        limit: int = 12,
        user_id: UserScope = ANY_USER,
    ) -> list[dict]:
        stmt = (
            select(KnowledgeChunkRow, KnowledgeDocumentRow, KnowledgeLibraryRow)
            .join(
                KnowledgeDocumentRow,
                KnowledgeDocumentRow.id == KnowledgeChunkRow.document_id,
            )
            .join(
                KnowledgeLibraryRow,
                KnowledgeLibraryRow.id == KnowledgeDocumentRow.library_id,
            )
        )
        stmt = _apply_scope(stmt, KnowledgeChunkRow.user_id, user_id)
        stmt = _apply_scope(stmt, KnowledgeDocumentRow.user_id, user_id)
        stmt = _apply_scope(stmt, KnowledgeLibraryRow.user_id, user_id)
        if library_id:
            stmt = stmt.where(KnowledgeDocumentRow.library_id == library_id)
        results: list[dict] = []
        for chunk, document, library in self.session.execute(stmt):
            score, matched = score_chunk(
                query,
                chunk.text,
                title=document.title,
                embedding=parse_embedding(chunk.embedding_json),
            )
            if score <= 0:
                continue
            results.append(
                {
                    "score": score,
                    "matched_terms": matched[:8],
                    "snippet": snippet_for(chunk.text, query),
                    "chunk": {
                        "id": chunk.id,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "start_offset": chunk.start_offset,
                        "end_offset": chunk.end_offset,
                    },
                    "document": {
                        "id": document.id,
                        "title": document.title,
                        "filename": document.filename,
                        "mime_type": document.mime_type,
                    },
                    "library": {
                        "id": library.id,
                        "name": library.name,
                        "color": library.color,
                    },
                }
            )
        results.sort(key=lambda item: (item["score"], -item["chunk"]["ordinal"]), reverse=True)
        return results[: max(1, min(limit, 50))]
