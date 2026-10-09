"""配置入库测试：数据库后端（默认）、JSON 导入与文件回退。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zhiji.config import JSON_BACKEND, ConfigManager
from zhiji.errors import ZhijiError
from zhiji.models import AppConfig, AppSettings, LLMModelConfig, PlatformSettings
from zhiji.storage import reset_database


def _sample_config() -> AppConfig:
    return AppConfig(
        models=[
            LLMModelConfig(
                name="DeepSeek V4", api_key="sk-abcdefghijklmnop", model="deepseek-chat", default=True
            ),
            LLMModelConfig(name="回退模型", api_key="sk-2", model="gpt-4o-mini", enabled=False),
        ],
        settings=AppSettings(
            output_dir="notes",
            whisper_model="medium",
            keep_audio=True,
            platforms=PlatformSettings(xiaohongshu=False),
        ),
    )


def test_explicit_path_uses_sibling_database_and_keeps_json(tmp_path: Path) -> None:
    path = tmp_path / "zhiji.json"
    cm = ConfigManager(path)
    assert cm.backend_name == "db"
    assert cm.location.startswith("sqlite:///")

    cm.add_model(LLMModelConfig(name="A", api_key="sk-1234567890"))

    assert (tmp_path / "zhiji.db").exists()
    assert path.exists()
    assert "A" in path.read_text(encoding="utf-8")


def test_config_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "zhiji.json"
    first = ConfigManager(path)
    first.add_model(LLMModelConfig(name="A", api_key="sk-1", model="deepseek-chat"))
    first.update_settings({"output_dir": "out-x", "keep_audio": True})

    second = ConfigManager(path)
    model = second.get_model("A")
    assert model is not None
    assert model.model == "deepseek-chat"
    assert second.get().settings.output_dir == "out-x"
    assert second.get().settings.keep_audio is True


def test_imports_existing_json_on_first_use(tmp_path: Path) -> None:
    path = tmp_path / "zhiji.json"
    path.write_text(_sample_config().model_dump_json(indent=2), encoding="utf-8")

    cm = ConfigManager(path)
    names = [m.name for m in cm.get().models]
    assert names == ["DeepSeek V4", "回退模型"]
    assert cm.get().default_model().name == "DeepSeek V4"  # type: ignore[union-attr]
    assert cm.get().settings.whisper_model == "medium"
    assert cm.get().settings.platforms.xiaohongshu is False

    # 再开一个实例：此时数据库已有数据，不应被 JSON 覆盖
    path.write_text(json.dumps({"models": [], "settings": {}}), encoding="utf-8")
    again = ConfigManager(path)
    assert [m.name for m in again.get().models] == ["DeepSeek V4", "回退模型"]


def test_default_model_stays_unique_after_reload(tmp_path: Path) -> None:
    path = tmp_path / "zhiji.json"
    cm = ConfigManager(path)
    cm.add_model(LLMModelConfig(name="A"))
    cm.add_model(LLMModelConfig(name="B"))
    cm.set_default_model("B")

    reloaded = ConfigManager(path)
    assert reloaded.get_model("A").default is False  # type: ignore[union-attr]
    assert reloaded.get_model("B").default is True  # type: ignore[union-attr]


def test_settings_update_persists_platforms(tmp_path: Path) -> None:
    path = tmp_path / "zhiji.json"
    cm = ConfigManager(path)
    cm.add_model(LLMModelConfig(name="A"))
    cm.update_settings({"platforms": {"douyin": False, "zhihu": True}})

    reloaded = ConfigManager(path)
    platforms = reloaded.get().settings.platforms
    assert platforms.douyin is False
    assert platforms.zhihu is True


def test_json_backend_keeps_file_only(tmp_path: Path) -> None:
    path = tmp_path / "zhiji.json"
    cm = ConfigManager(path, backend=JSON_BACKEND)
    assert cm.backend_name == JSON_BACKEND
    cm.add_model(LLMModelConfig(name="A"))
    assert path.exists()
    assert not (tmp_path / "zhiji.db").exists()


def test_backend_can_be_switched_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_CONFIG_BACKEND", JSON_BACKEND)
    cm = ConfigManager(tmp_path / "zhiji.json")
    cm.add_model(LLMModelConfig(name="A"))
    assert cm.backend_name == JSON_BACKEND
    assert not (tmp_path / "zhiji.db").exists()


def test_unknown_backend_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ZhijiError):
        ConfigManager(tmp_path / "zhiji.json", backend="redis")


def test_default_backend_uses_process_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZHIJI_CONFIG", str(tmp_path / "config" / "zhiji.json"))
    monkeypatch.setenv("ZHIJI_DATABASE_URL", f"sqlite:///{(tmp_path / 'zhiji.db').as_posix()}")
    reset_database()
    try:
        first = ConfigManager()
        first.add_model(LLMModelConfig(name="进程库模型"))
        second = ConfigManager()
        assert second.get_model("进程库模型") is not None
    finally:
        reset_database()
