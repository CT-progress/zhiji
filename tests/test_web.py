from fastapi.testclient import TestClient

from zhiji.config import ConfigManager
from zhiji.llm.client import LLMClient
from zhiji.web.app import create_app


def _client(tmp_path) -> tuple[TestClient, ConfigManager]:
    cm = ConfigManager(tmp_path / "zhiji.json")
    return TestClient(create_app(cm, data_dir=tmp_path / "data")), cm


def test_health(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/health").json()["ok"] is True


def test_model_crud(tmp_path):
    client, _ = _client(tmp_path)
    payload = {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-1234567890",
    }
    created = client.post("/api/models", json=payload)
    assert created.status_code == 200
    assert created.json()["model"]["api_key"] == "sk-1****7890"

    dup = client.post("/api/models", json={"name": "DeepSeek", "model": "deepseek-chat"})
    assert dup.status_code == 400

    updated = client.put("/api/models/DeepSeek", json={"model": "deepseek-reasoner", "default": True})
    assert updated.json()["model"]["model"] == "deepseek-reasoner"

    listed = client.get("/api/models").json()["models"]
    assert len(listed) == 1
    assert listed[0]["default"] is True

    deleted = client.delete("/api/models/DeepSeek")
    assert deleted.json()["ok"] is True
    assert len(client.get("/api/models").json()["models"]) == 0


def test_settings_update(tmp_path):
    client, _ = _client(tmp_path)
    out = str(tmp_path / "out")
    resp = client.put("/api/settings", json={"output_dir": out})
    assert resp.json()["output_dir"] == out
    assert client.get("/api/settings").json()["output_dir"] == out


def test_zhihu_cookie_endpoints(tmp_path, monkeypatch):
    from zhiji.platforms import zhihu_cookies

    monkeypatch.setattr(zhihu_cookies, "DEFAULT_PROFILE_PATH", tmp_path / "zhihu-profile.json")
    client, _ = _client(tmp_path)

    assert client.get("/api/cookies/zhihu").json() == {"configured": False}

    empty = client.post("/api/cookies/zhihu", json={"cookie": ""})
    assert empty.status_code == 400

    missing_d_c0 = client.post("/api/cookies/zhihu", json={"cookie": "z_c0=abc"})
    assert missing_d_c0.status_code == 400

    saved = client.post("/api/cookies/zhihu", json={"cookie": "d_c0=abc; z_c0=xyz; other=1"})
    assert saved.json()["ok"] is True

    status = client.get("/api/cookies/zhihu").json()
    assert status["configured"] is True
    assert status["cookie_length"] > 0

    assert client.delete("/api/cookies/zhihu").json()["ok"] is True
    assert client.get("/api/cookies/zhihu").json() == {"configured": False}


def test_conversation_crud(tmp_path):
    client, _ = _client(tmp_path)
    created = client.post("/api/conversations", json={})
    assert created.status_code == 200
    conversation = created.json()["conversation"]
    assert conversation["title"] == "新对话"

    listed = client.get("/api/conversations").json()["conversations"]
    assert listed[0]["id"] == conversation["id"]

    renamed = client.patch(f"/api/conversations/{conversation['id']}", json={"title": "我的会话"})
    assert renamed.json()["conversation"]["title"] == "我的会话"

    fetched = client.get(f"/api/conversations/{conversation['id']}")
    assert fetched.json()["conversation"]["title"] == "我的会话"

    missing = client.get("/api/conversations/not-exist")
    assert missing.status_code == 404

    deleted = client.delete(f"/api/conversations/{conversation['id']}")
    assert deleted.json()["ok"] is True
    assert client.get("/api/conversations").json()["conversations"] == []


def test_chat_roundtrip(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "DeepSeek", "model": "deepseek-chat", "api_key": "sk-test"})
    conversation = client.post("/api/conversations", json={}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "complete", lambda self, messages, **kwargs: "模拟回复")

    resp = client.post(
        "/api/chat",
        json={"conversation_id": conversation["id"], "message": "你好"},
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "模拟回复"

    conversation = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert conversation["model_name"] == "DeepSeek"
    roles = [message["role"] for message in conversation["messages"]]
    assert roles == ["user", "assistant"]
    assert conversation["messages"][1]["content"] == "模拟回复"
    assert conversation["title"] == "你好"


def test_chat_fetches_supported_link_as_context(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "DeepSeek", "model": "deepseek-chat", "api_key": "sk-test"})
    conversation = client.post("/api/conversations", json={}).json()["conversation"]
    captured = {}

    monkeypatch.setattr("zhiji.web.app.fetch_link_context", lambda message: "标题：测试文章\n正文：知乎正文")
    monkeypatch.setattr(
        LLMClient,
        "complete",
        lambda self, messages, **kwargs: captured.setdefault("messages", messages) and "已整理",
    )

    resp = client.post(
        "/api/chat",
        json={"conversation_id": conversation["id"], "message": "请整理 https://zhuanlan.zhihu.com/p/123"},
    )
    assert resp.status_code == 200
    assert "知乎正文" in captured["messages"][-1]["content"]
    assert "请整理 https://zhuanlan.zhihu.com/p/123" in captured["messages"][-1]["content"]


def test_chat_stream(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "DeepSeek", "model": "deepseek-chat", "api_key": "sk-test"})
    conversation = client.post("/api/conversations", json={}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "stream_chunks", lambda self, messages, **kwargs: iter(["答案", "片段"]))

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": conversation["id"], "message": "问题"},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "答案" in body
    assert "片段" in body
    conversation = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert conversation["model_name"] == "DeepSeek"
    assert conversation["messages"][-1]["content"] == "答案片段"


def test_chat_requires_model(tmp_path):
    client, _ = _client(tmp_path)
    conversation = client.post("/api/conversations", json={}).json()["conversation"]
    resp = client.post("/api/chat", json={"conversation_id": conversation["id"], "message": "hi"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "LLM_FAILED"


def test_chat_writes_back_model_name(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "ModelA", "model": "model-a", "base_url": "http://a", "api_key": "sk-a"})
    client.post("/api/models", json={"name": "ModelB", "model": "model-b", "base_url": "http://b", "api_key": "sk-b", "default": True})
    conversation = client.post("/api/conversations", json={"model_name": "ModelA"}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "complete", lambda self, messages, **kwargs: "ok")

    resp = client.post("/api/chat", json={"conversation_id": conversation["id"], "message": "test", "model_name": "ModelA"})
    assert resp.status_code == 200
    fetched = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert fetched["model_name"] == "ModelA"

    resp = client.post("/api/chat", json={"conversation_id": conversation["id"], "message": "switch", "model_name": "ModelB"})
    assert resp.status_code == 200
    fetched = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert fetched["model_name"] == "ModelB"


def test_chat_stream_writes_back_model_name(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "ModelA", "model": "model-a", "base_url": "http://a", "api_key": "sk-a"})
    client.post("/api/models", json={"name": "ModelB", "model": "model-b", "base_url": "http://b", "api_key": "sk-b", "default": True})
    conversation = client.post("/api/conversations", json={"model_name": "ModelA"}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "stream_chunks", lambda self, messages, **kwargs: iter(["ok"]))

    with client.stream("POST", "/api/chat/stream", json={"conversation_id": conversation["id"], "message": "test", "model_name": "ModelA"}) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())
    fetched = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert fetched["model_name"] == "ModelA"

    with client.stream("POST", "/api/chat/stream", json={"conversation_id": conversation["id"], "message": "switch", "model_name": "ModelB"}) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())
    fetched = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert fetched["model_name"] == "ModelB"


def test_chat_invalid_model_falls_back_to_default(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "Valid", "model": "valid", "base_url": "http://v", "api_key": "sk-v", "default": True})
    conversation = client.post("/api/conversations", json={}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "complete", lambda self, messages, **kwargs: "ok")

    resp = client.post("/api/chat", json={"conversation_id": conversation["id"], "message": "test", "model_name": "NonExistent"})
    assert resp.status_code == 200
    fetched = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert fetched["model_name"] == "Valid"


def test_chat_invalid_model_falls_back_stream(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "Valid", "model": "valid", "base_url": "http://v", "api_key": "sk-v", "default": True})
    conversation = client.post("/api/conversations", json={}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "stream_chunks", lambda self, messages, **kwargs: iter(["ok"]))

    with client.stream("POST", "/api/chat/stream", json={"conversation_id": conversation["id"], "message": "test", "model_name": "NonExistent"}) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())
    fetched = client.get(f"/api/conversations/{conversation['id']}").json()["conversation"]
    assert fetched["model_name"] == "Valid"


def test_open_conversation_fallback_to_current_model(tmp_path, monkeypatch):
    client, _ = _client(tmp_path)
    client.post("/api/models", json={"name": "NewModel", "model": "new", "base_url": "http://n", "api_key": "sk-n", "default": True})
    conv = client.post("/api/conversations", json={"model_name": "OldDeleted"}).json()["conversation"]
    monkeypatch.setattr(LLMClient, "complete", lambda self, messages, **kwargs: "ok")
    client.post("/api/chat", json={"conversation_id": conv["id"], "message": "hi", "model_name": "NewModel"})

    fetched = client.get(f"/api/conversations/{conv['id']}").json()["conversation"]
    assert fetched["model_name"] == "NewModel"
