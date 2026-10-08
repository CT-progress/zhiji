"""小红书适配器与登录态的离线测试。"""

from __future__ import annotations

import pytest

from zhiji.errors import InputUnsupportedError
from zhiji.models import ContentRef, ContentType, Platform
from zhiji.platforms.registry import detect_platform, resolve_adapter
from zhiji.platforms.xiaohongshu import (
    XiaohongshuAdapter,
    _bundle_from_note,
    _playwright_cookies,
)
from zhiji.platforms.xiaohongshu_cookies import XiaohongshuProfile, load_profile, save_profile


def test_xiaohongshu_registry_and_parse_urls() -> None:
    assert detect_platform("https://www.xiaohongshu.com/explore/abc123") == Platform.XIAOHONGSHU
    assert detect_platform("https://xhslink.com/abc123") == Platform.XIAOHONGSHU
    assert isinstance(resolve_adapter("https://www.xiaohongshu.com/explore/abc123"), XiaohongshuAdapter)

    adapter = XiaohongshuAdapter()
    image = adapter.parse_url("https://www.xiaohongshu.com/explore/abc123?xsec_token=x")
    assert image.content_id == "abc123"
    assert image.content_type == ContentType.IMAGE_POST

    video = adapter.parse_url("https://www.xiaohongshu.com/discovery/item/video123")
    assert video.content_id == "video123"
    assert video.content_type == ContentType.IMAGE_POST

    explicit_video = adapter.parse_url("https://www.xiaohongshu.com/video/video123")
    assert explicit_video.content_type == ContentType.VIDEO

    short = adapter.parse_url("https://xhslink.com/abc")
    assert short.content_id == "short"

    with pytest.raises(InputUnsupportedError):
        adapter.parse_url("https://www.xiaohongshu.com/user/profile/abc")


def test_xiaohongshu_bundle_extracts_note_and_video_cover() -> None:
    ref = ContentRef(
        platform=Platform.XIAOHONGSHU,
        content_type=ContentType.IMAGE_POST,
        content_id="abc123",
        source_url="https://www.xiaohongshu.com/explore/abc123",
    )
    note = {
        "title": "标题",
        "desc": "正文",
        "type": "video",
        "time": 1700000000000,
        "user": {"nickname": "作者", "userId": "u1"},
        "video": {"cover": {"url": "https://img.example/cover.jpg"}},
        "tagList": [{"name": "标签"}, "备用标签"],
    }

    bundle = _bundle_from_note(ref, note)

    assert bundle.ref.content_type == ContentType.VIDEO
    assert bundle.metadata.title == "标题"
    assert bundle.metadata.author == "作者"
    assert bundle.metadata.author_url.endswith("/u1")
    assert bundle.metadata.tags == ["标签", "备用标签"]
    assert bundle.metadata.cover_url == "https://img.example/cover.jpg"
    assert bundle.media[0].kind == "video"
    assert bundle.media[0].url == "https://img.example/cover.jpg"


def test_xiaohongshu_bundle_prefers_image_list() -> None:
    ref = ContentRef(
        platform=Platform.XIAOHONGSHU,
        content_type=ContentType.IMAGE_POST,
        content_id="abc123",
        source_url="https://www.xiaohongshu.com/explore/abc123",
    )
    note = {
        "title": "图文",
        "type": "normal",
        "imageList": [{"urlDefault": "https://img.example/1.jpg"}, "https://img.example/2.jpg"],
    }

    bundle = _bundle_from_note(ref, note)

    assert [item.url for item in bundle.media] == [
        "https://img.example/1.jpg",
        "https://img.example/2.jpg",
    ]
    assert bundle.metadata.cover_url == "https://img.example/1.jpg"


def test_xiaohongshu_cookie_prunes_and_requires_web_session(tmp_path) -> None:
    path = tmp_path / "xiaohongshu-profile.json"
    saved = save_profile(
        "web_session=abc; a1=dev; web_session=dup; junk=1; other=2",
        "UA",
        path,
    )

    assert saved.cookie == "web_session=abc; a1=dev"
    loaded = load_profile(path)
    assert loaded is not None
    assert loaded.user_agent == "UA"

    save_profile("a1=dev; junk=1", "UA", path)
    assert load_profile(path) is None


def test_playwright_cookie_conversion() -> None:
    profile = XiaohongshuProfile(cookie="web_session=abc; a1=dev; malformed", user_agent="UA")

    assert _playwright_cookies(profile) == [
        {"name": "web_session", "value": "abc", "domain": ".xiaohongshu.com", "path": "/"},
        {"name": "a1", "value": "dev", "domain": ".xiaohongshu.com", "path": "/"},
    ]
