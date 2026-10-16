"""与 OpenAPI 契约对齐的 Pydantic 模型。

字段名、类型、必填性严格对应 ``docs/contracts/worker-api.yaml``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class TaskDispatch(BaseModel):
    """控制面 → worker 的任务派发请求体。"""

    schemaVersion: str = SCHEMA_VERSION
    taskId: str
    userId: str | None = None
    type: str
    platform: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    traceId: str
    attempt: int = 1
    priority: int = 0


class DispatchResponse(BaseModel):
    accepted: bool = True
    taskId: str


class CancelResponse(BaseModel):
    cancelled: bool = True
    taskId: str


class WorkerHealth(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    capabilities: list[str] = Field(default_factory=lambda: ["note.generate"])


class TaskEventType(str, Enum):
    TASK_STARTED = "task.started"
    STEP_PROGRESS = "step.progress"
    STEP_SUCCEEDED = "step.succeeded"
    STEP_FAILED = "step.failed"
    TASK_SUCCEEDED = "task.succeeded"
    TASK_FAILED = "task.failed"


class TaskEvent(BaseModel):
    """worker → 控制面的回调事件。"""

    schemaVersion: str = SCHEMA_VERSION
    taskId: str
    eventType: TaskEventType
    traceId: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)


class AckResponse(BaseModel):
    acknowledged: bool = True


class ErrorResponse(BaseModel):
    error: dict[str, str] = Field(default_factory=dict)



