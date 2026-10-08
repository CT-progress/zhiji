"""B 站 WBI 请求签名。"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import quote, urlencode, urlparse

#: Bilibili 前端固定的字符重排表。
MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)


def _url_stem(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1].split(".", 1)[0]


def mixin_key_from_urls(img_url: str, sub_url: str) -> str:
    """从 nav 接口返回的两张图片 URL 生成 32 位混合密钥。"""

    raw = _url_stem(img_url) + _url_stem(sub_url)
    if len(raw) < 64:
        return ""
    return "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def build_signed_params(
    params: dict[str, object],
    mixin_key: str,
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    """按 WBI 规则排序、过滤特殊字符并追加 ``wts`` / ``w_rid``。"""

    if not mixin_key:
        raise ValueError("mixin_key 不能为空")
    signed = {key: str(value) for key, value in params.items()}
    signed["wts"] = str(timestamp if timestamp is not None else int(time.time()))
    sanitized = {
        key: value.translate({ord(char): None for char in "!'()*"})
        for key, value in sorted(signed.items())
    }
    query = urlencode(sanitized, quote_via=quote, safe="")
    sanitized["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode()).hexdigest()
    return sanitized
