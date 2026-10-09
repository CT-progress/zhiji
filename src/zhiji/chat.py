"""本地知识问答会话的存储与聊天调用封装。

会话默认写入关系数据库（``conversations`` / ``conversation_messages`` 两张表）；
旧的 ``data/conversations.json`` 可用 ``python -m zhiji.migrate`` 一次性导入。
把环境变量 ``ZHIJI_CHAT_BACKEND`` 设为 ``json`` 可回退旧的文件后端。
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.config import PROJECT_ROOT
from zhiji.errors import InputUnsupportedError, ZhijiError
from zhiji.platforms.registry import resolve_adapter
from zhiji.storage.db import Database, get_database
from zhiji.storage.tables import ConversationMessageRow, ConversationRow

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_TITLE = "新对话"
DEFAULT_DB_FILENAME = "zhiji.db"

#: 会话存储后端：db（默认，关系数据库）或 json（旧版文件后端）。
CHAT_BACKEND_ENV = "ZHIJI_CHAT_BACKEND"
DB_BACKEND = "db"
JSON_BACKEND = "json"

#: 进程内按存储位置复用的可重入锁，避免同一份会话数据的读改写竞争。
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for_key(key: str) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()) if path.exists() else str(path.absolute())
    return _lock_for_key(key)


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

#: 会话消息角色；append_message 只接受 user / assistant 两种。
ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    role: ChatRole
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


class ChatBackend(Protocol):
    """会话存储后端的最小接口，供 db / json 两种实现共享。"""

    backend_name: str
    location: str

    def list(self) -> list[Conversation]: ...

    def get(self, conversation_id: str) -> Conversation: ...

    def create(self, title: str = DEFAULT_TITLE, model_name: str | None = None) -> Conversation: ...

    def update(self, conversation_id: str, **changes: str | None) -> Conversation: ...

    def attach_note(
        self,
        conversation_id: str,
        *,
        platform: str | None = None,
        source_url: str | None = None,
        note_content: str | None = None,
        note_path: str | None = None,
        title: str | None = None,
    ) -> Conversation: ...

    def delete(self, conversation_id: str) -> None: ...

    def append_message(
        self, conversation_id: str, role: ChatRole, content: str
    ) -> Conversation: ...


class JsonChatStore:
    """旧版文件后端：把会话持久化到 data/conversations.json。"""

    backend_name = JSON_BACKEND

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.path = self.data_dir / "conversations.json"
        self.location = str(self.path)
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

    def append_message(
        self, conversation_id: str, role: ChatRole, content: str
    ) -> Conversation:
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


class DbChatStore:
    """默认后端：会话主表 + 消息表，每追加一条消息只插入一行。

    消息表天然避免了「读改写整个文件」的丢更新问题；SQLite 等单写者数据库
    在进程内再加一把锁，避免多线程同时升级写锁而报 database is locked。
    """

    backend_name = DB_BACKEND

    def __init__(
        self,
        database: Database | None = None,
        *,
        data_dir: str | Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.database = database or resolve_database(data_dir)
        self.location = self.database.url
        self.database.create_all()
        self._lock = _lock_for_key(f"db:{self.database.url}")

    @staticmethod
    def _row(session: Session, conversation_id: str) -> ConversationRow:
        row = session.get(ConversationRow, conversation_id)
        if row is None:
            raise ChatNotFoundError(f"会话不存在: {conversation_id}", hint="会话可能已被删除")
        return row

    def list(self) -> list[Conversation]:
        stmt = select(ConversationRow).order_by(
            ConversationRow.updated_at.desc(), ConversationRow.id
        )
        with self._lock, self.database.session_scope() as session:
            return [row_to_conversation(row) for row in session.scalars(stmt)]

    def get(self, conversation_id: str) -> Conversation:
        with self._lock, self.database.session_scope() as session:
            return row_to_conversation(self._row(session, conversation_id))

    def create(self, title: str = DEFAULT_TITLE, model_name: str | None = None) -> Conversation:
        conversation = Conversation(title=(title or DEFAULT_TITLE).strip(), model_name=model_name)
        with self._lock, self.database.session_scope() as session:
            session.add(conversation_to_row(conversation))
            session.flush()
            return row_to_conversation(self._row(session, conversation.id))

    def update(self, conversation_id: str, **changes: str | None) -> Conversation:
        with self._lock, self.database.session_scope() as session:
            row = self._row(session, conversation_id)
            for key in ("title", "model_name"):
                if key in changes and changes[key] is not None:
                    setattr(row, key, changes[key])
            row.updated_at = datetime.now(UTC)
            session.flush()
            return row_to_conversation(row)

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
        with self._lock, self.database.session_scope() as session:
            row = self._row(session, conversation_id)
            for key, value in {
                "platform": platform,
                "source_url": source_url,
                "note_content": note_content,
                "note_path": note_path,
            }.items():
                if value is not None:
                    setattr(row, key, value)
            if title:
                row.title = title.strip()[:60] or row.title
            row.updated_at = datetime.now(UTC)
            session.flush()
            return row_to_conversation(row)

    def delete(self, conversation_id: str) -> None:
        with self._lock, self.database.session_scope() as session:
            session.delete(self._row(session, conversation_id))

    def append_message(
        self, conversation_id: str, role: ChatRole, content: str
    ) -> Conversation:
        with self._lock, self.database.session_scope() as session:
            row = self._row(session, conversation_id)
            row.messages.append(ConversationMessageRow(role=role, content=content))
            row.updated_at = datetime.now(UTC)
            if role == "user" and row.title == DEFAULT_TITLE:
                row.title = _auto_title(content)
            session.flush()
            return row_to_conversation(row)


class ChatStore:
    """会话存储门面：默认数据库后端，可用 ``ZHIJI_CHAT_BACKEND=json`` 回退。

    显式传入 ``data_dir`` 时，数据库落在该目录下的 ``zhiji.db``（便于测试和
    按目录隔离）；否则复用进程级数据库，也就是 ``ZHIJI_DATABASE_URL`` 或
    项目内 ``data/zhiji.db``。
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        backend: str | None = None,
        database: Database | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        mode = (backend or os.getenv(CHAT_BACKEND_ENV) or DB_BACKEND).strip().lower()
        if mode == JSON_BACKEND:
            store: ChatBackend = JsonChatStore(data_dir)
        elif mode == DB_BACKEND:
            store = DbChatStore(database, data_dir=data_dir)
        else:
            raise ZhijiError(
                f"未知的会话存储后端: {mode}",
                hint=f"可选值 {DB_BACKEND} / {JSON_BACKEND}（环境变量 {CHAT_BACKEND_ENV}）",
            )
        self._store = store
        self.backend_name = store.backend_name
        self.location = store.location

    @property
    def database(self) -> Database | None:
        """数据库后端时返回底层 ``Database``；JSON 回退时返回 None。"""

        return getattr(self._store, "database", None)

    def list(self) -> list[Conversation]:
        return self._store.list()

    def get(self, conversation_id: str) -> Conversation:
        return self._store.get(conversation_id)

    def create(self, title: str = DEFAULT_TITLE, model_name: str | None = None) -> Conversation:
        return self._store.create(title, model_name)

    def update(self, conversation_id: str, **changes: str | None) -> Conversation:
        return self._store.update(conversation_id, **changes)

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
        return self._store.attach_note(
            conversation_id,
            platform=platform,
            source_url=source_url,
            note_content=note_content,
            note_path=note_path,
            title=title,
        )

    def delete(self, conversation_id: str) -> None:
        self._store.delete(conversation_id)

    def append_message(
        self, conversation_id: str, role: ChatRole, content: str
    ) -> Conversation:
        return self._store.append_message(conversation_id, role, content)


def resolve_database(data_dir: str | Path | None) -> Database:
    """没有显式 data_dir 时复用进程级数据库；否则用该目录下的 zhiji.db。"""

    if data_dir is None:
        return get_database()
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return get_database(f"sqlite:///{(directory / DEFAULT_DB_FILENAME).as_posix()}")


def _as_utc(value: datetime) -> datetime:
    """SQLite 不保存时区，读回来的是同一个 UTC 时刻，这里补回 tzinfo。"""

    return value if value.tzinfo else value.replace(tzinfo=UTC)


def conversation_to_row(conversation: Conversation) -> ConversationRow:
    """Pydantic 会话 -> ORM 行（含消息），建库与迁移脚本共用。"""

    row = ConversationRow(
        id=conversation.id,
        title=conversation.title,
        model_name=conversation.model_name,
        platform=conversation.platform,
        source_url=conversation.source_url,
        note_content=conversation.note_content,
        note_path=conversation.note_path,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )
    row.messages = [
        ConversationMessageRow(
            role=message.role, content=message.content, created_at=message.created_at
        )
        for message in conversation.messages
    ]
    return row


def row_to_conversation(row: ConversationRow) -> Conversation:
    """ORM 行 -> Pydantic 会话，消息按写入顺序排列。"""

    return Conversation(
        id=row.id,
        title=row.title,
        model_name=row.model_name,
        platform=row.platform,
        source_url=row.source_url,
        note_content=row.note_content,
        note_path=row.note_path,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        messages=[
            ChatMessage(
                role=cast(ChatRole, message.role),
                content=message.content,
                created_at=_as_utc(message.created_at),
            )
            for message in row.messages
        ],
    )


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


def extract_supported_links(message: str) -> str | None:
    """提取消息里受支持的链接，返回「链接 + 平台」提示文本。

    聊天模式下只做链接识别、不发起网络请求（笔记生成模式由 pipeline 调用
    ``adapter.fetch()``），因此函数名不提 fetch，避免语义误导。
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
