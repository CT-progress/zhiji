"""数据库底座测试：连接串、建表与任务仓储。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from zhiji.errors import StorageError, TaskNotFoundError
from zhiji.models import TaskStatus
from zhiji.storage import (
    DEFAULT_DB_RELATIVE,
    Database,
    TaskRepository,
    default_database_url,
    get_database,
    reset_database,
)


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


def test_default_database_url_falls_back_to_project_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZHIJI_DATABASE_URL", raising=False)
    url = default_database_url()
    assert url.startswith("sqlite:///")
    assert url.endswith(DEFAULT_DB_RELATIVE.as_posix())


def test_default_database_url_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/zhiji")
    assert default_database_url() == "postgresql+psycopg://u:p@localhost:5432/zhiji"


def test_invalid_url_raises_storage_error() -> None:
    with pytest.raises(StorageError):
        Database("not-a-valid-url://x")


def test_create_all_creates_tasks_table(database: Database) -> None:
    from sqlalchemy import inspect

    assert "tasks" in inspect(database.engine).get_table_names()


def test_task_create_and_get(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        row = repo.create(task_type="note.generate", platform="bilibili", payload={"url": "u1"})
        task_id = row.id
        assert row.status == TaskStatus.PENDING.value

    with database.session_scope() as session:
        fetched = TaskRepository(session).get(task_id)
        assert fetched.type == "note.generate"
        assert fetched.platform == "bilibili"
        assert '"u1"' in fetched.payload_json
        assert fetched.started_at is None


def test_get_missing_task_raises(database: Database) -> None:
    with database.session_scope() as session:
        with pytest.raises(TaskNotFoundError):
            TaskRepository(session).get("missing")
        assert TaskRepository(session).find("missing") is None


def test_list_filters_by_status_and_platform(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        a = repo.create(task_type="t1", platform="bilibili")
        repo.create(task_type="t2", platform="zhihu")
        repo.update_status(a.id, TaskStatus.RUNNING)

    with database.session_scope() as session:
        repo = TaskRepository(session)
        assert len(repo.list()) == 2
        assert [r.id for r in repo.list(status=TaskStatus.RUNNING)] == [a.id]
        assert [r.platform for r in repo.list(platform="zhihu")] == ["zhihu"]
        assert repo.list(limit=1) != []


def test_update_status_sets_timestamps(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        row = repo.create(task_type="t1")
        task_id = row.id
        repo.update_status(task_id, TaskStatus.RUNNING, progress=10)
        running = repo.get(task_id)
        assert running.started_at is not None
        assert running.finished_at is None
        assert running.progress == 10

    with database.session_scope() as session:
        repo = TaskRepository(session)
        repo.update_status(task_id, TaskStatus.SUCCEEDED, result={"note": "a.md"})
        done = repo.get(task_id)
        assert done.finished_at is not None
        assert done.result_json is not None and "a.md" in done.result_json


def test_update_status_rejects_unknown_status(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t1").id
        with pytest.raises(ValueError):
            repo.update_status(task_id, "not-a-status")


def test_session_scope_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError), database.session_scope() as session:
        TaskRepository(session).create(task_type="boom")
        raise RuntimeError("模拟失败")

    with database.session_scope() as session:
        assert TaskRepository(session).list() == []


def test_get_database_singleton_and_reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZHIJI_DATABASE_URL", f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    reset_database()
    try:
        first = get_database()
        assert get_database() is first
        other = get_database("sqlite:///:memory:")
        assert other is not first
        other.dispose()
    finally:
        reset_database()