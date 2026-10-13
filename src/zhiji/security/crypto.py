"""凭据加密：AES-256-GCM + 主密钥（阶段 4.1）。

设计要点：

- **密钥**：32 字节主密钥，按「显式参数 -> ``ZHIJI_MASTER_KEY`` 环境变量 ->
  ``data/.master_key`` 文件 -> 自动生成并落盘」的顺序解析。主密钥永不入库、
  永不进 Git（``data/`` 已 gitignore），文件权限尽量收紧到 0600。
- **算法**：AES-256-GCM，每次加密使用随机 96 位 nonce，密文自带认证标签，
  因此被篡改或密钥不匹配时解密必然失败，而不是返回错误明文。
- **格式**：``v1.<nonce>.<ciphertext>``，两段都是 URL-safe Base64（无填充）。
  带版本号是为了以后换算法 / 上 KMS 时能平滑迁移。
- **AAD**：调用方可以传入附加认证数据（如 ``user:alice/llm:deepseek``），
  把密文绑定到具体归属；把一行密文复制到另一行会解密失败。

这一层是「信封加密」的最简形态：现在主密钥直接加密数据，未来换成
KMS/Vault 托管的数据密钥时，只需要替换 :class:`CredentialCipher` 内部实现，
上层的存储与接口都不用改。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from zhiji.errors import CryptoError, MasterKeyError

#: 项目根目录。本地定义，避免 security <- storage 的循环导入
#: （storage 的 __init__ 会反向 import 本模块的加解密能力）。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 主密钥环境变量：内容是 URL-safe Base64 或十六进制编码的 32 字节密钥。
MASTER_KEY_ENV = "ZHIJI_MASTER_KEY"
#: 显式指定主密钥文件位置的变量（不填则用 data/.master_key）。
MASTER_KEY_PATH_ENV = "ZHIJI_MASTER_KEY_PATH"

DEFAULT_MASTER_KEY_FILENAME = ".master_key"

#: AES-256 要求 32 字节密钥。
KEY_BYTES = 32
#: GCM 推荐 96 位 nonce。
NONCE_BYTES = 12
#: 密文格式版本号，写作前缀。
CIPHERTEXT_VERSION = "v1"


def default_master_key_path() -> Path:
    """主密钥文件位置：``ZHIJI_MASTER_KEY_PATH`` 优先，否则 ``data/.master_key``。"""

    configured = os.getenv(MASTER_KEY_PATH_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path
    return PROJECT_ROOT / "data" / DEFAULT_MASTER_KEY_FILENAME


def generate_master_key() -> str:
    """生成一把新主密钥，返回 URL-safe Base64 文本（可直接放进环境变量）。"""

    return _b64e(secrets.token_bytes(KEY_BYTES))


def parse_master_key(raw: str) -> bytes:
    """把主密钥文本解析成 32 字节。

    接受 URL-safe Base64（可带 ``base64:`` 前缀）或十六进制（可带 ``hex:`` 前缀）。
    格式不对或长度不是 32 字节时抛 :class:`MasterKeyError`，绝不返回半成品。
    """

    text = (raw or "").strip()
    if not text:
        raise MasterKeyError(
            "主密钥为空",
            hint=f"用 generate_master_key() 生成，再写入环境变量 {MASTER_KEY_ENV}",
        )
    lowered = text.lower()
    if lowered.startswith("hex:"):
        try:
            return _check_key_length(bytes.fromhex(text[4:].strip()), source="hex")
        except ValueError as exc:
            raise MasterKeyError(
                "主密钥不是合法的十六进制", hint="hex: 后面应跟 64 位十六进制字符"
            ) from exc
    payload = text[len("base64:") :].strip() if lowered.startswith("base64:") else text
    if len(payload) == KEY_BYTES * 2 and _is_hex(payload):
        return _check_key_length(bytes.fromhex(payload), source="hex")
    try:
        key = _b64d(payload)
    except (binascii.Error, ValueError) as exc:
        raise MasterKeyError(
            "主密钥不是合法的 Base64",
            hint="应为 32 字节密钥的 URL-safe Base64 文本，可用 generate_master_key() 生成",
        ) from exc
    return _check_key_length(key, source="base64")


def resolve_master_key(
    value: str | bytes | None = None,
    *,
    path: str | Path | None = None,
    create: bool = True,
) -> bytes:
    """按「显式参数 -> 环境变量 -> 密钥文件 -> 新生成」的顺序解析主密钥。

    ``create=False`` 时不会生成新密钥：找不到就抛 :class:`MasterKeyError`，
    供只想读取已有密钥的场景使用。
    """

    if isinstance(value, bytes):
        return _check_key_length(value, source="参数")
    if isinstance(value, str) and value.strip():
        return parse_master_key(value)
    env_value = os.getenv(MASTER_KEY_ENV, "").strip()
    if env_value:
        return parse_master_key(env_value)
    target = Path(path).expanduser() if path else default_master_key_path()
    if target.exists():
        stored = target.read_text(encoding="utf-8").strip()
        if stored:
            return parse_master_key(stored)
    if not create:
        raise MasterKeyError(
            f"未找到主密钥（{target}）",
            hint=f"设置环境变量 {MASTER_KEY_ENV}，或先执行一次初始化生成密钥文件",
        )
    key = secrets.token_bytes(KEY_BYTES)
    _write_master_key_file(target, key)
    return key


def key_fingerprint(key: bytes) -> str:
    """给主密钥算一个短指纹，用于日志与诊断（不泄露密钥本身）。"""

    return hashlib.sha256(b"zhiji-master-key\x00" + key).hexdigest()[:12]


def mask_credential(value: str) -> str:
    """把凭据处理成不可逆的展示提示：保留前 4 位与后 4 位。

    长度不足 8 位时整体打码，避免短密钥被反推出内容。这是界面与 CLI
    唯一可见的形态，密文与明文都不会外泄。
    """

    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}****{text[-4:]}"


class CredentialCipher:
    """凭据加解密门面：AES-256-GCM，密文自带校验，篡改必然失败。"""

    def __init__(
        self,
        key: str | bytes | None = None,
        *,
        path: str | Path | None = None,
        create: bool = True,
    ) -> None:
        self._key = resolve_master_key(key, path=path, create=create)
        self._aesgcm = AESGCM(self._key)

    @property
    def fingerprint(self) -> str:
        return key_fingerprint(self._key)

    def encrypt(self, plaintext: str, *, aad: str | bytes = b"") -> str:
        """加密文本，返回 ``v1.<nonce>.<ciphertext>`` 形式的密文串。"""

        if not isinstance(plaintext, str):
            raise CryptoError("只能加密字符串", hint="其它类型请先用 encrypt_json 序列化")
        nonce = secrets.token_bytes(NONCE_BYTES)
        blob = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), _normalize_aad(aad))
        return f"{CIPHERTEXT_VERSION}.{_b64e(nonce)}.{_b64e(blob)}"

    def decrypt(self, token: str, *, aad: str | bytes = b"") -> str:
        """解密并校验密文；任何异常都转成 :class:`CryptoError`，不返回明文。"""

        nonce, blob = _split_token(token)
        try:
            data = self._aesgcm.decrypt(nonce, blob, _normalize_aad(aad))
        except InvalidTag as exc:
            raise CryptoError(
                "凭据解密失败：密钥不匹配或密文已被篡改",
                hint="确认主密钥没有更换过；若确实轮换了密钥，需要用旧密钥重新加密",
            ) from exc
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CryptoError("凭据明文不是合法的 UTF-8 文本") from exc

    def encrypt_json(self, payload: Any, *, aad: str | bytes = b"") -> str:
        """把可 JSON 序列化的对象加密成一个密文串（Cookie 包会用到）。"""

        return self.encrypt(json.dumps(payload, ensure_ascii=False), aad=aad)

    def decrypt_json(self, token: str, *, aad: str | bytes = b"") -> Any:
        """解密并解析 JSON；内容损坏时抛 :class:`CryptoError`。"""

        raw = self.decrypt(token, aad=aad)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise CryptoError("凭据内容不是合法的 JSON") from exc

    def __repr__(self) -> str:
        return f"<CredentialCipher fingerprint={self.fingerprint}>"


def _check_key_length(key: bytes, *, source: str) -> bytes:
    if len(key) != KEY_BYTES:
        raise MasterKeyError(
            f"主密钥长度不正确（{source} 解出 {len(key)} 字节，需要 {KEY_BYTES} 字节）",
            hint="重新生成 32 字节随机密钥的 URL-safe Base64 文本",
        )
    return key


def _write_master_key_file(target: Path, key: bytes) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_b64e(key) + "\n", encoding="utf-8")
        target.chmod(0o600)
    except OSError as exc:  # pragma: no cover - 依赖文件系统状态
        raise MasterKeyError(
            f"主密钥写入失败: {target}",
            hint="检查目录权限，或改为显式设置环境变量",
        ) from exc


def _split_token(token: str) -> tuple[bytes, bytes]:
    parts = (token or "").split(".")
    if len(parts) != 3 or parts[0] != CIPHERTEXT_VERSION:
        raise CryptoError(
            "凭据密文格式不正确",
            hint=f"应形如 {CIPHERTEXT_VERSION}.<nonce>.<ciphertext>",
        )
    try:
        nonce = _b64d(parts[1])
        blob = _b64d(parts[2])
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("凭据密文不是合法的 Base64") from exc
    if len(nonce) != NONCE_BYTES or not blob:
        raise CryptoError("凭据密文长度不正确")
    return nonce, blob


def _normalize_aad(aad: str | bytes) -> bytes:
    if isinstance(aad, bytes):
        return aad
    if isinstance(aad, str):
        return aad.encode("utf-8")
    raise CryptoError("附加认证数据只能是 str 或 bytes")


def _is_hex(text: str) -> bool:
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.b64decode(padded, altchars=b"-_", validate=True)
