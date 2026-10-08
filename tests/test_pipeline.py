"""主流水线分块总结与输出时间一致性的测试（离线，全部 mock）。"""

from __future__ import annotations

from datetime import datetime

import pytest

from zhiji.config import ConfigManager
from zhiji.errors import GenerationCancelledError
from zhiji.llm.prompts import CHUNK_SIZE, CHUNK_THRESHOLD, needs_chunking, split_text
from zhiji.models import (
    ContentBundle,
    ContentRef,
    ContentType,
    Metadata,
    NoteDocument,
    NoteFrontmatter,
    NoteSection,
    Platform,
)
from zhiji.pipeline import NotePipeline
from zhiji.writers.markdown import MarkdownWriter


def _bundle() -> ContentBundle:
    ref = ContentRef(
        platform=Platform.ZHIHU,
        content_type=ContentType.ARTICLE,
        content_id="1",
        source_url="https://zhuanlan.zhihu.com/p/1",
    )
    return ContentBundle(ref=ref, metadata=Metadata(title="标题"))


class _StubClient:
    """记录 complete 调用次数，避免真实网络请求。"""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], **_kwargs) -> str:
        self.calls.append(messages)
        return f"第 {len(self.calls)} 段摘要"


def test_needs_chunking_and_split_text():
    assert not needs_chunking("短" * CHUNK_THRESHOLD)
    assert needs_chunking("长" * (CHUNK_THRESHOLD + 1))

    chunks = split_text("a" * (CHUNK_SIZE * 2 + 5))
    assert [len(c) for c in chunks] == [CHUNK_SIZE, CHUNK_SIZE, 5]

    # size <= 0 时不分块
    assert split_text("abc", size=0) == ["abc"]


def test_short_text_uses_single_request(tmp_path):
    pipeline = NotePipeline(ConfigManager(tmp_path / "zhiji.json"))
    client = _StubClient()
    text = "短素材" * 10
    messages = pipeline._build_generation_messages(client, _bundle(), text, lambda *_a: None)

    assert client.calls == []
    assert "source_text" in messages[1]["content"]


def test_long_text_uses_map_reduce(tmp_path):
    pipeline = NotePipeline(ConfigManager(tmp_path / "zhiji.json"))
    client = _StubClient()
    reports: list[tuple[str, str]] = []
    text = "长" * (CHUNK_THRESHOLD + 1)

    messages = pipeline._build_generation_messages(
        client, _bundle(), text, lambda *args: reports.append(args)
    )

    assert len(client.calls) == 3  # 24001 -> 12000 / 12000 / 1
    assert "chunk_summaries" in messages[1]["content"]
    assert "以上长素材已被切分" in messages[1]["content"]
    assert any("分块总结第" in message for _stage, message in reports)


def test_markdown_filename_uses_local_date(tmp_path):
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    note = NoteDocument(
        frontmatter=NoteFrontmatter(
            title="标题",
            source="https://example.com",
            platform="zhihu",
            content_type="article",
            created=created,
        ),
        sections=[NoteSection(heading="", body="正文")],
        raw_transcript_ref="artifacts/zhihu_1/transcript.json",
    )

    receipt = MarkdownWriter().write(note, tmp_path, tmp_path)

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    assert receipt.note_path.name.startswith(today)


def test_run_honours_cancel_callback(tmp_path):
    pipeline = NotePipeline(ConfigManager(tmp_path / "zhiji.json"))
    with pytest.raises(GenerationCancelledError):
        pipeline.run(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            cancel_callback=lambda: True,
        )


def test_chunk_summary_stops_when_cancelled(tmp_path):
    pipeline = NotePipeline(ConfigManager(tmp_path / "zhiji.json"))
    client = _StubClient()
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # 第一段正常总结，第二段前被取消

    text = "长" * (CHUNK_THRESHOLD + 1)
    with pytest.raises(GenerationCancelledError):
        pipeline._build_generation_messages(client, _bundle(), text, lambda *_a: None, cancel)
    assert len(client.calls) == 1


def test_markdown_writer_avoids_overwrite(tmp_path):
    created = datetime.now().astimezone().isoformat(timespec="seconds")

    def make_note() -> NoteDocument:
        return NoteDocument(
            frontmatter=NoteFrontmatter(
                title="标题",
                source="https://example.com",
                platform="zhihu",
                content_type="article",
                created=created,
            ),
            sections=[NoteSection(heading="", body="正文")],
            raw_transcript_ref="artifacts/zhihu_1/transcript.json",
        )

    writer = MarkdownWriter()
    first = writer.write(make_note(), tmp_path, tmp_path)
    second = writer.write(make_note(), tmp_path, tmp_path)
    third = writer.write(make_note(), tmp_path, tmp_path)

    assert first.note_path == tmp_path / first.note_path.name
    assert second.note_path.name.endswith("-2.md")
    assert third.note_path.name.endswith("-3.md")
    assert len({first.note_path, second.note_path, third.note_path}) == 3
    assert all(path.exists() for path in (first.note_path, second.note_path, third.note_path))
