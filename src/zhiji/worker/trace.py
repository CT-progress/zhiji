"""Trace id 生成与透传工具。

使用 W3C 风格的 ``X-Trace-Id`` 头，控制面与 worker 之间贯穿同一 trace id。
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Self

# 进程级 trace 上下文，供 worker 执行线程继承
_trace_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "zhiji_trace_id", default=None
)


def generate_trace_id() -> str:
    return str(uuid.uuid4())


def get_trace_id() -> str:
    """获取当前上下文的 trace id；没有则生成一个并绑定。"""

    tid = _trace_ctx.get()
    if tid:
        return tid
    tid = generate_trace_id()
    _trace_ctx.set(tid)
    return tid


def set_trace_id(trace_id: str) -> contextvars.Token[str | None]:
    """显式设置 trace id，返回 token 供 restore。"""

    return _trace_ctx.set(trace_id)


def reset_trace_id(token: contextvars.Token[str | None]) -> None:
    _trace_ctx.reset(token)


class TraceContext:
    """上下文管理器：在 ``with`` 块内绑定 trace id。"""

    def __init__(self, trace_id: str) -> None:
        self._trace_id = trace_id
        self._token: contextvars.Token[str | None] | None = None

    def __enter__(self) -> Self:
        self._token = set_trace_id(self._trace_id)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            reset_trace_id(self._token)

    @property
    def trace_id(self) -> str:
        return self._trace_id


def propagate_trace_id(
    headers: dict[str, str], *, fallback: str | None = None
) -> str:
    """从请求头提取 trace id；没有则用 fallback 或生成新的。"""

    tid = headers.get("x-trace-id") or headers.get("X-Trace-Id") or fallback
    if not tid:
        tid = generate_trace_id()
    return tid
