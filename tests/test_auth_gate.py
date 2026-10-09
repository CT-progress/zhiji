"""阶段 3.2 测试：统一鉴权中间件（账号会话 + 旧版访问令牌）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zhiji.auth import SESSION_COOKIE_NAME, SessionManager
from zhiji.config import ConfigManager
from zhiji.web.app import create_app

PROTECTED = "/api/models"
WHOAMI = "/api/auth/me"
STATUS = "/api/auth/status"
HEALTH = "/api/health"
USERNAME = "alice"
PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_BCRYPT_ROUNDS", "4")


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离真实 .env，保证默认模式不受外部开关影响。"""

    monkeypatch.delenv("ZHIJI_REQUIRE_AUTH", raising=False)


def _client(tmp_path: Path, **kwargs) -> TestClient:
    cm = ConfigManager(tmp_path / "zhiji.json")
    return TestClient(
        create_app(cm, data_dir=tmp_path / "data", secret_key="test-secret", **kwargs)
    )


def _register(client: TestClient, **kwargs):
    return client.post(
        "/api/auth/register",
        json={"username": USERNAME, "password": PASSWORD},
        **kwargs,
    )


def _login(client: TestClient, **kwargs):
    return client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        **kwargs,
    )


# ---------- 账号鉴权开关 ----------


def test_require_auth_blocks_anonymous_but_allows_public(tmp_path: Path) -> None:
    client = _client(tmp_path, require_auth=True)

    assert client.get(PROTECTED).status_code == 401
    assert client.get(WHOAMI).status_code == 401
    # 公开资源不受影响
    assert client.get(HEALTH).status_code == 200
    status = client.get(STATUS)
    assert status.status_code == 200
    assert status.json() == {"require_auth": True, "user": None}
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    # 未登录也能注册 / 登录
    assert _register(client).status_code == 200
    assert _login(client).status_code == 200


def test_require_auth_allows_valid_session(tmp_path: Path) -> None:
    client = _client(tmp_path, require_auth=True)
    _register(client)
    login = _login(client)
    assert login.status_code == 200
    assert login.json()["token"]

    status = client.get(STATUS)
    assert status.status_code == 200
    assert status.json()["require_auth"] is True
    assert status.json()["user"]["username"] == USERNAME

    # 登录后自动带上会话 Cookie，受保护接口放行
    assert client.get(PROTECTED).status_code == 200
    me = client.get(WHOAMI)
    assert me.status_code == 200
    assert me.json()["user"]["username"] == USERNAME


def test_require_auth_accepts_bearer_session(tmp_path: Path) -> None:
    client = _client(tmp_path, require_auth=True)
    _register(client)
    session = _login(client).json()["token"]

    bare = _client(tmp_path, require_auth=True)
    assert bare.get(PROTECTED).status_code == 401
    resp = bare.get(PROTECTED, headers={"Authorization": f"Bearer {session}"})
    assert resp.status_code == 200


def test_invalid_session_cookie_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path, require_auth=True)
    client.cookies.set(SESSION_COOKIE_NAME, "forged.signature")
    assert client.get(PROTECTED).status_code == 401


def test_expired_session_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path, require_auth=True)
    _register(client)
    user_id = _login(client).json()["user"]["id"]
    expired = SessionManager("test-secret", ttl_seconds=60).issue(
        user_id, now=time.time() - 120
    )
    client.cookies.set(SESSION_COOKIE_NAME, expired)
    assert client.get(PROTECTED).status_code == 401


def test_logout_clears_session(tmp_path: Path) -> None:
    client = _client(tmp_path, require_auth=True)
    _register(client)
    _login(client)
    assert client.get(PROTECTED).status_code == 200

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get(PROTECTED).status_code == 401


def test_require_auth_env_enables_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_REQUIRE_AUTH", "1")
    client = _client(tmp_path)
    assert client.get(PROTECTED).status_code == 401
    assert client.get(HEALTH).status_code == 200


def test_default_mode_remains_open(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get(PROTECTED).status_code == 200
    assert client.get(STATUS).json() == {"require_auth": False, "user": None}


# ---------- 旧版令牌兼容 ----------


def test_token_mode_still_protects_all_api(tmp_path: Path) -> None:
    client = _client(tmp_path, token="secret")
    assert client.get(HEALTH).status_code == 401
    assert (
        client.get(HEALTH, headers={"Authorization": "Bearer secret"}).status_code == 200
    )


def test_token_and_session_coexist(tmp_path: Path) -> None:
    client = _client(tmp_path, token="shared", require_auth=True)
    shared = {"Authorization": "Bearer shared"}
    # 令牌模式保护全部 /api，无凭证一律 401
    assert client.get(PROTECTED).status_code == 401
    # 共享令牌放行
    assert client.get(PROTECTED, headers=shared).status_code == 200
    # 令牌模式下注册 / 登录也需要令牌
    assert _register(client).status_code == 401
    assert _register(client, headers=shared).status_code == 200
    session = _login(client, headers=shared).json()["token"]
    # 仅凭账号会话令牌（换掉共享令牌）也能访问
    resp = client.get(PROTECTED, headers={"Authorization": f"Bearer {session}"})
    assert resp.status_code == 200
