"""任务执行器测试：提交、同步执行、后台线程、取消与错误处理。"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from zhiji.errors import GenerationCancelledError, TaskTransitionError
from zhiji.models import TaskStatus
from zhiji.storage import Database, TaskRepository, reset_database
from zhiji.tasks import (
    EMPTY_RESULT,
    TaskContext,
    TaskWorker,
    get_worker,
    reset_worker,
)


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture()
def worker(database: Database) -> Iterator[TaskWorker]:
    w = TaskWorker(database=database, poll_interval=0.05)
    yield w
    w.stop()


def _row(worker: TaskWorker, task_id: str):
    with worker.database.session_scope() as session:
        return TaskRepository(session).get(task_id)


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待任务进入目标状态超时")


def test_run_once_executes_pending_task(worker: TaskWorker) -> None:
    seen: dict[str, object] = {}

    def handler(context: TaskContext) -> dict[str, object]:
        seen["payload"] = context.payload
        context.report(40)
        return {"note": "a.md", "count": 2}

    worker.register("note.generate", handler)
    task_id = worker.submit(
        task_type="note.generate",
        platform="bilibili",
        payload={"url": "u1"},
    )

    pending = _row(worker, task_id)
    assert pending.status == TaskStatus.PENDING.value
    assert pending.started_at is None

    assert worker.run_once() is True

    done = _row(worker, task_id)
    assert done.status == TaskStatus.SUCCEEDED.value
    assert done.result_json is not None
    assert json.loads(done.result_json) == {"note": "a.md", "count": 2}
    assert done.progress == 40
    assert done.started_at is not None and done.finished_at is not None
    assert seen["payload"] == {"url": "u1"}


def test_run_once_returns_false_when_idle(worker: TaskWorker) -> None:
    assert worker.run_once() is False


def test_submit_returns_immediately_without_running(worker: TaskWorker) -> None:
    called = threading.Event()

    def handler(context: TaskContext) -> dict[str, object]:
        called.set()
        return {}

    worker.register("t", handler)
    task_id = worker.submit(task_type="t")

    assert called.is_set() is False
    assert _row(worker, task_id).status == TaskStatus.PENDING.value


def test_handler_without_result_records_empty_object(worker: TaskWorker) -> None:
    worker.register("t", lambda context: None)
    task_id = worker.submit(task_type="t")
    assert worker.run_once() is True
    row = _row(worker, task_id)
    assert row.status == TaskStatus.SUCCEEDED.value
    assert json.loads(row.result_json or "null") == EMPTY_RESULT


def test_missing_handler_marks_failed(worker: TaskWorker) -> None:
    task_id = worker.submit(task_type="unknown.type")
    assert worker.run_once() is True
    row = _row(worker, task_id)
    assert row.status == TaskStatus.FAILED.value
    assert row.error is not None and "unknown.type" in row.error


def test_handler_exception_marks_failed_with_message(worker: TaskWorker) -> None:
    def handler(context: TaskContext) -> dict[str, object]:
        raise RuntimeError("抖音链接解析失败")

    worker.register("t", handler)
    task_id = worker.submit(task_type="t")
    assert worker.run_once() is True
    row = _row(worker, task_id)
    assert row.status == TaskStatus.FAILED.value
    assert row.error == "抖音链接解析失败"
    assert row.finished_at is not None


def test_generation_cancelled_marks_cancelled_not_failed(worker: TaskWorker) -> None:
    def handler(context: TaskContext) -> dict[str, object]:
        raise GenerationCancelledError("客户端断开")

    worker.register("t", handler)
    task_id = worker.submit(task_type="t")
    assert worker.run_once() is True
    row = _row(worker, task_id)
    assert row.status == TaskStatus.CANCELLED.value
    assert row.error is None


def test_cancel_pending_task(worker: TaskWorker) -> None:
    task_id = worker.submit(task_type="t")
    row = worker.cancel(task_id)
    assert row.status == TaskStatus.CANCELLED.value
    stored = _row(worker, task_id)
    assert stored.finished_at is not None
    assert stored.started_at is None


def test_cancel_terminal_task_raises(worker: TaskWorker) -> None:
    worker.register("t", lambda context: {})
    task_id = worker.submit(task_type="t")
    assert worker.run_once() is True
    with pytest.raises(TaskTransitionError):
        worker.cancel(task_id)


def test_external_cancel_wins_over_handler_result(worker: TaskWorker) -> None:
    """外部取消后处理函数才返回，不得把任务覆盖回成功。"""

    def handler(context: TaskContext) -> dict[str, object]:
        assert context.is_cancelled() is False
        worker.cancel(context.task_id)
        assert context.is_cancelled() is True
        context.report(90)  # 已取消，进度上报静默忽略
        return {"late": True}

    worker.register("t", handler)
    task_id = worker.submit(task_type="t")
    assert worker.run_once() is True
    row = _row(worker, task_id)
    assert row.status == TaskStatus.CANCELLED.value
    assert row.result_json is None


def test_higher_priority_runs_first(worker: TaskWorker) -> None:
    order: list[str] = []

    def make(name: str):
        def handler(context: TaskContext) -> dict[str, object]:
            order.append(name)
            return {"name": name}

        return handler

    worker.register("low", make("low"))
    worker.register("mid", make("mid"))
    worker.register("high", make("high"))
    worker.submit(task_type="low", priority=0)
    worker.submit(task_type="mid", priority=5)
    worker.submit(task_type="high", priority=10)

    assert worker.run_once() is True
    assert worker.run_once() is True
    assert worker.run_once() is True
    assert order == ["high", "mid", "low"]


def test_background_worker_completes_task(worker: TaskWorker) -> None:
    done = threading.Event()

    def handler(context: TaskContext) -> dict[str, object]:
        context.report(50)
        done.set()
        return {"ok": True}

    worker.register("t", handler)
    worker.start()
    task_id = worker.submit(task_type="t")

    assert done.wait(timeout=5.0) is True
    _wait_until(lambda: _row(worker, task_id).status == TaskStatus.SUCCEEDED.value)
    row = _row(worker, task_id)
    assert row.status == TaskStatus.SUCCEEDED.value
    assert row.progress == 50


def test_start_is_idempotent_and_stop_joins_threads(worker: TaskWorker) -> None:
    worker.start()
    worker.start()
    assert len(worker._threads) == worker.max_workers
    threads = list(worker._threads)

    worker.stop()
    assert worker._threads == []
    assert all(not thread.is_alive() for thread in threads)
    worker.stop()  # 幂等


def test_register_supports_decorator_and_positional(worker: TaskWorker) -> None:
    @worker.register("deco")
    def handler(context: TaskContext) -> dict[str, object]:
        return {"via": "deco"}

    assert worker.handlers["deco"] is handler

    def other(context: TaskContext) -> dict[str, object]:
        return {}

    assert worker.register("plain", other) is other
    assert worker.handlers["plain"] is other


def test_get_worker_is_process_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "ZHIJI_DATABASE_URL", f"sqlite:///{(tmp_path / 'default.db').as_posix()}"
    )

    def handler(context: TaskContext) -> dict[str, object]:
        return {}

    try:
        reset_worker()
        reset_database()
        first = get_worker(handlers={"a": handler}, poll_interval=0.05)
        assert get_worker() is first
        get_worker(handlers={"b": handler})
        assert first.handlers["a"] is handler
        assert first.handlers["b"] is handler

        reset_worker()
        second = get_worker(poll_interval=0.05)
        assert second is not first
    finally:
        reset_worker()
        reset_database()
