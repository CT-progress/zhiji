"""Markdown 笔记渲染与写入（按 ``zhiji_id`` 幂等更新）。

同一素材重复处理时不会产生 ``-2.md`` / ``(1).md``：写入前先按 front-matter
里的 ``zhiji_id`` 在输出目录里找已有文件，命中则**原地更新**，未命中才用新
文件名创建。更新时用 ``<!-- zhiji:begin -->`` / ``<!-- zhiji:end -->`` 区段
标记把知记生成的内容包起来——标记**之间**会被覆写，标记**之后**用户手写的
批注保留；旧文件没有标记则整篇覆写并在 ``OutputReceipt.warnings`` 里告警。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from zhiji.errors import OutputWriteError
from zhiji.models import NoteDocument, OutputReceipt

#: 知记生成内容的区段标记；标记之间会被更新覆写，标记之后留给用户手写批注。
BEGIN_MARKER = "<!-- zhiji:begin -->"
END_MARKER = "<!-- zhiji:end -->"


class MarkdownWriter:
    def write(
        self, note: NoteDocument, output_dir: Path, artifact_dir: Path
    ) -> OutputReceipt:
        output_dir.mkdir(parents=True, exist_ok=True)
        zhiji_id = note.frontmatter.zhiji_id
        existing = _find_by_zhiji_id(output_dir, zhiji_id)
        rendered = render_note(note)
        warnings: list[str] = []
        if existing is not None:
            target = existing
            text, preserved = _merge_note(
                existing.read_text(encoding="utf-8"), rendered
            )
            updated = True
            if not preserved:
                warnings.append(
                    "原文件没有 zhiji 区段标记，已整篇覆写；如需保留手写批注，"
                    "请把批注写在 <!-- zhiji:end --> 之后。"
                )
        else:
            target = _unique_path(_new_filename(note, output_dir))
            text = rendered
            updated = False
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise OutputWriteError(f"笔记写入失败: {target}") from exc
        return OutputReceipt(
            note_path=target,
            artifact_dir=artifact_dir,
            platform=note.frontmatter.platform,
            content_id=Path(note.raw_transcript_ref).name,
            title=note.frontmatter.title,
            updated=updated,
            warnings=warnings,
        )


def render_note(note: NoteDocument) -> str:
    """渲染整篇笔记：front-matter + 区段标记包裹的正文。"""

    frontmatter = _render_frontmatter(note)
    body = _render_body(note)
    return f"{frontmatter}\n\n{BEGIN_MARKER}\n{body}{END_MARKER}\n"


def _render_frontmatter(note: NoteDocument) -> str:
    front = note.frontmatter
    data = {
        "zhiji_id": front.zhiji_id,
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
        {k: v for k, v in data.items() if v is not None},
        allow_unicode=True,
        sort_keys=False,
    )
    return f"---\n{yaml_text}---"


def _render_body(note: NoteDocument) -> str:
    front = note.frontmatter
    lines = [f"# {front.title}", ""]
    source_line = f"> 来源：[{front.source}]({front.source})"
    if front.author:
        source_line += f" ｜ 作者：{front.author}"
    lines.append(source_line)
    lines.append("")
    for section in note.sections:
        if section.heading:
            lines.append(f"## {section.heading}")
            lines.append("")
        if section.body:
            lines.append(section.body)
            lines.append("")
    if note.raw_transcript_ref:
        lines.append("## 原文存证")
        lines.append("")
        lines.append(f"- 原始链接：{front.source}")
        lines.append(f"- 完整字幕 / 正文：`{note.raw_transcript_ref}`")
        lines.append(
            "- 说明：正文中明确标注为“补充 / 延伸”的内容为助手整理的背景知识，"
            "并非原始素材内容。"
        )
        lines.append("")
    return "\n".join(lines)


def _merge_note(existing: str, rendered: str) -> tuple[str, bool]:
    """把渲染结果合并进已有文件，保留区段标记之后的手写内容。

    返回 (合并后的正文, 是否保留了区段外的用户内容)。已有文件里找不到结束
    标记时（旧笔记或用户删了标记），整篇覆写并返回 ``preserved=False``。
    """

    end_index = existing.find(END_MARKER)
    if end_index == -1:
        return rendered, False
    tail = existing[end_index + len(END_MARKER) :]
    user_content = tail.lstrip("\r\n")
    if not user_content.strip():
        return rendered, True
    return f"{rendered.rstrip()}\n\n{user_content.rstrip()}\n", True


def _find_by_zhiji_id(output_dir: Path, zhiji_id: str) -> Path | None:
    """在输出目录里按 ``zhiji_id`` 查找已有笔记；找不到返回 ``None``。"""

    for path in sorted(output_dir.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # 廉价预筛：id 不在文件里就跳过，避免对整库解析 YAML。
        if zhiji_id not in raw:
            continue
        if _read_zhiji_id(raw) == zhiji_id:
            return path
    return None


def _read_zhiji_id(text: str) -> str | None:
    block = _frontmatter_block(text)
    if block is None:
        return None
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("zhiji_id")
    return value if isinstance(value, str) else None


def _frontmatter_block(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


def _new_filename(note: NoteDocument, output_dir: Path) -> Path:
    safe_title = _safe_filename(note.frontmatter.title)
    author = _safe_filename(note.frontmatter.author or "未知作者")
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return output_dir / f"{today} - {author} - {safe_title}.md"


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