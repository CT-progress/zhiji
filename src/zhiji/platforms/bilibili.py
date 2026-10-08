"""B 站适配器：公开接口获取视频信息、CC/AI 字幕与搜索候选。

字幕接口（x/player/wbi/v2）未登录时不返回字幕列表。用户可通过
``zhiji bilibili-login`` 或 Web 设置页保存 SESSDATA 登录态；存在登录态时
优先使用官方字幕，否则由上层流水线降级到音频转写。
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from zhiji.errors import InputUnsupportedError, PlatformFetchError
from zhiji.models import (
    ContentBundle,
    ContentRef,
    ContentType,
    Metadata,
    Platform,
    SearchResult,
    TranscriptSegment,
)
from zhiji.platforms.base import PlatformAdapter
from zhiji.platforms.bilibili_cookies import BilibiliProfile, load_profile, save_profile
from zhiji.platforms.bilibili_wbi import build_signed_params, mixin_key_from_urls

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_WBI_KEY_TTL = 6 * 60 * 60
_WBI_CACHE_LOCK = threading.Lock()
_WBI_CACHE: tuple[float, str] | None = None
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


class BilibiliAdapter(PlatformAdapter):
    platform = Platform.BILIBILI

    def parse_url(self, url: str) -> ContentRef:
        if "b23.tv" in url:
            return ContentRef(
                platform=self.platform,
                content_type=ContentType.VIDEO,
                content_id="short",
                source_url=url,
            )
        match = _BV_RE.search(url)
        if not match:
            raise InputUnsupportedError("未识别 B 站视频链接", hint="需要包含 BV 号")
        return ContentRef(
            platform=self.platform,
            content_type=ContentType.VIDEO,
            content_id=match.group(0),
            source_url=url,
        )

    def fetch(self, ref: ContentRef, progress: Callable[[str], None] | None = None) -> ContentBundle:
        bvid = self._resolve_bvid(ref)
        data = self._get_json(
            "https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}
        )
        if data.get("code") != 0:
            raise PlatformFetchError(
                f"B 站接口返回 {data.get('code')}: {data.get('message')}",
                platform=self.platform.value,
            )
        info = data["data"]
        published = None
        if info.get("pubdate"):
            published = datetime.fromtimestamp(info["pubdate"], tz=UTC)
        metadata = Metadata(
            title=info.get("title", ""),
            author=info.get("owner", {}).get("name"),
            author_url=f"https://space.bilibili.com/{info.get('mid', '')}",
            published_at=published,
            cover_url=info.get("pic"),
            tags=[info["tname"]] if info.get("tname") else [],
            raw=info,
        )
        segments = self._fetch_subtitles(bvid, info.get("cid"))
        return ContentBundle(
            ref=ref,
            metadata=metadata,
            segments=segments,
            body_text=info.get("desc"),
            source_json=data,
        )

    def search(self, keyword: str, limit: int = 10) -> list[SearchResult]:
        """使用 wbi 签名搜索视频，避免旧匿名接口的 412 风控。"""

        page_size = max(1, min(limit, 50))
        params = build_signed_params(
            {
                "search_type": "video",
                "keyword": keyword,
                "page": 1,
                "page_size": page_size,
            },
            self._get_wbi_key(),
        )
        data = self._get_json(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            params=params,
            headers={"Referer": "https://search.bilibili.com/"},
        )
        if data.get("code") != 0:
            raise PlatformFetchError(
                f"B 站搜索失败: {data.get('message')}", platform=self.platform.value
            )
        results = data.get("data", {}).get("result") or []
        out: list[SearchResult] = []
        for item in results[:limit]:
            bvid = item.get("bvid")
            if not bvid:
                continue
            out.append(
                SearchResult(
                    platform=self.platform,
                    title=_strip_tags(item.get("title", "")),
                    author=item.get("author"),
                    duration=_parse_duration(item.get("duration")),
                    url=f"https://www.bilibili.com/video/{bvid}",
                    description=item.get("description"),
                )
            )
        return out

    def _get_wbi_key(self) -> str:
        global _WBI_CACHE

        now = time.monotonic()
        with _WBI_CACHE_LOCK:
            if _WBI_CACHE and now - _WBI_CACHE[0] < _WBI_KEY_TTL:
                return _WBI_CACHE[1]
        nav = self._get_json("https://api.bilibili.com/x/web-interface/nav")
        wbi_img = nav.get("data", {}).get("wbi_img") or {}
        key = mixin_key_from_urls(
            str(wbi_img.get("img_url", "")),
            str(wbi_img.get("sub_url", "")),
        )
        if not key:
            raise PlatformFetchError("B 站未返回有效的 wbi 密钥", platform=self.platform.value)
        with _WBI_CACHE_LOCK:
            _WBI_CACHE = (now, key)
        return key

    def _resolve_bvid(self, ref: ContentRef) -> str:
        if ref.content_id != "short":
            return ref.content_id
        with httpx.Client(follow_redirects=True, headers=_UA, timeout=15) as client:
            response = client.get(ref.source_url)
        match = _BV_RE.search(str(response.url))
        if not match:
            raise PlatformFetchError("b23 短链未能解析出 BV 号", platform=self.platform.value)
        return match.group(0)

    def _fetch_subtitles(self, bvid: str, cid: int | None) -> list[TranscriptSegment]:
        if not cid:
            return []
        try:
            player = self._get_json(
                "https://api.bilibili.com/x/player/wbi/v2",
                params={"bvid": bvid, "cid": cid},
            )
            subtitle_list = player.get("data", {}).get("subtitle", {}).get("subtitles") or []
        except PlatformFetchError:
            return []
        subtitle = _pick_subtitle(subtitle_list)
        if subtitle is None:
            return []
        url = subtitle.get("subtitle_url")
        if not url:
            return []
        if url.startswith("//"):
            url = "https:" + url
        data = self._get_json(url, headers={"Referer": "https://www.bilibili.com"})
        return [
            TranscriptSegment(
                start=float(item.get("from", 0)),
                end=float(item.get("to", 0)),
                text=item.get("content", ""),
            )
            for item in data.get("body", [])
            if item.get("content")
        ]

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """读取本地 B 站登录态，存在时以 Cookie 头形式附加到所有接口请求。"""

        profile = load_profile()
        if profile and profile.cookie:
            return {"Cookie": profile.cookie}
        return {}

    def _get_json(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
        try:
            with httpx.Client(timeout=15, headers={**_UA, **self._auth_headers(), **(headers or {})}) as client:
                response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformFetchError(f"B 站请求失败: {exc}", platform=self.platform.value) from exc


def _pick_subtitle(subtitles: list[dict]) -> dict | None:
    """优先中文字幕（含 AI 字幕），否则取第一条；空列表返回 None。"""

    if not subtitles:
        return None
    for sub in subtitles:
        lan = str(sub.get("lan", "")).lower()
        if "zh" in lan or "cn" in lan:
            return sub
    return subtitles[0]


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _parse_duration(value: object) -> int | None:
    """把 B 站搜索返回的 ``mm:ss`` / ``hh:mm:ss`` 转成秒。"""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value).strip().split(":")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def login_with_browser() -> BilibiliProfile:
    """打开浏览器让用户手动登录 B 站，收割 Cookie 保存登录态。"""

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlatformFetchError(
            "Playwright 尚未安装",
            platform="bilibili",
            hint="运行 pip install playwright 后重试",
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=_UA["User-Agent"])
        page = context.new_page()

        page.goto("https://www.bilibili.com", wait_until="domcontentloaded")

        # 等待用户登录并关闭浏览器
        with contextlib.suppress(PlaywrightTimeoutError):
            page.wait_for_event("close", timeout=600000)

        cookies = context.cookies()
        cookie_parts = []
        for c in cookies:
            if ".bilibili.com" in c.get("domain", ""):
                cookie_parts.append(f"{c['name']}={c['value']}")
        cookie_str = "; ".join(cookie_parts)

        browser.close()

    if "SESSDATA=" not in cookie_str:
        raise PlatformFetchError(
            "未捕获到 SESSDATA，登录可能未完成",
            platform="bilibili",
            hint="请在浏览器中完成 B 站登录后再关闭窗口",
        )

    return save_profile(cookie_str, _UA["User-Agent"])