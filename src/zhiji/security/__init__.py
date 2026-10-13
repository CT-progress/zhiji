"""凭据安全：主密钥解析、加解密，以及后续阶段的凭据仓储。"""

from __future__ import annotations

from zhiji.security.crypto import (
    CIPHERTEXT_VERSION,
    DEFAULT_MASTER_KEY_FILENAME,
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

__all__ = [
    "CIPHERTEXT_VERSION",
    "DEFAULT_MASTER_KEY_FILENAME",
    "KEY_BYTES",
    "MASTER_KEY_ENV",
    "MASTER_KEY_PATH_ENV",
    "CredentialCipher",
    "default_master_key_path",
    "generate_master_key",
    "key_fingerprint",
    "parse_master_key",
    "resolve_master_key",
]
