"""抖音适配器：短链解析、Playwright 渲染 + 网络拦截获取音频 URL。"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx

from zhiji.errors import InputUnsupportedError, PlatformFetchError
from zhiji.models import (
    AppSettings,
    ContentBundle,
    ContentRef,
    ContentType,
    Metadata,
    Platform,
    TranscriptSegment,
)
from zhiji.platforms.base import PlatformAdapter

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _load_settings() -> AppSettings:
    """读取全局设置，使抖音转写与其它平台共用同一套 whisper 参数。"""

    from zhiji.config import ConfigManager

    return ConfigManager().get().settings


class DouyinAdapter(PlatformAdapter):
    platform = Platform.DOUYIN

    def parse_url(self, url: str) -> ContentRef:
        if "v.douyin.com" in url:
            return ContentRef(
                platform=self.platform,
                content_type=ContentType.VIDEO,
                content_id="short",
                source_url=url,
            )
        match = re.search(r"/video/(\d+)", url)
        if not match:
            raise InputUnsupportedError("未识别抖音视频链接")
        return ContentRef(
            platform=self.platform,
            content_type=ContentType.VIDEO,
            content_id=match.group(1),
            source_url=url,
        )

    def fetch(self, ref: ContentRef, progress: Callable[[str], None] | None = None) -> ContentBundle:
        report = progress or (lambda _msg: None)
        if ref.content_id == "short":
            report("解析抖音短链…")
            resolved = self._resolve_short_url(ref.source_url)
            match = re.search(r"/video/(\d+)", resolved)
            if not match:
                raise PlatformFetchError("抖音短链解析失败", platform=self.platform.value)
            ref = ref.model_copy(update={"source_url": resolved, "content_id": match.group(1)})
        report("启动浏览器加载抖音页面…")
        return self._fetch_via_playwright(ref, report)

    def _fetch_via_playwright(
        self, ref: ContentRef, report: Callable[[str], None] | None = None
    ) -> ContentBundle:
        """用 Playwright 渲染页面，拦截网络请求获取音频 URL。"""
        report = report or (lambda _msg: None)
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlatformFetchError(
                "Playwright 尚未安装",
                platform=self.platform.value,
                hint="运行 pip install playwright 后重试",
            ) from exc

        from zhiji.platforms.douyin_cookies import load_profile

        settings = _load_settings()
        profile = load_profile()
        cookie_str = profile.cookie if profile else ""

        audio_urls: list[str] = []
        video_urls: list[str] = []

        def handle_response(response):
            url = response.url
            # 拦截音视频流
            if ("media-audio" in url or "audio" in url.lower()) and url not in audio_urls:
                audio_urls.append(url)
            elif ("media-video" in url or "douyinvod.com" in url) and url not in video_urls:
                video_urls.append(url)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    channel="chrome",
                    args=["--disable-gpu", "--disable-software-rasterizer", "--no-sandbox"],
                )
                context = browser.new_context(user_agent=_UA)

                # 只加关键 cookie（63个全加会崩溃）
                essential_cookies = {"ttwid", "sessionid", "sid_tt", "uid_tt", "passport_csrf_token"}
                if cookie_str:
                    for part in cookie_str.split(";"):
                        part = part.strip()
                        if "=" in part:
                            name, value = part.split("=", 1)
                            if name.strip() in essential_cookies:
                                context.add_cookies([{
                                    "name": name.strip(),
                                    "value": value.strip(),
                                    "domain": ".douyin.com",
                                    "path": "/",
                                }])

                page = context.new_page()
                page.on("response", handle_response)

                # 先打开页面，不加 cookie
                page.goto(ref.source_url, wait_until="domcontentloaded", timeout=30000)

                # 等待拦截到音视频流
                report("页面已加载，等待拦截音视频流…")
                for _ in range(20):
                    if audio_urls or video_urls:
                        break
                    page.wait_for_timeout(500)
                if audio_urls:
                    report("已拦截到音频地址")
                else:
                    report("未拦截到音频流，将只使用页面文本")

                # 从页面提取元数据
                title = ""
                author = ""
                desc = ""

                try:
                    meta_desc = page.locator('meta[name="description"]').get_attribute("content")
                    if meta_desc:
                        desc = meta_desc
                        desc_match = re.match(r'^(.+?)\s*#', meta_desc)
                        if desc_match:
                            title = desc_match.group(1).strip()
                        author_match = re.search(r'[-–]\s*(.+?)\s*于\d{8}发布', meta_desc)
                        if author_match:
                            author = author_match.group(1).strip()
                except PlaywrightError:
                    # 页面结构可能变化，元数据缺失时回退为空值
                    pass

                if not title:
                    try:
                        og_title = page.locator('meta[property="og:title"]').get_attribute("content")
                        if og_title:
                            title = og_title
                    except PlaywrightError:
                        # og:title 缺失时回退到页面标题
                        pass

                if not title:
                    title = page.title() or "抖音视频"

                # 收集页面 cookie 用于下载
                page_cookies = {}
                try:
                    for c in context.cookies():
                        if ".douyin.com" in c.get("domain", ""):
                            page_cookies[c["name"]] = c["value"]
                except PlaywrightError:
                    # Cookie 收割失败时继续，后续仍可用页面文本
                    pass

                browser.close()

            # 用 httpx + cookie 下载音频
            segments: list[TranscriptSegment] = []
            downloaded_audio = None
            if audio_urls:
                report("下载音频…")
                try:
                    tmp_dir = Path(tempfile.gettempdir()) / "zhiji_audio"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    # 每次用唯一文件名，避免并发请求互相覆盖
                    downloaded_audio = tmp_dir / f"douyin_{uuid4().hex}.mp3"
                    with httpx.Client(timeout=60, follow_redirects=True) as client:
                        resp = client.get(
                            audio_urls[0],
                            cookies=page_cookies,
                            headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
                        )
                        resp.raise_for_status()
                        downloaded_audio.write_bytes(resp.content)
                except (httpx.HTTPError, OSError):
                    downloaded_audio = None

            # 转写音频
            if downloaded_audio and downloaded_audio.exists() and downloaded_audio.stat().st_size > 1000:
                report("音频下载完成，开始本地转写（faster-whisper）…")
                segments = self._transcribe_audio(downloaded_audio, settings)
                report(f"转写完成，共 {len(segments)} 段")
                if not settings.keep_audio:
                    try:
                        downloaded_audio.unlink(missing_ok=True)
                    except OSError:
                        pass

            # 构造返回数据
            raw_data = {
                "desc": desc,
                "audio_urls": audio_urls,
                "video_urls": video_urls,
            }

            body_parts = [title]
            if desc:
                body_parts.append(desc)

            body = "\n\n".join(filter(None, body_parts))

            metadata = Metadata(
                title=title,
                author=author or None,
                raw=raw_data,
            )

            return ContentBundle(
                ref=ref,
                metadata=metadata,
                body_text=body or None,
                segments=segments,
            )

        except Exception as exc:
            raise PlatformFetchError(
                f"抖音页面渲染失败: {exc}",
                platform=self.platform.value,
                hint="检查网络后重试",
            ) from exc

    def get_audio_url(self, ref: ContentRef) -> str | None:
        """获取音频 URL（供外部调用下载音频做语音转文字）。"""
        bundle = self.fetch(ref)
        audio_urls = bundle.metadata.raw.get("audio_urls", [])
        return audio_urls[0] if audio_urls else None

    def _transcribe_audio(
        self, audio_path: Path, settings: AppSettings
    ) -> list[TranscriptSegment]:
        """用与全局设置一致的 faster-whisper 参数将音频转为文字。"""
        from zhiji.errors import TranscriptMissingError
        from zhiji.transcription.engine import transcribe_file

        try:
            return transcribe_file(audio_path, settings)
        except TranscriptMissingError:
            return []

    def search(self, keyword: str, limit: int = 10) -> list:
        raise PlatformFetchError(
            "抖音搜索需要登录态 / Cookie",
            platform=self.platform.value,
            hint="请在 Web 配置页或 DOUYIN_COOKIE_FILE 中配置 Cookie",
        )

    def _resolve_short_url(self, url: str) -> str:
        try:
            with httpx.Client(follow_redirects=True, timeout=15) as client:
                response = client.get(url, headers={"User-Agent": _UA})
            return str(response.url)
        except httpx.HTTPError as exc:
            raise PlatformFetchError(f"抖音短链解析失败: {exc}", platform=self.platform.value) from exc


def login_with_browser() -> None:
    """打开浏览器让用户手动登录抖音，然后从浏览器收割 Cookie。"""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlatformFetchError(
            "Playwright 尚未安装",
            platform="douyin",
            hint="运行 pip install playwright 后重试",
        ) from exc

    from zhiji.platforms.douyin_cookies import save_profile

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=_UA)
        page = context.new_page()

        page.goto("https://www.douyin.com", wait_until="domcontentloaded")

        # 等待用户登录并关闭浏览器
        try:
            page.wait_for_event("close", timeout=600000)
        except PlaywrightError:
            # 等待超时或页面关闭即视为登录流程结束
            pass

        # 收割 Cookie
        cookies = context.cookies()
        cookie_parts = []
        for c in cookies:
            if ".douyin.com" in c.get("domain", ""):
                cookie_parts.append(f"{c['name']}={c['value']}")
        cookie_str = "; ".join(cookie_parts)

        browser.close()

    if not cookie_str:
        raise PlatformFetchError("未捕获到抖音 Cookie，登录可能未完成", platform="douyin")

    return save_profile(cookie_str, _UA)
