from types import SimpleNamespace

import pytest

from zhiji.models import (
    AppSettings,
    ContentBundle,
    ContentType,
    Metadata,
    Platform,
    TranscriptSegment,
)
from zhiji.platforms.bilibili import BilibiliAdapter, _pick_subtitle
from zhiji.platforms.bilibili_cookies import load_profile, prune_cookie, save_profile
from zhiji.platforms.douyin import DouyinAdapter
from zhiji.platforms.registry import detect_platform
from zhiji.platforms.zhihu import ZhihuAdapter, _api_url
from zhiji.platforms.zhihu_cookies import ZhihuProfile


def test_detect_platform():
    assert detect_platform("https://www.bilibili.com/video/BV1xx411c7mD") == Platform.BILIBILI
    assert detect_platform("https://www.zhihu.com/question/123") == Platform.ZHIHU
    assert detect_platform("https://v.douyin.com/abc123/") == Platform.DOUYIN


def test_bilibili_parse_url():
    ref = BilibiliAdapter().parse_url("https://www.bilibili.com/video/BV1xx411c7mD?p=1")
    assert ref.platform == Platform.BILIBILI
    assert ref.content_id == "BV1xx411c7mD"
    assert ref.content_type == ContentType.VIDEO


def test_bilibili_prune_cookie():
    raw = "junk=1; SESSDATA=abc123; buvid3=dev; bili_jct=csrf; other=x; SESSDATA=dup"
    pruned = prune_cookie(raw)
    assert "SESSDATA=abc123" in pruned
    assert "buvid3=dev" in pruned
    assert "bili_jct=csrf" in pruned
    assert "junk" not in pruned
    assert "other" not in pruned
    assert pruned.count("SESSDATA=") == 1


def test_bilibili_profile_roundtrip(tmp_path):
    path = tmp_path / "bilibili-profile.json"
    saved = save_profile("SESSDATA=abc; buvid3=dev; junk=1", "UA", path)
    assert saved.cookie == "SESSDATA=abc; buvid3=dev"

    profile = load_profile(path)
    assert profile is not None
    assert "SESSDATA=abc" in profile.cookie
    assert profile.user_agent == "UA"

    assert load_profile(tmp_path / "missing.json") is None


def test_bilibili_load_profile_requires_sessdata(tmp_path):
    path = tmp_path / "bilibili-profile.json"
    save_profile("buvid3=dev; bili_jct=csrf", "UA", path)
    # 保存时白名单过滤掉缺失 SESSDATA 的 Cookie 后，读取视为未配置
    assert load_profile(path) is None


def test_bilibili_pick_subtitle_prefers_chinese():
    subs = [
        {"lan": "en-US", "subtitle_url": "//sub/en.json"},
        {"lan": "zh-Hans", "subtitle_url": "//sub/zh.json"},
    ]
    assert _pick_subtitle(subs)["lan"] == "zh-Hans"
    assert _pick_subtitle([{"lan": "ai-zh"}, {"lan": "en-US"}])["lan"] == "ai-zh"
    assert _pick_subtitle([{"lan": "en-US"}])["lan"] == "en-US"
    assert _pick_subtitle([]) is None


def test_bilibili_auth_headers(monkeypatch):
    import zhiji.platforms.bilibili as bili

    monkeypatch.setattr(bili, "load_profile", lambda: None)
    assert BilibiliAdapter._auth_headers() == {}

    profile = SimpleNamespace(cookie="SESSDATA=abc")
    monkeypatch.setattr(bili, "load_profile", lambda: profile)
    assert BilibiliAdapter._auth_headers() == {"Cookie": "SESSDATA=abc"}


def test_bilibili_fetch_subtitles_prefers_chinese(monkeypatch):
    adapter = BilibiliAdapter()
    player = {
        "data": {
            "subtitle": {
                "subtitles": [
                    {"lan": "en-US", "subtitle_url": "//sub/en.json"},
                    {"lan": "zh-Hans", "subtitle_url": "//sub/zh.json"},
                ]
            }
        }
    }
    zh_body = {"body": [{"from": 0.0, "to": 1.5, "content": "你好"}]}
    calls: list[str] = []

    def fake_get_json(url, *, params=None, headers=None):
        calls.append(url)
        if "player" in url:
            return player
        return zh_body

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    segments = adapter._fetch_subtitles("BV1xx411c7mD", 123)
    assert [seg.text for seg in segments] == ["你好"]
    assert segments[0].end == 1.5
    assert "https://sub/zh.json" in calls
    assert "https://sub/en.json" not in calls


def test_zhihu_parse_url():
    ref = ZhihuAdapter().parse_url("https://www.zhihu.com/question/123456789/answer/987654321")
    assert ref.platform == Platform.ZHIHU
    assert ref.content_id == "q123456789"
    assert ref.content_type == ContentType.ANSWER

    question = ZhihuAdapter().parse_url("https://www.zhihu.com/question/123456789")
    assert question.content_id == "q123456789"
    assert question.content_type == ContentType.QUESTION


def test_zhihu_parse_article_url():
    ref = ZhihuAdapter().parse_url("https://zhuanlan.zhihu.com/p/2068355085243871946")
    assert ref.content_type == ContentType.ARTICLE
    assert ref.content_id == "p2068355085243871946"

    ref2 = ZhihuAdapter().parse_url("https://www.zhihu.com/article/123456")
    assert ref2.content_type == ContentType.ARTICLE
    assert ref2.content_id == "p123456"

    assert _api_url(ref) == "https://www.zhihu.com/api/v4/articles/2068355085243871946"


def test_zhihu_fetch_uses_api(monkeypatch):
    adapter = ZhihuAdapter()
    ref = adapter.parse_url("https://zhuanlan.zhihu.com/p/123")
    expected = ContentBundle(ref=ref, metadata=Metadata(title="文章"), body_text="正文")
    profile = ZhihuProfile(
        cookie="d_c0=abc123; z_c0=xyz",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Edg/120",
        saved_at="2026-01-01T00:00:00+00:00",
        verified=True,
    )
    monkeypatch.setattr("zhiji.platforms.zhihu.load_profile", lambda: profile)
    monkeypatch.setattr("zhiji.platforms.zhihu._fetch_content", lambda ref_, profile_: expected)

    assert adapter.fetch(ref) == expected


def test_douyin_parse_url():
    ref = DouyinAdapter().parse_url("https://www.douyin.com/video/7123456789012345678")
    assert ref.platform == Platform.DOUYIN
    assert ref.content_id.startswith("712")


def test_douyin_transcribe_uses_global_settings(monkeypatch, tmp_path):
    adapter = DouyinAdapter()
    captured: dict = {}

    def fake_transcribe(path, settings):
        captured["path"] = path
        captured["settings"] = settings
        return [TranscriptSegment(start=0.0, end=1.0, text="你好")]

    monkeypatch.setattr("zhiji.transcription.engine.transcribe_file", fake_transcribe)
    settings = AppSettings(whisper_model="base", whisper_device="cpu")

    segments = adapter._transcribe_audio(tmp_path / "a.mp3", settings)

    assert segments[0].text == "你好"
    assert captured["settings"].whisper_model == "base"


def test_registry_supports_short_links_and_subdomains():
    assert detect_platform("https://b23.tv/abc123") == Platform.BILIBILI
    assert detect_platform("https://www.iesdouyin.com/share/video/123") == Platform.DOUYIN
    assert detect_platform("https://ZHIHU.COM/question/123") == Platform.ZHIHU


def test_registry_rejects_lookalike_domain():
    from zhiji.errors import InputUnsupportedError

    with pytest.raises(InputUnsupportedError):
        detect_platform("https://evilbilibili.com/video/BV1xx411c7mD")


def test_resolve_adapter_returns_platform_adapter():
    from zhiji.platforms.registry import resolve_adapter

    assert isinstance(resolve_adapter("https://www.bilibili.com/video/BV1xx411c7mD"), BilibiliAdapter)
    assert isinstance(resolve_adapter("https://www.zhihu.com/question/123"), ZhihuAdapter)
    assert isinstance(resolve_adapter("https://www.douyin.com/video/7123456789012345678"), DouyinAdapter)


def test_adapter_rejects_unrecognised_urls():
    from zhiji.errors import InputUnsupportedError

    with pytest.raises(InputUnsupportedError):
        BilibiliAdapter().parse_url("https://www.bilibili.com/read/cv123")
    with pytest.raises(InputUnsupportedError):
        DouyinAdapter().parse_url("https://www.douyin.com/user/abc")
