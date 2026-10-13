"""阶段 4.1 测试：主密钥解析 + AES-GCM 加解密。"""

from __future__ import annotations

import base64
import os
import stat
import sys
from pathlib import Path

import pytest

from zhiji.errors import CryptoError, MasterKeyError
from zhiji.security import (
    CIPHERTEXT_VERSION,
    KEY_BYTES,
    MASTER_KEY_ENV,
    MASTER_KEY_PATH_ENV,
    CredentialCipher,
    default_master_key_path,
    generate_master_key,
    key_fingerprint,
    parse_master_key,
    resolve_master_key,
)

#: 固定的测试密钥，避免用例之间互相影响。
KEY_A = bytes(range(KEY_BYTES))
KEY_B = bytes(range(KEY_BYTES, KEY_BYTES * 2))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏蔽外部环境变量，保证用例不会读到开发者机器上的真实主密钥。"""

    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.delenv(MASTER_KEY_PATH_ENV, raising=False)


def generate_master_key_for(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")


@pytest.fixture()
def cipher() -> CredentialCipher:
    return CredentialCipher(KEY_A)


# ---------- 加解密 ----------


@pytest.mark.parametrize(
    "plaintext",
    ["sk-test-1234567890", "", "中文密码与 emoji 🎬", "x" * 5000, "带\n换行\t制表符"],
)
def test_roundtrip_various_plaintext(cipher: CredentialCipher, plaintext: str) -> None:
    token = cipher.encrypt(plaintext)
    assert token.startswith(CIPHERTEXT_VERSION + ".")
    if plaintext:
        assert plaintext not in token
    assert cipher.decrypt(token) == plaintext


def test_ciphertext_is_randomized(cipher: CredentialCipher) -> None:
    """同一明文两次加密结果不同（nonce 随机），但都能解回原文。"""

    first, second = cipher.encrypt("same"), cipher.encrypt("same")
    assert first != second
    assert cipher.decrypt(first) == cipher.decrypt(second) == "same"


def test_aad_binds_ciphertext_to_owner(cipher: CredentialCipher) -> None:
    token = cipher.encrypt("cookie-value", aad="user:alice/douyin")
    assert cipher.decrypt(token, aad="user:alice/douyin") == "cookie-value"
    # 换成别人的 AAD 解不开：密文无法跨行搬运
    with pytest.raises(CryptoError):
        cipher.decrypt(token, aad="user:bob/douyin")
    # 不带 AAD 也解不开
    with pytest.raises(CryptoError):
        cipher.decrypt(token)
    # bytes 与等价的 str AAD 视为同一个
    assert cipher.decrypt(token, aad=b"user:alice/douyin") == "cookie-value"


def test_json_roundtrip(cipher: CredentialCipher) -> None:
    payload = {"cookie": "a=1; b=2", "user_agent": "Mozilla/5.0", "verified": True}
    token = cipher.encrypt_json(payload, aad="user:alice/bilibili")
    assert cipher.decrypt_json(token, aad="user:alice/bilibili") == payload


def test_encrypt_rejects_non_string(cipher: CredentialCipher) -> None:
    with pytest.raises(CryptoError):
        cipher.encrypt(123)  # type: ignore[arg-type]


# ---------- 防篡改 ----------


def _flip_last_char(text: str, *, step: int = 32) -> str:
    """改动最后一个 Base64 字符的高位，保证解码出的字节确实变了。

    Base64 末字符可能带几个无意义的填充位，单纯 +1 未必改变原文，因此默认跨 32 位翻转。
    """

    table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    index = table.index(text[-1])
    return text[:-1] + table[(index + step) % len(table)]


def test_tampered_ciphertext_is_rejected(cipher: CredentialCipher) -> None:
    token = cipher.encrypt("secret")
    with pytest.raises(CryptoError) as excinfo:
        cipher.decrypt(_flip_last_char(token))
    assert "secret" not in str(excinfo.value)


def test_tampered_nonce_is_rejected(cipher: CredentialCipher) -> None:
    version, nonce, blob = cipher.encrypt("secret").split(".")
    broken = ".".join([version, _flip_last_char(nonce), blob])
    with pytest.raises(CryptoError):
        cipher.decrypt(broken)


def test_truncated_ciphertext_is_rejected(cipher: CredentialCipher) -> None:
    version, nonce, blob = cipher.encrypt("secret").split(".")
    with pytest.raises(CryptoError):
        cipher.decrypt(".".join([version, nonce, blob[:4]]))


def test_wrong_key_is_rejected(cipher: CredentialCipher) -> None:
    token = cipher.encrypt("secret")
    other = CredentialCipher(KEY_B)
    with pytest.raises(CryptoError) as excinfo:
        other.decrypt(token)
    assert "secret" not in str(excinfo.value)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "v1.only-two-parts",
        "v2.AAAA.BBBB",  # 未知版本
        "v1.!!!.!!!",  # 非法 Base64
        "v1.AAAA.",  # 空密文
        "v1.AAAA.BBBB",  # nonce 长度不对
    ],
)
def test_malformed_tokens_are_rejected(cipher: CredentialCipher, token: str) -> None:
    with pytest.raises(CryptoError):
        cipher.decrypt(token)


# ---------- 主密钥解析 ----------


def test_generate_master_key_is_valid_base64() -> None:
    raw = generate_master_key()
    key = parse_master_key(raw)
    assert len(key) == KEY_BYTES
    # 生成的密钥每次都不同
    assert generate_master_key() != raw


def test_parse_master_key_accepts_hex_and_prefixes() -> None:
    key = KEY_A
    assert parse_master_key(key.hex()) == key
    assert parse_master_key("HEX:" + key.hex()) == key
    assert parse_master_key(base64.urlsafe_b64encode(key).decode()) == key
    assert parse_master_key("base64:" + base64.urlsafe_b64encode(key).decode()) == key


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "short",
        "hex:zzzz",
        base64.urlsafe_b64encode(b"only-16-bytes!!!").decode(),
    ],
)
def test_parse_master_key_rejects_bad_material(raw: str) -> None:
    with pytest.raises(MasterKeyError) as excinfo:
        parse_master_key(raw)
    assert excinfo.value.code == "MASTER_KEY_UNAVAILABLE"


def test_key_fingerprint_is_stable_and_opaque() -> None:
    assert key_fingerprint(KEY_A) == key_fingerprint(bytes(KEY_A))
    assert key_fingerprint(KEY_A) != key_fingerprint(KEY_B)
    assert KEY_A.hex() not in key_fingerprint(KEY_A)
    assert len(key_fingerprint(KEY_A)) == 12


# ---------- 密钥解析顺序与落盘 ----------


def test_resolve_prefers_explicit_then_env_then_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_file = tmp_path / "master.key"
    key_file.write_text(generate_master_key_for(KEY_B), encoding="utf-8")

    # 只有文件时用文件
    assert resolve_master_key(path=key_file) == KEY_B
    # 有环境变量时优先环境变量
    monkeypatch.setenv(MASTER_KEY_ENV, generate_master_key_for(KEY_A))
    assert resolve_master_key(path=key_file) == KEY_A
    # 显式参数优先级最高
    assert resolve_master_key(KEY_B.hex(), path=key_file) == KEY_B


def test_resolve_generates_and_reuses_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / "nested" / "master.key"
    generated = resolve_master_key(path=key_file)
    assert len(generated) == KEY_BYTES
    assert key_file.exists()
    # 文件里存的是可解析的 Base64，内容与返回值一致
    assert parse_master_key(key_file.read_text(encoding="utf-8")) == generated
    # 再次解析会复用同一把密钥，而不是重新生成
    assert resolve_master_key(path=key_file) == generated


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 上 chmod 语义有限")
def test_master_key_file_is_owner_only(tmp_path: Path) -> None:
    key_file = tmp_path / "master.key"
    resolve_master_key(path=key_file)
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600


def test_resolve_without_create_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(MasterKeyError) as excinfo:
        resolve_master_key(path=tmp_path / "missing.key", create=False)
    assert "未找到主密钥" in excinfo.value.message


def test_cipher_without_create_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(MasterKeyError):
        CredentialCipher(path=tmp_path / "missing.key", create=False)


def test_empty_key_file_is_treated_as_missing(tmp_path: Path) -> None:
    key_file = tmp_path / "master.key"
    key_file.write_text("   \n", encoding="utf-8")
    with pytest.raises(MasterKeyError):
        resolve_master_key(path=key_file, create=False)


def test_cipher_repr_hides_key(cipher: CredentialCipher) -> None:
    text = repr(cipher)
    assert cipher.fingerprint in text
    assert KEY_A.hex() not in text


def test_default_master_key_path_follows_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert default_master_key_path().name == ".master_key"
    monkeypatch.setenv(MASTER_KEY_PATH_ENV, str(tmp_path / "custom.key"))
    assert default_master_key_path() == tmp_path / "custom.key"
