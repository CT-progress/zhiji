"""ORM 表定义。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from zhiji.models import TaskStatus


class Base(DeclarativeBase):
    """所有 ORM 表的基类。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskRow(Base):
    """任务表：一次抓取 / 转写 / 生成请求的执行记录。

    状态流转（由仓储层约束写入，见 repository.update_status）：
    PENDING -> RUNNING -> SUCCEEDED / FAILED / CANCELLED
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    status: Mapped[str] = mapped_column(
        String(16), index=True, default=TaskStatus.PENDING.value
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<TaskRow id={self.id!r} type={self.type!r} status={self.status!r}>"