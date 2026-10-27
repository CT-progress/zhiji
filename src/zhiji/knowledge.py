"""知识库本地索引：文本提取、切片、哈希向量与混合排序。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import PurePath

VECTOR_DIM = 128
MAX_IMPORT_CHARS = 2_000_000
CHUNK_SIZE = 900
CHUNK_OVERLAP = 160

TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm"}
)
BINARY_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"})

_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
_SPACE_RE = re.compile(r"[ \t\u3000]+")


class KnowledgeImportError(ValueError):
    """资料内容无法导入时抛出的用户可读错误。"""


class _HTMLTextExtractor(HTMLParser):
    _BLOCKS = frozenset(
        {"p", "div", "section", "article", "li", "br", "tr", "h1", "h2", "h3", "h4"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def normalize_text(value: str) -> str:
    """统一换行与空白，同时保留段落结构。"""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "").replace("\ufeff", "")
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_document_text(filename: str, content: str) -> tuple[str, str]:
    """返回 ``(规范化文本, 扩展名)``，当前只接受可本地解析的文本格式。"""

    if len(content) > MAX_IMPORT_CHARS:
        raise KnowledgeImportError("资料过大，单个文件请控制在 200 万字符以内")
    extension = PurePath(filename).suffix.lower()
    if extension in BINARY_EXTENSIONS:
        raise KnowledgeImportError(
            "当前版本先支持 TXT、Markdown、CSV、JSON 与 HTML；PDF / Office 解析将在后续接入"
        )
    if extension not in TEXT_EXTENSIONS:
        supported = "、".join(sorted(TEXT_EXTENSIONS))
        raise KnowledgeImportError(f"暂不支持该文件类型，当前支持：{supported}")
    if extension in {".html", ".htm"}:
        parser = _HTMLTextExtractor()
        parser.feed(content)
        text = normalize_text("".join(parser.parts))
    else:
        text = normalize_text(content)
    if not text:
        raise KnowledgeImportError("资料内容为空，未建立索引")
    return text, extension


def _break_point(text: str, lower: int, upper: int) -> int:
    candidates = ("\n\n", "\n", "。", "！", "？", ". ", "；", "; ", " ")
    best = -1
    for marker in candidates:
        point = text.rfind(marker, lower, upper)
        if point > best:
            best = point + len(marker)
    return best if best > lower else upper


def split_text(
    text: str,
    *,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """按段落和句末边界切片，返回文本与原文偏移。"""

    if not text:
        return []
    chunks: list[dict] = []
    start = 0
    total = len(text)
    while start < total:
        hard_end = min(start + chunk_size, total)
        end = hard_end
        if hard_end < total:
            end = _break_point(text, start + int(chunk_size * 0.55), hard_end)
        raw = text[start:end]
        left_trim = len(raw) - len(raw.lstrip())
        right_trim = len(raw.rstrip())
        chunk_text = raw.strip()
        if chunk_text:
            actual_start = start + left_trim
            actual_end = start + right_trim
            chunks.append(
                {
                    "ordinal": len(chunks),
                    "text": chunk_text,
                    "start_offset": actual_start,
                    "end_offset": actual_end,
                    "char_count": len(chunk_text),
                }
            )
        if end >= total:
            break
        start = max(start + 1, end - overlap)
    return chunks


def tokenize(text: str) -> list[str]:
    """中文双字 n-gram + 英文/数字词的轻量分词。"""

    value = normalize_text(text).lower()
    tokens: list[str] = []
    for match in _WORD_RE.finditer(value):
        tokens.append(match.group(0))
    for match in _CJK_RE.finditer(value):
        sequence = match.group(0)
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def embedding_for(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """用 SHA-256 做确定性特征哈希，生成本地余弦检索向量。"""

    vector = [0.0] * dim
    counts = Counter(tokenize(text))
    for token, count in counts.items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [round(value / norm, 6) for value in vector]
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return max(-1.0, min(1.0, sum(left[i] * right[i] for i in range(size))))


def parse_embedding(value: str) -> list[float]:
    try:
        data = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [float(item) for item in data if isinstance(item, (int, float))]


def score_chunk(
    query: str,
    chunk_text: str,
    *,
    title: str = "",
    embedding: list[float] | None = None,
) -> tuple[float, list[str]]:
    """混合排序：字符 n-gram 覆盖 + 向量余弦 + 连续短语加成。"""

    query_tokens = list(dict.fromkeys(tokenize(query)))
    chunk_tokens = set(tokenize(chunk_text + "\n" + title))
    if not query_tokens or not chunk_tokens:
        return 0.0, []
    matched = [token for token in query_tokens if token in chunk_tokens]
    if not matched:
        return 0.0, []
    coverage = len(matched) / len(query_tokens)
    jaccard = len(matched) / max(1, len(set(query_tokens) | chunk_tokens))
    query_norm = normalize_text(query).lower()
    haystack = normalize_text(chunk_text + "\n" + title).lower()
    phrase = 1.0 if query_norm and query_norm in haystack else 0.0
    if coverage < 0.14 and not phrase:
        return 0.0, []
    vector_score = cosine(
        embedding_for(query), embedding if embedding is not None else embedding_for(chunk_text)
    )
    score = 0.36 * coverage + 0.12 * min(1.0, jaccard * 4) + 0.42 * vector_score + 0.10 * phrase
    if phrase:
        score += 0.10
    return round(max(0.0, min(1.0, score)), 6), matched


def snippet_for(text: str, query: str, *, radius: int = 88) -> str:
    """截取命中词附近的一段原文，供检索结果引用。"""

    compact = normalize_text(text)
    lower = compact.lower()
    query_norm = normalize_text(query).lower()
    position = lower.find(query_norm) if query_norm else -1
    if position < 0:
        positions = [lower.find(token) for token in tokenize(query) if lower.find(token) >= 0]
        position = min(positions) if positions else 0
    start = max(0, position - radius)
    end = min(len(compact), position + max(len(query_norm), 1) + radius)
    return (
        ("…" if start else "")
        + compact[start:end].replace("\n", " ")
        + ("…" if end < len(compact) else "")
    )
