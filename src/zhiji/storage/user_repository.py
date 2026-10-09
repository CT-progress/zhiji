"""用户仓储：users 表的最小读写封装。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from zhiji.errors import DuplicateUserError, UserNotFoundError
from zhiji.storage.tables import UserRow


class UserRepository:
    """用户表读写入口。

    与 ``TaskRepository`` 一致：事务边界由调用方掌握，仓储只做 flush，
    方便上层把「查重 + 建用户」放进同一个事务。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None = None,
        user_id: str | None = None,
    ) -> UserRow:
        if self.find_by_username(username) is not None:
            raise DuplicateUserError(
                f"用户名已被占用: {username}",
                hint="换一个用户名，或直接登录已有账号",
            )
        row = UserRow(
            id=user_id or str(uuid4()),
            username=username,
            password_hash=password_hash,
            display_name=display_name,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def find(self, user_id: str) -> UserRow | None:
        return self.session.get(UserRow, user_id)

    def get(self, user_id: str) -> UserRow:
        row = self.find(user_id)
        if row is None:
            raise UserNotFoundError(f"用户不存在: {user_id}")
        return row

    def find_by_username(self, username: str) -> UserRow | None:
        return self.session.scalar(select(UserRow).where(UserRow.username == username))

    def list(self, *, limit: int = 100, offset: int = 0) -> list[UserRow]:
        stmt = select(UserRow).order_by(UserRow.created_at.asc(), UserRow.id)
        return list(self.session.scalars(stmt.offset(offset).limit(limit)))
