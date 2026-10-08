from zhiji import config as config_module
from zhiji.config import ConfigManager
from zhiji.errors import ConfigError
from zhiji.models import LLMModelConfig


def _manager(tmp_path):
    return ConfigManager(tmp_path / "zhiji.json")


def test_add_and_update_model(tmp_path):
    cm = _manager(tmp_path)
    cm.add_model(LLMModelConfig(name="DeepSeek", api_key="sk-123", model="deepseek-chat"))
    cm.update_model("DeepSeek", {"model": "deepseek-reasoner", "default": True})
    model = cm.get_model("DeepSeek")
    assert model.model == "deepseek-reasoner"
    assert model.default is True
    assert cm.path.exists()


def test_duplicate_name_raises(tmp_path):
    cm = _manager(tmp_path)
    cm.add_model(LLMModelConfig(name="A"))
    try:
        cm.add_model(LLMModelConfig(name="A"))
        raise AssertionError("should have raised")
    except ConfigError:
        pass


def test_set_default_clears_others(tmp_path):
    cm = _manager(tmp_path)
    cm.add_model(LLMModelConfig(name="A"))
    cm.add_model(LLMModelConfig(name="B", default=False))
    cm.set_default_model("B")
    assert cm.get_model("A").default is False
    assert cm.get_model("B").default is True


def test_delete_default_promotes_remaining(tmp_path):
    cm = _manager(tmp_path)
    cm.add_model(LLMModelConfig(name="A"))
    cm.add_model(LLMModelConfig(name="B", default=False))
    cm.delete_model("A")
    assert cm.get_model("B").default is True


def test_public_config_masks_keys(tmp_path):
    cm = _manager(tmp_path)
    cm.add_model(LLMModelConfig(name="A", api_key="sk-1234567890"))
    payload = cm.public_config()
    assert payload["models"][0]["api_key"] == "sk-1****7890"

def test_default_config_path_env_relative_to_project(monkeypatch):
    monkeypatch.setenv("ZHIJI_CONFIG", "config/zhiji.json")
    path = config_module.default_config_path()
    assert path == config_module.PROJECT_ROOT / "config" / "zhiji.json"


def test_default_config_path_env_absolute(monkeypatch, tmp_path):
    target = tmp_path / "custom.json"
    monkeypatch.setenv("ZHIJI_CONFIG", str(target))
    assert config_module.default_config_path() == target
