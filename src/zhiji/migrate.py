"""把旧版 JSON 会话数据一次性迁移到数据库。

用法：

    python -m zhiji.migrate                      # 迁移 data/conversations.json
    python -m zhiji.migrate --dry-run            # 只统计，不写入
    python -m zhiji.migrate --json other.json --database sqlite:///data/zhiji.db

迁移是幂等的：已经存在的会话 id 会被跳过，源文件保持不动。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from zhiji.chat import DATA_DIR, Conversation, conversation_to_row
from zhiji.errors import ZhijiError
from zhiji.storage import ConversationRow, Database, get_database


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
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args(argv)

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
