"""配置加载、持久化与多模型管理。

模型配置与全局设置默认写入数据库（``model_configs`` / ``app_settings`` 两张
表），同时在 ``self.path`` 的 JSON 文件里保留一份导出，既兼容旧有的
``config/zhiji.json``，也方便人工查看与回退。首次使用且数据库为空时，会从
该 JSON 文件导入一次。把环境变量 ``ZHIJI_CONFIG_BACKEND`` 设为 ``json`` 可
切回纯文件后端。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from zhiji.errors import ConfigError, OutputWriteError, ZhijiError
from zhiji.models import AppConfig, AppSettings, LLMModelConfig
from zhiji.security.crypto import mask_credential
from zhiji.storage import Database, get_database
from zhiji.storage.config_repository import ConfigRepository
from zhiji.storage.credential_repository import CredentialRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

#: 配置存储后端：db（默认，数据库 + JSON 导出）或 json（旧版纯文件）。
CONFIG_BACKEND_ENV = "ZHIJI_CONFIG_BACKEND"
DB_BACKEND = "db"
JSON_BACKEND = "json"

#: 显式指定配置文件时，数据库落在同目录下的这个文件名里。
DEFAULT_DB_FILENAME = "zhiji.db"


def default_config_path() -> Path:
    env_path = os.getenv("ZHIJI_CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        # 相对路径统一以项目根目录为基准，避免受启动时工作目录影响
        return path if path.is_absolute() else PROJECT_ROOT / path
    return PROJECT_ROOT / "config" / "zhiji.json"


def read_config_file(path: str | Path) -> AppConfig:
    """读取并校验 JSON 配置文件；文件不存在时返回默认配置。"""

    target = Path(path).expanduser()
    if not target.exists():
        return AppConfig()
    try:
        return AppConfig.model_validate_json(target.read_text(encoding="utf-8"))
    except (ValidationError, OSError, ValueError) as exc:
        raise ConfigError("配置文件解析失败", hint=f"{target}: {exc}") from exc


def mask_api_key(value: str) -> str:
    """Key 的展示掩码；实现见 :func:`zhiji.security.crypto.mask_credential`。"""

    return mask_credential(value)


class ConfigManager:
    """模型与全局设置的读写门面。

    默认数据库后端：显式传入 ``path`` 时，数据库落在同目录的 ``zhiji.db``；
    不传 ``path`` 时复用进程级数据库（``ZHIJI_DATABASE_URL`` 或项目内
    ``data/zhiji.db``）。无论哪种后端，写操作都会把配置导出到 ``self.path``
    的 JSON 文件，因此旧文件仍是可靠的回退与快照。
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        backend: str | None = None,
        database: Database | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path else default_config_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        mode = (backend or os.getenv(CONFIG_BACKEND_ENV) or DB_BACKEND).strip().lower()
        if mode not in (DB_BACKEND, JSON_BACKEND):
            raise ZhijiError(
                f"未知的配置存储后端: {mode}",
                hint=f"可选值 {DB_BACKEND} / {JSON_BACKEND}（环境变量 {CONFIG_BACKEND_ENV}）",
            )
        self.backend_name = mode
        self._repository: ConfigRepository | None = None
        if mode == DB_BACKEND:
            if database is not None:
                db = database
            elif path is not None:
                db = get_database(f"sqlite:///{(self.path.parent / DEFAULT_DB_FILENAME).as_posix()}")
            else:
                db = get_database()
            self._repository = ConfigRepository(db)
            self.location = db.url
        else:
            self.location = str(self.path)
        self._config = self._load()

    def _load(self) -> AppConfig:
        if self._repository is None:
            return read_config_file(self.path)
        existing = self._repository.load()
        if existing is not None:
            return existing
        # 数据库为空：从 JSON 文件导入一次，并把结果写回数据库。
        imported = read_config_file(self.path)
        self._repository.save(imported)
        return imported

    def save(self) -> None:
        if self._repository is None:
            # 纯文件后端：Key 必须落在 JSON 里，这是该后端选择的存储方式。
            self._write_file(sanitize=False)
            return
        self._repository.save(self._config)
        # 数据库后端：Key 已加密入库，导出的 JSON 不再保留任何明文。
        self._write_file(sanitize=True)

    def _write_file(self, *, sanitize: bool) -> None:
        """把配置导出到 ``self.path``；``sanitize=True`` 时清空所有 Key。"""

        if sanitize:
            payload = json.dumps(self._export_payload(), ensure_ascii=False, indent=2)
        else:
            payload = self._config.model_dump_json(indent=2)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            raise OutputWriteError(f"配置写入失败: {self.path}") from exc

    def _export_payload(self) -> dict:
        """导出用副本：模型 Key 一律置空，绝不把明文写进 JSON。"""

        data = self._config.model_dump(mode="json")
        for model in data.get("models", []):
            model["api_key"] = ""
        return data

    @property
    def credentials(self) -> CredentialRepository | None:
        """LLM Key 的密文仓储；JSON 文件后端没有这一层，返回 ``None``。"""

        return self._repository.credentials if self._repository is not None else None

    def get(self) -> AppConfig:
        return self._config

    def reload(self) -> AppConfig:
        if self._repository is not None:
            loaded = self._repository.load()
            self._config = loaded if loaded is not None else read_config_file(self.path)
        else:
            self._config = read_config_file(self.path)
        return self._config

    def add_model(self, model: LLMModelConfig, *, make_default: bool | None = None) -> LLMModelConfig:
        if any(old.name == model.name for old in self._config.models):
            raise ConfigError(f"模型名称已存在: {model.name}")
        if make_default is True or (make_default is None and not self._config.models):
            model.default = True
        if model.default:
            self._clear_defaults()
        self._config.models.append(model)
        self.save()
        return model

    def update_model(self, name: str, data: dict) -> LLMModelConfig:
        index = self._index_of(name)
        current = self._config.models[index]
        if (
            "name" in data
            and data["name"] != name
            and any(
                old.name == data["name"]
                for idx, old in enumerate(self._config.models)
                if idx != index
            )
        ):
            raise ConfigError(f"模型名称已存在: {data['name']}")
        try:
            updated = current.model_copy(update=data)
        except ValidationError as exc:
            raise ConfigError("模型参数不合法", hint=str(exc)) from exc
        if updated.default:
            self._clear_defaults()
        self._config.models[index] = updated
        self.save()
        return updated

    def delete_model(self, name: str) -> None:
        index = self._index_of(name)
        removed = self._config.models.pop(index)
        self.save()
        if removed.default and self._config.models:
            self._config.models[0].default = True
            self.save()

    def set_default_model(self, name: str) -> LLMModelConfig:
        index = self._index_of(name)
        if not self._config.models[index].enabled:
            raise ConfigError("禁用的模型不能设为默认")
        self._clear_defaults()
        self._config.models[index].default = True
        self.save()
        return self._config.models[index]

    def get_model(self, name: str | None = None) -> LLMModelConfig | None:
        if name:
            return next((m for m in self._config.models if m.name == name), None)
        return self._config.default_model()

    def update_settings(self, data: dict) -> AppSettings:
        current = self._config.settings.model_dump()
        current.update(data)
        try:
            self._config.settings = AppSettings.model_validate(current)
        except ValidationError as exc:
            raise ConfigError("设置参数不合法", hint=str(exc)) from exc
        self.save()
        return self._config.settings

    def public_config(self) -> dict:
        data = self._config.model_dump(mode="json")
        for model in data.get("models", []):
            model["api_key"] = mask_api_key(model.get("api_key", ""))
        return data

    def resolve_output_dir(self, override: str | None = None) -> Path:
        raw = override or self._config.settings.output_dir
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _index_of(self, name: str) -> int:
        for index, model in enumerate(self._config.models):
            if model.name == name:
                return index
        raise ConfigError(f"模型不存在: {name}")

    def _clear_defaults(self) -> None:
        for model in self._config.models:
            model.default = False
