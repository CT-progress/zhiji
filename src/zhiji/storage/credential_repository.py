"""LLM API Key 的密文仓储（阶段 4.2）。

设计要点：

- 明文 Key 只在 ``set_key`` 调用的瞬间存在于内存，落库前一律用
  :class:`~zhiji.security.crypto.CredentialCipher` 加密；库里只有
  ``v1.<nonce>.<ciphertext>`` 形式的密文与不可逆的尾号提示。
- 每条密文用 ``user:{user_id}/llm:{name}`` 作为 AAD，把密文绑定到
  「用户 + 模型名」；把一行密文抄到另一行会解密失败。
- 凭据默认作用域是「本机」（``user_id IS NULL``），与 ``ConfigRepository``
  的全局模型配置一一对应；将来做多用户时传入具体 ``user_id`` 即可，无需改表。
  默认值与 ``TaskRepository`` 的 ``ANY_USER`` 不同，是有意为之：凭据太敏感，
  宁可少查也不能误读他人作用域的数据。
- 解密失败不返回错误明文：默认吞掉异常返回 ``None``（界面退化为“未设置”），
  ``strict=True`` 时抛出 :class:`CryptoError` 供诊断。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.errors import CryptoError
from zhiji.security.crypto import (
    DEFAULT_MASTER_KEY_FILENAME,
    MASTER_KEY_PATH_ENV,
    CredentialCipher,
    default_master_key_path,
    mask_credential,
)
from zhiji.storage.db import Database, lock_for_key
from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import CREDENTIAL_ACTIVE, LLMCredentialRow


def credential_aad(user_id: UserScope, name: str) -> str:
    """密文的附加认证数据：把一行密文绑定到「作用域 + 模型名」。"""

    if user_id is None or user_id is ANY_USER:
        scope = "local"
    else:
        scope = str(user_id)
    return f"user:{scope}/llm:{name}"


def cipher_for_database(database: Database, *, create: bool = True) -> CredentialCipher:
    """为数据库挑一把主密钥。

    默认与数据库同目录（``<db 所在目录>/.master_key``），这样本地 SQLite
    的文件与密钥放在一起，测试用临时目录时也不会污染项目 ``data/``。
    ``ZHIJI_MASTER_KEY_PATH`` 显式指定位置或 ``ZHIJI_MASTER_KEY`` 提供密钥时，
    以环境变量为准（由 :func:`CredentialCipher` 内部解析）。
    """

    if os.getenv(MASTER_KEY_PATH_ENV, "").strip():
        path = default_master_key_path()
    else:
        db_file = _sqlite_file_path(database.url)
        path = (
            db_file.parent / DEFAULT_MASTER_KEY_FILENAME
            if db_file is not None
            else default_master_key_path()
        )
    return CredentialCipher(path=path, create=create)


def find_credential_row(
    session: Session, name: str, user_id: UserScope = None
) -> LLMCredentialRow | None:
    """按「模型名 + 作用域」找凭据行（同一 session 内使用）。"""

    stmt = select(LLMCredentialRow).where(LLMCredentialRow.name == name)
    condition = _scope_filter(user_id)
    if condition is not None:
        stmt = stmt.where(condition)
    return session.scalars(stmt).first()


def set_credential(
    session: Session,
    cipher: CredentialCipher,
    name: str,
    plaintext: str,
    *,
    user_id: UserScope = None,
) -> str:
    """在给定 session 内加密并 upsert；空明文表示删除。返回掩码提示。"""

    text = (plaintext or "").strip()
    row = find_credential_row(session, name, user_id)
    if not text:
        if row is not None:
            session.delete(row)
        return ""
    hint = mask_credential(text)
    if row is None:
        row = LLMCredentialRow(user_id=None if user_id is ANY_USER else user_id, name=name)
        session.add(row)
    row.key_cipher = cipher.encrypt(text, aad=credential_aad(user_id, name))
    row.key_hint = hint
    row.status = CREDENTIAL_ACTIVE
    row.updated_at = datetime.now(UTC)
    return hint


def read_credential(
    session: Session,
    cipher: CredentialCipher,
    name: str,
    *,
    user_id: UserScope = None,
    strict: bool = False,
) -> str | None:
    """在给定 session 内解密取回明文；缺失或解密失败时返回 ``None``。"""

    row = find_credential_row(session, name, user_id)
    if row is None or not row.key_cipher:
        return None
    try:
        return cipher.decrypt(row.key_cipher, aad=credential_aad(row.user_id, name))
    except CryptoError:
        if strict:
            raise
        return None


class CredentialRepository:
    """按作用域读写 LLM Key 密文的仓储。"""

    def __init__(
        self,
        database: Database,
        cipher: CredentialCipher | None = None,
        *,
        create_key: bool = True,
    ) -> None:
        self.database = database
        self.database.create_all()
        self.cipher = cipher if cipher is not None else cipher_for_database(database, create=create_key)
        self._lock = lock_for_key(f"credential:{database.url}")

    def set_key(self, name: str, plaintext: str, *, user_id: UserScope = None) -> str:
        """加密写入一条 Key，返回掩码提示；``plaintext`` 为空即删除。"""

        with self._lock, self.database.session_scope() as session:
            return set_credential(session, self.cipher, name, plaintext, user_id=user_id)

    def get_key(
        self, name: str, *, user_id: UserScope = None, strict: bool = False
    ) -> str | None:
        """解密取回明文 Key；不存在或解密失败时返回 ``None``。"""

        with self.database.session_scope() as session:
            return read_credential(
                session, self.cipher, name, user_id=user_id, strict=strict
            )

    def delete_key(self, name: str, *, user_id: UserScope = None) -> bool:
        with self._lock, self.database.session_scope() as session:
            row = find_credential_row(session, name, user_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def has_key(self, name: str, *, user_id: UserScope = None) -> bool:
        with self.database.session_scope() as session:
            row = find_credential_row(session, name, user_id)
            return row is not None and bool(row.key_cipher)

    def list_hints(self, *, user_id: UserScope = None) -> dict[str, str]:
        """返回 {模型名: 掩码提示}，用于界面展示，绝不返回明文。"""

        with self.database.session_scope() as session:
            stmt = select(LLMCredentialRow)
            condition = _scope_filter(user_id)
            if condition is not None:
                stmt = stmt.where(condition)
            return {row.name: row.key_hint for row in session.scalars(stmt)}

    def is_empty(self, *, user_id: UserScope = None) -> bool:
        return not self.list_hints(user_id=user_id)


def _scope_filter(user_id: UserScope):
    if user_id is ANY_USER:
        return None
    if user_id is None:
        return LLMCredentialRow.user_id.is_(None)
    return LLMCredentialRow.user_id == user_id


def _sqlite_file_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if not raw or raw.startswith(":memory:"):
        return None
    return Path(raw)
