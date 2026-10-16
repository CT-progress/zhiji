"""Worker 执行服务：接收任务派发，执行 pipeline，上报回调。

复用现有的 ``TaskWorker`` / ``TaskContext`` / ``NotePipeline``，
在外层套上回调上报与 trace 透传，不改动原有执行逻辑。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from zhiji.config import ConfigManager
from zhiji.errors import GenerationCancelledError
from zhiji.tasks import TaskContext, TaskWorker
from zhiji.worker.callback import CallbackClient
from zhiji.worker.models import TaskDispatch, WorkerHealth
from zhiji.worker.trace import TraceContext

logger = logging.getLogger(__name__)

NOTE_TASK_TYPE = "note.generate"


class WorkerService:
    """接收控制面派发的任务，在后台执行并通过回调上报进度。"""

    def __init__(
        self,
        callback: CallbackClient | None = None,
        *,
        config_manager: ConfigManager | None = None,
        worker: TaskWorker | None = None,
    ) -> None:
        self.callback = callback or CallbackClient()
        self.config = config_manager or ConfigManager()
        self._worker = worker
        self._running: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._cancel_flags: dict[str, threading.Event] = {}

    @property
    def worker(self) -> TaskWorker:
        if self._worker is None:
            self._worker = TaskWorker()
        self._install_handlers(self._worker)
        return self._worker

    def _install_handlers(self, worker: TaskWorker) -> None:
        """安装笔记生成处理器（如果尚未注册）。"""

        if NOTE_TASK_TYPE not in worker.handlers:
            worker.handlers[NOTE_TASK_TYPE] = self._note_handler

    def health(self) -> WorkerHealth:
        return WorkerHealth(
            status="ok",
            version="0.1.0",
            capabilities=list(self.worker.handlers.keys()),
        )

    def dispatch(self, dispatch: TaskDispatch) -> bool:
        """接收任务派发，启动后台线程执行。

        幂等：同一 taskId 已在运行则返回 False。
        """

        with self._lock:
            if dispatch.taskId in self._running:
                return False
            cancel_event = threading.Event()
            self._cancel_flags[dispatch.taskId] = cancel_event
            thread = threading.Thread(
                target=self._execute_dispatch,
                args=(dispatch, cancel_event),
                name=f"zhiji-worker-{dispatch.taskId}",
                daemon=True,
            )
            self._running[dispatch.taskId] = thread
        thread.start()
        return True

    def cancel(self, task_id: str) -> bool:
        """设置取消标志，由 pipeline 协作式退出。"""

        with self._lock:
            event = self._cancel_flags.get(task_id)
            if event is None:
                return False
            event.set()
        return True

    def _execute_dispatch(
        self, dispatch: TaskDispatch, cancel_event: threading.Event
    ) -> None:
        """后台线程：执行任务并上报回调。"""

        task_id = dispatch.taskId
        trace_id = dispatch.traceId

        with TraceContext(trace_id):
            try:
                self.callback.task_started(task_id, trace_id=trace_id)
                result = self._run_pipeline(dispatch, cancel_event, trace_id)
                self.callback.task_succeeded(
                    task_id, result, trace_id=trace_id
                )
            except GenerationCancelledError:
                self.callback.task_failed(
                    task_id,
                    error="任务已取消",
                    step="cancel",
                    trace_id=trace_id,
                )
            except Exception as exc:
                logger.exception("worker task failed: %s", task_id)
                self.callback.task_failed(
                    task_id,
                    error=str(exc) or exc.__class__.__name__,
                    trace_id=trace_id,
                )
            finally:
                with self._lock:
                    self._running.pop(task_id, None)
                    self._cancel_flags.pop(task_id, None)

    def _run_pipeline(
        self,
        dispatch: TaskDispatch,
        cancel_event: threading.Event,
        trace_id: str,
    ) -> dict[str, Any]:
        """执行笔记生成 pipeline，各阶段上报回调。"""

        from zhiji.pipeline import NotePipeline

        url = str(dispatch.input.get("url") or "").strip()
        if not url:
            raise ValueError("URL 不能为空")
        model_name = dispatch.input.get("model") or dispatch.input.get("model_name")

        stage_progress = {
            "parse": (5, 15),
            "fetch": (20, 40),
            "transcribe": (45, 65),
            "generate": (70, 90),
            "save": (95, 100),
        }

        def on_progress(stage: str, message: str, status: str = "start") -> None:
            starts, ends = stage_progress.get(stage, (1, 99))
            progress = ends if status == "ok" else starts
            if status == "ok":
                self.callback.step_succeeded(
                    dispatch.taskId, stage, message, trace_id=trace_id
                )
            else:
                self.callback.step_progress(
                    dispatch.taskId, stage, progress, message, trace_id=trace_id
                )

        def on_delta(chunk: str) -> None:
            self.callback.report_event(
                dispatch.taskId,
                None,  # type: ignore[arg-type]
                trace_id=trace_id,
                data={"delta": chunk},
            )

        def is_cancelled() -> bool:
            return cancel_event.is_set()

        pipeline = NotePipeline(self.config)
        receipt = pipeline.run(
            url,
            model_name=str(model_name) if model_name else None,
            stream_callback=None,  # delta 回调通过 step.progress 上报，不逐字转发
            progress_callback=on_progress,
            cancel_callback=is_cancelled,
        )

        return {
            "note": {
                "title": receipt.title,
                "path": str(receipt.note_path),
                "platform": receipt.platform,
                "content_id": receipt.content_id,
                "updated": receipt.updated,
                "warnings": receipt.warnings,
            },
            "transcript_path": (
                str(receipt.transcript_path) if receipt.transcript_path else None
            ),
        }

    def _note_handler(self, context: TaskContext) -> dict[str, Any]:
        """兼容现有 TaskWorker 的处理器签名（用于本地直接提交场景）。"""

        url = str(context.payload.get("url") or "").strip()
        if not url:
            raise ValueError("URL 不能为空")
        model_name = context.payload.get("model") or context.payload.get("model_name")

        from zhiji.pipeline import NotePipeline

        def on_progress(stage: str, message: str, status: str = "start") -> None:
            starts = {"parse": 5, "fetch": 20, "transcribe": 45, "generate": 70, "save": 95}
            ends = {"parse": 15, "fetch": 40, "transcribe": 65, "generate": 90, "save": 100}
            progress = ends.get(stage, 99) if status == "ok" else starts.get(stage, 1)
            context.report(progress)
            context.emit({"stage": stage, "status": status, "message": message})

        pipeline = NotePipeline(self.config)
        receipt = pipeline.run(
            url,
            model_name=str(model_name) if model_name else None,
            progress_callback=on_progress,
            cancel_callback=context.is_cancelled,
        )
        return {
            "note": {
                "title": receipt.title,
                "path": str(receipt.note_path),
                "platform": receipt.platform,
                "content_id": receipt.content_id,
                "updated": receipt.updated,
                "warnings": receipt.warnings,
            },
        }
