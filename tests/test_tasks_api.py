"""任务 API 测试：提交、查询、列表、取消、SSE 与 worker 生命周期。"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zhiji.config import ConfigManager
from zhiji.models import TaskStatus
from zhiji.storage import Database
from zhiji.tasks import TaskContext, TaskWorker
from zhiji.web.app import create_app


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture()
def worker(database: Database) -> Iterator[TaskWorker]:
    w = TaskWorker(database=database, poll_interval=0.05)
    yield w
    w.stop()


def _app(tmp_path: Path, worker: TaskWorker):
    cm = ConfigManager(tmp_path / "zhiji.json")
    return create_app(cm, data_dir=tmp_path / "data", worker=worker)


def _wait_for_status(
    client: TestClient, task_id: str, status: TaskStatus, timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()["task"]
        if task["status"] == status.value:
            return task
        time.sleep(0.02)
    raise AssertionError(f"任务未在超时内进入 {status.value}")


def test_submit_runs_in_background_and_can_be_queried(
    tmp_path: Path, worker: TaskWorker
) -> None:
    worker.register("note.generate", lambda context: {"note": "a.md"})

    with TestClient(_app(tmp_path, worker)) as client:
        created = client.post(
            "/api/tasks",
            json={
                "type": "note.generate",
                "platform": "bilibili",
                "payload": {"url": "u1"},
            },
        )
        assert created.status_code == 200
        task = created.json()["task"]
        assert task["type"] == "note.generate"
        assert task["platform"] == "bilibili"
        assert task["payload"] == {"url": "u1"}

        finished = _wait_for_status(client, task["id"], TaskStatus.SUCCEEDED)
        assert finished["result"] == {"note": "a.md"}
        assert finished["started_at"] is not None
        assert finished["finished_at"] is not None


def test_get_missing_task_returns_404(tmp_path: Path, worker: TaskWorker) -> None:
    client = TestClient(_app(tmp_path, worker))
    resp = client.get("/api/tasks/does-not-exist")
    assert resp.status_code == 404


def test_cancel_running_task_wins_over_handler_result(
    tmp_path: Path, worker: TaskWorker
) -> None:
    started = threading.Event()
    gate = threading.Event()

    def handler(context: TaskContext) -> dict[str, object]:
        started.set()
        gate.wait(timeout=5.0)
        return {"late": True}

    worker.register("t", handler)

    with TestClient(_app(tmp_path, worker)) as client:
        task_id = client.post("/api/tasks", json={"type": "t"}).json()["task"]["id"]
        assert started.wait(timeout=5.0) is True
        _wait_for_status(client, task_id, TaskStatus.RUNNING)

        cancelled = client.post(f"/api/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["task"]["status"] == "cancelled"

        gate.set()
        time.sleep(0.2)
        final = client.get(f"/api/tasks/{task_id}").json()["task"]
        assert final["status"] == "cancelled"
        assert final["result"] is None

        again = client.post(f"/api/tasks/{task_id}/cancel")
        assert again.status_code == 409


def test_list_tasks_filters(tmp_path: Path, worker: TaskWorker) -> None:
    worker.register("t", lambda context: {})

    with TestClient(_app(tmp_path, worker)) as client:
        first = client.post(
            "/api/tasks", json={"type": "t", "platform": "douyin"}
        ).json()["task"]["id"]
        second = client.post(
            "/api/tasks", json={"type": "t", "platform": "zhihu"}
        ).json()["task"]["id"]
        _wait_for_status(client, first, TaskStatus.SUCCEEDED)
        _wait_for_status(client, second, TaskStatus.SUCCEEDED)

        everything = client.get("/api/tasks").json()["tasks"]
        assert {item["id"] for item in everything} == {first, second}

        douyin = client.get("/api/tasks", params={"platform": "douyin"}).json()["tasks"]
        assert [item["id"] for item in douyin] == [first]

        succeeded = client.get(
            "/api/tasks", params={"status": "succeeded"}
        ).json()["tasks"]
        assert {item["id"] for item in succeeded} == {first, second}

        bad = client.get("/api/tasks", params={"status": "bogus"})
        assert bad.status_code == 400


def test_submit_requires_type(tmp_path: Path, worker: TaskWorker) -> None:
    client = TestClient(_app(tmp_path, worker))
    assert client.post("/api/tasks", json={"type": ""}).status_code == 422
    assert client.post("/api/tasks", json={}).status_code == 422


def test_task_events_streams_until_terminal(tmp_path: Path, worker: TaskWorker) -> None:
    worker.register("t", lambda context: {"ok": True})

    with TestClient(_app(tmp_path, worker)) as client:
        task_id = client.post("/api/tasks", json={"type": "t"}).json()["task"]["id"]
        _wait_for_status(client, task_id, TaskStatus.SUCCEEDED)

        resp = client.get(f"/api/tasks/{task_id}/events")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert resp.text.startswith("data: ")
        assert '"status": "succeeded"' in resp.text
