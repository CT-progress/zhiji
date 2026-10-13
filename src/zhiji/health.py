"""凭据健康检查（阶段 4.4）：手动触发的一次性「凭据还能用吗」探测。

设计要点：

- **只做手动触发**，不引入任何后台轮询 / 定时任务，避免频繁请求第三方接口触发风控；
  探测失败也绝不影响抓取主流程，只把结果记在凭据行上供界面展示。
- **零泄露**：失败摘要由本模块自己生成（HTTP 状态码、缺失的 Cookie 字段名、异常类名），
  或经 :func:`sanitize_reason` 清洗后截断的第三方错误文本；任何情况下都不会写入 Cookie
  或 Key 的片段。
- **两类探测**：
  * ``remote``：B 站 / 知乎有稳定的只读身份接口，真的带 Cookie 发一次请求判断登录态；
  * ``local``：抖音 / 小红书的接口需要签名头（a_bogus / x-s），联网探测既不稳定也
    不礼貌，因此只做本地结构校验（关键会话 Cookie 是否还在），并在 ``source`` 里
    如实标注，界面会提示「未联网校验」。
- **可测试**：HTTP 客户端以 ``opener`` 参数注入，测试传假实现即可，不必联网。
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from zhiji.errors import InputUnsupportedError, ZhijiError
from zhiji.platforms.cookies import CookieProfile
from zhiji.storage.tables import (
    CREDENTIAL_ACTIVE,
    CREDENTIAL_ERROR,
    CREDENTIAL_EXPIRED,
    CREDENTIAL_UNSET,
)

#: 健康状态：取值与凭据表的 ``status`` 列一致，界面据此展示。
HEALTH_OK = CREDENTIAL_ACTIVE
HEALTH_EXPIRED = CREDENTIAL_EXPIRED
HEALTH_ERROR = CREDENTIAL_ERROR
HEALTH_UNCONFIGURED = CREDENTIAL_UNSET
#: 只在界面上出现：已配置但还没检测过，与「未配置」区分开（不入库）。
HEALTH_UNCHECKED = "unchecked"

#: 状态 -> 中文文案。
HEALTH_LABELS: dict[str, str] = {
    HEALTH_OK: "正常",
    HEALTH_EXPIRED: "已失效",
    HEALTH_ERROR: "检测异常",
    HEALTH_UNCONFIGURED: "未配置",
    HEALTH_UNCHECKED: "未检测",
}

#: 探测来源：remote = 真的发了一次只读请求；local = 只做了本地结构校验。
SOURCE_REMOTE = "remote"
SOURCE_LOCAL = "local"

#: 平台顺序与展示名（与前端卡片保持一致）。
PLATFORM_META: tuple[tuple[str, str], ...] = (
    ("douyin", "抖音"),
    ("bilibili", "B 站"),
    ("zhihu", "知乎"),
    ("xiaohongshu", "小红书"),
)

#: 本地结构校验要看的「关键会话 Cookie」；缺了就等于登录态不可用。
REQUIRED_COOKIE_NAMES: dict[str, tuple[str, ...]] = {
    "douyin": ("sessionid",),
    "xiaohongshu": ("web_session",),
}

BILIBILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
ZHIHU_ME_URL = "https://www.zhihu.com/api/v4/me"

#: 知乎风控错误码（与 :mod:`zhiji.platforms.zhihu` 保持一致）。
_ZHIHU_RISK_CODES = {40352, 40362}

#: 一次探测最多等多久（秒）。
DEFAULT_TIMEOUT = 10.0


class Opener(Protocol):
    """带自定义请求头的一次 GET；返回 ``(状态码, 响应文本)``。"""

    def __call__(
        self, url: str, *, headers: Mapping[str, str], timeout: float = ...
    ) -> tuple[int, str]: ...


@dataclass(frozen=True)
class ProbeOutcome:
    """探测器的原始结论；``detail`` 保证不含任何凭据片段。"""

    status: str
    detail: str = ""
    source: str = SOURCE_LOCAL


@dataclass(frozen=True)
class CheckResult:
    """一次健康检查的结果，界面与数据库共用。"""

    kind: str  #: "platform" 或 "llm"
    name: str
    status: str
    detail: str = ""
    source: str = ""
    checked_at: str = ""

    @property
    def label(self) -> str:
        return HEALTH_LABELS.get(self.status) or HEALTH_LABELS[HEALTH_UNCHECKED]

    @property
    def ok(self) -> bool:
        return self.status == HEALTH_OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "label": self.label,
            "detail": self.detail,
            "source": self.source,
            "checked_at": self.checked_at,
        }


def now_iso() -> str:
    """当前时间的 ISO 文本（秒级），与凭据行的检测时间写法一致。"""

    return datetime.now(UTC).isoformat(timespec="seconds")


def http_get(
    url: str, *, headers: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT
) -> tuple[int, str]:
    """默认探测器：httpx GET，跟随跳转并带超时。测试里可整体替换。"""

    import httpx

    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        response = client.get(url, headers=dict(headers))
        return response.status_code, response.text


def request_headers(profile: CookieProfile) -> dict[str, str]:
    """带登录态的只读请求头；UA 为空时不发该字段（交给客户端默认值）。"""

    headers = {"Cookie": profile.cookie, "Accept": "application/json, text/plain, */*"}
    if profile.user_agent.strip():
        headers["User-Agent"] = profile.user_agent
    return headers


def _get(opener: Opener | None, url: str, profile: CookieProfile) -> tuple[int, str]:
    client = opener or http_get
    return client(url, headers=request_headers(profile))


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def probe_bilibili(profile: CookieProfile, *, opener: Opener | None = None) -> ProbeOutcome:
    """B 站 ``nav`` 接口：权威的「是否登录」只读接口，不需要 WBI 签名。"""

    code, body = _get(opener, BILIBILI_NAV_URL, profile)
    if code != 200:
        return ProbeOutcome(HEALTH_ERROR, f"接口返回 HTTP {code}", SOURCE_REMOTE)
    payload = _json_object(body)
    if payload is None:
        return ProbeOutcome(HEALTH_ERROR, "接口响应不是 JSON", SOURCE_REMOTE)
    data = payload.get("data")
    if not isinstance(data, dict):
        return ProbeOutcome(HEALTH_ERROR, "接口响应缺少 data 字段", SOURCE_REMOTE)
    if data.get("isLogin") in (True, 1):
        return ProbeOutcome(HEALTH_OK, "登录态有效", SOURCE_REMOTE)
    business_code = payload.get("code")
    if business_code not in (0, None):
        return ProbeOutcome(HEALTH_ERROR, f"接口返回业务码 {business_code}", SOURCE_REMOTE)
    return ProbeOutcome(HEALTH_EXPIRED, "登录态已失效，请重新登录", SOURCE_REMOTE)


def probe_zhihu(profile: CookieProfile, *, opener: Opener | None = None) -> ProbeOutcome:
    """知乎 ``/api/v4/me``：只有登录态有效时才会返回 200 + 用户信息。"""

    code, body = _get(opener, ZHIHU_ME_URL, profile)
    payload = _json_object(body)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("code") in _ZHIHU_RISK_CODES:
            return ProbeOutcome(HEALTH_EXPIRED, "触发风控，需在浏览器重新登录验证", SOURCE_REMOTE)
    if code == 200:
        if isinstance(payload, dict) and payload.get("id") is not None:
            return ProbeOutcome(HEALTH_OK, "登录态有效", SOURCE_REMOTE)
        return ProbeOutcome(HEALTH_ERROR, "接口响应缺少用户信息", SOURCE_REMOTE)
    if code in (401, 403):
        return ProbeOutcome(HEALTH_EXPIRED, "登录态已失效，请重新登录", SOURCE_REMOTE)
    return ProbeOutcome(HEALTH_ERROR, f"接口返回 HTTP {code}", SOURCE_REMOTE)


def cookie_names(cookie: str) -> frozenset[str]:
    """取出 Cookie 串里的字段名（只有名字，不含任何值）。"""

    names = set()
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        if name:
            names.add(name)
    return frozenset(names)


def probe_local(platform: str, profile: CookieProfile) -> ProbeOutcome:
    """本地结构校验：只看关键会话 Cookie 在不在，不发任何网络请求。"""

    if not profile.cookie.strip():
        return ProbeOutcome(HEALTH_UNCONFIGURED, "未配置登录态", SOURCE_LOCAL)
    required = REQUIRED_COOKIE_NAMES.get(platform, ())
    present = cookie_names(profile.cookie)
    missing = [name for name in required if name not in present]
    if missing:
        return ProbeOutcome(
            HEALTH_EXPIRED, f"缺少关键 Cookie：{', '.join(missing)}", SOURCE_LOCAL
        )
    return ProbeOutcome(HEALTH_OK, "关键字段齐全（未联网校验）", SOURCE_LOCAL)


def probe_douyin(profile: CookieProfile, *, opener: Opener | None = None) -> ProbeOutcome:
    """抖音接口需要 a_bogus 签名，联网探测不稳定，只做本地结构校验。"""

    return probe_local("douyin", profile)


def probe_xiaohongshu(profile: CookieProfile, *, opener: Opener | None = None) -> ProbeOutcome:
    """小红书接口需要 x-s 签名，联网探测不稳定，只做本地结构校验。"""

    return probe_local("xiaohongshu", profile)


#: 平台 -> 探测器。新增平台时在这里补一行即可。
PLATFORM_PROBES: dict[str, Callable[..., ProbeOutcome]] = {
    "bilibili": probe_bilibili,
    "zhihu": probe_zhihu,
    "douyin": probe_douyin,
    "xiaohongshu": probe_xiaohongshu,
}

#: 平台 -> 探测方式（remote = 真的发一次只读请求；local = 只做本地结构校验）。
#: 界面据此提示「未联网校验」，避免用户误以为登录态已经联网验证过。
PLATFORM_PROBE_SOURCES: dict[str, str] = {
    "bilibili": SOURCE_REMOTE,
    "zhihu": SOURCE_REMOTE,
    "douyin": SOURCE_LOCAL,
    "xiaohongshu": SOURCE_LOCAL,
}


def load_platform_profile(platform: str) -> CookieProfile | None:
    """惰性导入平台 Cookie 模块并读取登录态；不认识平台 / 读不到都返回 None。"""

    if platform not in PLATFORM_PROBES:
        return None
    try:
        module = importlib.import_module(f"zhiji.platforms.{platform}_cookies")
    except ImportError:
        return None
    loader = getattr(module, "load_profile", None)
    if not callable(loader):
        return None
    try:
        profile = loader()
    except ZhijiError:
        return None
    return profile if isinstance(profile, CookieProfile) else None


def check_platform(
    platform: str,
    *,
    profile: CookieProfile | None = None,
    opener: Opener | None = None,
    checked_at: str | None = None,
) -> CheckResult:
    """检测一个平台的登录态；未配置与探测异常都返回结果对象，不打断主流程。"""

    probe = PLATFORM_PROBES.get(platform)
    if probe is None:
        raise InputUnsupportedError(
            f"不支持的平台: {platform}",
            hint="可选值：" + " / ".join(key for key, _ in PLATFORM_META),
        )
    moment = checked_at or now_iso()
    if profile is None:
        profile = load_platform_profile(platform)
    if profile is None or not profile.cookie.strip():
        return CheckResult(
            "platform", platform, HEALTH_UNCONFIGURED, "未配置登录态", "", moment
        )
    try:
        outcome = probe(profile, opener=opener)
    except Exception as exc:  # noqa: BLE001 - 探测失败只留异常类名，绝不落任何原文
        return CheckResult(
            "platform",
            platform,
            HEALTH_ERROR,
            f"探测异常：{type(exc).__name__}",
            SOURCE_REMOTE,
            moment,
        )
    return CheckResult(
        "platform", platform, outcome.status, outcome.detail, outcome.source, moment
    )


def check_llm(
    name: str,
    tester: Callable[[], Any],
    *,
    checked_at: str | None = None,
) -> CheckResult:
    """检测一个 LLM Key：``tester`` 由调用方注入（通常是 ``LLMClient(...).test_connection``）。"""

    moment = checked_at or now_iso()
    try:
        tester()
    except ZhijiError as exc:
        reason = sanitize_reason(getattr(exc, "message", "") or str(exc)) or "调用失败"
        return CheckResult("llm", name, HEALTH_ERROR, reason, SOURCE_REMOTE, moment)
    except Exception as exc:  # noqa: BLE001 - 同上：只留异常类名
        return CheckResult(
            "llm", name, HEALTH_ERROR, f"探测异常：{type(exc).__name__}", SOURCE_REMOTE, moment
        )
    return CheckResult("llm", name, HEALTH_OK, "连通正常", SOURCE_REMOTE, moment)


#: 第三方错误文本里「看起来像密钥」的片段：Bearer / sk-xxx / api_key=xxx 等。
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{4,}"),
    re.compile(r"(?i)\b(?:sk|pk|api|key|token)[-_][A-Za-z0-9._\-]{6,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\b\s*[:=]\s*\S+"),
)


def sanitize_reason(text: str, *, limit: int = 160) -> str:
    """把第三方错误文本洗成可安全落库的摘要：抹掉疑似密钥并截断。"""

    cleaned = " ".join((text or "").split())
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("***", cleaned)
    return cleaned[:limit]


def summarize(
    *,
    configured: bool,
    status: str = "",
    last_checked_at: str = "",
    last_error: str = "",
) -> dict[str, str]:
    """把凭据行的健康字段翻译成界面可直接渲染的状态结构。"""

    if not configured:
        state = HEALTH_UNCONFIGURED
    elif not last_checked_at:
        state = HEALTH_UNCHECKED
    else:
        state = status if status in HEALTH_LABELS else HEALTH_UNCHECKED
    return {
        "state": state,
        "label": HEALTH_LABELS[state],
        "checked_at": last_checked_at,
        "detail": last_error or "",
    }
