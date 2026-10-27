"""媒体工坊 API：原始文件上传、预览下载、元数据与异步处理任务。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.errors import TaskNotFoundError, TaskTransitionError, error_response
from zhiji.media_service import (
    DEFAULT_MAX_UPLOAD_BYTES,
    MEDIA_TASK_EXTRACT_AUDIO,
    MEDIA_TASK_TRANSCODE,
    MEDIA_TASK_TRANSCRIBE,
    MEDIA_TASK_TRIM,
    MEDIA_TASK_TYPES,
    MediaStorage,
    MediaValidationError,
    clean_filename,
    media_type_for_extension,
    probe_media,
    sha256_file,
)
from zhiji.models import TaskStatus
from zhiji.storage import Database, TaskRepository
from zhiji.storage.media_repository import (
    MediaRepository,
    artifact_to_dict,
    media_to_dict,
)
from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import MediaItemRow, TaskRow
from zhiji.tasks import TaskWorker

MediaAction = Literal["extract_audio", "transcode", "trim", "transcribe"]
_ACTIVE_STATUSES = {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
_ACTION_TASKS = {
    "extract_audio": MEDIA_TASK_EXTRACT_AUDIO,
    "transcode": MEDIA_TASK_TRANSCODE,
    "trim": MEDIA_TASK_TRIM,
    "transcribe": MEDIA_TASK_TRANSCRIBE,
}


class MediaUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=240)


class MediaTaskCreate(BaseModel):
    action: MediaAction
    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, ge=0)


def _clean_title(value: str) -> str:
    title = " ".join(str(value or "").split())[:240]
    if not title:
        raise MediaValidationError("标题不能为空")
    return title


def _media_or_404(
    repo: MediaRepository, media_id: str, *, user_id: UserScope
) -> MediaItemRow:
    row = repo.get(media_id, user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="媒体不存在或无权访问")
    return row


def _task_payload(row: TaskRow) -> dict:
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _task_public(row: TaskRow) -> dict:
    def _iso(value) -> str | None:
        return value.isoformat() if value is not None else None

    try:
        result = json.loads(row.result_json) if row.result_json else None
    except json.JSONDecodeError:
        result = None
    return {
        "id": row.id,
        "type": row.type,
        "status": row.status,
        "priority": row.priority,
        "progress": row.progress,
        "payload": _task_payload(row),
        "result": result,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _apply_task_scope(stmt, user_id: UserScope):
    if user_id is None:
        return stmt.where(TaskRow.user_id.is_(None))
    if user_id is not ANY_USER:
        return stmt.where(TaskRow.user_id == user_id)
    return stmt


def _media_tasks(
    session: Session, media_id: str, *, user_id: UserScope, limit: int = 200
) -> list[TaskRow]:
    stmt = (
        select(TaskRow)
        .where(TaskRow.type.in_(MEDIA_TASK_TYPES))
        .order_by(TaskRow.created_at.desc(), TaskRow.id)
        .limit(max(1, min(limit, 500)))
    )
    stmt = _apply_task_scope(stmt, user_id)
    rows = []
    for row in session.scalars(stmt):
        if str(_task_payload(row).get("media_id") or "") == media_id:
            rows.append(row)
    return rows


def _active_media_tasks(
    session: Session, media_id: str, *, user_id: UserScope
) -> list[TaskRow]:
    return [
        row
        for row in _media_tasks(session, media_id, user_id=user_id, limit=500)
        if row.status in _ACTIVE_STATUSES
    ]


def _reconcile_processing(
    session: Session,
    row: MediaItemRow,
    *,
    user_id: UserScope,
    tasks: list[TaskRow] | None = None,
) -> MediaItemRow:
    active = (
        any(item.status in _ACTIVE_STATUSES for item in tasks)
        if tasks is not None
        else bool(_active_media_tasks(session, row.id, user_id=user_id))
    )
    if row.status == "processing" and not active:
        updated = MediaRepository(session).update(
            row.id, user_id=user_id, status="ready", error=row.error or ""
        )
        if updated is not None:
            return updated
    return row


def _media_detail(
    session: Session, row: MediaItemRow, *, user_id: UserScope
) -> dict:
    repo = MediaRepository(session)
    # 先读任务终态，再读产物：任务成功是在产物事务提交后才写入的。
    # 这样轮询到 succeeded 的同一响应不会漏掉刚生成的产物。
    tasks = _media_tasks(session, row.id, user_id=user_id)
    row = _reconcile_processing(session, row, user_id=user_id, tasks=tasks)
    artifacts = repo.list_artifacts(row.id, user_id=user_id)
    return {
        "media": media_to_dict(row, artifact_count=len(artifacts)),
        "artifacts": [artifact_to_dict(item) for item in artifacts],
        "tasks": [_task_public(item) for item in tasks],
    }


def _validate_probe(probe, *, media_type: str) -> None:
    if media_type == "image" and probe.width is None:
        raise MediaValidationError("文件不包含可预览的图片")
    if media_type == "audio" and not probe.audio_codec:
        raise MediaValidationError("文件不包含可读取的音轨")
    if media_type == "video" and not probe.video_codec:
        raise MediaValidationError("文件不包含可读取的视频轨")


def _validate_action(media: MediaItemRow, action: MediaAction) -> None:
    if action in {"extract_audio", "transcribe"}:
        if media.media_type == "image" or not media.audio_codec:
            raise MediaValidationError("该媒体没有可用于处理的音轨")
    elif action == "transcode" and media.media_type != "video":
        raise MediaValidationError("MP4 转码仅支持视频素材")
    elif action == "trim" and media.media_type == "image":
        raise MediaValidationError("图片不支持时间裁剪")


def _disposition(filename: str, *, inline: bool) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename).strip() or "media"
    fallback = fallback.replace('"', "_")
    encoded = quote(filename, safe="")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _file_response(
    storage: MediaStorage,
    *,
    storage_path: str,
    filename: str,
    media_type: str,
    inline: bool,
) -> FileResponse:
    try:
        path = storage.resolve(storage_path)
    except MediaValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": _disposition(filename, inline=inline),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def create_media_router(
    database: Database,
    scope_user: Callable[[Request], str | None],
    *,
    storage: MediaStorage,
    resolve_worker: Callable[[], TaskWorker],
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> APIRouter:
    """构建工作区⑤的媒体工坊路由。"""

    router = APIRouter()
    limit = max(1, int(max_upload_bytes))

    @router.get("/api/media/stats")
    def media_stats(request: Request) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            return {"stats": MediaRepository(session).stats(user_id=user_id)}

    @router.get("/api/media")
    def list_media(
        request: Request,
        q: str | None = Query(default=None, max_length=200),
        media_type: Literal["image", "audio", "video"] | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            rows = repo.list(
                q=q,
                media_type=media_type,
                offset=offset,
                limit=limit,
                user_id=user_id,
            )
            items = []
            for row in rows:
                row = _reconcile_processing(session, row, user_id=user_id)
                items.append(
                    media_to_dict(
                        row,
                        artifact_count=repo.artifact_count(row.id, user_id=user_id),
                    )
                )
            return {"media": items, "count": len(items)}

    @router.post("/api/media/upload")
    async def upload_media(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        title: str | None = Query(default=None, max_length=240),
    ) -> dict:
        user_id = scope_user(request)
        try:
            safe_filename, extension = clean_filename(filename)
            media_type = media_type_for_extension(extension)
            display_title = _clean_title(title or Path(safe_filename).stem)
        except MediaValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        media_id = str(uuid4())
        destination = storage.source_path(user_id, media_id, extension)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{media_id}.upload")
        size = 0
        persisted = False
        try:
            with temporary.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"文件超过上传上限（{limit // (1024 * 1024)} MiB）",
                        )
                    handle.write(chunk)
            if size <= 0:
                raise HTTPException(status_code=400, detail="上传文件不能为空")
            temporary.replace(destination)
            probe = probe_media(destination, extension)
            _validate_probe(probe, media_type=media_type)
            digest = sha256_file(destination)
            with database.session_scope() as session:
                row = MediaRepository(session).create(
                    media_id=media_id,
                    user_id=user_id,
                    title=display_title,
                    filename=safe_filename,
                    media_type=media_type,
                    mime_type=probe.mime_type,
                    extension=extension,
                    size=size,
                    sha256=digest,
                    duration_ms=probe.duration_ms,
                    width=probe.width,
                    height=probe.height,
                    video_codec=probe.video_codec,
                    audio_codec=probe.audio_codec,
                    storage_path=storage.relative(destination),
                )
                payload = media_to_dict(row, artifact_count=0)
                persisted = True
            return {"media": payload}
        except MediaValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)
            if not persisted:
                storage.remove_media(user_id, media_id)

    @router.get("/api/media/artifacts/{artifact_id}/file")
    def preview_artifact(request: Request, artifact_id: str) -> FileResponse:
        user_id = scope_user(request)
        with database.session_scope() as session:
            row = MediaRepository(session).get_artifact(artifact_id, user_id=user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="媒体产物不存在或无权访问")
            storage_path = row.storage_path
            filename = row.filename
            mime_type = row.mime_type
        return _file_response(
            storage,
            storage_path=storage_path,
            filename=filename,
            media_type=mime_type,
            inline=True,
        )

    @router.get("/api/media/artifacts/{artifact_id}/download")
    def download_artifact(request: Request, artifact_id: str) -> FileResponse:
        user_id = scope_user(request)
        with database.session_scope() as session:
            row = MediaRepository(session).get_artifact(artifact_id, user_id=user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="媒体产物不存在或无权访问")
            storage_path = row.storage_path
            filename = row.filename
            mime_type = row.mime_type
        return _file_response(
            storage,
            storage_path=storage_path,
            filename=filename,
            media_type=mime_type,
            inline=False,
        )

    @router.delete("/api/media/artifacts/{artifact_id}")
    def delete_artifact(request: Request, artifact_id: str) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            row = repo.get_artifact(artifact_id, user_id=user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="媒体产物不存在或无权访问")
            storage_path = row.storage_path
            repo.delete_artifact(artifact_id, user_id=user_id)
        try:
            storage.resolve(storage_path).unlink(missing_ok=True)
        except MediaValidationError:
            pass
        return {"deleted": True}

    @router.post("/api/media/tasks/{task_id}/cancel")
    def cancel_media_task(request: Request, task_id: str) -> dict:
        user_id = scope_user(request)
        worker = resolve_worker()
        try:
            cancelled = worker.cancel(task_id, user_id=user_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=error_response(exc)) from exc
        except TaskTransitionError as exc:
            raise HTTPException(status_code=409, detail=error_response(exc)) from exc
        payload = _task_payload(cancelled)
        media_id = str(payload.get("media_id") or "")
        if media_id:
            with database.session_scope() as session:
                repo = MediaRepository(session)
                media = repo.get(media_id, user_id=user_id)
                if media is not None and not _active_media_tasks(
                    session, media_id, user_id=user_id
                ):
                    repo.update(media_id, user_id=user_id, status="ready")
        return {"cancelled": True, "task": _task_public(cancelled)}

    @router.get("/api/media/{media_id}")
    def get_media(request: Request, media_id: str) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            row = _media_or_404(repo, media_id, user_id=user_id)
            return _media_detail(session, row, user_id=user_id)

    @router.patch("/api/media/{media_id}")
    def update_media(request: Request, media_id: str, body: MediaUpdate) -> dict:
        user_id = scope_user(request)
        try:
            title = _clean_title(body.title)
        except MediaValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with database.session_scope() as session:
            repo = MediaRepository(session)
            _media_or_404(repo, media_id, user_id=user_id)
            row = repo.update(media_id, user_id=user_id, title=title)
            assert row is not None
            return {"media": media_to_dict(row)}

    @router.delete("/api/media/{media_id}")
    def delete_media(request: Request, media_id: str) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            _media_or_404(repo, media_id, user_id=user_id)
            if _active_media_tasks(session, media_id, user_id=user_id):
                raise HTTPException(status_code=409, detail="媒体仍有处理任务，请先取消或等待完成")
            repo.delete(media_id, user_id=user_id)
        storage.remove_media(user_id, media_id)
        return {"deleted": True}

    @router.get("/api/media/{media_id}/file")
    def preview_media(request: Request, media_id: str) -> FileResponse:
        user_id = scope_user(request)
        with database.session_scope() as session:
            row = _media_or_404(
                MediaRepository(session), media_id, user_id=user_id
            )
            storage_path = row.storage_path
            filename = row.filename
            mime_type = row.mime_type
        return _file_response(
            storage,
            storage_path=storage_path,
            filename=filename,
            media_type=mime_type,
            inline=True,
        )

    @router.get("/api/media/{media_id}/download")
    def download_media(request: Request, media_id: str) -> FileResponse:
        user_id = scope_user(request)
        with database.session_scope() as session:
            row = _media_or_404(
                MediaRepository(session), media_id, user_id=user_id
            )
            storage_path = row.storage_path
            filename = row.filename
            mime_type = row.mime_type
        return _file_response(
            storage,
            storage_path=storage_path,
            filename=filename,
            media_type=mime_type,
            inline=False,
        )

    @router.get("/api/media/{media_id}/artifacts")
    def list_media_artifacts(request: Request, media_id: str) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            _media_or_404(repo, media_id, user_id=user_id)
            rows = repo.list_artifacts(media_id, user_id=user_id)
            return {"artifacts": [artifact_to_dict(row) for row in rows]}

    @router.get("/api/media/{media_id}/tasks")
    def list_media_tasks(request: Request, media_id: str) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            row = _media_or_404(repo, media_id, user_id=user_id)
            rows = _media_tasks(session, media_id, user_id=user_id)
            _reconcile_processing(session, row, user_id=user_id, tasks=rows)
            return {"tasks": [_task_public(row) for row in rows]}

    @router.post("/api/media/{media_id}/tasks")
    def create_media_task(
        request: Request, media_id: str, body: MediaTaskCreate
    ) -> dict:
        user_id = scope_user(request)
        with database.session_scope() as session:
            repo = MediaRepository(session)
            media = _media_or_404(repo, media_id, user_id=user_id)
            try:
                _validate_action(media, body.action)
            except MediaValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if _active_media_tasks(session, media_id, user_id=user_id):
                raise HTTPException(status_code=409, detail="该媒体已有处理任务")
            if body.action == "trim":
                if body.end_ms is not None and body.end_ms <= body.start_ms:
                    raise HTTPException(status_code=400, detail="裁剪结束时间必须晚于开始时间")
                if media.duration_ms and body.start_ms >= media.duration_ms:
                    raise HTTPException(status_code=400, detail="裁剪开始时间超出媒体时长")
                if (
                    media.duration_ms
                    and body.end_ms is not None
                    and body.end_ms > media.duration_ms + 1000
                ):
                    raise HTTPException(status_code=400, detail="裁剪结束时间超出媒体时长")
            repo.update(
                media_id,
                user_id=user_id,
                status="processing",
                error="",
            )

        worker = resolve_worker()
        payload: dict[str, Any] = {"media_id": media_id}
        if body.action == "trim":
            payload.update({"start_ms": body.start_ms, "end_ms": body.end_ms})
        try:
            task_id = worker.submit(
                task_type=_ACTION_TASKS[body.action],
                payload=payload,
                user_id=user_id,
            )
        except Exception as exc:
            with database.session_scope() as session:
                MediaRepository(session).update(
                    media_id, user_id=user_id, status="ready", error=str(exc)[:1000]
                )
            raise
        with database.session_scope() as session:
            row = TaskRepository(session).get(task_id, user_id=user_id)
            fresh_media = MediaRepository(session).get(media_id, user_id=user_id)
            return {
                "task": _task_public(row),
                "media": media_to_dict(fresh_media) if fresh_media is not None else None,
            }

    return router
