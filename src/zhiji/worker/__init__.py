"""Worker 模块：Python 数据面接收 Java 控制面的任务派发并上报回调。

阶段 6.1 契约固化产物：与 ``docs/contracts/worker-api.yaml`` 对齐。
"""

from zhiji.worker.api import create_worker_app, create_worker_router
from zhiji.worker.callback import CallbackClient
from zhiji.worker.models import (
    TaskDispatch,
    TaskEvent,
    TaskEventType,
    WorkerHealth,
)
from zhiji.worker.service import WorkerService
from zhiji.worker.trace import TraceContext, get_trace_id

__all__ = [
    "CallbackClient",
    "TaskDispatch",
    "TaskEvent",
    "TaskEventType",
    "TraceContext",
    "WorkerHealth",
    "WorkerService",
    "create_worker_app",
    "create_worker_router",
    "get_trace_id",
]
