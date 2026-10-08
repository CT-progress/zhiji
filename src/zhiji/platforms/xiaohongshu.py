"""小红书适配器：解析笔记链接，并用 Playwright 提取页面中的笔记数据。

小红书正文主要由页面初始化状态 ``window.__INITIAL_STATE__`` 提供；登录态可选，
但需要登录才能浏览的内容必须先在设置页或 ``zhiji xiaohongshu-login`` 中保存
Cookie。当前优先保证图文笔记链路，视频笔记会保留简介、封面和媒体地址。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from zhiji.config import PROJECT_ROOT
from zhiji.errors import InputUnsupportedError, PlatformFetchError
from zhiji.models import (
    ContentBundle,
    ContentRef,
    ContentType,
    MediaFile,
    Metadata,
    Platform,
)
from zhiji.platforms.base import PlatformAdapter
from zhiji.platforms.xiaohongshu_cookies import XiaohongshuProfile, load_profile, save_profile

if TYPE_CHECKING:
    from playwright._impl._api_structures import SetCookieParam

_NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item|video)/([0-9a-zA-Z]+)")
_BROWSER_PROFILE = PROJECT_ROOT / "data" / "browser" / "xiaohongshu-profile"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}
_SHORT_HOSTS = {"xhslink.com", "www.xhslink.com"}


class XiaohongshuAdapter(PlatformAdapter):
    platform = Platform.XIAOHONGSHU

    def parse_url(self, url: str) -> ContentRef:
        host = (urlparse(url).hostname or "").lower()
        if host in _SHORT_HOSTS or host.endswith(".xhslink.com"):
            return ContentRef(
                platform=self.platform,
                content_type=ContentType.IMAGE_POST,
                content_id="short",
                source_url=url,
            )
        match = _NOTE_ID_RE.search(url)
        if not match:
            raise InputUnsupportedError("未识别小红书笔记链接", hint="需要包含 /explore/ 或 /discovery/item/ 笔记 ID")
        content_type = ContentType.VIDEO if "/video/" in url else ContentType.IMAGE_POST
        return ContentRef(
            platform=self.platform,
            content_type=content_type,
            content_id=match.group(1),
            source_url=url,
        )

    def fetch(self, ref: ContentRef, progress: Callable[[str], None] | None = None) -> ContentBundle:
        report = progress or (lambda _message: None)
        if ref.content_id == "short":
            report("解析小红书短链…")
            resolved = self._resolve_short_url(ref.source_url)
            match = _NOTE_ID_RE.search(resolved)
            if not match:
                raise PlatformFetchError("小红书短链未能解析出笔记 ID", platform=self.platform.value)
            content_type = ContentType.VIDEO if "/video/" in resolved else ref.content_type
            ref = ref.model_copy(
                update={
                    "source_url": resolved,
                    "content_id": match.group(1),
                    "content_type": content_type,
                }
            )
        report("启动浏览器加载小红书页面…")
        return self._fetch_via_playwright(ref)

    def search(self, keyword: str, limit: int = 10) -> list:
        raise PlatformFetchError(
            "小红书搜索尚未接入",
            platform=self.platform.value,
            hint="当前支持直接粘贴小红书笔记链接生成笔记",
        )

    def _resolve_short_url(self, url: str) -> str:
        try:
            with httpx.Client(follow_redirects=True, timeout=15) as client:
                response = client.get(url, headers=_UA)
            return str(response.url)
        except httpx.HTTPError as exc:
            raise PlatformFetchError(f"小红书短链解析失败: {exc}", platform=self.platform.value) from exc

    def _fetch_via_playwright(self, ref: ContentRef) -> ContentBundle:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlatformFetchError(
                "Playwright 尚未安装",
                platform=self.platform.value,
                hint="运行 pip install playwright 后重试",
            ) from exc

        profile = load_profile()
        user_agent = profile.user_agent if profile and profile.user_agent else _UA["User-Agent"]
        browser_args = ["--disable-gpu", "--disable-software-rasterizer", "--no-sandbox"]
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=browser_args)
                try:
                    context = browser.new_context(user_agent=user_agent)
                    try:
                        if profile and profile.cookie:
                            context.add_cookies(_playwright_cookies(profile))
                        page = context.new_page()
                        page.goto(ref.source_url, wait_until="domcontentloaded", timeout=30_000)
                        page.wait_for_timeout(2_000)
                        title = page.title()
                        state = page.evaluate("() => window.__INITIAL_STATE__ || null")
                        meta = page.evaluate(
                            """() => {
                                const content = (name) => {
                                    const node = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
                                    return node ? (node.getAttribute('content') || '') : '';
                                };
                                return {
                                    title: content('og:title') || content('twitter:title'),
                                    description: content('description') || content('og:description'),
                                    author: content('author'),
                                    cover: content('og:image') || content('twitter:image'),
                                };
                            }"""
                        )
                    finally:
                        context.close()
                finally:
                    browser.close()
        except (PlaywrightError, OSError, TimeoutError) as exc:
            raise PlatformFetchError(
                f"小红书页面渲染失败: {exc}",
                platform=self.platform.value,
                hint="检查网络与登录态后重试",
            ) from exc
        return _bundle_from_page(ref, state, title or "", meta if isinstance(meta, dict) else {})


def _playwright_cookies(profile: XiaohongshuProfile) -> list[SetCookieParam]:
    cookies: list[SetCookieParam] = []
    for part in profile.cookie.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name:
            cookies.append(
                {"name": name, "value": value, "domain": ".xiaohongshu.com", "path": "/"}
            )
    return cookies


def _find_note(state: object) -> dict | None:
    if not isinstance(state, dict):
        return None
    note_state = state.get("note")
    if not isinstance(note_state, dict):
        return None
    detail_map = note_state.get("noteDetailMap")
    if not isinstance(detail_map, dict):
        return None
    for item in detail_map.values():
        if not isinstance(item, dict):
            continue
        note = item.get("note")
        if isinstance(note, dict):
            return note
    return None


def _media_url(value: object) -> str:
    """从图片 / 视频封面字段中取出可用的 URL。"""

    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("urlDefault", "url", "urlPre"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return ""


def _media_from_note(note: dict) -> list[MediaFile]:
    media: list[MediaFile] = []
    images = note.get("imageList") or note.get("images") or []
    if not isinstance(images, list):
        return media
    for item in images:
        url = ""
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = _media_url(item)
        if url:
            media.append(MediaFile(kind="image", url=url))
    return media


def _tags_from_note(note: dict) -> list[str]:
    tags: list[str] = []
    raw_tags = note.get("tagList") or note.get("tags") or []
    if not isinstance(raw_tags, list):
        return tags
    for tag in raw_tags:
        if isinstance(tag, dict):
            name = tag.get("name")
            if name:
                tags.append(str(name))
        elif tag:
            tags.append(str(tag))
    return tags


def _published_at(note: dict) -> datetime | None:
    value = note.get("time") or note.get("publishTime") or note.get("publish_time")
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _bundle_from_note(ref: ContentRef, note: dict) -> ContentBundle:
    title = str(note.get("title") or note.get("desc") or "小红书笔记").strip()
    desc = str(note.get("desc") or "").strip()
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    images = _media_from_note(note)
    tags = _tags_from_note(note)
    note_type = str(note.get("type") or "").lower()
    content_type = ContentType.VIDEO if note_type == "video" else ref.content_type
    if note_type == "video" and not images:
        video = note.get("video") if isinstance(note.get("video"), dict) else {}
        cover = _media_url(video.get("cover") if isinstance(video, dict) else None)
        if cover:
            images.append(MediaFile(kind="video", url=cover))
    author = user.get("nickname") if isinstance(user, dict) else None
    author_id = user.get("userId") if isinstance(user, dict) else None
    metadata = Metadata(
        title=title,
        author=str(author) if author else None,
        author_url=f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else None,
        published_at=_published_at(note),
        cover_url=images[0].url if images else None,
        tags=tags,
        raw=note,
    )
    updated_ref = ref.model_copy(update={"content_type": content_type})
    return ContentBundle(
        ref=updated_ref,
        metadata=metadata,
        body_text=desc or title,
        media=images,
        source_json=note,
    )


def _bundle_from_page(
    ref: ContentRef,
    state: object,
    page_title: str,
    meta: dict,
) -> ContentBundle:
    note = _find_note(state)
    if note is not None:
        return _bundle_from_note(ref, note)

    title = str(meta.get("title") or page_title or "小红书笔记").strip()
    description = str(meta.get("description") or "").strip()
    cover = _media_url(meta.get("cover")) or None
    author = str(meta.get("author") or "").strip() or None
    metadata = Metadata(title=title, author=author, cover_url=cover, raw={"meta": meta})
    return ContentBundle(
        ref=ref,
        metadata=metadata,
        body_text=description or title,
        media=[MediaFile(kind="image", url=cover)] if cover else [],
        source_json={"meta": meta},
    )


def login_with_browser(profile_dir: str | Path | None = None) -> XiaohongshuProfile:
    """打开 Edge 登录小红书，并保存 Cookie + UA。"""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlatformFetchError(
            "Playwright 尚未安装",
            platform=Platform.XIAOHONGSHU.value,
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
                    "https://www.xiaohongshu.com/explore",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                input("请完成小红书登录；确认可浏览后回到这里按 Enter 保存登录态...")
                cookies = context.cookies()
                cookie_str = "; ".join(
                    f"{item['name']}={item['value']}"
                    for item in cookies
                    if "xiaohongshu.com" in str(item.get("domain", ""))
                )
                user_agent = str(page.evaluate("navigator.userAgent"))
            finally:
                context.close()
    except (PlaywrightError, OSError) as exc:
        raise PlatformFetchError(
            "无法启动小红书登录浏览器",
            platform=Platform.XIAOHONGSHU.value,
            hint="请确认 Microsoft Edge 已安装且没有其他知记登录窗口占用会话",
        ) from exc

    if "web_session=" not in cookie_str:
        raise PlatformFetchError(
            "未捕获到小红书 web_session，登录可能未完成",
            platform=Platform.XIAOHONGSHU.value,
            hint="重新运行 zhiji xiaohongshu-login",
        )
    return save_profile(cookie_str, user_agent)
