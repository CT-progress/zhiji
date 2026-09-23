"""CLI output compatibility tests."""

from zhiji.cli import _console_safe_text


def test_console_safe_text_replaces_emoji_for_gbk() -> None:
    text = "note-\U0001f916.md"
    assert _console_safe_text(text, encoding="gbk") == "note-?.md"


def test_console_safe_text_preserves_unicode_for_utf8() -> None:
    text = "\u77e5\u4e4e-\U0001f916.md"
    assert _console_safe_text(text, encoding="utf-8") == text
