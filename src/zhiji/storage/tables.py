"""ORM 表定义。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    #: 数据归属用户；None 表示本机默认作用域（未开启账号鉴权时的历史数据）
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
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
    #: 数据归属用户；None 表示本机默认作用域（未开启账号鉴权时的历史数据）
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
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


#: 凭据状态：active = 有可用密文（未检测或检测正常）；expired = 检测确认已失效；
#: error = 检测异常（网络/接口问题，不等于凭据失效）；unset = 未设置或已清空。
CREDENTIAL_ACTIVE = "active"
CREDENTIAL_UNSET = "unset"
CREDENTIAL_EXPIRED = "expired"
CREDENTIAL_ERROR = "error"


class LLMCredentialRow(Base):
    """LLM API Key 密文表：明文只在写入瞬间存在，读取时才解密。

    唯一键是 ``(user_id, name)``；SQLite 下 ``NULL`` 不参与唯一约束比较，
    因此本机作用域（``user_id IS NULL``）的去重由仓储层保证。
    """

    __tablename__ = "llm_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_llm_credential_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 凭据归属用户；None 表示本机默认作用域（与全局模型配置对应）
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    name: Mapped[str] = mapped_column(String(120), index=True)
    #: ``v1.<nonce>.<ciphertext>`` 形式的密文，绝不含明文
    key_cipher: Mapped[str] = mapped_column(Text, default="")
    #: 不可逆尾号提示，供界面显示（如 ``sk-1****7890``）
    key_hint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default=CREDENTIAL_ACTIVE)
    #: 最近一次健康检查时间与失败摘要（阶段 4.4）；摘要不含任何 Key / Cookie 片段
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<LLMCredentialRow name={self.name!r} status={self.status!r}>"


class PlatformCredentialRow(Base):
    """平台登录态（Cookie + UA）密文表（阶段 4.3）。

    整份登录态字典（cookie / user_agent / saved_at / verified）序列化成 JSON 后
    整体加密，库里只有 ``v1.<nonce>.<ciphertext>`` 密文与零泄露的提示
    （形如 ``6 项``，只报 Cookie 字段个数，不含任何值）。

    唯一键是 ``(user_id, platform)``；``user_id IS NULL`` 表示本机作用域，
    与全局配置一一对应。SQLite 下 ``NULL`` 不参与唯一约束比较，本机作用域的
    去重由仓储层保证。
    """

    __tablename__ = "platform_credentials"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", name="uq_platform_credential_user_platform"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 凭据归属用户；None 表示本机默认作用域
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    #: 平台标识：douyin / bilibili / zhihu / xiaohongshu
    platform: Mapped[str] = mapped_column(String(32), index=True)
    #: 整份登录态的密文，绝不含明文 Cookie
    payload_cipher: Mapped[str] = mapped_column(Text, default="")
    #: 零泄露提示（如 ``6 项``），供界面显示
    hint: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default=CREDENTIAL_ACTIVE)
    #: 最近一次健康检查时间与失败摘要（阶段 4.4）；摘要不含任何 Key / Cookie 片段
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<PlatformCredentialRow platform={self.platform!r} status={self.status!r}>"


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
