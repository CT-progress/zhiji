"""阶段 C · 工作区④：文档助手 API 测试。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zhiji.config import ConfigManager
from zhiji.llm.client import LLMClient
from zhiji.models import LLMModelConfig
from zhiji.web.app import create_app

PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_BCRYPT_ROUNDS", "4")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    cm = ConfigManager(tmp_path / "zhiji.json")
    cm.add_model(
        LLMModelConfig(
            name="测试模型",
            api_key="test-key",
            model="test-model",
            base_url="http://127.0.0.1:9/v1",
        )
    )
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


def _create_doc(
    client: TestClient,
    token: str,
    *,
    title: str | None = None,
    content: str = "",
    source_type: str = "blank",
    source_id: str | None = None,
) -> dict:
    payload = {
        "content": content,
        "source_type": source_type,
        "source_id": source_id,
    }
    if title is not None:
        payload["title"] = title
    response = client.post("/api/docs", headers=_headers(token), json=payload)
    assert response.status_code == 200, response.text
    return response.json()["document"]


def _events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_doc_crud_search_stats_and_markdown_export(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)

    first = _create_doc(
        client,
        token,
        title="发布复盘",
        content="# 发布复盘\n\n本阶段完成了知识库。下一步进入文档助手。",
    )
    second = _create_doc(
        client,
        token,
        title="写作提纲",
        content="一、背景\n二、目标\n三、行动",
    )

    listed = client.get("/api/docs", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["documents"]] == [second["id"], first["id"]]
    assert "content" not in listed.json()["documents"][0]
    assert listed.json()["documents"][1]["excerpt"].startswith("# 发布复盘")

    searched = client.get("/api/docs", headers=headers, params={"q": "文档助手"})
    assert searched.status_code == 200
    assert [item["id"] for item in searched.json()["documents"]] == [first["id"]]

    fetched = client.get(f"/api/docs/{first['id']}", headers=headers)
    assert fetched.status_code == 200
    assert "下一步进入文档助手" in fetched.json()["document"]["content"]

    updated = client.put(
        f"/api/docs/{first['id']}",
        headers=headers,
        json={"title": "发布复盘 · 定稿", "content": "# 定稿\n\n内容已经补齐。"},
    )
    assert updated.status_code == 200
    assert updated.json()["document"]["title"] == "发布复盘 · 定稿"
    assert updated.json()["document"]["characters"] == len("# 定稿\n\n内容已经补齐。")

    blank_title = client.put(
        f"/api/docs/{second['id']}", headers=headers, json={"title": "   "}
    )
    assert blank_title.status_code == 200
    assert blank_title.json()["document"]["title"] == "无标题文档"

    stats = client.get("/api/docs/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.json()["stats"]["documents"] == 2
    assert stats.json()["stats"]["characters"] > 0
    assert stats.json()["stats"]["updated_today"] == 2
    assert stats.json()["stats"]["sources"] == 0

    exported = client.get(f"/api/docs/{first['id']}/export", headers=headers)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/markdown")
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.text == "# 定稿\n\n内容已经补齐。"

    deleted = client.delete(f"/api/docs/{first['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/docs/{first['id']}", headers=headers).status_code == 404
    assert client.delete(f"/api/docs/{first['id']}", headers=headers).status_code == 404


def test_doc_can_be_created_from_note_and_knowledge_source(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)

    note = client.post(
        "/api/notes",
        headers=headers,
        json={
            "title": "访谈笔记",
            "content": "用户希望文档助手保留原始素材，并允许直接插入生成结果。",
        },
    ).json()["note"]
    library = client.post(
        "/api/knowledge/libraries",
        headers=headers,
        json={"name": "产品资料", "description": "需求资料"},
    ).json()["library"]
    knowledge_doc = client.post(
        f"/api/knowledge/libraries/{library['id']}/documents",
        headers=headers,
        json={
            "filename": "brief.md",
            "title": "产品简报",
            "content": "# 产品简报\n\n先完成单文档闭环，再扩展批处理。",
        },
    ).json()["document"]

    sources = client.get("/api/docs/sources", headers=headers)
    assert sources.status_code == 200
    items = sources.json()["sources"]
    assert {item["type"] for item in items} == {"note", "knowledge"}
    assert {item["title"] for item in items} == {"访谈笔记", "产品简报"}

    from_note = _create_doc(
        client,
        token,
        source_type="note",
        source_id=note["id"],
    )
    assert from_note["title"] == "访谈笔记"
    assert from_note["source_type"] == "note"
    assert "允许直接插入生成结果" in from_note["content"]

    from_knowledge = _create_doc(
        client,
        token,
        source_type="knowledge",
        source_id=knowledge_doc["id"],
    )
    assert from_knowledge["title"] == "产品简报"
    assert from_knowledge["source_type"] == "knowledge"
    assert "先完成单文档闭环" in from_knowledge["content"]

    stats = client.get("/api/docs/stats", headers=headers).json()["stats"]
    assert stats["documents"] == 2
    assert stats["sources"] == 2


def test_docs_are_isolated_between_users(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    alice_headers = _headers(alice_token)
    bob_headers = _headers(bob_token)

    bob_note = client.post(
        "/api/notes",
        headers=bob_headers,
        json={"title": "鲍勃的笔记", "content": "私有素材"},
    ).json()["note"]
    bob_doc = _create_doc(client, bob_token, title="仅鲍勃可见", content="私有文档正文")

    assert client.get("/api/docs", headers=alice_headers).json()["documents"] == []
    assert client.get("/api/docs/sources", headers=alice_headers).json()["sources"] == []
    assert client.get("/api/docs/stats", headers=alice_headers).json()["stats"] == {
        "documents": 0,
        "characters": 0,
        "updated_today": 0,
        "sources": 0,
    }
    assert (
        client.get(f"/api/docs/{bob_doc['id']}", headers=alice_headers).status_code == 404
    )
    assert (
        client.put(
            f"/api/docs/{bob_doc['id']}",
            headers=alice_headers,
            json={"title": "越权修改"},
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/docs/{bob_doc['id']}/export", headers=alice_headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/docs/{bob_doc['id']}/assist/stream",
            headers=alice_headers,
            json={"action": "summarize"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/docs",
            headers=alice_headers,
            json={"source_type": "note", "source_id": bob_note["id"]},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/docs/{bob_doc['id']}", headers=alice_headers).status_code
        == 404
    )
    assert client.get(f"/api/docs/{bob_doc['id']}", headers=bob_headers).status_code == 200


def test_assist_stream_uses_current_draft_and_action_prompt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    doc = _create_doc(client, token, title="阶段复盘", content="数据库中的旧内容")
    captured: dict[str, object] = {}

    def fake_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        captured["messages"] = messages
        captured["model"] = self.model.name
        yield "核心结论："
        yield "文档助手闭环已打通。"

    monkeypatch.setattr(LLMClient, "stream_chunks", fake_stream)

    response = client.post(
        f"/api/docs/{doc['id']}/assist/stream",
        headers=headers,
        json={
            "action": "summarize",
            "content": "浏览器里尚未保存的新正文，包含关键结论。",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert events[0] == {"meta": {"model": "测试模型", "action": "summarize"}}
    assert "".join(event.get("delta", "") for event in events) == "核心结论：文档助手闭环已打通。"
    assert events[-1] == {"done": True}
    assert captured["model"] == "测试模型"
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "浏览器里尚未保存的新正文" in messages[1]["content"]
    assert "数据库中的旧内容" not in messages[1]["content"]

    missing_question = client.post(
        f"/api/docs/{doc['id']}/assist/stream",
        headers=headers,
        json={"action": "ask"},
    )
    assert missing_question.status_code == 400
    invalid_action = client.post(
        f"/api/docs/{doc['id']}/assist/stream",
        headers=headers,
        json={"action": "rewrite-everything"},
    )
    assert invalid_action.status_code == 422


def test_assist_rejects_empty_document_and_invalid_source(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    empty = _create_doc(client, token, title="空白文档")

    response = client.post(
        f"/api/docs/{empty['id']}/assist/stream",
        headers=headers,
        json={"action": "continue"},
    )
    assert response.status_code == 400
    assert "还是空的" in response.json()["detail"]

    missing_source = client.post(
        "/api/docs",
        headers=headers,
        json={"source_type": "knowledge", "source_id": "missing"},
    )
    assert missing_source.status_code == 404
    missing_note_id = client.post(
        "/api/docs",
        headers=headers,
        json={"source_type": "note"},
    )
    assert missing_note_id.status_code == 400


def test_document_order_is_stable_when_clock_does_not_advance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiji.storage import doc_repository

    fixed = datetime(2026, 10, 27, 8, 0, 0, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(doc_repository, "datetime", FrozenDateTime)
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)

    first = _create_doc(client, token, title="第一篇", content="same tick")
    second = _create_doc(client, token, title="第二篇", content="same tick")

    assert first["updated_at"] == "2026-10-27T08:00:00+00:00"
    assert second["updated_at"] == "2026-10-27T08:00:00.000001+00:00"
    listed = client.get("/api/docs", headers=headers)
    assert [item["id"] for item in listed.json()["documents"]] == [second["id"], first["id"]]
