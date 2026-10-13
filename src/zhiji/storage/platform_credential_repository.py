"""平台登录态（Cookie + UA）的密文仓储（阶段 4.3）。

设计要点：

- 整份登录态（cookie / user_agent / saved_at / verified）序列化成 JSON 后一次性
  加密，库里只有 ``v1.<nonce>.<ciphertext>`` 密文与零泄露提示（形如 ``6 项``，
  只报 Cookie 字段个数，不含任何值）。
- 密文 AAD 为 ``user:{scope}/platform:{platform}``，把一行密文绑定到「作用域 +
  平台」；密文被抄到别的平台或别的账号会解密失败。
- 默认作用域是「本机」（``user_id IS NULL``），与全局配置一一对应；将来做多用户
  时传入具体 ``user_id`` 即可，无需改表。默认值与 ``TaskRepository`` 的
  ``ANY_USER`` 不同，是有意为之：登录态太敏感，宁可少查也不能误读他人数据。
- 解密失败不返回错误明文：默认返回 ``None``（上层退化为「未配置」，引导重新
  登录），``strict=True`` 时抛出 :class:`~zhiji.errors.CryptoError` 供诊断。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.errors import CryptoError
from zhiji.security.crypto import CredentialCipher
from zhiji.storage.credential_repository import cipher_for_database
from zhiji.storage.db import Database, lock_for_key
from zhiji.storage.scope import ANY_USER, UserScope
from zhiji.storage.tables import CREDENTIAL_ACTIVE, PlatformCredentialRow

#: 登录态字典允许的字段；多余字段在入库前被丢弃，避免夹带无关内容。
PROFILE_FIELDS = ("cookie", "user_agent", "saved_at", "verified")


def platform_aad(user_id: UserScope, platform: str) -> str:
    """密文的附加认证数据：把一行密文绑定到「作用域 + 平台」。"""

    if user_id is None or user_id is ANY_USER:
        scope = "local"
    else:
        scope = str(user_id)
    return f"user:{scope}/platform:{platform}"


def normalize_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """只保留已知字段，cookie 统一转成字符串。

    ``None`` 一律当作空串：JSON 里的 ``null`` 不该被写成字面量 ``"None"``，
    否则会被当成一份「非空但无意义」的 Cookie 存进库里。
    """

    return {
        "cookie": _text(profile.get("cookie")),
        "user_agent": _text(profile.get("user_agent")),
        "saved_at": _text(profile.get("saved_at")),
        "verified": bool(profile.get("verified", True)),
    }


def _text(value: Any) -> str:
    """把可能为 None 的字段转成字符串；None 视为空串。"""

    return "" if value is None else str(value)


def profile_hint(profile: Mapping[str, Any]) -> str:
    """给界面看的零泄露提示：只报「几项 Cookie」，绝不含 Cookie 值。"""

    cookie = _text(profile.get("cookie"))
    fields = [part for part in cookie.split(";") if "=" in part]
    return f"{len(fields)} 项" if fields else ""


def find_platform_row(
    session: Session, platform: str, user_id: UserScope = None
) -> PlatformCredentialRow | None:
    """按「平台 + 作用域」找登录态行（同一 session 内使用）。"""

    stmt = select(PlatformCredentialRow).where(PlatformCredentialRow.platform == platform)
    condition = _scope_filter(user_id)
    if condition is not None:
        stmt = stmt.where(condition)
    return session.scalars(stmt).first()


def set_platform_credential(
    session: Session,
    cipher: CredentialCipher,
    platform: str,
    profile: Mapping[str, Any],
    *,
    user_id: UserScope = None,
) -> str:
    """在给定 session 内加密并 upsert；Cookie 为空表示删除。返回提示文本。"""

    data = normalize_profile(profile)
    row = find_platform_row(session, platform, user_id)
    if not data["cookie"].strip():
        if row is not None:
            session.delete(row)
        return ""
    hint = profile_hint(data)
    if row is None:
        row = PlatformCredentialRow(
            user_id=None if user_id is ANY_USER else user_id, platform=platform
        )
        session.add(row)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    row.payload_cipher = cipher.encrypt(payload, aad=platform_aad(user_id, platform))
    row.hint = hint
    row.status = CREDENTIAL_ACTIVE
    row.updated_at = datetime.now(UTC)
    return hint


def read_platform_credential(
    session: Session,
    cipher: CredentialCipher,
    platform: str,
    *,
    user_id: UserScope = None,
    strict: bool = False,
) -> dict[str, Any] | None:
    """在给定 session 内解密取回登录态字典；缺失或解密失败时返回 ``None``。"""

    row = find_platform_row(session, platform, user_id)
    if row is None or not row.payload_cipher:
        return None
    try:
        raw = cipher.decrypt(row.payload_cipher, aad=platform_aad(row.user_id, platform))
    except CryptoError:
        if strict:
            raise
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return normalize_profile(data)


class PlatformCredentialRepository:
    """按作用域读写平台登录态密文的仓储。"""

    def __init__(
        self,
        database: Database,
        cipher: CredentialCipher | None = None,
        *,
        create_key: bool = True,
    ) -> None:
        self.database = database
        self.database.create_all()
        self.cipher = (
            cipher if cipher is not None else cipher_for_database(database, create=create_key)
        )
        self._lock = lock_for_key(f"platform-credential:{database.url}")

    def save(
        self, platform: str, profile: Mapping[str, Any], *, user_id: UserScope = None
    ) -> str:
        """加密写入一份登录态，返回零泄露提示；Cookie 为空即删除。"""

        with self._lock, self.database.session_scope() as session:
            return set_platform_credential(
                session, self.cipher, platform, profile, user_id=user_id
            )

    def load(
        self, platform: str, *, user_id: UserScope = None, strict: bool = False
    ) -> dict[str, Any] | None:
        """解密取回登录态；不存在或解密失败时返回 ``None``。"""

        with self.database.session_scope() as session:
            return read_platform_credential(
                session, self.cipher, platform, user_id=user_id, strict=strict
            )

    def delete(self, platform: str, *, user_id: UserScope = None) -> bool:
        with self._lock, self.database.session_scope() as session:
            row = find_platform_row(session, platform, user_id)
            if row is None:
                return False
            session.delete(row)
            return True

    def has(self, platform: str, *, user_id: UserScope = None) -> bool:
        with self.database.session_scope() as session:
            row = find_platform_row(session, platform, user_id)
            return row is not None and bool(row.payload_cipher)

    def list_hints(self, *, user_id: UserScope = None) -> dict[str, str]:
        """返回 {平台: 提示}，用于界面展示，绝不返回明文。"""

        with self.database.session_scope() as session:
            stmt = select(PlatformCredentialRow)
            condition = _scope_filter(user_id)
            if condition is not None:
                stmt = stmt.where(condition)
            return {row.platform: row.hint for row in session.scalars(stmt)}

    def configured(self, *, user_id: UserScope = None) -> set[str]:
        """已配置登录态的平台集合（只查库，不触发解密）。"""

        with self.database.session_scope() as session:
            stmt = select(PlatformCredentialRow.platform)
            condition = _scope_filter(user_id)
            if condition is not None:
                stmt = stmt.where(condition)
            return {platform for platform in session.scalars(stmt) if platform}

    def is_empty(self, *, user_id: UserScope = None) -> bool:
        return not self.list_hints(user_id=user_id)


def _scope_filter(user_id: UserScope):
    if user_id is ANY_USER:
        return None
    if user_id is None:
        return PlatformCredentialRow.user_id.is_(None)
    return PlatformCredentialRow.user_id == user_id
