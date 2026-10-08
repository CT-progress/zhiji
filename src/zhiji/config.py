"""配置加载、持久化与多模型管理。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from zhiji.errors import ConfigError, OutputWriteError
from zhiji.models import AppConfig, AppSettings, LLMModelConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def default_config_path() -> Path:
    env_path = os.getenv("ZHIJI_CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        # 相对路径统一以项目根目录为基准，避免受启动时工作目录影响
        return path if path.is_absolute() else PROJECT_ROOT / path
    return PROJECT_ROOT / "config" / "zhiji.json"


def mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}****{value[-4:]}"


class ConfigManager:
    """负责 config/zhiji.json 的读写与模型 CRUD。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_config_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._config = self._load()

    def _load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return AppConfig.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (ValidationError, OSError, ValueError) as exc:
            raise ConfigError("配置文件解析失败", hint=f"{self.path}: {exc}") from exc

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(self._config.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            raise OutputWriteError(f"配置写入失败: {self.path}") from exc

    def get(self) -> AppConfig:
        return self._config

    def reload(self) -> AppConfig:
        self._config = self._load()
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