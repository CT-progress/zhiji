"""阶段 3.3 测试：会话 / 任务的用户隔离与旧库补列迁移。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from zhiji.chat import ChatNotFoundError, ChatStore
from zhiji.config import ConfigManager
from zhiji.errors import TaskNotFoundError
from zhiji.storage import Database, TaskRepository, UserRepository
from zhiji.storage.scope import ANY_USER
from zhiji.tasks import TaskWorker
from zhiji.web.app import create_app

PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_BCRYPT_ROUNDS", "4")


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = Database(f"sqlite:///{(data_dir / 'zhiji.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture()
def worker(database: Database) -> Iterator[TaskWorker]:
    value = TaskWorker(database=database, poll_interval=0.05)
    yield value
    value.stop()


@pytest.fixture()
def client(tmp_path: Path, worker: TaskWorker) -> Iterator[TestClient]:
    cm = ConfigManager(tmp_path / "zhiji.json")
    app = create_app(
        cm,
        data_dir=tmp_path / "data",
        worker=worker,
        secret_key="test-secret",
        require_auth=True,
    )
    value = TestClient(app)
    yield value
    value.close()


def _register_and_login(client: TestClient, username: str) -> tuple[str, str]:
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD},
    )
    assert registered.status_code == 200
    logged_in = client.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert logged_in.status_code == 200
    payload = logged_in.json()
    client.cookies.clear()
    return payload["user"]["id"], payload["token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_conversation(client: TestClient, token: str, title: str) -> str:
    response = client.post(
        "/api/conversations",
        headers=_headers(token),
        json={"title": title},
    )
    assert response.status_code == 200
    return response.json()["conversation"]["id"]


def test_conversations_are_isolated_between_users(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")

    alice_conversation = _create_conversation(client, alice_token, "爱丽丝的会话")
    bob_conversation = _create_conversation(client, bob_token, "鲍勃的会话")

    alice_list = client.get("/api/conversations", headers=_headers(alice_token))
    bob_list = client.get("/api/conversations", headers=_headers(bob_token))
    assert alice_list.status_code == 200
    assert bob_list.status_code == 200
    assert {item["id"] for item in alice_list.json()["conversations"]} == {
        alice_conversation
    }
    assert {item["id"] for item in bob_list.json()["conversations"]} == {
        bob_conversation
    }


def test_cross_user_conversation_read_is_404(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    bob_conversation = _create_conversation(client, bob_token, "仅鲍勃可见")

    response = client.get(
        f"/api/conversations/{bob_conversation}",
        headers=_headers(alice_token),
    )
    assert response.status_code == 404


def test_cross_user_conversation_delete_is_404(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    bob_conversation = _create_conversation(client, bob_token, "不能删除")

    response = client.delete(
        f"/api/conversations/{bob_conversation}",
        headers=_headers(alice_token),
    )
    assert response.status_code == 404

    still_there = client.get(
        f"/api/conversations/{bob_conversation}",
        headers=_headers(bob_token),
    )
    assert still_there.status_code == 200


def _create_task(client: TestClient, token: str, task_type: str) -> str:
    response = client.post(
        "/api/tasks",
        headers=_headers(token),
        json={"type": task_type},
    )
    assert response.status_code == 200
    return response.json()["task"]["id"]


def test_tasks_are_isolated_between_users(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")

    alice_task = _create_task(client, alice_token, "alice-task")
    bob_task = _create_task(client, bob_token, "bob-task")

    alice_list = client.get("/api/tasks", headers=_headers(alice_token))
    bob_list = client.get("/api/tasks", headers=_headers(bob_token))
    assert alice_list.status_code == 200
    assert bob_list.status_code == 200
    assert {item["id"] for item in alice_list.json()["tasks"]} == {alice_task}
    assert {item["id"] for item in bob_list.json()["tasks"]} == {bob_task}


def test_cross_user_task_read_is_404(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    bob_task = _create_task(client, bob_token, "bob-task")

    response = client.get(f"/api/tasks/{bob_task}", headers=_headers(alice_token))
    assert response.status_code == 404


def test_cross_user_task_cancel_is_404(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    bob_task = _create_task(client, bob_token, "bob-task")

    response = client.post(
        f"/api/tasks/{bob_task}/cancel",
        headers=_headers(alice_token),
    )
    assert response.status_code == 404

    still_there = client.get(f"/api/tasks/{bob_task}", headers=_headers(bob_token))
    assert still_there.status_code == 200


def test_none_scope_sees_only_legacy_data(database: Database) -> None:
    with database.session_scope() as session:
        UserRepository(session).create(
            username="alice",
            password_hash="hash",
            user_id="alice-id",
        )

    store = ChatStore(backend="db", database=database)
    legacy_conversation = store.create("本机历史会话")
    alice_conversation = store.create("爱丽丝会话", user_id="alice-id")

    assert [item.id for item in store.list(user_id=None)] == [legacy_conversation.id]
    assert store.get(alice_conversation.id, user_id=ANY_USER).title == "爱丽丝会话"
    with pytest.raises(ChatNotFoundError):
        store.get(alice_conversation.id, user_id=None)

    with database.session_scope() as session:
        repository = TaskRepository(session)
        legacy_task = repository.create(task_type="legacy-task")
        alice_task = repository.create(task_type="alice-task", user_id="alice-id")

    with database.session_scope() as session:
        repository = TaskRepository(session)
        assert [row.id for row in repository.list(user_id=None)] == [legacy_task.id]
        assert repository.get(alice_task.id, user_id=ANY_USER).type == "alice-task"
        with pytest.raises(TaskNotFoundError):
            repository.get(alice_task.id, user_id=None)


def test_create_all_adds_user_columns_to_legacy_database(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE conversations (id VARCHAR(32) PRIMARY KEY)")
        connection.execute("CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY)")

    database = Database(f"sqlite:///{db_path.as_posix()}")
    try:
        database.create_all()
        inspector = inspect(database.engine)

        conversation_columns = {
            column["name"] for column in inspector.get_columns("conversations")
        }
        task_columns = {column["name"] for column in inspector.get_columns("tasks")}
        conversation_indexes = {
            index["name"] for index in inspector.get_indexes("conversations")
        }
        task_indexes = {index["name"] for index in inspector.get_indexes("tasks")}

        assert "user_id" in conversation_columns
        assert "user_id" in task_columns
        assert "ix_conversations_user_id" in conversation_indexes
        assert "ix_tasks_user_id" in task_indexes
    finally:
        database.dispose()