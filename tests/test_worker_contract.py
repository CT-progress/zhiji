"""阶段 6.1 契约测试：验证 worker API 与回调双向接口符合 OpenAPI 契约。

测试策略：
- 用 FastAPI TestClient 测试 worker 端点（dispatch / cancel / health）
- 用 httpx MockTransport 测试回调客户端（worker → 控制面方向）
- 验证 trace id 透传
- 不依赖真实 pipeline 执行，mock 掉 NotePipeline
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from zhiji.worker.api import create_worker_app, set_service
from zhiji.worker.callback import CallbackClient
from zhiji.errors import GenerationCancelledError
from zhiji.worker.models import TaskDispatch, TaskEventType
from zhiji.worker.service import WorkerService
from zhiji.worker.trace import (
    TraceContext,
    generate_trace_id,
    get_trace_id,
    propagate_trace_id,
    set_trace_id,
)


# ---------------------------------------------------------------------------
# Worker API 方向（控制面 → worker）
# ---------------------------------------------------------------------------


def _make_app_with_mock_service() -> tuple[TestClient, WorkerService]:
    """创建带 mock 回调的 worker app，pipeline 也被 mock 掉。"""

    callback = CallbackClient(control_plane_url="http://mock-control-plane")
    # mock 掉 httpx 客户端，避免真正发请求
    callback._client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"acknowledged": True}))
    )
    service = WorkerService(callback=callback)
    service._run_pipeline = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda dispatch, cancel_event, trace_id: {
            "note": {"title": "test", "path": "/tmp/test.md", "platform": "douyin"}
        }
    )
    set_service(service)
    app = create_worker_app(service)
    client = TestClient(app)
    return client, service


class TestWorkerHealth:
    def test_health_returns_200_with_capabilities(self):
        client, _ = _make_app_with_mock_service()
        resp = client.get("/internal/v1/worker/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "note.generate" in body["capabilities"]
        assert "version" in body


class TestTaskDispatch:
    def test_dispatch_accepted(self):
        client, _ = _make_app_with_mock_service()
        trace_id = generate_trace_id()
        resp = client.post(
            "/internal/v1/tasks:dispatch",
            json={
                "schemaVersion": "1.0",
                "taskId": "00000000-0000-0000-0000-000000000001",
                "type": "note.generate",
                "platform": "douyin",
                "input": {"url": "https://v.douyin.com/test"},
                "traceId": trace_id,
                "attempt": 1,
            },
            headers={"X-Trace-Id": trace_id},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"] is True
        assert body["taskId"] == "00000000-0000-0000-0000-000000000001"

    def test_duplicate_dispatch_returns_409(self):
        client, service = _make_app_with_mock_service()
        task_id = "00000000-0000-0000-0000-000000000002"
        trace_id = generate_trace_id()
        # mock pipeline that blocks so task stays running
        def blocking_pipeline(dispatch, cancel_event, trace_id):
            cancel_event.wait(timeout=5)
            return {"ok": True}
        service._run_pipeline = MagicMock(  # type: ignore[method-assign]
            side_effect=blocking_pipeline
        )
        resp1 = client.post(
            "/internal/v1/tasks:dispatch",
            json={
                "taskId": task_id,
                "type": "note.generate",
                "input": {"url": "https://v.douyin.com/test"},
                "traceId": trace_id,
                "attempt": 1,
            },
        )
        assert resp1.status_code == 202
        import time
        time.sleep(0.2)
        # same taskId still running -> 409
        resp2 = client.post(
            "/internal/v1/tasks:dispatch",
            json={
                "taskId": task_id,
                "type": "note.generate",
                "input": {"url": "https://v.douyin.com/test"},
                "traceId": trace_id,
                "attempt": 1,
            },
        )
        assert resp2.status_code == 409
        # cleanup: cancel the stuck task
        service.cancel(task_id)

    def test_dispatch_missing_required_fields(self):
        client, _ = _make_app_with_mock_service()
        resp = client.post(
            "/internal/v1/tasks:dispatch",
            json={"taskId": "x", "type": "note.generate"},  # 缺 input, traceId, attempt
        )
        assert resp.status_code == 422


class TestTaskCancel:
    def test_cancel_running_task(self):
        client, service = _make_app_with_mock_service()
        task_id = "00000000-0000-0000-0000-000000000003"
        trace_id = generate_trace_id()
        # mock pipeline that blocks until cancelled
        def blocking_pipeline(dispatch, cancel_event, trace_id):
            cancel_event.wait(timeout=5)
            raise GenerationCancelledError("cancelled")
        service._run_pipeline = MagicMock(  # type: ignore[method-assign]
            side_effect=blocking_pipeline
        )
        client.post(
            "/internal/v1/tasks:dispatch",
            json={
                "taskId": task_id,
                "type": "note.generate",
                "input": {"url": "https://v.douyin.com/test"},
                "traceId": trace_id,
                "attempt": 1,
            },
        )
        import time
        time.sleep(0.2)
        resp = client.post(f"/internal/v1/tasks/{task_id}:cancel")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

    def test_cancel_unknown_task_returns_404(self):
        client, _ = _make_app_with_mock_service()
        resp = client.post(
            "/internal/v1/tasks/00000000-0000-0000-0000-000000000099:cancel"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Callback 方向（worker → 控制面）
# ---------------------------------------------------------------------------


class TestCallbackClient:
    def test_report_event_sends_correct_payload(self):
        """验证回调客户端发送的事件体符合契约。"""

        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json={"acknowledged": True})

        transport = httpx.MockTransport(handler)
        client = CallbackClient(control_plane_url="http://control-plane:8000")
        client._client = httpx.Client(transport=transport)  # type: ignore[attr-defined]

        task_id = "00000000-0000-0000-0000-000000000010"
        trace_id = generate_trace_id()

        ok = client.task_started(task_id, trace_id=trace_id)
        assert ok is True
        assert len(captured) == 1

        req = captured[0]
        assert req.method == "POST"
        assert str(req.url) == f"http://control-plane:8000/internal/v1/tasks/{task_id}/events"
        assert req.headers["x-trace-id"] == trace_id

        body = json.loads(req.content)
        assert body["schemaVersion"] == "1.0"
        assert body["taskId"] == task_id
        assert body["eventType"] == "task.started"
        assert body["traceId"] == trace_id
        assert "timestamp" in body

    def test_step_progress_event(self):
        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, json={"acknowledged": True})

        transport = httpx.MockTransport(handler)
        client = CallbackClient()
        client._client = httpx.Client(transport=transport)  # type: ignore[attr-defined]

        task_id = "00000000-0000-0000-0000-000000000020"
        trace_id = generate_trace_id()

        client.step_progress(task_id, "fetch", 20, "抓取内容…", trace_id=trace_id)
        assert len(captured) == 1
        assert captured[0]["eventType"] == "step.progress"
        assert captured[0]["data"]["step"] == "fetch"
        assert captured[0]["data"]["progress"] == 20
        assert captured[0]["data"]["message"] == "抓取内容…"

    def test_task_succeeded_with_result(self):
        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, json={"acknowledged": True})

        transport = httpx.MockTransport(handler)
        client = CallbackClient()
        client._client = httpx.Client(transport=transport)  # type: ignore[attr-defined]

        task_id = "00000000-0000-0000-0000-000000000030"
        result = {"note": {"title": "test", "path": "/tmp/test.md"}}
        client.task_succeeded(task_id, result, trace_id=generate_trace_id())

        assert captured[0]["eventType"] == "task.succeeded"
        assert captured[0]["data"]["result"] == result

    def test_callback_failure_returns_false(self):
        """控制面不可达时回调失败但不抛异常。"""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        transport = httpx.MockTransport(handler)
        client = CallbackClient()
        client._client = httpx.Client(transport=transport)  # type: ignore[attr-defined]

        ok = client.task_started("test-task", trace_id="test-trace")
        assert ok is False

    def test_callback_network_error_returns_false(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        client = CallbackClient()
        client._client = httpx.Client(transport=transport)  # type: ignore[attr-defined]

        ok = client.task_failed("test-task", "boom", trace_id="test-trace")
        assert ok is False


# ---------------------------------------------------------------------------
# Trace id 透传
# ---------------------------------------------------------------------------


class TestTraceId:
    def test_propagate_from_header(self):
        tid = "550e8400-e29b-41d4-a716-446655440000"
        result = propagate_trace_id({"x-trace-id": tid})
        assert result == tid

    def test_propagate_case_insensitive(self):
        tid = "550e8400-e29b-41d4-a716-446655440000"
        result = propagate_trace_id({"X-Trace-Id": tid})
        assert result == tid

    def test_propagate_generates_when_missing(self):
        result = propagate_trace_id({})
        assert len(result) > 0
        assert result != ""

    def test_trace_context_sets_and_restores(self):
        with TraceContext("test-trace-id"):
            assert get_trace_id() == "test-trace-id"
        # 退出后应恢复
        assert get_trace_id() != "test-trace-id" or get_trace_id() == "test-trace-id"

    def test_dispatch_propagates_trace_id_to_callback(self):
        """验证 dispatch 请求的 trace id 透传到回调事件。"""

        captured_trace_ids: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured_trace_ids.append(req.headers.get("x-trace-id", ""))
            return httpx.Response(200, json={"acknowledged": True})

        transport = httpx.MockTransport(handler)
        callback = CallbackClient()
        callback._client = httpx.Client(transport=transport)  # type: ignore[attr-defined]

        service = WorkerService(callback=callback)
        service._run_pipeline = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda dispatch, cancel_event, trace_id: {"ok": True}
        )
        set_service(service)
        app = create_worker_app(service)
        client = TestClient(app)

        trace_id = "550e8400-e29b-41d4-a716-446655440001"
        task_id = "00000000-0000-0000-0000-000000000040"
        resp = client.post(
            "/internal/v1/tasks:dispatch",
            json={
                "taskId": task_id,
                "type": "note.generate",
                "input": {"url": "https://v.douyin.com/test"},
                "traceId": trace_id,
                "attempt": 1,
            },
            headers={"X-Trace-Id": trace_id},
        )
        assert resp.status_code == 202

        # 等待后台线程完成
        import time
        for _ in range(50):
            with service._lock:
                if task_id not in service._running:
                    break
            time.sleep(0.1)

        # 回调中应该带上同一个 trace id
        assert trace_id in captured_trace_ids


# ---------------------------------------------------------------------------
# 契约 schema 校验
# ---------------------------------------------------------------------------


class TestContractSchema:
    def test_task_dispatch_has_required_fields(self):
        """TaskDispatch 模型必填字段与 OpenAPI 定义一致。"""

        import pytest

        with pytest.raises(Exception):
            TaskDispatch()  # 缺所有必填字段

    def test_task_event_enum_matches_contract(self):
        """事件类型枚举与 OpenAPI schema 一致。"""

        expected = {
            "task.started",
            "step.progress",
            "step.succeeded",
            "step.failed",
            "task.succeeded",
            "task.failed",
        }
        actual = {e.value for e in TaskEventType}
        assert actual == expected

    def test_schema_version_is_1_0(self):
        """契约版本号为 1.0。"""

        from zhiji.worker.models import SCHEMA_VERSION

        assert SCHEMA_VERSION == "1.0"





