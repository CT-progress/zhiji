"""业务数据的用户作用域语义。

- ``ANY_USER``：内部维护 / worker 使用，不按用户过滤；
- ``None``：本机默认作用域，只匹配 ``user_id IS NULL`` 的历史数据；
- ``str``：具体用户 id，只匹配该用户的数据。
"""

from __future__ import annotations


class _AnyUser:
    """不限定用户的内部查询哨兵。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "ANY_USER"


ANY_USER = _AnyUser()
UserScope = str | None | _AnyUser
