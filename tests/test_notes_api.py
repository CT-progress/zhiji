"""阶段 C · 工作区②：笔记与待办 API 测试。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zhiji.config import ConfigManager
from zhiji.web.app import create_app

PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_BCRYPT_ROUNDS", "4")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    cm = ConfigManager(tmp_path / "zhiji.json")
    app = create_app(
        cm,
        data_dir=tmp_path / "data",
        secret_key="test-secret",
        require_auth=True,
    )
    with TestClient(app) as value:
        yield value


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


def _create_note(
    client: TestClient,
    token: str,
    *,
    title: str = "无标题",
    content: str = "",
    tags: str = "",
    pinned: bool = False,
) -> dict:
    response = client.post(
        "/api/notes",
        headers=_headers(token),
        json={
            "title": title,
            "content": content,
            "tags": tags,
            "pinned": pinned,
        },
    )
    assert response.status_code == 200
    return response.json()["note"]


def test_notes_crud_search_tags_and_pin_order(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)

    first = _create_note(
        client,
        token,
        title="项目计划",
        content="第一阶段先完成工作台。",
        tags="工作, 项目",
    )
    second = _create_note(
        client,
        token,
        title="读书笔记",
        content="这里包含检索关键字。",
        tags="阅读",
    )

    searched = client.get("/api/notes", headers=headers, params={"q": "检索关键字"})
    assert searched.status_code == 200
    assert [note["id"] for note in searched.json()["notes"]] == [second["id"]]

    tagged = client.get("/api/notes", headers=headers, params={"tag": "项目"})
    assert tagged.status_code == 200
    assert [note["id"] for note in tagged.json()["notes"]] == [first["id"]]

    updated = client.put(
        f"/api/notes/{second['id']}",
        headers=headers,
        json={"content": "修订后的正文", "pinned": True},
    )
    assert updated.status_code == 200
    assert updated.json()["note"]["content"] == "修订后的正文"
    assert updated.json()["note"]["pinned"] is True

    listed = client.get("/api/notes", headers=headers)
    assert listed.status_code == 200
    assert [note["id"] for note in listed.json()["notes"]] == [second["id"], first["id"]]

    fetched = client.get(f"/api/notes/{first['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["note"]["title"] == "项目计划"

    deleted = client.delete(f"/api/notes/{first['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/notes/{first['id']}", headers=headers).status_code == 404
    assert client.put(
        f"/api/notes/{first['id']}", headers=headers, json={"title": "不存在"}
    ).status_code == 404
    assert client.delete(f"/api/notes/{first['id']}", headers=headers).status_code == 404


def test_todos_create_filter_update_and_clear_relations(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    note = _create_note(client, token, title="关联笔记")

    created = client.post(
        "/api/todos",
        headers=headers,
        json={
            "title": "整理阶段 C 验收记录",
            "priority": 2,
            "due_date": "2026-10-30T09:30:00",
            "note_id": note["id"],
        },
    )
    assert created.status_code == 200
    todo = created.json()["todo"]
    assert todo["done"] is False
    assert todo["priority"] == 2
    assert todo["due_date"].startswith("2026-10-30T09:30:00")
    assert todo["note_id"] == note["id"]

    by_note = client.get(
        "/api/todos", headers=headers, params={"note_id": note["id"]}
    )
    assert by_note.status_code == 200
    assert [item["id"] for item in by_note.json()["todos"]] == [todo["id"]]

    completed = client.put(
        f"/api/todos/{todo['id']}",
        headers=headers,
        json={"done": True, "priority": 1, "due_date": None, "note_id": None},
    )
    assert completed.status_code == 200
    payload = completed.json()["todo"]
    assert payload["done"] is True
    assert payload["priority"] == 1
    assert payload["due_date"] is None
    assert payload["note_id"] is None

    done_only = client.get("/api/todos", headers=headers, params={"done": True})
    assert done_only.status_code == 200
    assert [item["id"] for item in done_only.json()["todos"]] == [todo["id"]]

    deleted = client.delete(f"/api/todos/{todo['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.put(
        f"/api/todos/{todo['id']}", headers=headers, json={"done": False}
    ).status_code == 404
    assert client.delete(f"/api/todos/{todo['id']}", headers=headers).status_code == 404


def test_deleting_note_keeps_todo_and_clears_note_id(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    note = _create_note(client, token, title="会被删除的笔记")
    created = client.post(
        "/api/todos",
        headers=headers,
        json={"title": "保留的待办", "note_id": note["id"]},
    )
    assert created.status_code == 200
    todo_id = created.json()["todo"]["id"]

    assert client.delete(f"/api/notes/{note['id']}", headers=headers).status_code == 200

    todos = client.get("/api/todos", headers=headers)
    assert todos.status_code == 200
    payload = todos.json()["todos"]
    assert len(payload) == 1
    assert payload[0]["id"] == todo_id
    assert payload[0]["note_id"] is None


def test_notes_and_todos_are_isolated_between_users(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    alice_headers = _headers(alice_token)
    bob_headers = _headers(bob_token)

    bob_note = _create_note(client, bob_token, title="仅鲍勃可见")
    bob_todo = client.post(
        "/api/todos",
        headers=bob_headers,
        json={"title": "鲍勃的待办", "note_id": bob_note["id"]},
    ).json()["todo"]

    assert client.get("/api/notes", headers=alice_headers).json()["notes"] == []
    assert client.get("/api/todos", headers=alice_headers).json()["todos"] == []
    assert (
        client.get(f"/api/notes/{bob_note['id']}", headers=alice_headers).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/notes/{bob_note['id']}",
            headers=alice_headers,
            json={"title": "越权修改"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/notes/{bob_note['id']}", headers=alice_headers).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/todos/{bob_todo['id']}",
            headers=alice_headers,
            json={"done": True},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/todos/{bob_todo['id']}", headers=alice_headers).status_code
        == 404
    )
    assert (
        client.post(
            "/api/todos",
            headers=alice_headers,
            json={"title": "不能关联别人的笔记", "note_id": bob_note["id"]},
        ).status_code
        == 404
    )

    still_there = client.get(f"/api/notes/{bob_note['id']}", headers=bob_headers)
    assert still_there.status_code == 200
    assert still_there.json()["note"]["title"] == "仅鲍勃可见"
    assert [item["id"] for item in client.get(
        "/api/todos", headers=bob_headers
    ).json()["todos"]] == [bob_todo["id"]]
