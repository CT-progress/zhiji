"""账号与认证：bcrypt 密码哈希 + HMAC 签名会话令牌。

设计要点：

- 密码只以 bcrypt 哈希落库，任何地方都不保存、不返回明文；
- 会话令牌自包含（payload + HMAC-SHA256 签名），服务端不存 token，适合单机 MVP；
  未来接 OIDC / Casdoor 时替换 ``AuthService`` 实现即可；
- 签名密钥优先取 ``ZHIJI_SECRET_KEY``，没有则生成并落盘到 ``data/.session_secret``，
  保证进程重启后已签发的会话仍然有效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
import time
from pathlib import Path

import bcrypt

from zhiji.errors import AuthError
from zhiji.storage.db import PROJECT_ROOT, Database, lock_for_key
from zhiji.storage.tables import UserRow
from zhiji.storage.user_repository import UserRepository

#: 用户名校验规则：统一小写后入库，只允许字母 / 数字 / 下划线 / 点 / 连字符。
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]+$")

PASSWORD_MIN_LENGTH = 8
#: bcrypt 只处理前 72 字节，超长直接拒绝，避免静默截断带来的困惑。
PASSWORD_MAX_BYTES = 72

SESSION_COOKIE_NAME = "zhiji_session"
SECRET_KEY_ENV = "ZHIJI_SECRET_KEY"
BCRYPT_ROUNDS_ENV = "ZHIJI_BCRYPT_ROUNDS"
DEFAULT_BCRYPT_ROUNDS = 12

DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_SECRET_FILENAME = ".session_secret"


def normalize_username(raw: str) -> str:
    """去掉首尾空白并转小写，保证用户名大小写不敏感。"""

    return raw.strip().lower()


def validate_username(username: str) -> str:
    if not username:
        raise AuthError("用户名不能为空")
    if not USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH:
        raise AuthError(
            f"用户名长度需为 {USERNAME_MIN_LENGTH}-{USERNAME_MAX_LENGTH} 个字符",
            hint="例如 zhangsan、user_01",
        )
    if not USERNAME_PATTERN.match(username):
        raise AuthError(
            "用户名只能包含字母、数字、下划线、点或连字符",
            hint="不支持中文或空格，可先用拼音",
        )
    return username


def validate_password(password: str) -> str:
    if not password:
        raise AuthError("密码不能为空")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 位")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise AuthError(
            f"密码过长（bcrypt 上限 {PASSWORD_MAX_BYTES} 字节）",
            hint="换一个更短的密码，或改用长随机口令的截断写法",
        )
    return password


def _default_rounds() -> int:
    raw = os.getenv(BCRYPT_ROUNDS_ENV, "").strip()
    if raw.isdigit():
        return max(4, min(16, int(raw)))
    return DEFAULT_BCRYPT_ROUNDS


def hash_password(password: str, *, rounds: int | None = None) -> str:
    """把明文密码哈希成 bcrypt 字符串（含随机盐与代价因子）。"""

    validate_password(password)
    cost = rounds if rounds is not None else _default_rounds()
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=cost))
    return digest.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文与哈希是否匹配；哈希损坏或为空时返回 False 而不是抛异常。"""

    if not password or not password_hash:
        return False
    try:
        return bool(bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii")))
    except (ValueError, TypeError):
        return False


def resolve_secret(secret: str | None = None, *, path: Path | None = None) -> str:
    """按「显式参数 -> 环境变量 -> 落盘文件 -> 新生成」的顺序解析签名密钥。"""

    if secret and secret.strip():
        return secret.strip()
    env_value = os.getenv(SECRET_KEY_ENV, "").strip()
    if env_value:
        return env_value
    target = path or (PROJECT_ROOT / "data" / DEFAULT_SECRET_FILENAME)
    if target.exists():
        stored = target.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    value = _secrets.token_urlsafe(48)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:  # pragma: no cover - Windows 上 chmod 语义有限
        pass
    return value


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class SessionManager:
    """签发 / 校验自包含会话令牌（base64url(payload).base64url(hmac)）。

    密钥是惰性解析的：只有真正签发或校验令牌时才会读取环境变量或落盘文件，
    避免只跑注册接口就产生写文件的副作用。
    """

    def __init__(
        self,
        secret: str | None = None,
        *,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        secret_path: Path | None = None,
    ) -> None:
        self._secret = secret.strip() if secret and secret.strip() else None
        self._secret_path = secret_path
        self.ttl_seconds = max(60, int(ttl_seconds))

    @property
    def secret(self) -> str:
        if self._secret is None:
            self._secret = resolve_secret(path=self._secret_path)
        return self._secret

    def _sign(self, payload: bytes) -> str:
        digest = hmac.new(self.secret.encode("utf-8"), payload, hashlib.sha256).digest()
        return _b64encode(digest)

    def issue(self, user_id: str, *, now: float | None = None) -> str:
        issued = time.time() if now is None else now
        payload = json.dumps(
            {"sub": user_id, "exp": int(issued) + self.ttl_seconds},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"{_b64encode(payload)}.{self._sign(payload)}"

    def verify(self, token: str, *, now: float | None = None) -> str | None:
        """验签并检查过期；任一环节不合法都返回 None。"""

        if not token:
            return None
        try:
            payload_b64, signature = token.split(".", 1)
            payload = _b64decode(payload_b64)
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(self._sign(payload), signature):
            return None
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        subject = data.get("sub")
        expires_at = data.get("exp")
        if not isinstance(subject, str) or not subject:
            return None
        if not isinstance(expires_at, int):
            return None
        current = time.time() if now is None else now
        if expires_at <= current:
            return None
        return subject


class AuthService:
    """账号服务：注册、登录校验与会话读写。"""

    def __init__(
        self,
        database: Database,
        *,
        secret: str | None = None,
        session_ttl: int = DEFAULT_SESSION_TTL_SECONDS,
        bcrypt_rounds: int | None = None,
        secret_path: Path | None = None,
    ) -> None:
        self._database = database
        self._lock = lock_for_key(f"users:{database.url}")
        self._rounds = bcrypt_rounds
        self.sessions = SessionManager(
            secret=secret, ttl_seconds=session_ttl, secret_path=secret_path
        )

    @property
    def session_ttl(self) -> int:
        return self.sessions.ttl_seconds

    def register(
        self,
        username: str,
        password: str,
        *,
        display_name: str | None = None,
    ) -> UserRow:
        """注册新用户；用户名重复抛 ``DuplicateUserError``，密码不合规抛 ``AuthError``。"""

        normalized = validate_username(normalize_username(username))
        password_hash = hash_password(password, rounds=self._rounds)
        clean_display = (display_name or "").strip() or None
        with self._lock, self._database.session_scope() as session:
            return UserRepository(session).create(
                username=normalized,
                password_hash=password_hash,
                display_name=clean_display,
            )

    def authenticate(self, username: str, password: str) -> UserRow:
        """校验用户名密码；成功返回用户，失败抛 ``AuthError``（不区分哪一项错误）。"""

        normalized = normalize_username(username)
        with self._database.session_scope() as session:
            user = UserRepository(session).find_by_username(normalized)
            if user is None or not user.is_active:
                raise AuthError("用户名或密码不正确", hint="请检查后重试")
            if not verify_password(password, user.password_hash):
                raise AuthError("用户名或密码不正确", hint="请检查后重试")
            return user

    def create_session(self, user_id: str) -> str:
        return self.sessions.issue(user_id)

    def read_session(self, token: str) -> str | None:
        return self.sessions.verify(token)

    def get_user(self, user_id: str) -> UserRow | None:
        with self._database.session_scope() as session:
            user = UserRepository(session).find(user_id)
            if user is None or not user.is_active:
                return None
            return user
