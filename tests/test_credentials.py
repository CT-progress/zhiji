"""LLM Key 密文仓储测试（阶段 4.2）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from zhiji.errors import CryptoError
from zhiji.storage import (
    CREDENTIAL_ACTIVE,
    CredentialRepository,
    Database,
    LLMCredentialRow,
    UserRow,
)

PLAINTEXT = "sk-super-secret-1234567890abcdef"


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'cred.db').as_posix()}")
    yield db
    db.dispose()


@pytest.fixture()
def repo(database: Database) -> CredentialRepository:
    return CredentialRepository(database)


def test_set_and_get_roundtrip(repo: CredentialRepository) -> None:
    hint = repo.set_key("deepseek", PLAINTEXT)
    assert hint == "sk-s****cdef"
    assert repo.get_key("deepseek") == PLAINTEXT
    assert repo.has_key("deepseek") is True
    assert repo.is_empty() is False


def test_missing_key_returns_none(repo: CredentialRepository) -> None:
    assert repo.get_key("nope") is None
    assert repo.has_key("nope") is False


def test_ciphertext_is_not_plaintext(database: Database, repo: CredentialRepository) -> None:
    repo.set_key("deepseek", PLAINTEXT)
    with database.session_scope() as session:
        row = session.scalars(select(LLMCredentialRow)).one()
    assert row.key_cipher.startswith("v1.")
    assert PLAINTEXT not in row.key_cipher
    assert row.key_hint == "sk-s****cdef"
    assert row.status == CREDENTIAL_ACTIVE
    assert PLAINTEXT not in row.key_hint


def test_database_file_contains_no_plaintext(tmp_path: Path) -> None:
    db_path = tmp_path / "raw.db"
    db = Database(f"sqlite:///{db_path.as_posix()}")
    try:
        CredentialRepository(db).set_key("deepseek", PLAINTEXT)
        with db.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        db.dispose()
    blob = db_path.read_bytes()
    for suffix in ("-wal", "-shm"):
        extra = db_path.with_name(db_path.name + suffix)
        if extra.exists():
            blob += extra.read_bytes()
    assert PLAINTEXT.encode("utf-8") not in blob


def test_aad_binds_ciphertext_to_name(database: Database, repo: CredentialRepository) -> None:
    token = repo.cipher.encrypt(PLAINTEXT, aad="user:local/llm:deepseek")
    with database.session_scope() as session:
        session.add(
            LLMCredentialRow(
                name="other", key_cipher=token, key_hint="x", status=CREDENTIAL_ACTIVE
            )
        )
    # 密文被抄到别的模型名上，AAD 不符 -> 解密失败
    assert repo.get_key("other") is None
    with pytest.raises(CryptoError):
        repo.get_key("other", strict=True)


def test_empty_plaintext_removes_key(repo: CredentialRepository) -> None:
    repo.set_key("deepseek", PLAINTEXT)
    assert repo.set_key("deepseek", "") == ""
    assert repo.get_key("deepseek") is None
    assert repo.is_empty() is True


def test_delete_key(repo: CredentialRepository) -> None:
    repo.set_key("deepseek", PLAINTEXT)
    assert repo.delete_key("deepseek") is True
    assert repo.delete_key("deepseek") is False
    assert repo.get_key("deepseek") is None


def test_overwrite_replaces_ciphertext(repo: CredentialRepository) -> None:
    repo.set_key("deepseek", PLAINTEXT)
    repo.set_key("deepseek", "sk-another-key-999999")
    assert repo.get_key("deepseek") == "sk-another-key-999999"
    assert len(repo.list_hints()) == 1


def test_user_scope_isolation(database: Database, repo: CredentialRepository) -> None:
    with database.session_scope() as session:
        session.add(UserRow(id="alice", username="alice", password_hash="x"))
    repo.set_key("m", "sk-local-0000", user_id=None)
    repo.set_key("m", "sk-alice-1111", user_id="alice")
    assert repo.get_key("m", user_id=None) == "sk-local-0000"
    assert repo.get_key("m", user_id="alice") == "sk-alice-1111"
    assert repo.list_hints(user_id=None) == {"m": "sk-l****0000"}
    assert repo.list_hints(user_id="alice") == {"m": "sk-a****1111"}


def test_list_hints_never_returns_plaintext(repo: CredentialRepository) -> None:
    repo.set_key("a", PLAINTEXT)
    repo.set_key("b", "sk-2")
    hints = repo.list_hints()
    assert hints == {"a": "sk-s****cdef", "b": "*" * len("sk-2")}
    assert PLAINTEXT not in str(hints)
