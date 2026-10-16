"""Worker FastAPI 路由：暴露给 Java 控制面的 HTTP 接口。

端点对应 ``docs/contracts/worker-api.yaml`` 中的 Worker API 部分：
- POST /internal/v1/tasks:dispatch
- POST /internal/v1/tasks/{taskId}:cancel
- GET  /internal/v1/worker/health
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request

from zhiji.worker.models import (
    CancelResponse,
    DispatchResponse,
    ErrorResponse,
    TaskDispatch,
    WorkerHealth,
)
from zhiji.worker.service import WorkerService
from zhiji.worker.trace import propagate_trace_id

router = APIRouter(prefix="/internal/v1", tags=["worker"])

#: 模块级单例，由 ``create_worker_app`` 或调用方设置。
_service: WorkerService | None = None


def set_service(service: WorkerService) -> None:
    global _service
    _service = service


def get_service() -> WorkerService:
    if _service is None:
        set_service(WorkerService())
    return _service  # type: ignore[return-value]


@router.post("/tasks:dispatch", status_code=202)
def dispatch_task(
    payload: TaskDispatch, request: Request
) -> DispatchResponse:
    trace_id = propagate_trace_id(
        dict(request.headers), fallback=payload.traceId
    )
    payload.traceId = trace_id
    service = get_service()
    accepted = service.dispatch(payload)
    if not accepted:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                error={
                    "code": "ALREADY_RUNNING",
                    "message": f"task {payload.taskId} is already running",
                }
            ).model_dump(),
        )
    return DispatchResponse(accepted=True, taskId=payload.taskId)


@router.post("/tasks/{task_id}:cancel")
def cancel_task(task_id: str) -> CancelResponse:
    service = get_service()
    cancelled = service.cancel(task_id)
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error={
                    "code": "NOT_FOUND",
                    "message": f"task {task_id} not found on this worker",
                }
            ).model_dump(),
        )
    return CancelResponse(cancelled=True, taskId=task_id)


@router.get("/worker/health")
def worker_health() -> WorkerHealth:
    return get_service().health()


def create_worker_router(service: WorkerService | None = None) -> APIRouter:
    """创建独立的 worker 路由，供挂载到已有 FastAPI 应用或独立运行。"""

    if service is not None:
        set_service(service)
    return router


def create_worker_app(
    service: WorkerService | None = None,
) -> FastAPI:
    """创建独立 worker 应用（不挂载前端页面，只暴露内部 API）。"""

    if service is not None:
        set_service(service)
    else:
        get_service()

    app = FastAPI(
        title="Zhiji Worker",
        version="0.1.0",
        docs_url="/docs",
    )
    app.include_router(router)
    return app



