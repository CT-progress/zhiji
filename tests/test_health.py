"""凭据健康检查的纯逻辑测试（阶段 4.4）。

只覆盖 :mod:`zhiji.health` 里的探测与摘要逻辑：HTTP 客户端用假 ``opener`` 注入，
不联网、不碰真实数据库。
"""

from __future__ import annotations

import json

import pytest

from zhiji.errors import InputUnsupportedError, ZhijiError
from zhiji.health import (
    BILIBILI_NAV_URL,
    HEALTH_ERROR,
    HEALTH_EXPIRED,
    HEALTH_OK,
    HEALTH_UNCHECKED,
    HEALTH_UNCONFIGURED,
    PLATFORM_PROBE_SOURCES,
    PLATFORM_PROBES,
    REQUIRED_COOKIE_NAMES,
    SOURCE_LOCAL,
    SOURCE_REMOTE,
    CheckResult,
    CookieProfile,
    ProbeOutcome,
    check_llm,
    check_platform,
    cookie_names,
    probe_bilibili,
    probe_douyin,
    probe_xiaohongshu,
    probe_zhihu,
    request_headers,
    sanitize_reason,
    summarize,
)


class FakeOpener:
    """假探测器：记录调用并返回预置的 ``(状态码, 响应文本)``。"""

    def __init__(self, code: int, body: str) -> None:
        self.code = code
        self.body = body
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url, *, headers, timeout=10.0):
        self.calls.append((url, dict(headers)))
        return self.code, self.body


def _profile(cookie: str = "d_c0=abc", user_agent: str = "UA") -> CookieProfile:
    return CookieProfile(cookie=cookie, user_agent=user_agent)


def _nav(is_login: bool, code: int = 0) -> str:
    return json.dumps({"code": code, "data": {"isLogin": is_login}})


# ---------------------------------------------------------------------------
# 远端探测：B 站 / 知乎
# ---------------------------------------------------------------------------


def test_probe_bilibili_ok_sends_cookie_and_reads_nav() -> None:
    opener = FakeOpener(200, _nav(True))
    out = probe_bilibili(_profile(), opener=opener)

    assert out.status == HEALTH_OK
    assert out.source == SOURCE_REMOTE
    assert opener.calls[0][0] == BILIBILI_NAV_URL
    assert opener.calls[0][1]["Cookie"] == "d_c0=abc"
    assert opener.calls[0][1]["User-Agent"] == "UA"


def test_probe_bilibili_expired_when_not_logged_in() -> None:
    out = probe_bilibili(_profile(), opener=FakeOpener(200, _nav(False)))
    assert out.status == HEALTH_EXPIRED
    assert out.source == SOURCE_REMOTE


def test_probe_bilibili_business_error_is_not_expired() -> None:
    out = probe_bilibili(_profile(), opener=FakeOpener(200, _nav(False, code=-101)))
    assert out.status == HEALTH_ERROR
    assert "-101" in out.detail


def test_probe_bilibili_http_and_payload_errors() -> None:
    assert probe_bilibili(_profile(), opener=FakeOpener(500, "boom")).status == HEALTH_ERROR
    assert probe_bilibili(_profile(), opener=FakeOpener(200, "<html>")).status == HEALTH_ERROR
    assert probe_bilibili(_profile(), opener=FakeOpener(200, "[]")).status == HEALTH_ERROR
    assert probe_bilibili(_profile(), opener=FakeOpener(200, '{"code": 0}')).status == HEALTH_ERROR


def test_probe_zhihu_ok_and_unauthorized() -> None:
    ok = probe_zhihu(_profile(), opener=FakeOpener(200, json.dumps({"id": "user-1"})))
    assert ok.status == HEALTH_OK
    assert ok.source == SOURCE_REMOTE

    assert probe_zhihu(_profile(), opener=FakeOpener(401, "{}")).status == HEALTH_EXPIRED
    assert probe_zhihu(_profile(), opener=FakeOpener(403, "{}")).status == HEALTH_EXPIRED


def test_probe_zhihu_risk_code_counts_as_expired() -> None:
    body = json.dumps({"error": {"code": 40352, "message": "risk"}})
    out = probe_zhihu(_profile(), opener=FakeOpener(403, body))
    assert out.status == HEALTH_EXPIRED
    assert "风控" in out.detail


def test_probe_zhihu_200_without_user_is_error() -> None:
    assert probe_zhihu(_profile(), opener=FakeOpener(200, "{}")).status == HEALTH_ERROR


def test_probe_zhihu_server_error_is_error() -> None:
    out = probe_zhihu(_profile(), opener=FakeOpener(503, "{}"))
    assert out.status == HEALTH_ERROR
    assert "503" in out.detail


# ---------------------------------------------------------------------------
# 本地结构校验：抖音 / 小红书
# ---------------------------------------------------------------------------


def test_probe_outcome_defaults_to_local_source() -> None:
    assert ProbeOutcome(HEALTH_OK).source == SOURCE_LOCAL
    assert ProbeOutcome(HEALTH_OK).detail == ""


def test_douyin_local_probe_flags_missing_session() -> None:
    missing = probe_douyin(_profile("ttwid=dev"))
    assert missing.status == HEALTH_EXPIRED
    assert missing.source == SOURCE_LOCAL
    assert "sessionid" in missing.detail

    ok = probe_douyin(_profile("sessionid=abc; ttwid=dev"))
    assert ok.status == HEALTH_OK
    assert ok.source == SOURCE_LOCAL
    assert "未联网" in ok.detail


def test_xiaohongshu_local_probe_requires_web_session() -> None:
    assert probe_xiaohongshu(_profile("a1=dev")).status == HEALTH_EXPIRED
    assert probe_xiaohongshu(_profile("a1=dev; web_session=x")).status == HEALTH_OK


def test_local_probe_reports_unconfigured_for_blank_cookie() -> None:
    assert probe_douyin(_profile("   ")).status == HEALTH_UNCONFIGURED


def test_cookie_names_reads_names_only() -> None:
    names = cookie_names("a=1; b=2; broken; =blank; c =3")
    assert names == frozenset({"a", "b", "c"})


def test_platform_metadata_stays_consistent() -> None:
    assert set(PLATFORM_PROBE_SOURCES) == set(PLATFORM_PROBES)
    assert PLATFORM_PROBE_SOURCES["bilibili"] == SOURCE_REMOTE
    assert PLATFORM_PROBE_SOURCES["zhihu"] == SOURCE_REMOTE
    assert PLATFORM_PROBE_SOURCES["douyin"] == SOURCE_LOCAL
    assert PLATFORM_PROBE_SOURCES["xiaohongshu"] == SOURCE_LOCAL
    assert REQUIRED_COOKIE_NAMES["douyin"] == ("sessionid",)
    assert REQUIRED_COOKIE_NAMES["xiaohongshu"] == ("web_session",)


def test_request_headers_only_adds_ua_when_present() -> None:
    with_ua = request_headers(_profile("a=1", "UA/1"))
    assert with_ua["Cookie"] == "a=1"
    assert with_ua["User-Agent"] == "UA/1"

    without = request_headers(_profile("a=1", "   "))
    assert "User-Agent" not in without


# ---------------------------------------------------------------------------
# check_platform / check_llm：统一入口
# ---------------------------------------------------------------------------


def test_check_platform_returns_unconfigured_without_probing() -> None:
    result = check_platform("douyin", profile=_profile("  "))
    assert result.status == HEALTH_UNCONFIGURED
    assert result.label == "未配置"
    assert result.ok is False
    assert result.kind == "platform"


def test_check_platform_unknown_platform_raises() -> None:
    with pytest.raises(InputUnsupportedError):
        check_platform("weibo", profile=_profile())


def test_check_platform_swallows_probe_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(profile, *, opener=None):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(PLATFORM_PROBES, "douyin", boom)
    result = check_platform("douyin", profile=_profile())

    assert result.status == HEALTH_ERROR
    assert result.detail == "探测异常：RuntimeError"
    assert "kaboom" not in result.detail


def test_check_platform_honours_fixed_moment() -> None:
    result = check_platform(
        "douyin", profile=_profile("sessionid=x"), checked_at="2026-02-02T00:00:00+00:00"
    )
    assert result.checked_at == "2026-02-02T00:00:00+00:00"
    assert CheckResult("platform", "douyin", HEALTH_OK).label == "正常"


def test_check_platform_as_dict_roundtrip() -> None:
    result = check_platform(
        "bilibili", profile=_profile("SESSDATA=x"), opener=FakeOpener(200, _nav(True))
    )
    data = result.as_dict()
    assert data["kind"] == "platform"
    assert data["name"] == "bilibili"
    assert data["status"] == HEALTH_OK
    assert data["label"] == "正常"
    assert data["source"] == SOURCE_REMOTE
    assert data["checked_at"]


def test_check_llm_ok_and_failure_paths() -> None:
    assert check_llm("m", lambda: "OK").status == HEALTH_OK

    def rejected() -> None:
        raise ZhijiError("调用失败 Bearer sk-abcdef123456")

    failure = check_llm("m", rejected)
    assert failure.status == HEALTH_ERROR
    assert failure.source == SOURCE_REMOTE
    assert "sk-abcdef123456" not in failure.detail
    assert "***" in failure.detail

    def crashed() -> None:
        raise ValueError("boom")

    assert check_llm("m", crashed).detail == "探测异常：ValueError"


# ---------------------------------------------------------------------------
# 摘要与脱敏
# ---------------------------------------------------------------------------


def test_sanitize_reason_masks_secrets_and_collapses_whitespace() -> None:
    cleaned = sanitize_reason("failed: Bearer sk-abcdef123, api_key=abcd1234, secret=topsecret")
    for secret in ("sk-abcdef123", "abcd1234", "topsecret"):
        assert secret not in cleaned
    assert "***" in cleaned

    assert sanitize_reason("  多\n  空白 ") == "多 空白"
    assert sanitize_reason("x" * 500, limit=10) == "x" * 10


def test_summarize_maps_credential_states() -> None:
    assert summarize(configured=False)["state"] == HEALTH_UNCONFIGURED
    assert summarize(configured=True)["state"] == HEALTH_UNCHECKED

    checked = summarize(
        configured=True, status=HEALTH_OK, last_checked_at="2026-01-01T00:00:00+00:00"
    )
    assert checked["state"] == HEALTH_OK
    assert checked["label"] == "正常"
    assert checked["checked_at"] == "2026-01-01T00:00:00+00:00"

    expired = summarize(
        configured=True, status=HEALTH_EXPIRED, last_checked_at="t", last_error="登录态失效"
    )
    assert expired["state"] == HEALTH_EXPIRED
    assert expired["label"] == "已失效"
    assert expired["detail"] == "登录态失效"

    unknown = summarize(configured=True, status="weird", last_checked_at="t")
    assert unknown["state"] == HEALTH_UNCHECKED
