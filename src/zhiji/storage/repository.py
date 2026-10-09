"""任务仓储：对 tasks 表的最小读写封装。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.errors import TaskNotFoundError, TaskTransitionError
from zhiji.models import TASK_TRANSITIONS, TaskStatus
from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import TaskRow

TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
)


def _coerce_status(status: TaskStatus | str) -> TaskStatus:
    """把字符串状态转成枚举；未知状态抛 ValueError（保持原契约）。"""

    try:
        return TaskStatus(status)
    except ValueError as exc:
        raise ValueError(f"未知的任务状态: {status!r}") from exc


def _legal_targets(status: TaskStatus) -> str:
    targets = sorted(item.value for item in TASK_TRANSITIONS[status])
    return ", ".join(targets) if targets else "无（终态不可再流转）"


def _check_progress(progress: int) -> int:
    if not 0 <= progress <= 100:
        raise TaskTransitionError(f"进度必须在 0-100 之间: {progress}")
    return progress


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
        user_id: str | None = None,
    ) -> TaskRow:
        row = TaskRow(
            id=task_id or str(uuid4()),
            user_id=user_id,
            type=task_type,
            platform=platform,
            status=TaskStatus.PENDING.value,
            priority=priority,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def find(self, task_id: str, *, user_id: UserScope = ANY_USER) -> TaskRow | None:
        stmt = select(TaskRow).where(TaskRow.id == task_id)
        if user_id is None:
            stmt = stmt.where(TaskRow.user_id.is_(None))
        elif user_id is not ANY_USER:
            stmt = stmt.where(TaskRow.user_id == user_id)
        return self.session.scalars(stmt).first()

    def get(self, task_id: str, *, user_id: UserScope = ANY_USER) -> TaskRow:
        row = self.find(task_id, user_id=user_id)
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
        user_id: UserScope = ANY_USER,
    ) -> list[TaskRow]:
        stmt = select(TaskRow).order_by(TaskRow.created_at.desc(), TaskRow.id)
        if user_id is None:
            stmt = stmt.where(TaskRow.user_id.is_(None))
        elif user_id is not ANY_USER:
            stmt = stmt.where(TaskRow.user_id == user_id)
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
        """按状态机流转任务；非法流转或失败缺原因都会被拒绝。

        合法流转见 ``zhiji.models.TASK_TRANSITIONS``：
        PENDING -> RUNNING / CANCELLED，RUNNING -> SUCCEEDED / FAILED / CANCELLED，
        终态不可再流转。
        """

        row = self.get(task_id)
        current = TaskStatus(row.status)
        target = _coerce_status(status)
        if not current.can_transition_to(target):
            raise TaskTransitionError(
                f"非法的任务状态流转: {current.value} -> {target.value}",
                hint=f"{current.value} 的合法目标: {_legal_targets(current)}",
            )
        if target is TaskStatus.FAILED and not (error and error.strip()):
            raise TaskTransitionError(
                "失败任务必须记录失败原因",
                hint="调用 update_status(..., status=FAILED, error='...')",
            )
        if progress is not None:
            row.progress = _check_progress(progress)

        now = datetime.now(UTC)
        row.status = target.value
        row.updated_at = now
        if target is TaskStatus.RUNNING and row.started_at is None:
            row.started_at = now
        if error is not None:
            row.error = error
        if result is not None:
            row.result_json = json.dumps(result, ensure_ascii=False)
        if target.value in TERMINAL_STATUSES:
            row.finished_at = now
        self.session.flush()
        return row

    def update_progress(self, task_id: str, progress: int) -> TaskRow:
        """只更新运行中任务的进度，不改变状态。"""

        row = self.get(task_id)
        current = TaskStatus(row.status)
        if current is not TaskStatus.RUNNING:
            raise TaskTransitionError(
                f"只有运行中的任务能更新进度，当前为 {current.value}",
                hint="先流转到 RUNNING",
            )
        row.progress = _check_progress(progress)
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row
