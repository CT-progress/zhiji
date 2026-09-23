from zhiji.config import mask_api_key
from zhiji.models import AppConfig, LLMModelConfig


def test_default_model_prefers_default_enabled():
    config = AppConfig(
        models=[
            LLMModelConfig(name="A", enabled=True, default=False),
            LLMModelConfig(name="B", enabled=True, default=True),
        ]
    )
    assert config.default_model().name == "B"


def test_default_model_falls_back_to_first_enabled():
    config = AppConfig(models=[LLMModelConfig(name="A"), LLMModelConfig(name="B", enabled=False)])
    assert config.default_model().name == "A"


def test_mask_api_key():
    assert mask_api_key("sk-1234567890abcd") == "sk-1****abcd"
    assert mask_api_key("") == ""