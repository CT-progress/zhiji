"""JSON 会话迁移到数据库的测试。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from zhiji.chat import JSON_BACKEND, ChatNotFoundError, ChatStore
from zhiji.config import ConfigManager, read_config_file
from zhiji.errors import ZhijiError
from zhiji.migrate import (
    import_config,
    load_conversations,
    main,
    migrate_conversations,
    migrate_llm_keys,
)
from zhiji.models import LLMModelConfig
from zhiji.storage import ConfigRepository, Database, LLMCredentialRow, ModelConfigRow


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}")
    yield db
    db.dispose()


def _json_store(tmp_path: Path) -> tuple[str, Path]:
    """用 JSON 后端造一份旧数据，返回会话 id 与文件路径。"""

    store = ChatStore(tmp_path, backend=JSON_BACKEND)
    conversation = store.create(title="旧会话")
    store.append_message(conversation.id, "user", "你好")
    store.append_message(conversation.id, "assistant", "在的")
    store.attach_note(
        conversation.id,
        platform="zhihu",
        source_url="https://www.zhihu.com/question/1/answer/2",
        note_content="笔记正文",
    )
    return conversation.id, store.data_dir / "conversations.json"


def test_migrate_imports_conversation_with_messages(tmp_path: Path, database: Database) -> None:
    conversation_id, json_path = _json_store(tmp_path)
    conversations = load_conversations(json_path)
    assert [c.id for c in conversations] == [conversation_id]

    result = migrate_conversations(conversations, database)
    assert (result.total, result.imported, result.skipped) == (1, 1, 0)
    assert result.dry_run is False

    store = ChatStore(backend="db", database=database)
    loaded = store.get(conversation_id)
    assert loaded.title == "旧会话"
    assert [m.content for m in loaded.messages] == ["你好", "在的"]
    assert loaded.platform == "zhihu"
    assert loaded.note_content == "笔记正文"
    assert loaded.source_url == "https://www.zhihu.com/question/1/answer/2"


def test_migrate_is_idempotent(tmp_path: Path, database: Database) -> None:
    _, json_path = _json_store(tmp_path)
    conversations = load_conversations(json_path)
    migrate_conversations(conversations, database)

    again = migrate_conversations(conversations, database)
    assert (again.imported, again.skipped) == (0, 1)


def test_migrate_dry_run_writes_nothing(tmp_path: Path, database: Database) -> None:
    conversation_id, json_path = _json_store(tmp_path)
    result = migrate_conversations(load_conversations(json_path), database, dry_run=True)
    assert (result.imported, result.dry_run) == (1, True)

    store = ChatStore(backend="db", database=database)
    with pytest.raises(ChatNotFoundError):
        store.get(conversation_id)


def test_migrate_handles_empty_file(tmp_path: Path, database: Database) -> None:
    json_path = tmp_path / "conversations.json"
    json_path.write_text("[]", encoding="utf-8")
    result = migrate_conversations(load_conversations(json_path), database)
    assert (result.total, result.imported, result.skipped) == (0, 0, 0)


def test_load_conversations_rejects_broken_payload(tmp_path: Path) -> None:
    json_path = tmp_path / "conversations.json"
    json_path.write_text("{不是合法 JSON", encoding="utf-8")
    with pytest.raises(ZhijiError):
        load_conversations(json_path)

    json_path.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ZhijiError):
        load_conversations(json_path)


def test_main_reports_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", str(tmp_path / "nope.json")]) == 0
    assert "无需迁移" in capsys.readouterr().out


def test_main_migrates_into_explicit_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, json_path = _json_store(tmp_path)
    db_path = tmp_path / "zhiji.db"
    url = f"sqlite:///{db_path.as_posix()}"

    assert main(["--json", str(json_path), "--database", url]) == 0
    assert "导入 1 个" in capsys.readouterr().out
    assert db_path.exists()


def _config_json(tmp_path: Path) -> Path:
    """用 JSON 后端造一份旧配置文件。"""

    path = tmp_path / "zhiji.json"
    cm = ConfigManager(path, backend=JSON_BACKEND)
    cm.add_model(LLMModelConfig(name="DeepSeek", api_key="sk-1", default=True))
    cm.update_settings({"output_dir": "notes", "keep_audio": True})
    return path


def test_import_config_writes_models_and_settings(tmp_path: Path, database: Database) -> None:
    path = _config_json(tmp_path)
    result = import_config(read_config_file(path), database)
    assert (result.imported, result.models) == (True, 1)

    cm = ConfigManager(tmp_path / "loaded.json", database=database)
    model = cm.get_model("DeepSeek")
    assert model is not None
    assert model.api_key == "sk-1"
    assert cm.get().settings.output_dir == "notes"
    assert cm.get().settings.keep_audio is True


def test_import_config_skips_when_already_present(tmp_path: Path, database: Database) -> None:
    path = _config_json(tmp_path)
    import_config(read_config_file(path), database)
    again = import_config(read_config_file(path), database)
    assert (again.imported, again.dry_run) == (False, False)


def test_import_config_dry_run_writes_nothing(tmp_path: Path, database: Database) -> None:
    path = _config_json(tmp_path)
    result = import_config(read_config_file(path), database, dry_run=True)
    assert (result.imported, result.dry_run) == (False, True)
    assert ConfigRepository(database).load() is None


def test_main_imports_config_into_explicit_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_json(tmp_path)
    db_path = tmp_path / "zhiji.db"
    url = f"sqlite:///{db_path.as_posix()}"

    assert main(["--config", str(path), "--database", url]) == 0
    assert "导入 1 个" in capsys.readouterr().out
    assert db_path.exists()


def test_main_config_reports_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(tmp_path / "nope.json")]) == 0
    assert "无需导入" in capsys.readouterr().out


def test_main_config_dry_run_reports_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _config_json(tmp_path)
    url = f"sqlite:///{(tmp_path / 'zhiji.db').as_posix()}"
    assert main(["--config", str(path), "--database", url, "--dry-run"]) == 0
    assert "将导入 1 个" in capsys.readouterr().out
    assert ConfigRepository(Database(url)).load() is None


def _seed_plaintext_key(database: Database, name: str, key: str) -> None:
    """直接写一行带明文 Key 的旧模型配置（模拟 1.3 导入后的库）。"""

    database.create_all()
    with database.session_scope() as session:
        session.add(ModelConfigRow(name=name, api_key=key))


def _rows(database: Database) -> tuple[list[ModelConfigRow], list[LLMCredentialRow]]:
    with database.session_scope() as session:
        models = list(session.query(ModelConfigRow))
        creds = list(session.query(LLMCredentialRow))
    return models, creds


def test_migrate_llm_keys_encrypts_and_clears(tmp_path: Path, database: Database) -> None:
    secret = "sk-plain-abcdefghijklmnop"
    _seed_plaintext_key(database, "DeepSeek", secret)
    config_path = tmp_path / "zhiji.json"
    config_path.write_text(
        json.dumps({"models": [{"name": "DeepSeek", "api_key": secret}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = migrate_llm_keys(database, config_path)
    assert (result.found, result.migrated, result.cleared_rows) == (1, 1, 1)
    assert result.json_cleared is True

    models, creds = _rows(database)
    assert models[0].api_key == ""
    assert creds[0].key_cipher.startswith("v1.")
    assert secret not in creds[0].key_cipher
    assert secret not in config_path.read_text(encoding="utf-8")

    cm = ConfigManager(tmp_path / "loaded.json", database=database)
    assert cm.get_model("DeepSeek").api_key == secret  # type: ignore[union-attr]


def test_migrate_llm_keys_is_idempotent(tmp_path: Path, database: Database) -> None:
    _seed_plaintext_key(database, "DeepSeek", "sk-plain-1")
    config_path = tmp_path / "zhiji.json"
    config_path.write_text(json.dumps({"models": []}), encoding="utf-8")
    migrate_llm_keys(database, config_path)

    again = migrate_llm_keys(database, config_path)
    assert (again.found, again.migrated, again.already_stored, again.cleared_rows) == (0, 0, 0, 0)


def test_migrate_llm_keys_dry_run_writes_nothing(tmp_path: Path, database: Database) -> None:
    secret = "sk-plain-dryrun-123456"
    _seed_plaintext_key(database, "DeepSeek", secret)
    config_path = tmp_path / "zhiji.json"
    config_path.write_text(
        json.dumps({"models": [{"name": "DeepSeek", "api_key": secret}]}), encoding="utf-8"
    )

    result = migrate_llm_keys(database, config_path, dry_run=True)
    assert (result.found, result.migrated, result.dry_run) == (1, 1, True)

    models, creds = _rows(database)
    assert models[0].api_key == secret  # 未清空
    assert creds == []  # 未入库
    assert secret in config_path.read_text(encoding="utf-8")  # 文件未动


def test_main_migrate_keys_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "zhiji.json"
    config_path.write_text(
        json.dumps({"models": [{"name": "DeepSeek", "api_key": "sk-cli-1234567890"}]}),
        encoding="utf-8",
    )
    db_path = tmp_path / "zhiji.db"
    url = f"sqlite:///{db_path.as_posix()}"

    assert main(["--migrate-keys", "--config", str(config_path), "--database", url]) == 0
    out = capsys.readouterr().out
    assert "新加密 1 个" in out
    assert "sk-cli-1234567890" not in out


def test_migrate_llm_keys_leaves_no_plaintext_in_db_file(
    tmp_path: Path, database: Database
) -> None:
    secret = "sk-residue-abcdefghijklmnop"
    _seed_plaintext_key(database, "DeepSeek", secret)
    config_path = tmp_path / "zhiji.json"
    config_path.write_text(json.dumps({"models": []}), encoding="utf-8")
    migrate_llm_keys(database, config_path)

    db_path = Path(database.url.replace("sqlite:///", ""))
    blob = db_path.read_bytes()
    for suffix in ("-wal", "-shm"):
        extra = db_path.with_name(db_path.name + suffix)
        if extra.exists():
            blob += extra.read_bytes()
    assert secret.encode("utf-8") not in blob
