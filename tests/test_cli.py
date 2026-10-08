"""CLI output compatibility tests."""

from typer.testing import CliRunner

from zhiji import cli
from zhiji.cli import _console_safe_text
from zhiji.config import ConfigManager
from zhiji.models import LLMModelConfig


def test_console_safe_text_replaces_emoji_for_gbk() -> None:
    text = "note-🤖.md"
    assert _console_safe_text(text, encoding="gbk") == "note-?.md"


def test_console_safe_text_preserves_unicode_for_utf8() -> None:
    text = "知乎-🤖.md"
    assert _console_safe_text(text, encoding="utf-8") == text


def _invoke(tmp_path, monkeypatch, args):
    cm = ConfigManager(tmp_path / "zhiji.json")
    monkeypatch.setattr(cli, "_manager", lambda: cm)
    return CliRunner().invoke(cli.app, args), cm


def test_check_env_command(tmp_path, monkeypatch) -> None:
    result, _ = _invoke(tmp_path, monkeypatch, ["check-env"])
    assert result.exit_code == 0
    assert "python" in result.output.lower()


def test_models_lists_configured_model_masked(tmp_path, monkeypatch) -> None:
    result, cm = _invoke(tmp_path, monkeypatch, ["models"])
    assert result.exit_code == 0

    cm.add_model(
        LLMModelConfig(
            name="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            api_key="sk-1234567890",
        )
    )
    result, _ = _invoke(tmp_path, monkeypatch, ["models"])
    assert result.exit_code == 0
    assert "DeepSeek" in result.output
    assert "sk-1234567890" not in result.output


def test_model_add_remove_roundtrip(tmp_path, monkeypatch) -> None:
    added, cm = _invoke(
        tmp_path,
        monkeypatch,
        ["model-add", "Local", "--base-url", "http://localhost:11434/v1", "--model", "llama3"],
    )
    assert added.exit_code == 0
    assert cm.get_model("Local") is not None

    removed, cm = _invoke(tmp_path, monkeypatch, ["model-remove", "Local"])
    assert removed.exit_code == 0
    assert cm.get_model("Local") is None


def test_config_show_prints_settings(tmp_path, monkeypatch) -> None:
    result, _ = _invoke(tmp_path, monkeypatch, ["config-show"])
    assert result.exit_code == 0
    assert "output_dir" in result.output


def test_search_rejects_unknown_platform(tmp_path, monkeypatch) -> None:
    result, _ = _invoke(tmp_path, monkeypatch, ["search", "python", "--platform", "nope"])
    assert result.exit_code == 1
    assert "不支持的平台" in result.output


def test_note_rejects_unsupported_url(tmp_path, monkeypatch) -> None:
    result, _ = _invoke(tmp_path, monkeypatch, ["note", "https://example.com/not-supported"])
    assert result.exit_code == 1
    assert "OK" not in result.output
