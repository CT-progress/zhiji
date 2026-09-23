"""URL -> 平台适配器解析。"""

from __future__ import annotations

from urllib.parse import urlparse

from zhiji.errors import InputUnsupportedError
from zhiji.models import Platform
from zhiji.platforms.base import PlatformAdapter
from zhiji.platforms.bilibili import BilibiliAdapter
from zhiji.platforms.douyin import DouyinAdapter
from zhiji.platforms.zhihu import ZhihuAdapter

_RULES: list[tuple[tuple[str, ...], Platform]] = [
    (("bilibili.com", "b23.tv"), Platform.BILIBILI),
    (("zhihu.com",), Platform.ZHIHU),
    (("douyin.com", "iesdouyin.com"), Platform.DOUYIN),
]


def detect_platform(url: str) -> Platform:
    host = (urlparse(url).hostname or "").lower()
    for domains, platform in _RULES:
        if any(domain in host or host.endswith("." + domain) for domain in domains):
            return platform
    raise InputUnsupportedError(f"暂不支持该链接: {url}", hint="支持 B 站 / 知乎 / 抖音链接")


def resolve_adapter(url: str) -> PlatformAdapter:
    platform = detect_platform(url)
    adapters: dict[Platform, type[PlatformAdapter]] = {
        Platform.BILIBILI: BilibiliAdapter,
        Platform.ZHIHU: ZhihuAdapter,
        Platform.DOUYIN: DouyinAdapter,
    }
    adapter_cls = adapters.get(platform)
    if adapter_cls is None:
        raise InputUnsupportedError(f"平台未实现适配器: {platform.value}")
    return adapter_cls()