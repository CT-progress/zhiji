"""ORM 表定义。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from zhiji.models import TaskStatus


class Base(DeclarativeBase):
    """所有 ORM 表的基类。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskRow(Base):
    """任务表：一次抓取 / 转写 / 生成请求的执行记录。

    状态流转（由仓储层约束写入，见 repository.update_status）：
    PENDING -> RUNNING -> SUCCEEDED / FAILED / CANCELLED
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    status: Mapped[str] = mapped_column(
        String(16), index=True, default=TaskStatus.PENDING.value
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"<TaskRow id={self.id!r} type={self.type!r} status={self.status!r}>"


class UserRow(Base):
    """用户表：密码只存 bcrypt 哈希，任何地方都不保存明文。

    用户名统一小写后入库，保证大小写不敏感的唯一性。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(120), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<UserRow id={self.id!r} username={self.username!r} active={self.is_active!r}>"


class ConversationRow(Base):
    """会话主表：字段与 zhiji.chat.Conversation 一一对应。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    model_name: Mapped[str | None] = mapped_column(String(64), default=None)
    platform: Mapped[str | None] = mapped_column(String(32), default=None)
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    note_content: Mapped[str | None] = mapped_column(Text, default=None)
    note_path: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    messages: Mapped[list[ConversationMessageRow]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessageRow.id",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ConversationRow id={self.id!r} title={self.title!r}>"


class ConversationMessageRow(Base):
    """会话消息表：每次 append 只插入一行，天然并发安全，不会互相覆盖。"""

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    conversation: Mapped[ConversationRow] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<ConversationMessageRow id={self.id!r} role={self.role!r}>"


#: app_settings 是单行表，固定使用这一行的主键。
SETTINGS_ROW_ID = 1


class ModelConfigRow(Base):
    """模型配置表：字段与 zhiji.models.LLMModelConfig 一一对应。"""

    __tablename__ = "model_configs"

    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    base_url: Mapped[str] = mapped_column(String(500), default="")
    api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<ModelConfigRow name={self.name!r}>"


class AppSettingsRow(Base):
    """全局设置表（单行）：字段与 zhiji.models.AppSettings 对应。"""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SETTINGS_ROW_ID)
    output_dir: Mapped[str] = mapped_column(String(500), default="output")
    whisper_model: Mapped[str] = mapped_column(String(64), default="small")
    whisper_device: Mapped[str] = mapped_column(String(32), default="cpu")
    keep_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    platforms_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AppSettingsRow id={self.id!r} output_dir={self.output_dir!r}>"
