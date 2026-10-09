"""阶段 3.1 测试：用户表、bcrypt 密码、注册 / 登录 / 登出与会话令牌。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from zhiji.auth import (
    SESSION_COOKIE_NAME,
    AuthService,
    SessionManager,
    hash_password,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)
from zhiji.config import ConfigManager
from zhiji.errors import AuthError, DuplicateUserError
from zhiji.storage import Database
from zhiji.storage.tables import UserRow
from zhiji.storage.user_repository import UserRepository
from zhiji.web.app import create_app


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试用最低 bcrypt 代价因子，避免每个用例多花几百毫秒。"""

    monkeypatch.setenv("ZHIJI_BCRYPT_ROUNDS", "4")


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'auth.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture()
def service(database: Database) -> AuthService:
    return AuthService(database, secret="unit-test-secret")


def _client(tmp_path: Path) -> TestClient:
    cm = ConfigManager(tmp_path / "zhiji.json")
    return TestClient(create_app(cm, data_dir=tmp_path / "data", secret_key="test-secret"))


def _app_database(tmp_path: Path) -> Database:
    return Database(f"sqlite:///{(tmp_path / 'data' / 'zhiji.db').as_posix()}")


# ---------- 密码哈希 ----------


def test_hash_password_is_irreversible_and_salted() -> None:
    digest = hash_password("secret123", rounds=4)
    assert digest != "secret123"
    assert digest.startswith("$2")
    assert verify_password("secret123", digest) is True
    assert verify_password("secret124", digest) is False
    # 同一密码两次哈希因随机盐不同而结果不同
    assert hash_password("secret123", rounds=4) != digest


def test_hash_password_rejects_short_and_overlong() -> None:
    with pytest.raises(AuthError):
        hash_password("short", rounds=4)
    with pytest.raises(AuthError):
        hash_password("a" * 100, rounds=4)
    with pytest.raises(AuthError):
        validate_password("")


def test_verify_password_handles_broken_hash() -> None:
    assert verify_password("secret123", "") is False
    assert verify_password("secret123", "not-a-hash") is False


# ---------- 用户名 ----------


def test_username_normalization_and_rules() -> None:
    assert normalize_username("  Alice ") == "alice"
    assert validate_username("zhangsan_01") == "zhangsan_01"
    for bad in ["", "ab", "a" * 33, "张三", "has space", "bad/char"]:
        with pytest.raises(AuthError):
            validate_username(normalize_username(bad))


# ---------- 会话令牌 ----------


def test_session_manager_roundtrip_tamper_and_expiry() -> None:
    manager = SessionManager("secret", ttl_seconds=600)
    token = manager.issue("user-1", now=1_000.0)
    assert manager.verify(token, now=1_100.0) == "user-1"
    # 篡改签名
    assert manager.verify(token + "x", now=1_100.0) is None
    # 换一把密钥
    assert SessionManager("other", ttl_seconds=600).verify(token, now=1_100.0) is None
    # 过期
    assert manager.verify(token, now=1_000.0 + 601) is None
    # 结构不合法
    assert manager.verify("not-a-token", now=1_100.0) is None


def test_session_manager_default_ttl_positive() -> None:
    assert SessionManager("secret").ttl_seconds > 0


# ---------- 仓储与服务 ----------


def test_user_repository_create_find_and_duplicate(database: Database) -> None:
    with database.session_scope() as session:
        repo = UserRepository(session)
        user = repo.create(username="alice", password_hash="hash")
        assert repo.find(user.id) is user
        assert repo.find_by_username("alice") is user
        assert repo.list() == [user]
        with pytest.raises(DuplicateUserError):
            repo.create(username="alice", password_hash="hash2")


def test_service_register_and_authenticate(service: AuthService) -> None:
    user = service.register("Alice", "secret123", display_name="爱丽丝")
    assert user.username == "alice"
    assert user.display_name == "爱丽丝"
    assert user.password_hash != "secret123"
    assert service.authenticate("ALICE", "secret123").id == user.id
    with pytest.raises(DuplicateUserError):
        service.register("alice", "another123")
    with pytest.raises(AuthError):
        service.authenticate("alice", "wrong-password")


def test_service_rejects_inactive_user(database: Database, service: AuthService) -> None:
    user = service.register("alice", "secret123")
    with database.session_scope() as session:
        row = session.get(UserRow, user.id)
        assert row is not None
        row.is_active = False
    with pytest.raises(AuthError):
        service.authenticate("alice", "secret123")
    assert service.get_user(user.id) is None


def test_service_session_roundtrip(service: AuthService) -> None:
    user = service.register("alice", "secret123")
    token = service.create_session(user.id)
    assert service.read_session(token) == user.id
    assert service.read_session(token + "x") is None


# ---------- HTTP 接口 ----------


def test_register_stores_only_hash(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/api/auth/register",
        json={"username": "Alice", "password": "secret123", "display_name": "爱丽丝"},
    )
    assert resp.status_code == 200
    payload = resp.json()["user"]
    assert payload["username"] == "alice"
    assert payload["display_name"] == "爱丽丝"
    assert "password" not in resp.text

    db = _app_database(tmp_path)
    try:
        with db.session_scope() as session:
            row = session.scalar(select(UserRow).where(UserRow.username == "alice"))
            assert row is not None
            assert row.password_hash != "secret123"
            assert row.password_hash.startswith("$2")
    finally:
        db.dispose()


def test_login_me_logout_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert client.get("/api/auth/me").status_code == 401

    login = client.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
    assert login.status_code == 200
    assert SESSION_COOKIE_NAME in login.headers.get("set-cookie", "")
    assert login.json()["token"]

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_accepts_bearer_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    token = client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret123"}
    ).json()["token"]

    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"


def test_login_wrong_password_returns_401(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "nope-nope"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "AUTH_FAILED"


def test_register_duplicate_username_is_case_insensitive(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post(
        "/api/auth/register", json={"username": "Alice", "password": "secret123"}
    ).status_code == 200
    dup = client.post("/api/auth/register", json={"username": "ALICE", "password": "secret123"})
    assert dup.status_code == 400
    assert dup.json()["detail"]["error"]["code"] == "USER_ALREADY_EXISTS"


def test_register_validates_payload(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/auth/register", json={"username": "ab", "password": "secret123"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "alice", "password": "short"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "张三", "password": "secret123"}).status_code == 400
    assert client.post("/api/auth/register", json={}).status_code == 422


def test_me_rejects_tampered_session(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.cookies.set(SESSION_COOKIE_NAME, "forged.token")
    assert client.get("/api/auth/me").status_code == 401


def test_session_survives_process_restart(tmp_path: Path) -> None:
    """同一密钥下，换一个 app 实例仍能验证旧会话（模拟进程重启）。"""

    client = _client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    token = client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret123"}
    ).json()["token"]

    fresh = _client(tmp_path)
    fresh.cookies.set(SESSION_COOKIE_NAME, token)
    assert fresh.get("/api/auth/me").status_code == 200


def test_expired_session_cookie_is_rejected(tmp_path: Path) -> None:
    """用同一密钥签发一个已过期的令牌，接口应拒绝而不是放行。"""

    client = _client(tmp_path)
    client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    manager = SessionManager("test-secret", ttl_seconds=60)
    expired = manager.issue("any-user", now=time.time() - 3600)
    client.cookies.set(SESSION_COOKIE_NAME, expired)
    assert client.get("/api/auth/me").status_code == 401
