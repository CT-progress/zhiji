"""平台适配器抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from zhiji.errors import PlatformFetchError
from zhiji.models import ContentBundle, ContentRef, Platform, SearchResult


class PlatformAdapter(ABC):
    platform: Platform

    @abstractmethod
    def parse_url(self, url: str) -> ContentRef:
        """从链接解析出平台无关的 ContentRef。"""

    @abstractmethod
    def fetch(self, ref: ContentRef, progress: Callable[[str], None] | None = None) -> ContentBundle:
        """获取元数据、正文/字幕与媒体信息。progress 用于上报抓取进度。"""

    def search(self, keyword: str, limit: int = 10) -> list[SearchResult]:
        raise PlatformFetchError(
            f"{self.platform.value} 搜索暂不可用",
            platform=self.platform.value,
            hint="请在对应平台适配器中接入搜索接口",
        )