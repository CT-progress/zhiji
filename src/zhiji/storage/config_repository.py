"""模型配置与全局设置的 ORM 读写。

把 ``zhiji.models`` 里的 Pydantic 配置对象与数据库表互相转换，供
``zhiji.config.ConfigManager`` 在数据库后端下复用。表定义见
``zhiji.storage.tables``：``model_configs`` 多行 + ``app_settings`` 单行。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.models import AppConfig, AppSettings, LLMModelConfig, PlatformSettings
from zhiji.storage.db import Database, lock_for_key
from zhiji.storage.tables import SETTINGS_ROW_ID, AppSettingsRow, ModelConfigRow


def model_to_row(model: LLMModelConfig, position: int) -> ModelConfigRow:
    """Pydantic 模型配置 -> ORM 行。"""

    return ModelConfigRow(
        name=model.name,
        position=position,
        base_url=model.base_url,
        api_key=model.api_key,
        model=model.model,
        enabled=model.enabled,
        is_default=model.default,
        note=model.note,
    )


def apply_model(row: ModelConfigRow, model: LLMModelConfig, position: int) -> None:
    """把 Pydantic 模型配置写回已存在的 ORM 行（改名由调用方先删后插）。"""

    row.position = position
    row.base_url = model.base_url
    row.api_key = model.api_key
    row.model = model.model
    row.enabled = model.enabled
    row.is_default = model.default
    row.note = model.note
    row.updated_at = datetime.now(UTC)


def row_to_model(row: ModelConfigRow) -> LLMModelConfig:
    """ORM 行 -> Pydantic 模型配置。"""

    return LLMModelConfig(
        name=row.name,
        base_url=row.base_url,
        api_key=row.api_key,
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
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self.database.create_all()
        self._lock = lock_for_key(f"config:{database.url}")

    def is_empty(self) -> bool:
        """数据库里既没有模型行、也没有设置行时视为空。"""

        with self._lock, self.database.session_scope() as session:
            has_model = session.scalar(select(ModelConfigRow.name).limit(1)) is not None
            has_settings = session.get(AppSettingsRow, SETTINGS_ROW_ID) is not None
            return not has_model and not has_settings

    def load(self) -> AppConfig | None:
        """读取全部配置；数据库为空时返回 ``None``（区别于「空配置」）。"""

        with self._lock, self.database.session_scope() as session:
            stmt = select(ModelConfigRow).order_by(ModelConfigRow.position, ModelConfigRow.name)
            rows = list(session.scalars(stmt))
            settings_row = session.get(AppSettingsRow, SETTINGS_ROW_ID)
            if not rows and settings_row is None:
                return None
            return AppConfig(
                models=[row_to_model(row) for row in rows],
                settings=row_to_settings(settings_row),
            )

    def save(self, config: AppConfig) -> None:
        """整体覆盖数据库中的模型与设置。"""

        with self._lock, self.database.session_scope() as session:
            self._replace(session, config)

    @staticmethod
    def _replace(session: Session, config: AppConfig) -> None:
        existing = {row.name: row for row in session.scalars(select(ModelConfigRow))}
        keep: set[str] = set()
        for position, model in enumerate(config.models):
            row = existing.get(model.name)
            if row is None:
                session.add(model_to_row(model, position))
            else:
                apply_model(row, model, position)
            keep.add(model.name)
        for name, row in existing.items():
            if name not in keep:
                session.delete(row)

        settings_row = session.get(AppSettingsRow, SETTINGS_ROW_ID)
        if settings_row is None:
            session.add(_new_settings_row(config.settings))
        else:
            settings_to_row(settings_row, config.settings)
