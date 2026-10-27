"""持久化层：数据库引擎、ORM 表与仓储。"""

from __future__ import annotations

from zhiji.storage.config_repository import ConfigRepository
from zhiji.storage.credential_repository import CredentialRepository
from zhiji.storage.db import (
    DEFAULT_DB_RELATIVE,
    Database,
    create_database_engine,
    default_database_url,
    get_database,
    lock_for_key,
    reset_database,
)
from zhiji.storage.doc_repository import DocRepository
from zhiji.storage.knowledge_repository import KnowledgeRepository
from zhiji.storage.media_repository import MediaRepository
from zhiji.storage.note_repository import NoteRepository, TodoRepository
from zhiji.storage.platform_credential_repository import PlatformCredentialRepository
from zhiji.storage.repository import TERMINAL_STATUSES, TaskRepository
from zhiji.storage.tables import (
    CREDENTIAL_ACTIVE,
    CREDENTIAL_ERROR,
    CREDENTIAL_EXPIRED,
    CREDENTIAL_UNSET,
    SETTINGS_ROW_ID,
    AppSettingsRow,
    Base,
    ConversationMessageRow,
    ConversationRow,
    DocRow,
    KnowledgeChunkRow,
    KnowledgeDocumentRow,
    KnowledgeLibraryRow,
    LLMCredentialRow,
    MediaArtifactRow,
    MediaItemRow,
    ModelConfigRow,
    NoteRow,
    PlatformCredentialRow,
    TaskRow,
    TodoRow,
    UserRow,
)
from zhiji.storage.user_repository import UserRepository

__all__ = [
    "CREDENTIAL_ACTIVE",
    "CREDENTIAL_ERROR",
    "CREDENTIAL_EXPIRED",
    "CREDENTIAL_UNSET",
    "DEFAULT_DB_RELATIVE",
    "SETTINGS_ROW_ID",
    "TERMINAL_STATUSES",
    "AppSettingsRow",
    "Base",
    "ConfigRepository",
    "ConversationMessageRow",
    "ConversationRow",
    "CredentialRepository",
    "Database",
    "DocRepository",
    "DocRow",
    "KnowledgeChunkRow",
    "KnowledgeDocumentRow",
    "KnowledgeLibraryRow",
    "KnowledgeRepository",
    "LLMCredentialRow",
    "MediaArtifactRow",
    "MediaItemRow",
    "MediaRepository",
    "ModelConfigRow",
    "NoteRepository",
    "NoteRow",
    "PlatformCredentialRepository",
    "PlatformCredentialRow",
    "TaskRepository",
    "TaskRow",
    "TodoRepository",
    "TodoRow",
    "UserRepository",
    "UserRow",
    "create_database_engine",
    "default_database_url",
    "get_database",
    "lock_for_key",
    "reset_database",
]
