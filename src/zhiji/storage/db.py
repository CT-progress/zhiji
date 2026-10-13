"""数据库引擎与会话管理。"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from zhiji.errors import StorageError
from zhiji.storage.tables import Base

#: 项目根目录。这里本地定义，避免 db <- config <- storage 的循环导入。
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DB_RELATIVE = Path("data") / "zhiji.db"
DATABASE_URL_ENV = "ZHIJI_DATABASE_URL"

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def lock_for_key(key: str) -> threading.RLock:
    """进程内按资源名复用可重入锁。

    SQLite 这类单写者数据库在「先读后写」的事务里容易撞上 database is locked，
    写操作外面套一把进程内锁即可避免；key 用数据库 URL，保证同一个库共锁。
    """

    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def default_database_url() -> str:
    """返回数据库连接串：优先环境变量，否则使用项目内 data/zhiji.db。"""

    env_url = os.getenv(DATABASE_URL_ENV, "").strip()
    if env_url:
        return env_url
    path = PROJECT_ROOT / DEFAULT_DB_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
    """SQLite 连接初始化：打开外键约束、放宽写锁等待、文件库启用 WAL。"""

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        database = str(getattr(dbapi_connection, "database", ""))
        if database and database != ":memory:":
            cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def create_database_engine(url: str | None = None) -> Engine:
    """按连接串创建引擎；SQLite 自动放宽线程限制。"""

    resolved = url or default_database_url()
    kwargs: dict[str, Any] = {}
    if resolved.startswith("sqlite"):
        # FastAPI / 后台线程会跨线程复用连接，SQLite 默认禁止这一点。
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in resolved:
            # 内存库必须复用同一连接，否则每次连接都是一个新的空库。
            kwargs["poolclass"] = StaticPool
    try:
        engine = create_engine(resolved, **kwargs)
    except (ArgumentError, NoSuchModuleError) as exc:
        raise StorageError("数据库连接串无效", hint=str(exc)) from exc
    if resolved.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_on_connect)
    return engine


class Database:
    """持有引擎与会话工厂，供上层按需获取 Session。"""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or default_database_url()
        self.engine = create_database_engine(self.url)
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )

    def create_all(self) -> None:
        """按 ORM 定义建表（幂等），并为早期版本的库补上后续新增列。"""

        Base.metadata.create_all(self.engine)
        self._ensure_added_columns()

    #: 后续版本新增的「可空」列：建表无法补列，这里做幂等的轻量迁移。
    #: 元组含义为 (表名, 列名, 列类型, 是否建索引)。
    _ADDED_COLUMNS: tuple[tuple[str, str, str, bool], ...] = (
        ("conversations", "user_id", "VARCHAR(36)", True),
        ("tasks", "user_id", "VARCHAR(36)", True),
        # 阶段 4.4：凭据健康检查的检测时间与失败摘要。
        ("llm_credentials", "last_checked_at", "DATETIME", False),
        ("llm_credentials", "last_error", "VARCHAR(200)", False),
        ("platform_credentials", "last_checked_at", "DATETIME", False),
        ("platform_credentials", "last_error", "VARCHAR(200)", False),
    )

    def _ensure_added_columns(self) -> None:
        """给已存在的表补上缺失的可空列与索引（SQLite / PostgreSQL 通用）。"""

        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        with self.engine.begin() as conn:
            for table, column, ddl_type, _indexed in self._ADDED_COLUMNS:
                if table not in tables:
                    continue
                existing = {col["name"] for col in inspector.get_columns(table)}
                if column in existing:
                    continue
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
                )
            for table, column, _ddl_type, indexed in self._ADDED_COLUMNS:
                if indexed and table in tables:
                    conn.exec_driver_sql(
                        f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} "
                        f"ON {table} ({column})"
                    )

    def drop_all(self) -> None:
        """删除所有表，仅用于测试。"""

        Base.metadata.drop_all(self.engine)

    def session(self) -> Session:
        """创建一个 Session；调用方负责 close。"""

        return self._session_factory()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """事务作用域：正常提交，异常回滚，最后必然关闭。"""

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


_default_database: Database | None = None


def get_database(url: str | None = None) -> Database:
    """获取进程级默认数据库；传入 url 时返回一个独立实例。"""

    global _default_database
    if url is not None:
        return Database(url)
    if _default_database is None:
        _default_database = Database()
    return _default_database


def reset_database() -> None:
    """释放并清空进程级默认实例，供测试隔离使用。"""

    global _default_database
    if _default_database is not None:
        _default_database.dispose()
    _default_database = None
