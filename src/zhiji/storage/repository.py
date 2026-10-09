"""任务仓储：对 tasks 表的最小读写封装。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.errors import TaskNotFoundError
from zhiji.models import TaskStatus
from zhiji.storage.tables import TaskRow

TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
)


class TaskRepository:
    """任务表读写入口。

    事务边界由调用方掌握（自行 commit / rollback），仓储只做 flush，
    这样上层可以把多步操作放进同一个事务。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        task_type: str,
        platform: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
        task_id: str | None = None,
    ) -> TaskRow:
        row = TaskRow(
            id=task_id or str(uuid4()),
            type=task_type,
            platform=platform,
            status=TaskStatus.PENDING.value,
            priority=priority,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def find(self, task_id: str) -> TaskRow | None:
        return self.session.get(TaskRow, task_id)

    def get(self, task_id: str) -> TaskRow:
        row = self.find(task_id)
        if row is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        return row

    def list(
        self,
        *,
        status: TaskStatus | str | None = None,
        platform: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRow]:
        stmt = select(TaskRow).order_by(TaskRow.created_at.desc(), TaskRow.id)
        if status is not None:
            stmt = stmt.where(TaskRow.status == TaskStatus(status).value)
        if platform is not None:
            stmt = stmt.where(TaskRow.platform == platform)
        stmt = stmt.offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def update_status(
        self,
        task_id: str,
        status: TaskStatus | str,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        progress: int | None = None,
    ) -> TaskRow:
        row = self.get(task_id)
        value = TaskStatus(status).value
        now = datetime.now(UTC)
        row.status = value
        row.updated_at = now
        if error is not None:
            row.error = error
        if result is not None:
            row.result_json = json.dumps(result, ensure_ascii=False)
        if progress is not None:
            row.progress = progress
        if value == TaskStatus.RUNNING.value and row.started_at is None:
            row.started_at = now
        if value in TERMINAL_STATUSES:
            row.finished_at = now
        self.session.flush()
        return row