"""把旧版 JSON 会话数据一次性迁移到数据库。

用法：

    python -m zhiji.migrate                      # 迁移 data/conversations.json
    python -m zhiji.migrate --dry-run            # 只统计，不写入
    python -m zhiji.migrate --json other.json --database sqlite:///data/zhiji.db
    python -m zhiji.migrate --config config/zhiji.json   # 只导入模型/设置

迁移是幂等的：已经存在的会话 id 会被跳过，源文件保持不动。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, update

from zhiji.auth import normalize_username
from zhiji.chat import DATA_DIR, Conversation, conversation_to_row
from zhiji.config import default_config_path, read_config_file
from zhiji.errors import ZhijiError
from zhiji.models import AppConfig
from zhiji.storage import (
    ConfigRepository,
    ConversationRow,
    Database,
    ModelConfigRow,
    TaskRow,
    UserRepository,
    get_database,
)
from zhiji.storage.credential_repository import read_credential, set_credential


@dataclass(frozen=True)
class MigrationResult:
    """一次迁移的统计结果。"""

    total: int
    imported: int
    skipped: int
    dry_run: bool = False


def load_conversations(json_path: Path) -> list[Conversation]:
    """读取并校验旧版会话 JSON。"""

    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ZhijiError("会话 JSON 读取失败", hint=str(json_path)) from exc
    if not isinstance(raw, list):
        raise ZhijiError("会话 JSON 结构异常", hint="顶层应为数组")
    return [Conversation.model_validate(item) for item in raw]


def migrate_conversations(
    conversations: list[Conversation],
    database: Database,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    """把会话写入数据库；已存在的 id 跳过，dry_run 时整体回滚。"""

    database.create_all()
    imported = 0
    skipped = 0
    session = database.session()
    try:
        for conversation in conversations:
            if session.get(ConversationRow, conversation.id) is not None:
                skipped += 1
                continue
            session.add(conversation_to_row(conversation))
            imported += 1
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return MigrationResult(
        total=len(conversations), imported=imported, skipped=skipped, dry_run=dry_run
    )


@dataclass(frozen=True)
class ConfigImportResult:
    """一次模型/设置导入的统计结果。"""

    models: int
    imported: bool
    dry_run: bool = False


def import_config(
    config: AppConfig,
    database: Database,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> ConfigImportResult:
    """把 JSON 配置写入数据库；数据库已有配置时跳过，overwrite=True 才覆盖。"""

    repository = ConfigRepository(database)
    if not overwrite and not repository.is_empty():
        return ConfigImportResult(models=len(config.models), imported=False, dry_run=dry_run)
    if dry_run:
        return ConfigImportResult(models=len(config.models), imported=False, dry_run=True)
    repository.save(config)
    return ConfigImportResult(models=len(config.models), imported=True)


@dataclass(frozen=True)
class CredentialKeyResult:
    """一次「明文 LLM Key -> 加密凭据表」迁移的统计。"""

    found: int
    migrated: int
    already_stored: int
    cleared_rows: int
    json_cleared: bool
    dry_run: bool = False


def migrate_llm_keys(
    database: Database,
    config_path: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> CredentialKeyResult:
    """把明文 LLM Key 加密迁入 ``llm_credentials``，并清空明文来源。

    数据源是「数据库 ``model_configs.api_key``」与「配置文件里的 ``api_key``」
    的并集；已经在凭据表里的模型不覆盖。迁移是幂等的：第二次运行时已无明文可迁。
    整个过程只看得到密文与掩码，不打印也不返回任何明文 Key。
    """

    database.create_all()
    repository = ConfigRepository(database)
    cipher = repository.credentials.cipher
    path = Path(config_path) if config_path is not None else None
    json_config = read_config_file(path) if path is not None and path.exists() else None

    session = database.session()
    try:
        rows = list(session.scalars(select(ModelConfigRow)))
        candidates: dict[str, str] = {}
        for row in rows:
            key = (row.api_key or "").strip()
            if key:
                candidates.setdefault(row.name, key)
        if json_config is not None:
            for model in json_config.models:
                key = (model.api_key or "").strip()
                if key:
                    candidates.setdefault(model.name, key)

        migrated = 0
        already_stored = 0
        for name, key in candidates.items():
            if read_credential(session, cipher, name) is not None:
                already_stored += 1
                continue
            migrated += 1
            if not dry_run:
                set_credential(session, cipher, name, key)

        cleared_rows = 0
        for row in rows:
            if (row.api_key or "").strip():
                cleared_rows += 1
                if not dry_run:
                    row.api_key = ""
        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    json_cleared = False
    if path is not None and json_config is not None and any(m.api_key for m in json_config.models):
        # dry-run 时表示「计划会清空」；真实运行时才落盘。
        json_cleared = True
        if not dry_run:
            _clear_config_file(path, json_config)

    if not dry_run:
        # SQLite 的 UPDATE/DELETE 只是把旧行标记为空闲，明文仍可能留在页里；
        # VACUUM 重建库文件，确保「库里查不到明文」这件事真的成立。
        _vacuum_sqlite(database)

    return CredentialKeyResult(
        found=len(candidates),
        migrated=migrated,
        already_stored=already_stored,
        cleared_rows=cleared_rows,
        json_cleared=json_cleared,
        dry_run=dry_run,
    )


def _vacuum_sqlite(database: Database) -> None:
    """重建 SQLite 库文件，清除删除 / 更新后残留在空闲页里的旧数据。"""

    if not database.url.startswith("sqlite") or ":memory:" in database.url:
        return
    with database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")


def _clear_config_file(path: Path, config: AppConfig) -> None:
    """把配置文件里的 Key 清空后重新落盘（保留其它字段）。"""

    payload = config.model_dump(mode="json")
    for model in payload.get("models", []):
        model["api_key"] = ""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def assign_legacy_data(
    database: Database, username: str, *, dry_run: bool = False
) -> tuple[int, int]:
    """把 user_id 为空的历史会话 / 任务认领给指定用户，返回 (会话数, 任务数)。"""

    database.create_all()
    normalized = normalize_username(username)
    with database.session_scope() as session:
        user = UserRepository(session).find_by_username(normalized)
        if user is None:
            raise ZhijiError(
                f"用户不存在: {username}", hint="请先用同一数据库注册该账号"
            )
        conversations = session.scalar(
            select(func.count())
            .select_from(ConversationRow)
            .where(ConversationRow.user_id.is_(None))
        )
        tasks = session.scalar(
            select(func.count()).select_from(TaskRow).where(TaskRow.user_id.is_(None))
        )
        if not dry_run:
            session.execute(
                update(ConversationRow)
                .where(ConversationRow.user_id.is_(None))
                .values(user_id=user.id)
            )
            session.execute(
                update(TaskRow)
                .where(TaskRow.user_id.is_(None))
                .values(user_id=user.id)
            )
    return int(conversations or 0), int(tasks or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zhiji.migrate",
        description="把旧版 JSON 会话迁移进数据库（幂等，源文件保留）",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=str(DATA_DIR / "conversations.json"),
        help="旧版会话 JSON 路径，默认 data/conversations.json",
    )
    parser.add_argument(
        "--database",
        dest="database_url",
        default=None,
        help="目标数据库连接串，默认 ZHIJI_DATABASE_URL 或项目内 data/zhiji.db",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="改为导入模型/设置配置文件（如 config/zhiji.json）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    parser.add_argument(
        "--migrate-keys",
        action="store_true",
        help="把配置文件 / 数据库里的明文 LLM Key 加密迁入凭据表并清空明文",
    )
    parser.add_argument(
        "--assign-user",
        dest="assign_user",
        default=None,
        help="把 user_id 为空的历史会话 / 任务认领给该用户",
    )
    args = parser.parse_args(argv)

    if args.migrate_keys:
        database = Database(args.database_url) if args.database_url else get_database()
        config_path = Path(args.config_path) if args.config_path else default_config_path()
        key_result = migrate_llm_keys(database, config_path, dry_run=args.dry_run)
        prefix = "[dry-run] " if key_result.dry_run else ""
        print(
            f"{prefix}发现 {key_result.found} 个明文 Key：新加密 {key_result.migrated} 个，"
            f"已在库中 {key_result.already_stored} 个；"
            f"清空数据库明文列 {key_result.cleared_rows} 处，"
            f"配置文件{'将清空' if key_result.dry_run and key_result.json_cleared else ('已清空' if key_result.json_cleared else '无需清空')}"
        )
        return 0

    if args.assign_user:
        database = Database(args.database_url) if args.database_url else get_database()
        claimed_conversations, claimed_tasks = assign_legacy_data(
            database, args.assign_user, dry_run=args.dry_run
        )
        prefix = "[dry-run] " if args.dry_run else ""
        print(
            f"{prefix}认领 {claimed_conversations} 个会话、"
            f"{claimed_tasks} 个任务 -> {args.assign_user}"
        )
        return 0

    if args.config_path:
        config_path = Path(args.config_path)
        if not config_path.exists():
            print(f"未找到配置文件：{config_path}（无需导入）")
            return 0
        config = read_config_file(config_path)
        database = Database(args.database_url) if args.database_url else get_database()
        config_result = import_config(config, database, dry_run=args.dry_run)
        if config_result.dry_run:
            print(f"[dry-run] 将导入 {config_result.models} 个模型配置 -> {database.url}")
        elif config_result.imported:
            print(f"导入 {config_result.models} 个模型配置 -> {database.url}")
            print(f"源文件保留：{config_path}")
        else:
            print(f"数据库已有配置，跳过导入（{database.url}）")
        return 0

    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"未找到 JSON 文件：{json_path}（无需迁移）")
        return 0

    conversations = load_conversations(json_path)
    database = Database(args.database_url) if args.database_url else get_database()
    result = migrate_conversations(conversations, database, dry_run=args.dry_run)
    prefix = "[dry-run] " if result.dry_run else ""
    print(
        f"{prefix}读取 {result.total} 个会话，导入 {result.imported} 个，"
        f"跳过 {result.skipped} 个（已存在）"
    )
    print(f"{prefix}目标数据库：{database.url}")
    if not result.dry_run and result.imported:
        print(f"源文件保留：{json_path}（确认无误后可自行归档或删除）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
