"""本地知识问答会话的存储与聊天调用封装。"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from zhiji.config import PROJECT_ROOT
from zhiji.errors import InputUnsupportedError, ZhijiError
from zhiji.platforms.registry import resolve_adapter

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_TITLE = "新对话"

#: 进程内按文件路径复用的可重入锁，避免同一会话文件的读改写竞争。
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()) if path.exists() else str(path.absolute())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock

CHAT_SYSTEM_PROMPT = (
    "你是知记助手，一个帮助用户深度学习和系统整理知识的中文助手。\n"
    "当用户提供文章/回答链接时，请生成一份饱满、通透的学习笔记，要求：\n"
    "1. **核心观点提炼**：用1-2句话概括文章的核心论点\n"
    "2. **内容深度梳理**：按逻辑结构展开，保留关键论据、案例、数据\n"
    "3. **知识延伸**：补充相关背景知识、概念解释、与其他知识的关联\n"
    "4. **批判性思考**：分析论证的优缺点、适用场景、潜在局限\n"
    "5. **实践价值**：总结可落地的行动建议或思考框架\n\n"
    "使用 Markdown 格式，包含标题、小标题、列表、引用块、表格等。\n"
    "内容要详实充分，不要为了简洁而丢失重要信息。\n"
    "不要编造事实，也不要声称自己执行了本机没有提供的操作。"
)

_MAX_HISTORY = 24
_MAX_SOURCE_TEXT = 30000
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = DEFAULT_TITLE
    model_name: str | None = None
    platform: str | None = None
    source_url: str | None = None
    note_content: str | None = None
    note_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatNotFoundError(ZhijiError):
    code = "CHAT_NOT_FOUND"


class ChatStore:
    """把会话持久化到 data/conversations.json。"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.path = self.data_dir / "conversations.json"
        self._lock = _lock_for(self.path)

    def _read(self) -> list[Conversation]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ZhijiError("会话数据解析失败", hint=str(self.path)) from exc
        return [Conversation.model_validate(item) for item in raw]

    def _write(self, conversations: list[Conversation]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = json.dumps(
            [c.model_dump(mode="json") for c in conversations],
            ensure_ascii=False,
            indent=2,
        )
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[Conversation]:
        with self._lock:
            return sorted(self._read(), key=lambda c: c.updated_at, reverse=True)

    def get(self, conversation_id: str) -> Conversation:
        with self._lock:
            for conversation in self._read():
                if conversation.id == conversation_id:
                    return conversation
        raise ChatNotFoundError(f"会话不存在: {conversation_id}", hint="会话可能已被删除")

    def create(self, title: str = DEFAULT_TITLE, model_name: str | None = None) -> Conversation:
        conversation = Conversation(title=(title or DEFAULT_TITLE).strip(), model_name=model_name)
        with self._lock:
            data = self._read()
            data.append(conversation)
            self._write(data)
        return conversation

    def update(self, conversation_id: str, **changes: str | None) -> Conversation:
        with self._lock:
            conversation = self.get(conversation_id)
            payload = conversation.model_dump()
            for key in ("title", "model_name"):
                if key in changes and changes[key] is not None:
                    payload[key] = changes[key]
            conversation = Conversation.model_validate(payload)
            conversation.updated_at = datetime.now(UTC)
            self._replace(conversation)
        return conversation

    def attach_note(
        self,
        conversation_id: str,
        *,
        platform: str | None = None,
        source_url: str | None = None,
        note_content: str | None = None,
        note_path: str | None = None,
        title: str | None = None,
    ) -> Conversation:
        """把生成的笔记绑定到会话，后续对话即可基于笔记上下文。"""
        with self._lock:
            conversation = self.get(conversation_id)
            payload = conversation.model_dump()
            for key, value in {
                "platform": platform,
                "source_url": source_url,
                "note_content": note_content,
                "note_path": note_path,
            }.items():
                if value is not None:
                    payload[key] = value
            if title:
                payload["title"] = title.strip()[:60] or conversation.title
            conversation = Conversation.model_validate(payload)
            conversation.updated_at = datetime.now(UTC)
            self._replace(conversation)
        return conversation

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            self.get(conversation_id)
            data = [c for c in self._read() if c.id != conversation_id]
            self._write(data)

    def append_message(self, conversation_id: str, role: Literal["user", "assistant"], content: str) -> Conversation:
        with self._lock:
            conversation = self.get(conversation_id)
            conversation.messages.append(ChatMessage(role=role, content=content))
            conversation.updated_at = datetime.now(UTC)
            if role == "user" and conversation.title == DEFAULT_TITLE:
                conversation.title = _auto_title(content)
            self._replace(conversation)
        return conversation

    def _replace(self, conversation: Conversation) -> None:
        with self._lock:
            data = self._read()
            for index, current in enumerate(data):
                if current.id == conversation.id:
                    data[index] = conversation
                    break
            self._write(data)


def build_chat_messages(conversation: Conversation, source_context: str | None = None) -> list[dict]:
    """取最近一段历史，加上系统提示，构造 OpenAI 兼容 messages。"""

    system = CHAT_SYSTEM_PROMPT
    if conversation.note_content:
        note_text = conversation.note_content[:_MAX_SOURCE_TEXT]
        source_line = f"（来源：{conversation.source_url}）" if conversation.source_url else ""
        system += (
            f"\n\n当前会话关联一篇笔记{source_line}，笔记全文如下：\n\n{note_text}\n\n"
            "用户会基于这篇笔记提问或要求修改。"
            "若用户要求修改笔记，请直接输出修改后的完整笔记全文（Markdown 格式），"
            "不要只描述改动点；若只是提问，则结合笔记内容简洁作答。"
        )

    recent = [{"role": m.role, "content": m.content} for m in conversation.messages[-_MAX_HISTORY:]]
    if source_context and recent and recent[-1]["role"] == "user":
        recent[-1]["content"] += (
            "\n\n--- 以下是用户链接中的资料，仅用于回答本次问题 ---\n"
            + source_context
        )
    return [{"role": "system", "content": system}, *recent]


def fetch_link_context(message: str) -> str | None:
    """提取消息中的知记支持链接，返回供模型使用的正文上下文。
    
    聊天模式下只返回链接信息，不阻塞在 Playwright 上。
    笔记生成模式由 pipeline 直接调用 adapter.fetch()。
    """
    blocks: list[str] = []
    seen: set[str] = set()
    for raw_url in _URL_RE.findall(message):
        url = raw_url.rstrip(".,!?;:)]}")
        if url in seen:
            continue
        seen.add(url)
        try:
            adapter = resolve_adapter(url)
        except InputUnsupportedError:
            continue
        # 聊天模式：只返回链接信息，不调用 adapter.fetch()
        # 这样不会因为 Playwright 超时而卡住
        blocks.append(f"链接：{url}\n平台：{adapter.platform.value}")
    return "\n\n".join(blocks) or None


def _auto_title(content: str) -> str:
    cleaned = re.sub(r"\s+", " ", content).strip()
    return cleaned[:28] + "..." if len(cleaned) > 28 else cleaned
