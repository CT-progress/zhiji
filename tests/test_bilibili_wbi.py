"""B 站 WBI 签名与搜索接口的离线测试。"""

from __future__ import annotations

import pytest

from zhiji.errors import PlatformFetchError
from zhiji.models import Platform
from zhiji.platforms.bilibili import BilibiliAdapter, _parse_duration
from zhiji.platforms.bilibili_wbi import build_signed_params, mixin_key_from_urls

_IMG = "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9429c1d37a7.png"
_SUB = "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab26b4f45a1e18cbf.png"


def test_mixin_key_from_urls() -> None:
    assert mixin_key_from_urls(_IMG, _SUB) == "ea1df124a637e062d74293fa734f4ff8"


def test_mixin_key_requires_full_stems() -> None:
    assert mixin_key_from_urls("https://example.com/short.png", _SUB) == ""


def test_build_signed_params_filters_special_chars_and_is_deterministic() -> None:
    signed = build_signed_params(
        {"q": "a!b'c(d)e*f", "n": 2},
        "0123456789abcdef0123456789abcdef",
        timestamp=1700000000,
    )

    assert signed == {
        "n": "2",
        "q": "abcdef",
        "wts": "1700000000",
        "w_rid": "60d2cc16a8e869a9810132655542bc84",
    }
    with pytest.raises(ValueError):
        build_signed_params({"q": "x"}, "")


def test_parse_duration() -> None:
    assert _parse_duration("12:34") == 754
    assert _parse_duration("01:02:03") == 3723
    assert _parse_duration(90) == 90
    assert _parse_duration("not-a-duration") is None
    assert _parse_duration(True) is None


def test_bilibili_search_uses_wbi_signature(monkeypatch) -> None:
    adapter = BilibiliAdapter()
    captured: dict = {}

    def fake_get_json(url, *, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return {
            "code": 0,
            "data": {
                "result": [
                    {
                        "bvid": "BV1xx411c7mD",
                        "title": "<em>标题</em>",
                        "author": "作者",
                        "duration": "12:34",
                        "description": "简介",
                    }
                ]
            },
        }

    monkeypatch.setattr(adapter, "_get_wbi_key", lambda: "mixin-key")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    results = adapter.search("测试", limit=1)

    assert captured["url"] == "https://api.bilibili.com/x/web-interface/wbi/search/type"
    assert captured["params"]["wts"]
    assert captured["params"]["w_rid"]
    assert captured["params"]["keyword"] == "测试"
    assert captured["params"]["page_size"] == "1"
    assert captured["headers"]["Referer"] == "https://search.bilibili.com/"
    assert results[0].platform == Platform.BILIBILI
    assert results[0].title == "标题"
    assert results[0].duration == 754


def test_bilibili_search_reports_api_error(monkeypatch) -> None:
    adapter = BilibiliAdapter()
    monkeypatch.setattr(adapter, "_get_wbi_key", lambda: "mixin-key")
    monkeypatch.setattr(adapter, "_get_json", lambda *args, **kwargs: {"code": -412, "message": "风控"})

    with pytest.raises(PlatformFetchError):
        adapter.search("测试")
