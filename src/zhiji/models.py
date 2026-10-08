"""平台无关的统一数据模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Platform(str, Enum):
    BILIBILI = "bilibili"
    ZHIHU = "zhihu"
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    GENERIC = "generic"


class ContentType(str, Enum):
    VIDEO = "video"
    IMAGE_POST = "image_post"
    ARTICLE = "article"
    ANSWER = "answer"
    QUESTION = "question"


class ContentRef(BaseModel):
    platform: Platform
    content_type: ContentType
    content_id: str
    source_url: str


class Metadata(BaseModel):
    title: str
    author: str | None = None
    author_url: str | None = None
    published_at: datetime | None = None
    cover_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class MediaFile(BaseModel):
    path: str = ""
    kind: str = "image"
    url: str | None = None


class ContentBundle(BaseModel):
    ref: ContentRef
    metadata: Metadata
    body_text: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    media: list[MediaFile] = Field(default_factory=list)
    source_json: dict[str, Any] = Field(default_factory=dict)

    def plain_text(self) -> str:
        """合并简介/正文与字幕分段，确保转写文本完整进入 LLM 输入。"""
        parts: list[str] = []
        if self.body_text and self.body_text.strip():
            parts.append(self.body_text.strip())
        if self.segments:
            parts.append("\n".join(seg.text for seg in self.segments))
        return "\n\n".join(parts)


class SearchResult(BaseModel):
    platform: Platform
    title: str
    author: str | None = None
    duration: int | None = None
    url: str
    score: float | None = None
    description: str | None = None


class LLMModelConfig(BaseModel):
    name: str
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    enabled: bool = True
    default: bool = False
    note: str = ""

    @field_validator("name", "base_url", "model")
    @classmethod
    def strip_fields(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return value.strip()


class PlatformSettings(BaseModel):
    bilibili: bool = True
    zhihu: bool = True
    douyin: bool = True
    xiaohongshu: bool = True


class AppSettings(BaseModel):
    output_dir: str = "output"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    keep_audio: bool = False
    platforms: PlatformSettings = Field(default_factory=PlatformSettings)


class AppConfig(BaseModel):
    models: list[LLMModelConfig] = Field(default_factory=list)
    settings: AppSettings = Field(default_factory=AppSettings)

    def default_model(self) -> LLMModelConfig | None:
        for model in self.models:
            if model.default and model.enabled:
                return model
        for model in self.models:
            if model.enabled:
                return model
        return self.models[0] if self.models else None


class NoteFrontmatter(BaseModel):
    title: str
    source: str
    platform: str
    content_type: str
    author: str | None = None
    published: str | None = None
    created: str
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None


class NoteSection(BaseModel):
    heading: str
    body: str


class NoteDocument(BaseModel):
    frontmatter: NoteFrontmatter
    sections: list[NoteSection]
    raw_transcript_ref: str


class OutputReceipt(BaseModel):
    note_path: Path
    artifact_dir: Path
    platform: str
    content_id: str
    title: str
    transcript_path: Path | None = None
    warnings: list[str] = Field(default_factory=list)
