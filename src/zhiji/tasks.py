"""进程内任务执行器：从 ``tasks`` 表取活、执行、回写状态。

设计取舍（对应计划书阶段 2）：

* **数据库即队列**：任务落 ``tasks`` 表，worker 轮询 ``PENDING`` 并按优先级取活，
  这样进程重启后未完成的任务仍在，无需引入 Celery / RabbitMQ。
* **进程内并发**：用若干个线程共享一个 ``TaskWorker``，靠 ``lock_for_key`` 串行化
  「读 + 抢占」这一步，避免同一个任务被两个 worker 同时取走。
* **协作式取消**：取消把状态置为 ``CANCELLED``；处理函数可调用
  ``context.is_cancelled()`` 主动退出，worker 回写时若状态已变则不会覆盖。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from zhiji.errors import GenerationCancelledError, TaskTransitionError
from zhiji.models import TaskStatus
from zhiji.storage import Database, TaskRepository, get_database, lock_for_key
from zhiji.storage.tables import TaskRow

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 2
DEFAULT_POLL_INTERVAL = 0.5
#: 没有返回值时的空结果，保证 ``result_json`` 始终是合法 JSON。
EMPTY_RESULT: dict[str, Any] = {}


class TaskContext:
    """传给处理函数的执行上下文：读取参数、上报进度、查询取消。"""

    def __init__(self, row: TaskRow, database: Database, lock: threading.RLock) -> None:
        self.task_id = row.id
        self.task_type = row.type
        self.platform = row.platform
        self.payload: dict[str, Any] = json.loads(row.payload_json or "{}")
        self._database = database
        self._lock = lock

    def report(self, progress: int) -> None:
        """上报 0-100 的进度；任务已取消/结束时静默忽略。"""

        with self._lock, self._database.session_scope() as session:
            try:
                TaskRepository(session).update_progress(self.task_id, progress)
            except TaskTransitionError:
                pass

    def is_cancelled(self) -> bool:
        """任务是否已被取消（或记录已消失）。处理函数据此提前退出。"""

        with self._lock, self._database.session_scope() as session:
            row = TaskRepository(session).find(self.task_id)
            return row is None or row.status == TaskStatus.CANCELLED.value


#: 任务处理函数：接收上下文，返回可 JSON 序列化的结果字典或 None。
TaskHandler = Callable[[TaskContext], "dict[str, Any] | None"]


class TaskWorker:
    """进程内任务执行器：提交、执行、取消、启停。"""

    def __init__(
        self,
        database: Database | None = None,
        handlers: dict[str, TaskHandler] | None = None,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self.database = database or get_database()
        self.database.create_all()
        self.handlers: dict[str, TaskHandler] = dict(handlers or {})
        self.max_workers = max(1, int(max_workers))
        self.poll_interval = max(0.01, float(poll_interval))
        self._lock = lock_for_key(f"tasks:{self.database.url}")
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---- 注册与提交 -------------------------------------------------

    def register(
        self, task_type: str, handler: TaskHandler | None = None
    ) -> TaskHandler | Callable[[TaskHandler], TaskHandler]:
        """注册处理函数；既支持 ``register("x", fn)`` 也支持 ``@register("x")``。"""

        if handler is not None:
            self.handlers[task_type] = handler
            return handler

        def decorator(func: TaskHandler) -> TaskHandler:
            self.handlers[task_type] = func
            return func

        return decorator

    def submit(
        self,
        *,
        task_type: str,
        platform: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
    ) -> str:
        """创建任务并唤醒 worker，立即返回 task id。"""

        with self._lock, self.database.session_scope() as session:
            task_id = TaskRepository(session).create(
                task_type=task_type, platform=platform, payload=payload, priority=priority
            ).id
        self._wakeup.set()
        return task_id

    def cancel(self, task_id: str) -> TaskRow:
        """把未结束的任务置为取消；终态任务不可取消。"""

        with self._lock, self.database.session_scope() as session:
            repo = TaskRepository(session)
            row = repo.get(task_id)
            current = TaskStatus(row.status)
            if current.is_terminal():
                raise TaskTransitionError(
                    f"任务已结束，无法取消: {current.value}",
                    hint="只有 pending / running 的任务可以取消",
                )
            return repo.update_status(task_id, TaskStatus.CANCELLED)

    # ---- 启停与执行 -------------------------------------------------

    def start(self) -> None:
        """启动后台线程（幂等）。"""

        if self._threads:
            return
        self._stop.clear()
        for index in range(self.max_workers):
            thread = threading.Thread(
                target=self._run_loop, name=f"zhiji-task-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 5.0) -> None:
        """通知线程退出并等待收尾（幂等）。"""

        self._stop.set()
        self._wakeup.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads = []

    def run_once(self) -> bool:
        """同步处理一个待执行任务；没有则返回 ``False``（测试与脚本复用）。"""

        row = self._claim_next()
        if row is None:
            return False
        self._execute(row)
        return True

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            if self.run_once():
                continue
            self._wakeup.wait(self.poll_interval)
            self._wakeup.clear()

    def _claim_next(self) -> TaskRow | None:
        """按优先级取一个 PENDING 任务并抢占为 RUNNING。"""

        with self._lock, self.database.session_scope() as session:
            stmt = (
                select(TaskRow)
                .where(TaskRow.status == TaskStatus.PENDING.value)
                .order_by(TaskRow.priority.desc(), TaskRow.created_at, TaskRow.id)
                .limit(1)
            )
            row = session.scalars(stmt).first()
            if row is None:
                return None
            TaskRepository(session).update_status(row.id, TaskStatus.RUNNING)
            return row

    def _execute(self, row: TaskRow) -> None:
        handler = self.handlers.get(row.type)
        if handler is None:
            self._finish(row.id, TaskStatus.FAILED, error=f"没有注册的任务处理器: {row.type}")
            return
        context = TaskContext(row, self.database, self._lock)
        try:
            result = handler(context)
        except GenerationCancelledError:
            self._finish(row.id, TaskStatus.CANCELLED)
        except Exception as exc:  # worker 必须兜住所有异常
            logger.exception("任务执行失败: %s", row.id)
            self._finish(row.id, TaskStatus.FAILED, error=str(exc) or exc.__class__.__name__)
        else:
            self._finish(row.id, TaskStatus.SUCCEEDED, result=result or EMPTY_RESULT)

    def _finish(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """回写终态；若任务已被取消等导致流转非法，则忽略而不是抛错。"""

        with self._lock, self.database.session_scope() as session:
            try:
                TaskRepository(session).update_status(
                    task_id, status, result=result, error=error
                )
            except TaskTransitionError:
                logger.info("任务 %s 的状态已被外部改变，跳过回写 %s", task_id, status.value)


_default_worker: TaskWorker | None = None
_default_worker_guard = threading.Lock()


def get_worker(
    handlers: dict[str, TaskHandler] | None = None,
    **kwargs: Any,
) -> TaskWorker:
    """获取进程级默认 worker（不自动启动，由调用方决定 start/stop）。"""

    global _default_worker
    with _default_worker_guard:
        if _default_worker is None:
            _default_worker = TaskWorker(handlers=handlers, **kwargs)
        elif handlers:
            _default_worker.handlers.update(handlers)
        return _default_worker


def reset_worker() -> None:
    """停止并清空进程级默认 worker，供测试与关停复用。"""

    global _default_worker
    with _default_worker_guard:
        worker = _default_worker
        _default_worker = None
    if worker is not None:
        worker.stop()
