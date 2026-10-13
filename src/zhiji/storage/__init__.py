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
from zhiji.storage.platform_credential_repository import PlatformCredentialRepository
from zhiji.storage.repository import TERMINAL_STATUSES, TaskRepository
from zhiji.storage.tables import (
    CREDENTIAL_ACTIVE,
    CREDENTIAL_UNSET,
    SETTINGS_ROW_ID,
    AppSettingsRow,
    Base,
    ConversationMessageRow,
    ConversationRow,
    LLMCredentialRow,
    ModelConfigRow,
    PlatformCredentialRow,
    TaskRow,
    UserRow,
)
from zhiji.storage.user_repository import UserRepository

__all__ = [
    "CREDENTIAL_ACTIVE",
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
    "LLMCredentialRow",
    "ModelConfigRow",
    "PlatformCredentialRepository",
    "PlatformCredentialRow",
    "TaskRepository",
    "TaskRow",
    "UserRepository",
    "UserRow",
    "create_database_engine",
    "default_database_url",
    "get_database",
    "lock_for_key",
    "reset_database",
]
