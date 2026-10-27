"""媒体工坊仓储：素材、产物与用户作用域内的统计。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import MediaArtifactRow, MediaItemRow


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _apply_scope(stmt, column, user_id: UserScope):
    if user_id is None:
        return stmt.where(column.is_(None))
    if user_id is not ANY_USER:
        return stmt.where(column == user_id)
    return stmt


def media_to_dict(row: MediaItemRow, *, artifact_count: int | None = None) -> dict:
    payload = {
        "id": row.id,
        "title": row.title,
        "filename": row.filename,
        "media_type": row.media_type,
        "mime_type": row.mime_type,
        "extension": row.extension,
        "size": row.size,
        "sha256": row.sha256,
        "duration_ms": row.duration_ms,
        "width": row.width,
        "height": row.height,
        "video_codec": row.video_codec,
        "audio_codec": row.audio_codec,
        "status": row.status,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "preview_url": f"/api/media/{row.id}/file",
        "download_url": f"/api/media/{row.id}/download",
    }
    if artifact_count is not None:
        payload["artifact_count"] = artifact_count
    return payload


def artifact_to_dict(row: MediaArtifactRow) -> dict:
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "id": row.id,
        "media_id": row.media_id,
        "kind": row.kind,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size": row.size,
        "task_id": row.task_id,
        "metadata": metadata,
        "created_at": _iso(row.created_at),
        "file_url": f"/api/media/artifacts/{row.id}/file",
        "download_url": f"/api/media/artifacts/{row.id}/download",
    }


class MediaRepository:
    """媒体表读写入口；公开查询一律显式按用户作用域过滤。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list(
        self,
        *,
        q: str | None = None,
        media_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
        user_id: UserScope = ANY_USER,
    ) -> list[MediaItemRow]:
        stmt = select(MediaItemRow).order_by(
            MediaItemRow.updated_at.desc(), MediaItemRow.created_at.desc()
        )
        stmt = _apply_scope(stmt, MediaItemRow.user_id, user_id)
        if q:
            term = q.strip()
            if term:
                stmt = stmt.where(
                    MediaItemRow.title.contains(term) | MediaItemRow.filename.contains(term)
                )
        if media_type:
            stmt = stmt.where(MediaItemRow.media_type == media_type)
        stmt = stmt.offset(max(0, offset)).limit(max(1, min(limit, 500)))
        return list(self.session.scalars(stmt))

    def get(self, media_id: str, *, user_id: UserScope = ANY_USER) -> MediaItemRow | None:
        stmt = select(MediaItemRow).where(MediaItemRow.id == media_id)
        stmt = _apply_scope(stmt, MediaItemRow.user_id, user_id)
        return self.session.scalars(stmt).first()

    def create(
        self,
        *,
        title: str,
        filename: str,
        media_type: str,
        mime_type: str,
        extension: str,
        size: int,
        sha256: str,
        storage_path: str,
        duration_ms: int | None = None,
        width: int | None = None,
        height: int | None = None,
        video_codec: str | None = None,
        audio_codec: str | None = None,
        user_id: str | None = None,
        media_id: str | None = None,
    ) -> MediaItemRow:
        row = MediaItemRow(
            id=media_id or str(uuid4()),
            user_id=user_id,
            title=title,
            filename=filename,
            media_type=media_type,
            mime_type=mime_type,
            extension=extension,
            size=size,
            sha256=sha256,
            duration_ms=duration_ms,
            width=width,
            height=height,
            video_codec=video_codec,
            audio_codec=audio_codec,
            status="ready",
            storage_path=storage_path,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update(
        self, media_id: str, *, user_id: UserScope = ANY_USER, **fields
    ) -> MediaItemRow | None:
        row = self.get(media_id, user_id=user_id)
        if row is None:
            return None
        for key in ("title", "status", "error"):
            if key in fields and fields[key] is not None:
                setattr(row, key, fields[key])
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete(self, media_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get(media_id, user_id=user_id)
        if row is None:
            return False
        artifact_stmt = delete(MediaArtifactRow).where(MediaArtifactRow.media_id == media_id)
        artifact_stmt = _apply_scope(artifact_stmt, MediaArtifactRow.user_id, user_id)
        self.session.execute(artifact_stmt)
        self.session.delete(row)
        self.session.flush()
        return True

    def artifact_count(self, media_id: str, *, user_id: UserScope = ANY_USER) -> int:
        stmt = select(func.count(MediaArtifactRow.id)).where(MediaArtifactRow.media_id == media_id)
        stmt = _apply_scope(stmt, MediaArtifactRow.user_id, user_id)
        return int(self.session.scalar(stmt) or 0)

    def list_artifacts(
        self, media_id: str, *, user_id: UserScope = ANY_USER
    ) -> list[MediaArtifactRow]:
        stmt = (
            select(MediaArtifactRow)
            .where(MediaArtifactRow.media_id == media_id)
            .order_by(MediaArtifactRow.created_at.desc())
        )
        stmt = _apply_scope(stmt, MediaArtifactRow.user_id, user_id)
        return list(self.session.scalars(stmt))

    def get_artifact(
        self, artifact_id: str, *, user_id: UserScope = ANY_USER
    ) -> MediaArtifactRow | None:
        stmt = select(MediaArtifactRow).where(MediaArtifactRow.id == artifact_id)
        stmt = _apply_scope(stmt, MediaArtifactRow.user_id, user_id)
        return self.session.scalars(stmt).first()

    def create_artifact(
        self,
        *,
        media_id: str,
        kind: str,
        filename: str,
        mime_type: str,
        size: int,
        storage_path: str,
        user_id: str | None,
        task_id: str | None = None,
        metadata: dict | None = None,
        artifact_id: str | None = None,
    ) -> MediaArtifactRow:
        row = MediaArtifactRow(
            id=artifact_id or str(uuid4()),
            media_id=media_id,
            user_id=user_id,
            kind=kind,
            filename=filename,
            mime_type=mime_type,
            size=size,
            storage_path=storage_path,
            task_id=task_id,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def delete_artifact(self, artifact_id: str, *, user_id: UserScope = ANY_USER) -> bool:
        row = self.get_artifact(artifact_id, user_id=user_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def stats(self, *, user_id: UserScope = ANY_USER) -> dict[str, int]:
        stmt = select(MediaItemRow)
        stmt = _apply_scope(stmt, MediaItemRow.user_id, user_id)
        rows = list(self.session.scalars(stmt))

        artifact_stmt = select(func.count(MediaArtifactRow.id))
        artifact_stmt = _apply_scope(artifact_stmt, MediaArtifactRow.user_id, user_id)
        artifact_count = int(self.session.scalar(artifact_stmt) or 0)
        today = datetime.now(UTC).date()
        return {
            "items": len(rows),
            "images": sum(1 for row in rows if row.media_type == "image"),
            "audio": sum(1 for row in rows if row.media_type == "audio"),
            "videos": sum(1 for row in rows if row.media_type == "video"),
            "bytes": sum(int(row.size or 0) for row in rows),
            "duration_ms": sum(int(row.duration_ms or 0) for row in rows),
            "artifacts": artifact_count,
            "added_today": sum(
                1 for row in rows if row.created_at and row.created_at.date() == today
            ),
        }
