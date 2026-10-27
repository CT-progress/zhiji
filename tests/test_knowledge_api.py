"""阶段 C · 工作区③：知识库 API 测试。"""

from __future__ import annotations

import json
from collections.abc import Iterator
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


def _create_library(
    client: TestClient,
    token: str,
    *,
    name: str = "研究资料",
    description: str = "长期维护的主题资料",
) -> dict:
    response = client.post(
        "/api/knowledge/libraries",
        headers=_headers(token),
        json={"name": name, "description": description, "color": "info"},
    )
    assert response.status_code == 200
    return response.json()["library"]


def _import_document(
    client: TestClient,
    token: str,
    library_id: str,
    *,
    filename: str,
    content: str,
    title: str | None = None,
    mime_type: str | None = None,
) -> dict:
    body = {"filename": filename, "content": content}
    if title is not None:
        body["title"] = title
    if mime_type is not None:
        body["mime_type"] = mime_type
    response = client.post(
        f"/api/knowledge/libraries/{library_id}/documents",
        headers=_headers(token),
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]


def test_library_crud_and_text_import_formats(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    library = _create_library(client, token)

    updated = client.patch(
        f"/api/knowledge/libraries/{library['id']}",
        headers=headers,
        json={"name": "产品研究", "description": "竞品与访谈"},
    )
    assert updated.status_code == 200
    assert updated.json()["library"]["name"] == "产品研究"

    text_document = _import_document(
        client,
        token,
        library["id"],
        filename="访谈记录.txt",
        content="  第一段  访谈结论。\r\n\r\n\r\n第二段包含 关键需求。  ",
    )
    assert text_document["title"] == "访谈记录"
    assert text_document["status"] == "indexed"
    assert text_document["chunk_count"] >= 1
    assert text_document["char_count"] > 0

    markdown_document = _import_document(
        client,
        token,
        library["id"],
        filename="notes.markdown",
        title="路线图笔记",
        content="# 路线图\n\n- 建立本地全文检索\n- 完成引用问答",
    )
    assert markdown_document["title"] == "路线图笔记"
    assert markdown_document["mime_type"] == "text/markdown"

    html_document = _import_document(
        client,
        token,
        library["id"],
        filename="article.html",
        content=(
            "<html><head><style>.hide{display:none}</style><script>alert(1)</script>"
            "</head><body><h1>知识库标题</h1><p>HTML 正文段落。</p></body></html>"
        ),
    )

    listed = client.get("/api/knowledge/documents", headers=headers)
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["documents"]} == {
        text_document["id"],
        markdown_document["id"],
        html_document["id"],
    }

    detail = client.get(
        f"/api/knowledge/documents/{html_document['id']}", headers=headers
    )
    assert detail.status_code == 200
    document = detail.json()["document"]
    assert "HTML 正文段落" in document["text"]
    assert "alert(1)" not in document["text"]
    assert "display:none" not in document["text"]

    libraries = client.get("/api/knowledge/libraries", headers=headers)
    assert libraries.status_code == 200
    item = libraries.json()["libraries"][0]
    assert item["document_count"] == 3
    assert item["char_count"] == sum(
        doc["char_count"]
        for doc in [text_document, markdown_document, html_document]
    )

    stats = client.get("/api/knowledge/stats", headers=headers)
    assert stats.status_code == 200
    payload = stats.json()["stats"]
    assert payload["libraries"] == 1
    assert payload["documents"] == 3
    assert payload["chunks"] >= 3
    assert payload["characters"] > 0

    renamed = client.patch(
        f"/api/knowledge/documents/{text_document['id']}",
        headers=headers,
        json={"title": "用户访谈 · 第一轮"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["document"]["title"] == "用户访谈 · 第一轮"

    deleted = client.delete(
        f"/api/knowledge/documents/{markdown_document['id']}", headers=headers
    )
    assert deleted.status_code == 200
    assert (
        client.get(
            f"/api/knowledge/documents/{markdown_document['id']}", headers=headers
        ).status_code
        == 404
    )


def test_import_validation_rejects_unsupported_empty_and_foreign_library(
    client: TestClient,
) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    library = _create_library(client, token)

    unsupported = client.post(
        f"/api/knowledge/libraries/{library['id']}/documents",
        headers=headers,
        json={"filename": "报告.pdf", "content": "not really a PDF"},
    )
    assert unsupported.status_code == 400
    assert "PDF" in unsupported.json()["detail"]

    empty = client.post(
        f"/api/knowledge/libraries/{library['id']}/documents",
        headers=headers,
        json={"filename": "空白.md", "content": " \n\t "},
    )
    assert empty.status_code == 400
    assert "为空" in empty.json()["detail"]

    missing = client.post(
        "/api/knowledge/libraries/not-found/documents",
        headers=headers,
        json={"filename": "note.md", "content": "内容"},
    )
    assert missing.status_code == 404


def test_hybrid_search_ranking_snippets_and_library_filter(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    technical = _create_library(client, token, name="技术资料")
    product = _create_library(client, token, name="产品资料")

    vector_document = _import_document(
        client,
        token,
        technical["id"],
        filename="search.md",
        title="本地检索设计",
        content=(
            "知识库使用本地哈希向量与中文双字 n-gram 完成混合检索。"
            "余弦相似度负责语义近邻排序，连续短语命中会获得额外加成。"
        ),
    )
    _import_document(
        client,
        token,
        product["id"],
        filename="roadmap.txt",
        title="产品路线图",
        content="下一阶段关注用户访谈、权限管理和数据导出，不包含检索算法实现。",
    )

    searched = client.get(
        "/api/knowledge/search",
        headers=headers,
        params={"q": "余弦相似度 混合检索"},
    )
    assert searched.status_code == 200
    payload = searched.json()
    assert payload["count"] >= 1
    first = payload["results"][0]
    assert first["document"]["id"] == vector_document["id"]
    assert first["library"]["id"] == technical["id"]
    assert first["score"] > 0
    assert "余弦相似度" in first["snippet"]
    assert first["matched_terms"]
    assert first["chunk"]["ordinal"] == 0

    filtered = client.get(
        "/api/knowledge/search",
        headers=headers,
        params={"q": "余弦相似度 混合检索", "library_id": product["id"]},
    )
    assert filtered.status_code == 200
    assert all(
        item["library"]["id"] == product["id"] for item in filtered.json()["results"]
    )

    phrase = client.get(
        "/api/knowledge/search",
        headers=headers,
        params={"q": "知识库使用本地哈希向量"},
    )
    assert phrase.status_code == 200
    assert phrase.json()["results"][0]["score"] >= first["score"]

    assert client.get(
        "/api/knowledge/search", headers=headers, params={"q": "   "}
    ).status_code == 400


def test_document_and_library_deletes_cascade_index_rows(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    library = _create_library(client, token)
    document = _import_document(
        client,
        token,
        library["id"],
        filename="long.md",
        content=("级联删除测试段落。" * 180),
    )

    before = client.get("/api/knowledge/stats", headers=headers).json()["stats"]
    assert before["documents"] == 1
    assert before["chunks"] > 1

    assert (
        client.delete(
            f"/api/knowledge/documents/{document['id']}", headers=headers
        ).status_code
        == 200
    )
    after_document = client.get("/api/knowledge/stats", headers=headers).json()["stats"]
    assert after_document["documents"] == 0
    assert after_document["chunks"] == 0
    assert after_document["characters"] == 0

    replacement = _import_document(
        client,
        token,
        library["id"],
        filename="replacement.txt",
        content="这个资料会随资料库一起删除。",
    )
    assert (
        client.delete(
            f"/api/knowledge/libraries/{library['id']}", headers=headers
        ).status_code
        == 200
    )
    after_library = client.get("/api/knowledge/stats", headers=headers).json()["stats"]
    assert after_library == {
        "libraries": 0,
        "documents": 0,
        "chunks": 0,
        "characters": 0,
    }
    assert (
        client.get(
            f"/api/knowledge/documents/{replacement['id']}", headers=headers
        ).status_code
        == 404
    )


def test_cross_user_knowledge_access_is_404(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    alice_headers = _headers(alice_token)
    bob_headers = _headers(bob_token)

    bob_library = _create_library(client, bob_token, name="鲍勃私有资料")
    bob_document = _import_document(
        client,
        bob_token,
        bob_library["id"],
        filename="private.md",
        title="仅鲍勃可见",
        content="这是鲍勃独有的私有知识片段。",
    )

    assert client.get("/api/knowledge/libraries", headers=alice_headers).json() == {
        "libraries": []
    }
    assert client.get("/api/knowledge/documents", headers=alice_headers).json() == {
        "documents": []
    }
    assert client.get("/api/knowledge/stats", headers=alice_headers).json()["stats"] == {
        "libraries": 0,
        "documents": 0,
        "chunks": 0,
        "characters": 0,
    }

    assert (
        client.get(
            f"/api/knowledge/libraries/{bob_library['id']}", headers=alice_headers
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/knowledge/libraries/{bob_library['id']}",
            headers=alice_headers,
            json={"name": "越权修改"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/knowledge/libraries/{bob_library['id']}", headers=alice_headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/knowledge/documents/{bob_document['id']}", headers=alice_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/knowledge/documents/{bob_document['id']}", headers=alice_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/knowledge/libraries/{bob_library['id']}/documents",
            headers=alice_headers,
            json={"filename": "x.md", "content": "越权导入"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/knowledge/search",
            headers=alice_headers,
            params={"q": "私有知识", "library_id": bob_library["id"]},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/knowledge/ask/stream",
            headers=alice_headers,
            json={"question": "私有知识是什么", "library_id": bob_library["id"]},
        ).status_code
        == 404
    )

    bob_detail = client.get(
        f"/api/knowledge/documents/{bob_document['id']}", headers=bob_headers
    )
    assert bob_detail.status_code == 200
    assert bob_detail.json()["document"]["title"] == "仅鲍勃可见"
    assert client.get("/api/knowledge/libraries", headers=bob_headers).json()[
        "libraries"
    ][0]["id"] == bob_library["id"]


def test_ask_stream_uses_retrieval_sources_and_never_calls_network(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    library = _create_library(client, token, name="问答资料")
    document = _import_document(
        client,
        token,
        library["id"],
        filename="answer.md",
        title="发布计划",
        content="阶段 C 将在完成知识库后进入文档助手。知识库问答必须附带资料引用。",
    )
    captured: dict[str, object] = {}

    def fake_stream(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        captured["messages"] = messages
        captured["model"] = self.model.name
        yield "阶段 C 的下一项是文档助手 [1]。"

    monkeypatch.setattr(LLMClient, "stream_chunks", fake_stream)

    response = client.post(
        "/api/knowledge/ask/stream",
        headers=headers,
        json={
            "question": "阶段 C 接下来做什么？",
            "library_id": library["id"],
            "top_k": 4,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0]["meta"]["model"] == "测试模型"
    source = events[0]["meta"]["sources"][0]
    assert source["document"]["id"] == document["id"]
    assert source["library"]["id"] == library["id"]
    assert any(event.get("delta") for event in events)
    assert events[-1] == {"done": True}
    assert captured["model"] == "测试模型"
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "发布计划" in messages[0]["content"]
    assert "阶段 C 将在完成知识库后进入文档助手" in messages[0]["content"]

    no_sources = client.post(
        "/api/knowledge/ask/stream",
        headers=headers,
        json={"question": "完全无关的火星火山资料", "library_id": library["id"]},
    )
    assert no_sources.status_code == 404