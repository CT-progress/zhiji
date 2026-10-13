"""模型配置与全局设置的 ORM 读写。

把 ``zhiji.models`` 里的 Pydantic 配置对象与数据库表互相转换，供
``zhiji.config.ConfigManager`` 在数据库后端下复用。表定义见
``zhiji.storage.tables``：``model_configs`` 多行 + ``app_settings`` 单行。

阶段 4.2 起，模型配置表里的 ``api_key`` 列**不再写明文**（恒为空串）：
真正的 Key 由 :class:`~zhiji.storage.credential_repository.CredentialRepository`
加密存进 ``llm_credentials`` 表。对上层而言 ``LLMModelConfig.api_key`` 依旧是
可用的明文（加载时解密、保存在内存），但落盘与查表都只有密文与掩码。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.models import AppConfig, AppSettings, LLMModelConfig, PlatformSettings
from zhiji.storage.credential_repository import (
    CredentialRepository,
    read_credential,
    set_credential,
)
from zhiji.storage.db import Database, lock_for_key
from zhiji.storage.tables import SETTINGS_ROW_ID, AppSettingsRow, ModelConfigRow


def model_to_row(model: LLMModelConfig, position: int) -> ModelConfigRow:
    """Pydantic 模型配置 -> ORM 行（``api_key`` 恒为空，明文密钥绝不落此表）。"""

    return ModelConfigRow(
        name=model.name,
        position=position,
        base_url=model.base_url,
        api_key="",
        model=model.model,
        enabled=model.enabled,
        is_default=model.default,
        note=model.note,
    )


def apply_model(row: ModelConfigRow, model: LLMModelConfig, position: int) -> None:
    """把 Pydantic 模型配置写回已存在的 ORM 行（改名由调用方先删后插）。"""

    row.position = position
    row.base_url = model.base_url
    row.api_key = ""
    row.model = model.model
    row.enabled = model.enabled
    row.is_default = model.default
    row.note = model.note
    row.updated_at = datetime.now(UTC)


def row_to_model(row: ModelConfigRow, *, api_key: str = "") -> LLMModelConfig:
    """ORM 行 -> Pydantic 模型配置；``api_key`` 由调用方从凭据表解密后传入。"""

    return LLMModelConfig(
        name=row.name,
        base_url=row.base_url,
        api_key=api_key,
        model=row.model,
        enabled=row.enabled,
        default=row.is_default,
        note=row.note or "",
    )


def settings_to_row(row: AppSettingsRow, settings: AppSettings) -> None:
    """把 Pydantic 全局设置写回 ORM 行。"""

    row.output_dir = settings.output_dir
    row.whisper_model = settings.whisper_model
    row.whisper_device = settings.whisper_device
    row.keep_audio = settings.keep_audio
    row.platforms_json = json.dumps(
        settings.platforms.model_dump(), ensure_ascii=False, sort_keys=True
    )
    row.updated_at = datetime.now(UTC)


def _new_settings_row(settings: AppSettings) -> AppSettingsRow:
    row = AppSettingsRow(id=SETTINGS_ROW_ID)
    settings_to_row(row, settings)
    return row


def row_to_settings(row: AppSettingsRow | None) -> AppSettings:
    """ORM 行 -> Pydantic 全局设置；缺行或平台字段损坏时退回默认值。"""

    if row is None:
        return AppSettings()
    try:
        platforms = PlatformSettings.model_validate_json(row.platforms_json or "{}")
    except ValueError:
        platforms = PlatformSettings()
    return AppSettings(
        output_dir=row.output_dir,
        whisper_model=row.whisper_model,
        whisper_device=row.whisper_device,
        keep_audio=row.keep_audio,
        platforms=platforms,
    )


class ConfigRepository:
    """模型配置 / 全局设置的关系数据库仓储。

    保存采用「整体替换」语义：模型按列表顺序重写 ``position``，被移除的模型
    直接删除。配置写入频率极低，这样能保证数据库与内存视图始终一致。
    LLM Key 不在本表，而由 ``self.credentials`` 加密读写。
    """

    def __init__(
        self, database: Database, *, credentials: CredentialRepository | None = None
    ) -> None:
        self.database = database
        self.database.create_all()
        self.credentials = credentials if credentials is not None else CredentialRepository(database)
        self._cipher = self.credentials.cipher
        self._lock = lock_for_key(f"config:{database.url}")

    def is_empty(self) -> bool:
        """数据库里既没有模型行、也没有设置行时视为空。"""

        with self._lock, self.database.session_scope() as session:
            has_model = session.scalar(select(ModelConfigRow.name).limit(1)) is not None
            has_settings = session.get(AppSettingsRow, SETTINGS_ROW_ID) is not None
            return not has_model and not has_settings

    def load(self) -> AppConfig | None:
        """读取全部配置（Key 从凭据表解密）；数据库为空时返回 ``None``。"""

        with self._lock, self.database.session_scope() as session:
            stmt = select(ModelConfigRow).order_by(ModelConfigRow.position, ModelConfigRow.name)
            rows = list(session.scalars(stmt))
            settings_row = session.get(AppSettingsRow, SETTINGS_ROW_ID)
            if not rows and settings_row is None:
                return None
            models = [
                row_to_model(
                    row,
                    api_key=read_credential(session, self._cipher, row.name) or "",
                )
                for row in rows
            ]
            return AppConfig(models=models, settings=row_to_settings(settings_row))

    def save(self, config: AppConfig) -> None:
        """整体覆盖数据库中的模型与设置，并同步加密凭据。"""

        with self._lock, self.database.session_scope() as session:
            self._replace(session, config)

    def _replace(self, session: Session, config: AppConfig) -> None:
        existing = {row.name: row for row in session.scalars(select(ModelConfigRow))}
        keep: set[str] = set()
        for position, model in enumerate(config.models):
            row = existing.get(model.name)
            if row is None:
                session.add(model_to_row(model, position))
            else:
                apply_model(row, model, position)
            # 非空明文 -> 加密 upsert；空明文 -> 删除该模型的凭据
            set_credential(session, self._cipher, model.name, model.api_key)
            keep.add(model.name)
        for name, row in existing.items():
            if name not in keep:
                session.delete(row)
                set_credential(session, self._cipher, name, "")

        settings_row = session.get(AppSettingsRow, SETTINGS_ROW_ID)
        if settings_row is None:
            session.add(_new_settings_row(config.settings))
        else:
            settings_to_row(settings_row, config.settings)
