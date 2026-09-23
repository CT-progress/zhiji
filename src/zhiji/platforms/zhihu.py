"""知乎适配器：登录态靠有头浏览器收割，抓取直接走知乎 JSON API。

请求链路（四步改法）：
1. ``zhiji zhihu-login`` 用有头 Edge 登录，把 Cookie + UA 收割到
   ``data/zhihu-profile.json``（该目录已被 .gitignore 忽略）。
2. 抓取时用 curl_cffi（Edge TLS 指纹）调用 ``/api/v4/*`` 接口，并用
   ``d_c0`` 生成 ``x-zse-96`` 签名头（实现见 :mod:`zhiji.platforms.zse`）。
3. 遇到知乎风控（40352/40362 或 zh-zse-ck 挑战）不自动破解，抛出
   :class:`ZhihuRiskError` 提示重新登录人工验证。
4. API 失败时回退解析页面 ``#js-initialData``；结果按内容 ID 缓存 24 小时，
   请求之间带随机延时、不并发。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from zhiji.config import PROJECT_ROOT
from zhiji.errors import InputUnsupportedError, PlatformFetchError
from zhiji.models import ContentBundle, ContentRef, ContentType, Metadata, Platform
from zhiji.platforms.base import PlatformAdapter
from zhiji.platforms.zhihu_cookies import ZhihuProfile, load_profile, save_profile

_BROWSER_PROFILE = PROJECT_ROOT / "data" / "browser" / "zhihu-profile"
_CACHE_ROOT = PROJECT_ROOT / "data" / "cache" / "zhihu"
_CACHE_TTL = timedelta(hours=24)
_RISK_CODES = {40352, 40362}
_ZSE93 = "101_3_3.0"
_MIN_BODY_LENGTH = 50
_LAST_REQUEST_AT = 0.0


class ZhihuRiskError(PlatformFetchError):
    """知乎风控或需人工验证的错误；不尝试自动绕过，交回用户处理。"""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            platform=Platform.ZHIHU.value,
            hint="先运行 zhiji zhihu-login 在 Edge 中完成人工验证，再重试",
        )


class ZhihuAdapter(PlatformAdapter):
    platform = Platform.ZHIHU

    def parse_url(self, url: str) -> ContentRef:
        if "/question/" in url:
            question_id = re.search(r"/question/(\d+)", url)
            if not question_id:
                raise InputUnsupportedError("未识别知乎问题链接")
            content_type = ContentType.ANSWER if "/answer/" in url else ContentType.QUESTION
            return ContentRef(
                platform=self.platform,
                content_type=content_type,
                content_id=f"q{question_id.group(1)}",
                source_url=url,
            )
        article_id = re.search(r"/(?:p|article)/(\d+)", url)
        if article_id:
            return ContentRef(
                platform=self.platform,
                content_type=ContentType.ARTICLE,
                content_id=f"p{article_id.group(1)}",
                source_url=url,
            )
        raise InputUnsupportedError("暂只支持知乎问题/回答/文章链接")

    def fetch(self, ref: ContentRef, progress: Callable[[str], None] | None = None) -> ContentBundle:
        profile = load_profile()
        if profile is None or not profile.verified:
            raise _login_required("知乎登录态缺失")
        return _fetch_content(ref, profile)

    def search(self, keyword: str, limit: int = 10) -> list:
        raise PlatformFetchError(
            "知乎搜索接口尚未接入",
            platform=self.platform.value,
            hint="M1 骨架先支持知乎链接处理，搜索将在后续迭代补齐",
        )


def _fetch_content(ref: ContentRef, profile: ZhihuProfile) -> ContentBundle:
    """带 24h 缓存的抓取入口：API 优先，失败回退页面 HTML。"""

    cache_path = _cache_path(ref)
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached
    try:
        bundle = _fetch_via_api(ref, profile)
    except ZhihuRiskError:
        raise
    except PlatformFetchError:
        bundle = _fetch_via_html(ref, profile)
    _ensure_body(bundle)
    _store_cache(cache_path, bundle)
    return bundle


def _fetch_via_api(ref: ContentRef, profile: ZhihuProfile) -> ContentBundle:
    """走知乎 JSON API（curl_cffi + x-zse-96 签名）。"""

    payload = _api_get(_api_url(ref), ref, profile)
    title, content_html, author, published_at, source = _extract_from_payload(ref, payload)
    body_text = _html_to_markdown(content_html)
    metadata = Metadata(
        title=title or "知乎内容",
        author=author,
        published_at=published_at,
        raw={"source": "zhihu_api"},
    )
    return ContentBundle(ref=ref, metadata=metadata, body_text=body_text, source_json=source)


def _fetch_via_html(ref: ContentRef, profile: ZhihuProfile) -> ContentBundle:
    """兜底：用 curl_cffi 拉页面并解析 ``#js-initialData``（不启动浏览器）。"""

    from curl_cffi.requests import Session

    _throttle()
    headers = {
        "User-Agent": profile.user_agent,
        "Cookie": profile.cookie,
        "Referer": "https://www.zhihu.com/",
        "x-requested-with": "fetch",
    }
    try:
        with Session(impersonate=_browser_family(profile.user_agent), timeout=20) as session:
            response = session.get(ref.source_url, headers=headers)
    except Exception as exc:
        raise PlatformFetchError(
            "知乎页面网络请求失败",
            platform=Platform.ZHIHU.value,
            hint="检查网络后重试",
        ) from exc

    if response.status_code == 403:
        _raise_risk_if_needed(response)
        raise PlatformFetchError(
            "知乎拒绝了当前请求（HTTP 403）",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 刷新登录态后重试",
        )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise PlatformFetchError(
            f"知乎页面请求失败（HTTP {response.status_code}）",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 刷新登录态后重试",
        ) from exc
    try:
        initial = _parse_initial_data(response.text)
    except ValueError as exc:
        raise PlatformFetchError(
            "知乎页面已打开，但没有提取到完整正文",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 检查登录或安全验证状态",
        ) from exc
    title, content_html, author, published_at, source = _extract_from_initial_data(ref, initial)
    body_text = _html_to_markdown(content_html)
    metadata = Metadata(
        title=title or "知乎内容",
        author=author,
        published_at=published_at,
        raw={"source": "zhihu_page"},
    )
    return ContentBundle(ref=ref, metadata=metadata, body_text=body_text, source_json=source)


def _api_url(ref: ContentRef) -> str:
    if ref.content_type == ContentType.ARTICLE:
        return f"https://www.zhihu.com/api/v4/articles/{ref.content_id[1:]}"
    if ref.content_type == ContentType.ANSWER:
        answer_id = _extract_answer_id(ref.source_url)
        if not answer_id:
            raise _login_required("回答链接缺少 answer id，无法定位回答")
        return f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=content,excerpt,voteup_count,comment_count"
    return f"https://www.zhihu.com/api/v4/questions/{ref.content_id[1:]}"


def _api_get(url: str, ref: ContentRef, profile: ZhihuProfile) -> dict:
    from curl_cffi.requests import Session

    _throttle()
    d_c0 = profile.d_c0()
    if not d_c0:
        raise ZhihuRiskError("登录态中缺少 d_c0 凭证")
    headers = {
        "User-Agent": profile.user_agent,
        "Cookie": profile.cookie,
        "Referer": ref.source_url or "https://www.zhihu.com/",
        "x-requested-with": "fetch",
    }
    parsed = urlparse(url)
    path_and_query = parsed.path
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"
    headers.update(_sign_headers(path_and_query, d_c0))
    try:
        with Session(impersonate=_browser_family(profile.user_agent), timeout=20) as session:
            response = session.get(url, headers=headers)
    except Exception as exc:
        raise PlatformFetchError(
            "知乎接口网络请求失败",
            platform=Platform.ZHIHU.value,
            hint="检查网络后重试",
        ) from exc

    if response.status_code == 403:
        _raise_risk_if_needed(response)
        raise PlatformFetchError(
            "知乎拒绝了当前请求（HTTP 403）",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 刷新登录态后重试",
        )
    try:
        response.raise_for_status()
    except Exception as exc:
        raise PlatformFetchError(
            f"知乎接口请求失败（HTTP {response.status_code}）",
            platform=Platform.ZHIHU.value,
            hint="稍后重试",
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise PlatformFetchError(
            "知乎接口返回了非 JSON 内容",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 刷新登录态后重试",
        ) from exc
    if isinstance(payload, dict) and payload.get("error"):
        _raise_risk_if_needed(response)
        error = payload["error"]
        detail = error.get("message") or error.get("code") or error
        raise PlatformFetchError(
            f"知乎接口返回错误: {detail}",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 刷新登录态后重试",
        )
    return payload


def _sign_headers(path_and_query: str, d_c0: str) -> dict[str, str]:
    """按 zhihu-cli 的规则构造 x-zse-93 / x-zse-96 签名头。"""

    from zhiji.platforms.zse import ZSECipher

    source = f"{_ZSE93}+{path_and_query}+{d_c0}"
    digest = hashlib.md5(source.encode("utf-8")).hexdigest()
    signature = ZSECipher().encrypt(digest)
    return {
        "x-zse-93": _ZSE93,
        "x-zse-96": f"2.0_{signature}",
    }


def _raise_risk_if_needed(response) -> None:
    """识别知乎风控响应（zh-zse-ck / 40352 / 40362 / need_login）并抛错。"""

    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        error = {}
    code = error.get("code")
    if code in _RISK_CODES or error.get("need_login"):
        raise ZhihuRiskError(f"知乎风控拦截（错误码 {code}）")
    # zh-zse-ck 挑战页只有在真·403 时才算风控；400/502 错误页正文里
    # 也可能出现这个字符串（例如请求头超限回显），不能用来做判断。
    if response.status_code == 403 and "zh-zse-ck" in response.text:
        raise ZhihuRiskError("知乎返回了 zh-zse-ck 反爬挑战页")


def _extract_from_payload(ref: ContentRef, payload: dict) -> tuple[str, str, str | None, datetime | None, dict]:
    if ref.content_type == ContentType.ARTICLE:
        title = payload.get("title") or ""
        content_html = payload.get("content") or ""
        author = (payload.get("author") or {}).get("name")
        published_at = _ts_to_dt(payload.get("created"))
    elif ref.content_type == ContentType.ANSWER:
        question = payload.get("question") or {}
        title = question.get("title") or ""
        content_html = payload.get("content") or ""
        author = (payload.get("author") or {}).get("name")
        published_at = _ts_to_dt(payload.get("created_time") or payload.get("created"))
    else:
        title = payload.get("title") or ""
        content_html = payload.get("detail") or payload.get("content") or ""
        author = (payload.get("author") or {}).get("name")
        published_at = _ts_to_dt(payload.get("created"))
    return title, content_html, author, published_at, payload


def _extract_from_initial_data(
    ref: ContentRef, initial: dict
) -> tuple[str, str, str | None, datetime | None, dict]:
    entities = (initial.get("initialState") or {}).get("entities") or {}
    if ref.content_type == ContentType.ARTICLE:
        node = (entities.get("articles") or {}).get(ref.content_id[1:]) or {}
    elif ref.content_type == ContentType.ANSWER:
        node = (entities.get("answers") or {}).get(_extract_answer_id(ref.source_url)) or {}
    else:
        node = (entities.get("questions") or {}).get(ref.content_id[1:]) or {}
    title = node.get("title") or ""
    content_html = node.get("content") or node.get("detail") or node.get("excerpt") or ""
    author = (node.get("author") or {}).get("name")
    published_at = _ts_to_dt(node.get("created") or node.get("created_time"))
    return title, content_html, author, published_at, node


def _parse_initial_data(html_text: str) -> dict:
    match = re.search(r'<script id="js-initialData"[^>]*>(.*?)</script>', html_text, re.DOTALL)
    if not match:
        raise ValueError("Could not find 'js-initialData' script tag")
    return json.loads(match.group(1))


def _html_to_markdown(html: str) -> str:
    import html2text

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.body_width = 0
    converter.unicode_snob = True
    return converter.handle(html or "").strip()


def _ts_to_dt(value) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _ensure_body(bundle: ContentBundle) -> None:
    if not bundle.body_text or len(bundle.body_text) < _MIN_BODY_LENGTH:
        raise PlatformFetchError(
            "知乎内容为空或正文过短",
            platform=Platform.ZHIHU.value,
            hint="运行 zhiji zhihu-login 刷新登录态后重试",
        )


def _cache_path(ref: ContentRef) -> Path:
    if ref.content_type == ContentType.ANSWER:
        name = f"answer_{_extract_answer_id(ref.source_url)}"
    else:
        name = f"{ref.content_type.value}_{ref.content_id}"
    return _CACHE_ROOT / f"{name}.json"


def _load_cache(path: Path) -> ContentBundle | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(data["saved_at"])
        if datetime.now(UTC) - saved_at > _CACHE_TTL:
            return None
        return ContentBundle.model_validate(data["bundle"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _store_cache(path: Path, bundle: ContentBundle) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "bundle": bundle.model_dump(mode="json"),
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _throttle() -> None:
    """请求间随机延时（1~2.5s），降低触发风控的概率。"""

    global _LAST_REQUEST_AT
    delay = random.uniform(1.0, 2.5)
    elapsed = time.monotonic() - _LAST_REQUEST_AT
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _LAST_REQUEST_AT = time.monotonic()


def _browser_family(user_agent: str) -> str:
    if re.search(r"Edg/", user_agent):
        return "edge"
    if re.search(r"Firefox/", user_agent):
        return "firefox"
    if re.search(r"Safari/", user_agent) and not re.search(r"Chrome/", user_agent):
        return "safari"
    return "chrome"


def _extract_answer_id(url: str) -> str:
    match = re.search(r"/answer/(\d+)", url)
    return match.group(1) if match else ""


def _login_required(message: str) -> PlatformFetchError:
    return PlatformFetchError(
        message,
        platform=Platform.ZHIHU.value,
        hint="先运行 zhiji zhihu-login 在 Edge 中完成登录，再重试",
    )


def login_with_browser(profile_dir: str | Path | None = None) -> ZhihuProfile:
    """打开有头 Edge 完成登录，收割 Cookie/UA 写入 ``data/zhihu-profile.json``。"""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlatformFetchError(
            "Playwright 尚未安装",
            platform=Platform.ZHIHU.value,
            hint="运行 pip install playwright 后重试",
        ) from exc

    browser_profile = Path(profile_dir) if profile_dir else _BROWSER_PROFILE
    browser_profile.mkdir(parents=True, exist_ok=True)
    cookie_str = ""
    user_agent = ""
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(browser_profile),
                channel="msedge",
                headless=False,
                locale="zh-CN",
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    "https://www.zhihu.com/signin",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                input("请在 Edge 中完成知乎登录；确认可以正常浏览后，回到这里按 Enter 保存登录态...")
                # 再访问一次知乎首页，让挑战/风控 Cookie 落地后再收割
                page.goto("https://www.zhihu.com/", wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(2_000)
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in context.cookies())
                user_agent = page.evaluate("navigator.userAgent")
            finally:
                context.close()
    except (PlaywrightError, OSError) as exc:
        raise PlatformFetchError(
            "无法启动知乎登录浏览器",
            platform=Platform.ZHIHU.value,
            hint="请确认 Microsoft Edge 已安装且没有其他知记登录窗口占用会话",
        ) from exc

    if not cookie_str:
        raise PlatformFetchError(
            "未捕获到知乎 Cookie，登录可能未完成",
            platform=Platform.ZHIHU.value,
            hint="重新运行 zhiji zhihu-login",
        )
    return save_profile(cookie_str, user_agent)
