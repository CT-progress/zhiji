"""阶段 2.4 测试：笔记生成任务化（提交即返回、后台跑完、SSE 订阅、断连不影响）。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zhiji.config import ConfigManager
from zhiji.models import OutputReceipt, TaskStatus
from zhiji.pipeline import NotePipeline
from zhiji.storage import Database
from zhiji.tasks import TaskWorker
from zhiji.web.app import create_app


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'note.db').as_posix()}")
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


def _receipt(tmp_path: Path) -> OutputReceipt:
    note_path = tmp_path / "out" / "note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("---\ntitle: T\n---\n\n# 标题\n\n正文\n", encoding="utf-8")
    return OutputReceipt(
        note_path=note_path,
        artifact_dir=tmp_path / "out" / "artifacts",
        platform="zhihu",
        content_id="zhihu_1",
        title="标题",
        warnings=["示例告警"],
    )


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


def test_note_generate_returns_task_and_finishes_in_background(
    tmp_path: Path, worker: TaskWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提交即返回任务 id；不订阅任何事件，后台仍跑到 succeeded 并落库结果。"""

    receipt = _receipt(tmp_path)

    def fake_run(self, url, **kwargs):
        return receipt

    monkeypatch.setattr(NotePipeline, "run", fake_run)

    with TestClient(_app(tmp_path, worker)) as client:
        created = client.post(
            "/api/note/generate", json={"url": "https://zhuanlan.zhihu.com/p/1"}
        )
        assert created.status_code == 200
        task = created.json()["task"]
        assert task["id"]
        assert task["type"] == "note.generate"
        assert task["status"] in {"pending", "running", "succeeded"}
        assert task["payload"]["url"] == "https://zhuanlan.zhihu.com/p/1"

        finished = _wait_for_status(client, task["id"], TaskStatus.SUCCEEDED)
        result = finished["result"]
        assert result["done"] is True
        assert result["note"]["title"] == "标题"
        assert result["note"]["markdown"].lstrip().startswith("# 标题")
        assert "---" not in result["note"]["markdown"]
        assert result["note"]["platform"] == "zhihu"
        assert result["warnings"] == ["示例告警"]
        conversation = result["conversation"]
        assert conversation["id"]
        assert conversation["note_path"]
        # 结果也落到会话接口，可被前端直接读回
        fetched = client.get(f"/api/conversations/{conversation['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["conversation"]["note_content"].lstrip().startswith("# 标题")


def test_note_generate_streams_delta_stage_progress_until_terminal(
    tmp_path: Path, worker: TaskWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务运行期间订阅 events，能收到 delta / stage / progress，并最终收到终态。"""

    receipt = _receipt(tmp_path)
    started = threading.Event()
    gate = threading.Event()

    def fake_run(self, url, **kwargs):
        progress = kwargs["progress_callback"]
        stream = kwargs["stream_callback"]
        progress("fetch", "抓取内容", "start")
        stream("增量片段")
        started.set()
        gate.wait(timeout=5.0)
        progress("save", "保存笔记", "ok")
        return receipt

    monkeypatch.setattr(NotePipeline, "run", fake_run)

    with TestClient(_app(tmp_path, worker)) as client:
        task_id = client.post(
            "/api/note/generate", json={"url": "https://zhuanlan.zhihu.com/p/2"}
        ).json()["task"]["id"]
        assert started.wait(timeout=5.0) is True

        events: list[dict] = []
        with client.stream("GET", f"/api/tasks/{task_id}/events") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            lines = iter(resp.iter_lines())
            for line in lines:
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                events.append(event)
                if "delta" in event:
                    break
            # 已经读到实时增量，放行任务，继续读到终态
            gate.set()
            for line in lines:
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    assert {"delta": "增量片段"} in events
    stages = [event["stage"] for event in events if "stage" in event]
    assert "fetch" in stages
    assert "save" in stages
    progress = [event["progress"] for event in events if "progress" in event]
    assert 20 in progress  # fetch start
    assert 100 in progress  # save ok
    snapshots = [event["task"] for event in events if "task" in event]
    assert snapshots[-1]["status"] == "succeeded"
    assert snapshots[-1]["result"]["done"] is True


def test_note_generate_missing_conversation_returns_404(
    tmp_path: Path, worker: TaskWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指定不存在的会话时，提交前即拒绝，不产生任务。"""

    monkeypatch.setattr(NotePipeline, "run", lambda self, url, **kwargs: _receipt(tmp_path))

    with TestClient(_app(tmp_path, worker)) as client:
        resp = client.post(
            "/api/note/generate",
            json={"url": "https://zhuanlan.zhihu.com/p/3", "conversation_id": "missing"},
        )
        assert resp.status_code == 404
        assert client.get("/api/tasks").json()["tasks"] == []


def test_note_generate_requires_url(tmp_path: Path, worker: TaskWorker) -> None:
    with TestClient(_app(tmp_path, worker)) as client:
        assert client.post("/api/note/generate", json={"url": ""}).status_code == 422
        assert client.post("/api/note/generate", json={}).status_code == 422
