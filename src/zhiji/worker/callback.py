"""回调客户端：worker 执行过程中向控制面上报事件。

使用 httpx 同步客户端，在 worker 线程内调用。
失败时记录日志但不中断任务执行——控制面最终能从任务表读到终态。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from zhiji.worker.models import SCHEMA_VERSION, TaskEvent, TaskEventType
from zhiji.worker.trace import get_trace_id

logger = logging.getLogger(__name__)


class CallbackClient:
    """向控制面 POST 事件回调的 HTTP 客户端。"""

    def __init__(
        self,
        control_plane_url: str = "http://127.0.0.1:8000",
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = control_plane_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def report_event(
        self,
        task_id: str,
        event_type: TaskEventType,
        *,
        trace_id: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> bool:
        """向控制面上报一个事件；成功返回 True，失败返回 False。"""

        tid = trace_id or get_trace_id()
        event = TaskEvent(
            schemaVersion=SCHEMA_VERSION,
            taskId=task_id,
            eventType=event_type,
            traceId=tid,
            data=dict(data or {}),
        )
        url = f"{self._base_url}/internal/v1/tasks/{task_id}/events"
        try:
            resp = self.client.post(
                url,
                json=event.model_dump(mode="json"),
                headers={"X-Trace-Id": tid, "Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning(
                    "callback failed: %s -> %d %s",
                    url,
                    resp.status_code,
                    resp.text[:200],
                )
                return False
            return True
        except httpx.HTTPError as exc:
            logger.warning("callback error: %s -> %s", url, exc)
            return False

    def task_started(self, task_id: str, *, trace_id: str | None = None) -> bool:
        return self.report_event(
            task_id,
            TaskEventType.TASK_STARTED,
            trace_id=trace_id,
            data={"type": "note.generate"},
        )

    def step_progress(
        self,
        task_id: str,
        step: str,
        progress: int,
        message: str = "",
        *,
        trace_id: str | None = None,
    ) -> bool:
        return self.report_event(
            task_id,
            TaskEventType.STEP_PROGRESS,
            trace_id=trace_id,
            data={"step": step, "progress": progress, "message": message},
        )

    def step_succeeded(
        self,
        task_id: str,
        step: str,
        message: str = "",
        *,
        trace_id: str | None = None,
    ) -> bool:
        return self.report_event(
            task_id,
            TaskEventType.STEP_SUCCEEDED,
            trace_id=trace_id,
            data={"step": step, "message": message},
        )

    def step_failed(
        self,
        task_id: str,
        step: str,
        error: str,
        *,
        trace_id: str | None = None,
    ) -> bool:
        return self.report_event(
            task_id,
            TaskEventType.STEP_FAILED,
            trace_id=trace_id,
            data={"step": step, "error": error},
        )

    def task_succeeded(
        self,
        task_id: str,
        result: Mapping[str, Any],
        *,
        trace_id: str | None = None,
    ) -> bool:
        return self.report_event(
            task_id,
            TaskEventType.TASK_SUCCEEDED,
            trace_id=trace_id,
            data={"result": dict(result)},
        )

    def task_failed(
        self,
        task_id: str,
        error: str,
        *,
        step: str | None = None,
        trace_id: str | None = None,
    ) -> bool:
        data: dict[str, Any] = {"error": error}
        if step:
            data["step"] = step
        return self.report_event(
            task_id,
            TaskEventType.TASK_FAILED,
            trace_id=trace_id,
            data=data,
        )
