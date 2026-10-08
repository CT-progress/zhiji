"""知乎 ZSE v4 签名（``zse.py``）与 ``zhihu._sign_headers`` 的回归测试。

zse.py 是从 zhihu-cli 原样移植的 550 行密码学实现，此前没有任何测试覆盖。
这里固定若干已知向量 + 往返一致性，防止未来重构悄悄改坏签名。
"""

import pytest

from zhiji.platforms.zhihu import _sign_headers
from zhiji.platforms.zse import ZSECipher


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "d41d8cd98f00b204e9800998ecf8427e",
        "中文-测试",
        "with space and ~!*'()",
        "a" * 500,
    ],
)
def test_cipher_roundtrip(text):
    encoded = ZSECipher().encrypt(text)
    assert ZSECipher().decrypt(encoded) == text


def test_cipher_is_deterministic():
    cipher = ZSECipher()
    assert cipher.encrypt("hello") == cipher.encrypt("hello")


def test_cipher_known_vector_md5():
    # encrypt 的输入通常是 32 位十六进制 md5 摘要，固定一个已知向量
    encoded = ZSECipher().encrypt("d41d8cd98f00b204e9800998ecf8427e")
    assert encoded == "ttAd4/lU9wYAos7uMmt18Gm8Vz1O4Tr0sgMnR1OUuAtlXAx9JkkmU+DySoO3bhMR"


def test_sign_headers_known_vector():
    headers = _sign_headers("/api/v4/articles/123", "ABCdef123")
    assert headers == {
        "x-zse-93": "101_3_3.0",
        "x-zse-96": "2.0_5dXj+H/rAwR5G/0=ffvjIP3Q+IKSGfh6CV3tPQJnepBL1TDfILO1LUWJqpnwOAAZ",
    }


def test_sign_headers_depends_on_path_and_cookie():
    base = _sign_headers("/api/v4/articles/123", "ABCdef123")
    other_path = _sign_headers("/api/v4/articles/999", "ABCdef123")
    other_cookie = _sign_headers("/api/v4/articles/123", "ZZZ")

    assert base["x-zse-96"] != other_path["x-zse-96"]
    assert base["x-zse-96"] != other_cookie["x-zse-96"]
    assert base["x-zse-93"] == other_path["x-zse-93"] == "101_3_3.0"


def test_sign_headers_shape():
    headers = _sign_headers("/x", "d")
    assert set(headers) == {"x-zse-93", "x-zse-96"}
    assert headers["x-zse-96"].startswith("2.0_")
