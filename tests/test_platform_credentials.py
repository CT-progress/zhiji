"""平台登录态（Cookie）密文仓储测试（阶段 4.3）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from zhiji.errors import CryptoError
from zhiji.storage import (
    CREDENTIAL_ACTIVE,
    CREDENTIAL_ERROR,
    CREDENTIAL_EXPIRED,
    Database,
    PlatformCredentialRepository,
    PlatformCredentialRow,
    UserRow,
)
from zhiji.storage.platform_credential_repository import (
    normalize_profile,
    platform_aad,
    profile_hint,
)

#: 只要库里出现这段文本就说明加密失败。
COOKIE = "sessionid=super-secret-session-cookie; ttwid=device-token-1"


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'platform.db').as_posix()}")
    yield db
    db.dispose()


@pytest.fixture()
def repo(database: Database) -> PlatformCredentialRepository:
    return PlatformCredentialRepository(database)


def _profile(cookie: str = COOKIE) -> dict:
    return {
        "cookie": cookie,
        "user_agent": "UA",
        "saved_at": "2026-01-01T00:00:00+00:00",
        "verified": True,
    }


def test_save_and_load_roundtrip(repo: PlatformCredentialRepository) -> None:
    hint = repo.save("douyin", _profile())
    assert hint == "2 项"
    assert repo.has("douyin") is True
    assert repo.is_empty() is False

    loaded = repo.load("douyin")
    assert loaded is not None
    assert loaded["cookie"] == COOKIE
    assert loaded["user_agent"] == "UA"
    assert loaded["verified"] is True


def test_missing_platform_returns_none(repo: PlatformCredentialRepository) -> None:
    assert repo.load("nope") is None
    assert repo.has("nope") is False
    assert repo.configured() == set()


def test_hint_reports_field_count_without_values(repo: PlatformCredentialRepository) -> None:
    hint = repo.save("douyin", _profile())
    assert hint == "2 项"
    assert repo.list_hints() == {"douyin": "2 项"}
    for value in ("super-secret-session-cookie", "device-token-1", "sessionid"):
        assert value not in str(repo.list_hints())


def test_ciphertext_is_not_plaintext(
    database: Database, repo: PlatformCredentialRepository
) -> None:
    repo.save("douyin", _profile())
    with database.session_scope() as session:
        row = session.scalars(select(PlatformCredentialRow)).one()
    assert row.payload_cipher.startswith("v1.")
    assert "super-secret-session-cookie" not in row.payload_cipher
    assert row.hint == "2 项"
    assert row.status == CREDENTIAL_ACTIVE
    assert row.platform == "douyin"


def test_database_file_contains_no_plaintext_cookie(tmp_path: Path) -> None:
    db_path = tmp_path / "raw.db"
    db = Database(f"sqlite:///{db_path.as_posix()}")
    try:
        PlatformCredentialRepository(db).save("douyin", _profile())
        with db.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        db.dispose()
    blob = db_path.read_bytes()
    for suffix in ("-wal", "-shm"):
        extra = db_path.with_name(db_path.name + suffix)
        if extra.exists():
            blob += extra.read_bytes()
    assert b"super-secret-session-cookie" not in blob
    assert b"device-token-1" not in blob


def test_aad_binds_ciphertext_to_platform(
    database: Database, repo: PlatformCredentialRepository
) -> None:
    payload = json.dumps(_profile(), ensure_ascii=False, sort_keys=True)
    token = repo.cipher.encrypt(payload, aad=platform_aad(None, "douyin"))
    with database.session_scope() as session:
        session.add(
            PlatformCredentialRow(
                platform="bilibili", payload_cipher=token, hint="2 项", status=CREDENTIAL_ACTIVE
            )
        )
    # 密文被抄到别的平台上，AAD 不符 -> 解密失败
    assert repo.load("bilibili") is None
    with pytest.raises(CryptoError):
        repo.load("bilibili", strict=True)


def test_user_scope_isolation(database: Database, repo: PlatformCredentialRepository) -> None:
    with database.session_scope() as session:
        session.add(UserRow(id="alice", username="alice", password_hash="x"))
    repo.save("douyin", _profile("sessionid=local-cookie"), user_id=None)
    repo.save("douyin", _profile("sessionid=alice-cookie"), user_id="alice")

    local = repo.load("douyin")
    alice = repo.load("douyin", user_id="alice")
    assert local is not None and local["cookie"] == "sessionid=local-cookie"
    assert alice is not None and alice["cookie"] == "sessionid=alice-cookie"
    assert repo.list_hints() == {"douyin": "1 项"}
    assert repo.delete("douyin") is True
    assert repo.load("douyin") is None
    assert repo.load("douyin", user_id="alice") is not None


def test_empty_cookie_removes_row(repo: PlatformCredentialRepository) -> None:
    repo.save("douyin", _profile())
    assert repo.save("douyin", _profile("   ")) == ""
    assert repo.load("douyin") is None
    assert repo.is_empty() is True


def test_delete_and_overwrite(repo: PlatformCredentialRepository) -> None:
    repo.save("douyin", _profile())
    assert repo.delete("douyin") is True
    assert repo.delete("douyin") is False

    repo.save("douyin", _profile())
    repo.save("douyin", _profile("sessionid=replaced"))
    assert repo.configured() == {"douyin"}
    loaded = repo.load("douyin")
    assert loaded is not None
    assert loaded["cookie"] == "sessionid=replaced"


def test_configured_lists_platforms(repo: PlatformCredentialRepository) -> None:
    repo.save("douyin", _profile())
    repo.save("zhihu", _profile("d_c0=abc"))
    assert repo.configured() == {"douyin", "zhihu"}
    assert repo.list_hints() == {"douyin": "2 项", "zhihu": "1 项"}


def test_normalize_profile_drops_unknown_fields() -> None:
    data = normalize_profile({"cookie": None, "junk": "x", "verified": 0})
    assert data == {"cookie": "", "user_agent": "", "saved_at": "", "verified": False}


def test_profile_hint_and_aad_helpers() -> None:
    assert profile_hint({"cookie": "no-equals-here"}) == ""
    assert platform_aad("alice", "douyin") == "user:alice/platform:douyin"
    assert platform_aad(None, "douyin") == "user:local/platform:douyin"


# ---------------------------------------------------------------------------
# 阶段 4.4：健康检查结果写回与读取
# ---------------------------------------------------------------------------


def test_record_check_updates_existing_row(repo: PlatformCredentialRepository) -> None:
    repo.save("douyin", _profile())

    assert repo.record_check("douyin", CREDENTIAL_EXPIRED, "缺少关键 Cookie：sessionid") is True

    rows = repo.statuses()
    assert rows["douyin"]["status"] == CREDENTIAL_EXPIRED
    assert rows["douyin"]["last_error"].startswith("缺少关键")
    assert rows["douyin"]["last_checked_at"]
    assert rows["douyin"]["hint"] == "2 项"


def test_record_check_never_creates_row(repo: PlatformCredentialRepository) -> None:
    assert repo.record_check("bilibili", CREDENTIAL_ACTIVE, "登录态有效") is False
    assert repo.statuses() == {}
    assert repo.is_empty() is True


def test_record_check_clears_detail_when_active(
    repo: PlatformCredentialRepository,
) -> None:
    repo.save("douyin", _profile())
    repo.record_check("douyin", CREDENTIAL_ERROR, "探测异常：TimeoutError")
    assert repo.statuses()["douyin"]["last_error"] == "探测异常：TimeoutError"

    repo.record_check("douyin", CREDENTIAL_ACTIVE, "")
    assert repo.statuses()["douyin"]["last_error"] == ""


def test_statuses_scope_isolation(
    database: Database, repo: PlatformCredentialRepository
) -> None:
    with database.session_scope() as session:
        session.add(UserRow(id="alice", username="alice", password_hash="x"))

    repo.save("douyin", _profile("sessionid=local-cookie"), user_id=None)
    repo.save("douyin", _profile("sessionid=alice-cookie"), user_id="alice")
    repo.record_check(
        "douyin", CREDENTIAL_ACTIVE, "", user_id="alice", checked_at="2026-03-03T00:00:00+00:00"
    )

    assert repo.statuses()["douyin"]["last_checked_at"] == ""
    assert (
        repo.statuses(user_id="alice")["douyin"]["last_checked_at"]
        == "2026-03-03T00:00:00+00:00"
    )
