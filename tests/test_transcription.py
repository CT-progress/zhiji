import pytest

from zhiji.errors import TranscriptMissingError
from zhiji.models import (
    AppSettings,
    ContentBundle,
    ContentRef,
    ContentType,
    Metadata,
    Platform,
    TranscriptSegment,
)
from zhiji.transcription import engine


def _bundle(
    content_type: ContentType = ContentType.VIDEO,
    body_text: str | None = None,
    segments: list[TranscriptSegment] | None = None,
) -> ContentBundle:
    ref = ContentRef(
        platform=Platform.BILIBILI,
        content_type=content_type,
        content_id="BV1xx411c7mD",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
    )
    return ContentBundle(
        ref=ref,
        metadata=Metadata(title="t"),
        body_text=body_text,
        segments=segments or [],
    )


def test_existing_segments_returned_without_transcribe(monkeypatch):
    seg = TranscriptSegment(start=0.0, end=1.0, text="字幕")
    monkeypatch.setattr(
        engine,
        "transcribe_url",
        lambda *a, **k: pytest.fail("不应触发音频转写"),
    )
    assert engine.ensure_segments(_bundle(segments=[seg]), AppSettings(), []) == [seg]


def test_video_with_body_text_still_transcribes(monkeypatch):
    """视频简介非空也不能短路音频转写（B 站场景回归测试）。"""
    called: list[str] = []

    def fake_transcribe(url, settings):
        called.append(url)
        return [TranscriptSegment(start=0.0, end=2.0, text="口播内容")]

    monkeypatch.setattr(engine, "transcribe_url", fake_transcribe)
    bundle = _bundle(body_text="视频简介")
    segments = engine.ensure_segments(bundle, AppSettings(), [])
    assert called == [bundle.ref.source_url]
    assert [s.text for s in segments] == ["口播内容"]


def test_video_transcribe_failure_falls_back_to_body_text(monkeypatch):
    def fail(url, settings):
        raise TranscriptMissingError("下载失败")

    monkeypatch.setattr(engine, "transcribe_url", fail)
    warnings: list[str] = []
    segments = engine.ensure_segments(_bundle(body_text="视频简介"), AppSettings(), warnings)
    assert segments == []
    assert any("降级" in w for w in warnings)


def test_video_transcribe_failure_without_body_text_raises(monkeypatch):
    def fail(url, settings):
        raise TranscriptMissingError("下载失败")

    monkeypatch.setattr(engine, "transcribe_url", fail)
    with pytest.raises(TranscriptMissingError):
        engine.ensure_segments(_bundle(body_text=None), AppSettings(), [])


def test_non_video_with_body_text_skips_transcribe(monkeypatch):
    """图文内容（知乎文章/回答）无音频，保持直接使用正文。"""
    monkeypatch.setattr(
        engine,
        "transcribe_url",
        lambda *a, **k: pytest.fail("图文内容不应触发音频转写"),
    )
    warnings: list[str] = []
    segments = engine.ensure_segments(
        _bundle(content_type=ContentType.ARTICLE, body_text="正文"), AppSettings(), warnings
    )
    assert segments == []
    assert any("无音频" in w for w in warnings)
