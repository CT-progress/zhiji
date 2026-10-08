"""主流水线：链接 / 关键词 -> Markdown 笔记。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from zhiji.config import ConfigManager
from zhiji.errors import ConfigError, LLMError
from zhiji.llm.client import LLMClient
from zhiji.llm.prompts import (
    build_chunk_summary_messages,
    build_note_messages,
    build_reduce_messages,
    needs_chunking,
    sanitize_note_markdown,
    split_text,
)
from zhiji.models import (
    ContentBundle,
    ContentType,
    NoteDocument,
    NoteFrontmatter,
    NoteSection,
    OutputReceipt,
    Platform,
    TranscriptSegment,
)
from zhiji.platforms.registry import resolve_adapter
from zhiji.transcription.engine import ensure_segments
from zhiji.writers.markdown import MarkdownWriter

_PLATFORM_LABELS = {
    "bilibili": "B 站",
    "zhihu": "知乎",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}

class NotePipeline:
    def __init__(self, config_manager: ConfigManager | None = None) -> None:
        self.config = config_manager or ConfigManager()
        self.writer = MarkdownWriter()

    def run(
        self,
        url: str,
        model_name: str | None = None,
        output_dir: str | None = None,
        stream_callback: Callable[[str], None] | None = None,
        progress_callback: Callable[[str, str, str], None] | None = None,
    ) -> OutputReceipt:
        def report(stage: str, message: str, status: str = "start") -> None:
            if progress_callback:
                progress_callback(stage, message, status)

        report("parse", "解析链接…")
        adapter = resolve_adapter(url)
        ref = adapter.parse_url(url)
        report("parse", f"识别为{_PLATFORM_LABELS.get(ref.platform.value, ref.platform.value)}内容", "ok")

        report("fetch", "抓取内容…")
        bundle = adapter.fetch(ref, progress=lambda msg: report("fetch", msg))
        report("fetch", "内容抓取完成", "ok")

        warnings: list[str] = []
        if bundle.segments:
            report("transcribe", f"已获取字幕 / 转写文本（{len(bundle.segments)} 段）", "ok")
        elif bundle.ref.content_type == ContentType.VIDEO:
            report("transcribe", "下载音频并本地转写（faster-whisper）…")
        elif bundle.body_text:
            report("transcribe", "该内容无音频，直接使用正文文本", "ok")
        else:
            report("transcribe", "下载音频并本地转写（faster-whisper）…")
        segments = ensure_segments(bundle, self.config.get().settings, warnings)
        if segments and not bundle.segments:
            report("transcribe", f"转写完成，共 {len(segments)} 段", "ok")

        # 兜底转写（transcribe_url）返回的分段不在 bundle 里，回写以保证 plain_text() 能取到
        if segments and not bundle.segments:
            bundle.segments = segments

        # 转写文本在生成笔记前先落地：artifacts 存 JSON 存证，transcripts 存可读文本
        base_dir = self.config.resolve_output_dir(output_dir)
        artifact_dir = base_dir / "artifacts" / f"{bundle.ref.platform.value}_{bundle.ref.content_id}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "meta.json").write_text(
            json.dumps(bundle.source_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (artifact_dir / "transcript.json").write_text(
            json.dumps(
                {"segments": [seg.model_dump() for seg in segments], "body": bundle.body_text},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        transcript_path = self._write_transcript(bundle, segments, base_dir)
        if transcript_path:
            report("transcribe", f"转写文本已保存：{transcript_path}", "ok")

        model = self.config.get_model(model_name)
        if model is None:
            raise ConfigError("尚未配置可用模型", hint="请在 Web 配置页或 config/zhiji.json 中添加模型")

        report("generate", "AI 正在生成笔记…")
        client = LLMClient(model)
        text = bundle.plain_text()
        messages = self._build_generation_messages(client, bundle, text, report)

        # 流式输出 or 普通输出
        if stream_callback:
            response = ""
            for chunk in client.stream_chunks(messages):
                response += chunk
                stream_callback(chunk)
        else:
            response = client.complete(messages)
        report("generate", "笔记生成完成", "ok")

        note_body = sanitize_note_markdown(response)
        if not note_body:
            raise LLMError("LLM 未返回有效笔记内容")
        report("save", "保存笔记文件…")
        note = self._build_note(bundle, segments, note_body)
        receipt = self.writer.write(note, base_dir, artifact_dir)
        receipt.transcript_path = transcript_path
        receipt.warnings.extend(warnings)
        report("save", "笔记已保存", "ok")
        return receipt

    def _build_generation_messages(
        self,
        client: LLMClient,
        bundle: ContentBundle,
        text: str,
        report: Callable[[str, str, str], None],
    ) -> list[dict]:
        """素材过长时先逐段摘要、再汇总生成，避免超出模型上下文。"""

        if not needs_chunking(text):
            return build_note_messages(bundle.metadata, text)
        chunks = split_text(text)
        total = len(chunks)
        report("generate", f"素材较长（约 {len(text)} 字），分 {total} 段逐段总结…")
        summaries: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            report("generate", f"正在分块总结第 {index}/{total} 段…")
            summaries.append(
                client.complete(
                    build_chunk_summary_messages(bundle.metadata, chunk, index, total)
                )
            )
        report("generate", "分块总结完成，正在汇总生成笔记…")
        return build_reduce_messages(bundle.metadata, summaries)

    def search(
        self,
        keyword: str,
        platform: Platform,
        limit: int = 10,
    ) -> list:
        if platform not in {Platform.BILIBILI, Platform.ZHIHU, Platform.DOUYIN}:
            raise ConfigError(f"平台搜索未实现: {platform.value}")
        adapter = resolve_adapter(f"https://{platform.value}.com/search")
        return adapter.search(keyword, limit=limit)

    def _write_transcript(
        self,
        bundle: ContentBundle,
        segments: list[TranscriptSegment],
        base_dir: Path,
    ) -> Path | None:
        """把转写文本/正文落成可读 txt，放在输出目录的 transcripts/ 下。"""
        lines: list[str] = []
        if segments:
            lines = [f"[{_format_ts(seg.start)}] {seg.text}" for seg in segments]
        elif bundle.body_text:
            lines = [bundle.body_text]
        if not lines:
            return None
        transcripts_dir = base_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        path = transcripts_dir / f"{bundle.ref.platform.value}_{bundle.ref.content_id}.txt"
        header = (
            f"# {bundle.metadata.title}\n"
            f"# 来源：{bundle.ref.source_url}\n"
            f"# 平台：{bundle.ref.platform.value}\n\n"
        )
        path.write_text(header + "\n".join(lines), encoding="utf-8")
        return path

    def _build_note(
        self,
        bundle: ContentBundle,
        segments: list[TranscriptSegment],
        note_body: str,
    ) -> NoteDocument:
        meta = bundle.metadata
        ref = bundle.ref
        raw_ref = f"artifacts/{ref.platform.value}_{ref.content_id}/transcript.json"
        frontmatter = NoteFrontmatter(
            title=meta.title,
            source=ref.source_url,
            platform=ref.platform.value,
            content_type=ref.content_type.value,
            author=meta.author,
            published=meta.published_at.isoformat() if meta.published_at else None,
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
            tags=meta.tags,
            summary=_extract_summary(note_body),
        )
        sections = [NoteSection(heading="", body=note_body)]
        return NoteDocument(frontmatter=frontmatter, sections=sections, raw_transcript_ref=raw_ref)


def _format_ts(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _extract_summary(markdown: str) -> str | None:
    """从笔记正文中提取“一句话总结”部分的首行内容，用于 frontmatter。"""
    lines = markdown.splitlines()
    in_summary = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_summary = "一句话总结" in stripped
            continue
        if in_summary and stripped and not stripped.startswith("#"):
            return stripped
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return None