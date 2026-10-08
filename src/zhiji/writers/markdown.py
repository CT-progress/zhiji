"""Markdown 笔记渲染与写入。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from zhiji.errors import OutputWriteError
from zhiji.models import NoteDocument, OutputReceipt


class MarkdownWriter:
    def write(self, note: NoteDocument, output_dir: Path, artifact_dir: Path) -> OutputReceipt:
        safe_title = _safe_filename(note.frontmatter.title)
        author = note.frontmatter.author or "未知作者"
        # 与 frontmatter.created 一致：都基于带本地时区的当前时间
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        filename = f"{today} - {_safe_filename(author)} - {safe_title}.md"
        target = _unique_path(output_dir / filename)
        try:
            target.write_text(render_note(note), encoding="utf-8")
        except OSError as exc:
            raise OutputWriteError(f"笔记写入失败: {target}") from exc
        return OutputReceipt(
            note_path=target,
            artifact_dir=artifact_dir,
            platform=note.frontmatter.platform,
            content_id=Path(note.raw_transcript_ref).name,
            title=note.frontmatter.title,
        )


def render_note(note: NoteDocument) -> str:
    front = note.frontmatter
    body = [f"# {front.title}", ""]
    source_line = f"> 来源：[{front.source}]({front.source})"
    if front.author:
        source_line += f" ｜ 作者：{front.author}"
    body.append(source_line)
    body.append("")
    for section in note.sections:
        text = section.body
        if section.heading:
            body.append(f"## {section.heading}")
            body.append("")
        if text:
            body.append(text)
            body.append("")
    if note.raw_transcript_ref:
        body.append("## 原文存证")
        body.append("")
        body.append(f"- 原始链接：{front.source}")
        body.append(f"- 完整字幕 / 正文：`{note.raw_transcript_ref}`")
        body.append(
            "- 说明：正文中明确标注为“补充 / 延伸”的内容为助手整理的背景知识，"
            "并非原始素材内容。"
        )
        body.append("")
    frontmatter = {
        "title": front.title,
        "source": front.source,
        "platform": front.platform,
        "content_type": front.content_type,
        "author": front.author,
        "published": front.published,
        "created": front.created,
        "tags": front.tags,
        "summary": front.summary,
    }
    yaml_text = yaml.safe_dump(
        {k: v for k, v in frontmatter.items() if v is not None},
        allow_unicode=True,
        sort_keys=False,
    )
    return "---\n" + yaml_text + "---\n\n" + "\n".join(body)


def _unique_path(target: Path) -> Path:
    """同名文件已存在时追加 -2 / -3 …，避免静默覆盖已有笔记。"""

    if not target.exists():
        return target
    index = 2
    while True:
        candidate = target.with_name(f"{target.stem}-{index}{target.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip()
    cleaned = re.sub(r"[\s-]+", "-", cleaned)
    return cleaned[:120] or "untitled"