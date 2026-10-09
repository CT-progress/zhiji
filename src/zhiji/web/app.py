"""FastAPI 本地配置服务。"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from zhiji.chat import (
    DEFAULT_TITLE,
    ChatNotFoundError,
    ChatStore,
    Conversation,
    build_chat_messages,
    extract_supported_links,
)
from zhiji.config import ConfigManager, mask_api_key
from zhiji.errors import (
    ConfigError,
    GenerationCancelledError,
    LLMError,
    TaskNotFoundError,
    TaskTransitionError,
    ZhijiError,
    error_response,
)
from zhiji.llm.client import LLMClient
from zhiji.models import LLMModelConfig, TaskStatus
from zhiji.storage import TaskRepository
from zhiji.storage.tables import TaskRow
from zhiji.tasks import TaskContext, TaskWorker, get_worker

STATIC_DIR = Path(__file__).resolve().parent / "static"
CONFIG_HTML = STATIC_DIR / "config.html"

# 异步生成器轮询队列超时的哨兵值（区别于队列里的 None 结束标记）
_QUEUE_EMPTY = object()
NOTE_TASK_TYPE = "note.generate"


def _queue_get(q: queue.Queue, timeout: float):
    """带超时地取队列；超时返回哨兵，便于 async 生成器之间穿插断连检查。"""

    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return _QUEUE_EMPTY


class CreateConversationRequest(BaseModel):
    title: str | None = None
    model_name: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    model_name: str | None = None


class ChatRequest(BaseModel):
    conversation_id: str
    message: str = Field(min_length=1)
    model_name: str | None = None


class SubmitTaskRequest(BaseModel):
    type: str = Field(min_length=1)
    platform: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0


class GenerateNoteRequest(BaseModel):
    url: str = Field(min_length=1)
    model: str | None = None
    conversation_id: str | None = None


def _conv_summary(conversation: Conversation) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "model_name": conversation.model_name,
        "platform": conversation.platform,
        "has_note": bool(conversation.note_content),
        "message_count": len(conversation.messages),
        "updated_at": conversation.updated_at.isoformat(),
    }


def _conv_public(conversation: Conversation) -> dict:
    data = conversation.model_dump(mode="json")
    data["message_count"] = len(conversation.messages)
    return data


def _task_public(row: TaskRow) -> dict:
    def _iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": row.id,
        "type": row.type,
        "platform": row.platform,
        "status": row.status,
        "priority": row.priority,
        "progress": row.progress,
        "payload": json.loads(row.payload_json or "{}"),
        "result": json.loads(row.result_json) if row.result_json else None,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _load_task(worker: TaskWorker, task_id: str) -> TaskRow:
    with worker.database.session_scope() as session:
        row = TaskRepository(session).find(task_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=error_response(TaskNotFoundError(f"任务不存在: {task_id}")),
        )
    return row


def _http_error(status_code: int, exc: ZhijiError) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error_response(exc))


def _get_or_404(store: ChatStore, conversation_id: str) -> Conversation:
    try:
        return store.get(conversation_id)
    except ChatNotFoundError as exc:
        raise HTTPException(status_code=404, detail=error_response(exc)) from exc


def _resolve_model(config: ConfigManager, model_name: str | None):
    model = None
    if model_name:
        model = config.get_model(model_name)
        if model is None or not model.enabled:
            model = config.get().default_model()
    else:
        model = config.get().default_model()
    if model is None:
        raise LLMError("还没有可用模型", hint="请先到设置中添加一个模型")
    return model


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _strip_frontmatter(markdown: str) -> str:
    """去掉 Markdown 文件顶部的 YAML frontmatter，用于页面展示和对话上下文。"""
    text = markdown.lstrip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return markdown


def create_app(
    config_manager: ConfigManager | None = None,
    data_dir: str | Path | None = None,
    token: str | None = None,
    worker: TaskWorker | None = None,
) -> FastAPI:
    config = config_manager or ConfigManager()
    store = ChatStore(data_dir)
    task_worker = worker

    def _generate_note_result(
        *,
        url: str,
        model_name: str | None,
        conversation_id: str | None,
        on_delta: Callable[[str], None] | None,
        on_progress: Callable[[str, str, str], None] | None,
        cancel_callback: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        """执行一次笔记流水线，返回前端与任务表共用的结果结构。"""

        from zhiji.pipeline import NotePipeline

        receipt = NotePipeline(config).run(
            url,
            model_name=model_name,
            stream_callback=on_delta,
            progress_callback=on_progress,
            cancel_callback=cancel_callback,
        )
        markdown = receipt.note_path.read_text(encoding="utf-8")
        note_body = _strip_frontmatter(markdown)
        if conversation_id:
            _get_or_404(store, conversation_id)
            conversation = store.attach_note(
                conversation_id,
                platform=receipt.platform,
                source_url=url,
                note_content=note_body,
                note_path=str(receipt.note_path),
                title=receipt.title,
            )
        else:
            conversation = store.create(title=(receipt.title or DEFAULT_TITLE)[:60])
            conversation = store.attach_note(
                conversation.id,
                platform=receipt.platform,
                source_url=url,
                note_content=note_body,
                note_path=str(receipt.note_path),
            )
        return {
            "done": True,
            "note": {
                "title": receipt.title,
                "path": str(receipt.note_path),
                "markdown": note_body,
                "transcript_path": (
                    str(receipt.transcript_path) if receipt.transcript_path else None
                ),
                "platform": receipt.platform,
                "source_url": url,
            },
            "conversation": _conv_public(conversation),
            "warnings": receipt.warnings,
        }

    def _note_progress(context: TaskContext, stage: str, message: str, status: str) -> None:
        """把流水线阶段映射为任务进度，并向前端转发阶段事件。"""

        start_progress = {
            "parse": 5,
            "fetch": 20,
            "transcribe": 45,
            "generate": 70,
            "save": 95,
        }
        done_progress = {
            "parse": 15,
            "fetch": 40,
            "transcribe": 65,
            "generate": 90,
            "save": 100,
        }
        progress = done_progress.get(stage, 99) if status == "ok" else start_progress.get(stage, 1)
        context.report(progress)
        context.emit({"stage": stage, "status": status, "message": message})

    def _note_handler(context: TaskContext) -> dict[str, Any]:
        """``note.generate`` 任务处理器：页面断开也继续执行到终态。"""

        url = str(context.payload.get("url") or "").strip()
        if not url:
            raise ValueError("URL 不能为空")
        model_name = context.payload.get("model") or context.payload.get("model_name")
        conversation_id = context.payload.get("conversation_id")

        def on_progress(stage: str, message: str, status: str = "start") -> None:
            _note_progress(context, stage, message, status)

        def on_delta(chunk: str) -> None:
            context.emit({"delta": chunk})

        return _generate_note_result(
            url=url,
            model_name=str(model_name) if model_name else None,
            conversation_id=str(conversation_id) if conversation_id else None,
            on_delta=on_delta,
            on_progress=on_progress,
            cancel_callback=context.is_cancelled,
        )

    def _install_note_handler(active: TaskWorker) -> None:
        """安装默认笔记处理器，同时允许测试或扩展方覆盖它。"""

        if NOTE_TASK_TYPE not in active.handlers:
            active.handlers[NOTE_TASK_TYPE] = _note_handler

    def _resolve_worker() -> TaskWorker:
        nonlocal task_worker
        if task_worker is None:
            task_worker = get_worker()
        _install_note_handler(task_worker)
        return task_worker

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        active = _resolve_worker()
        active.start()
        try:
            yield
        finally:
            active.stop()

    app = FastAPI(
        title="知记本地配置", version="0.1.0", docs_url="/docs", lifespan=lifespan
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if token:

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            """非本机绑定时校验访问令牌，避免 API Key / Cookie 接口暴露在局域网。"""

            provided = (
                request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                or request.query_params.get("token")
                or request.cookies.get("zhiji_token")
            )
            if request.url.path.startswith("/api/") and provided != token:
                return JSONResponse(
                    status_code=401,
                    content=error_response(
                        ZhijiError("缺少或无效的访问令牌", hint="请使用带 ?token= 的地址访问")
                    ),
                )
            response = await call_next(request)
            if request.url.path == "/" and request.query_params.get("token") == token:
                response.set_cookie("zhiji_token", token, httponly=True, samesite="strict")
            return response

    @app.exception_handler(ZhijiError)
    async def zhiji_error_handler(request, exc: ZhijiError):
        return JSONResponse(status_code=400, content=error_response(exc))

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "config": str(config.path)}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(CONFIG_HTML)

    @app.get("/api/models")
    def list_models() -> dict:
        return {"models": [_public(m) for m in config.get().models]}

    @app.post("/api/models")
    def create_model(model: LLMModelConfig) -> dict:
        return {"model": _public(config.add_model(model))}

    @app.put("/api/models/{name}")
    def update_model(name: str, data: dict[str, Any]) -> dict:
        return {"model": _public(config.update_model(name, data))}

    @app.delete("/api/models/{name}")
    def delete_model(name: str) -> dict:
        config.delete_model(name)
        return {"ok": True}

    @app.post("/api/models/{name}/set-default")
    def set_default(name: str) -> dict:
        return {"model": _public(config.set_default_model(name))}

    @app.post("/api/models/{name}/test")
    def test_model(name: str) -> dict:
        model = config.get_model(name)
        if model is None:
            raise ConfigError(f"模型不存在: {name}")
        reply = LLMClient(model).test_connection()
        return {"ok": True, "reply": reply}

    @app.get("/api/settings")
    def get_settings() -> dict:
        return config.get().settings.model_dump(mode="json")

    @app.put("/api/settings")
    def put_settings(data: dict[str, Any]) -> dict:
        return config.update_settings(data).model_dump(mode="json")

    @app.get("/api/conversations")
    def list_conversations() -> dict:
        return {"conversations": [_conv_summary(c) for c in store.list()]}

    @app.post("/api/conversations")
    def create_conversation(payload: CreateConversationRequest) -> dict:
        return {"conversation": _conv_public(store.create(payload.title or DEFAULT_TITLE, payload.model_name))}

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> dict:
        return {"conversation": _conv_public(_get_or_404(store, conversation_id))}

    @app.patch("/api/conversations/{conversation_id}")
    def update_conversation(conversation_id: str, payload: UpdateConversationRequest) -> dict:
        return {"conversation": _conv_public(store.update(conversation_id, title=payload.title, model_name=payload.model_name))}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict:
        store.delete(conversation_id)
        return {"ok": True}

    @app.get("/api/conversations/{conversation_id}/export")
    def export_conversation(conversation_id: str) -> dict:
        conversation = _get_or_404(store, conversation_id)
        lines = [f"# {conversation.title}\n"]
        for msg in conversation.messages:
            role = "**我**" if msg.role == "user" else "**知记**"
            lines.append(f"## {role}\n")
            lines.append(f"{msg.content}\n")
        return {"markdown": "\n".join(lines)}

    @app.post("/api/note/save")
    def save_note(payload: dict) -> dict:
        content = payload.get("content", "")
        path_str = payload.get("path")
        output_root = config.resolve_output_dir().resolve()
        if path_str:
            # 覆盖已有笔记文件（“设为当前笔记”场景），仅允许写入输出目录内
            path = Path(path_str).resolve()
            if path.suffix.lower() != ".md":
                raise HTTPException(status_code=400, detail="仅支持 Markdown 文件")
            if not path.is_relative_to(output_root):
                raise HTTPException(status_code=400, detail="只能覆盖输出目录内的笔记文件")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        else:
            title = payload.get("title", "未命名笔记")
            safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:80]
            notes_dir = output_root / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            path = notes_dir / f"{safe_title}.md"
            path.write_text(content, encoding="utf-8")
        conversation_id = payload.get("conversation_id")
        if conversation_id:
            store.attach_note(
                conversation_id,
                note_content=_strip_frontmatter(content),
                note_path=str(path),
            )
        return {"ok": True, "path": str(path)}

    @app.post("/api/note/generate")
    def generate_note(payload: GenerateNoteRequest) -> dict:
        url = payload.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL 不能为空")
        if payload.conversation_id:
            _get_or_404(store, payload.conversation_id)
        task_payload: dict[str, Any] = {"url": url}
        if payload.model:
            task_payload["model"] = payload.model
        if payload.conversation_id:
            task_payload["conversation_id"] = payload.conversation_id
        active = _resolve_worker()
        # 生成接口必须使用当前应用配置的处理器；低层任务 API 仍允许外部覆盖。
        active.handlers[NOTE_TASK_TYPE] = _note_handler
        task_id = active.submit(task_type=NOTE_TASK_TYPE, payload=task_payload)
        return {"task": _task_public(_load_task(active, task_id))}

    @app.post("/api/note/generate/stream")
    def generate_note_stream(payload: dict, request: Request) -> StreamingResponse:
        url = (payload.get("url") or "").strip()
        model_name = payload.get("model")
        conversation_id = payload.get("conversation_id")
        if not url:
            raise HTTPException(status_code=400, detail="URL 不能为空")

        async def event_source():
            events: queue.Queue = queue.Queue()
            cancelled = threading.Event()
            loop = asyncio.get_running_loop()

            def on_delta(chunk: str) -> None:
                events.put({"delta": chunk})

            def on_progress(stage: str, message: str, status: str = "start") -> None:
                events.put({"stage": stage, "status": status, "message": message})

            def work() -> None:
                try:
                    events.put(
                        _generate_note_result(
                            url=url,
                            model_name=model_name,
                            conversation_id=conversation_id,
                            on_delta=on_delta,
                            on_progress=on_progress,
                            cancel_callback=cancelled.is_set,
                        )
                    )
                except GenerationCancelledError:
                    # 客户端已断连，生成的中间态无需回传
                    pass
                except Exception as exc:  # noqa: BLE001
                    events.put({"error": error_response(exc)["error"]})
                finally:
                    events.put(None)

            threading.Thread(target=work, daemon=True).start()
            try:
                while True:
                    if await request.is_disconnected():
                        cancelled.set()
                        break
                    item = await loop.run_in_executor(None, _queue_get, events, 0.5)
                    if item is _QUEUE_EMPTY:
                        continue
                    if item is None:
                        break
                    yield _sse(item)
            finally:
                # 生成器被提前关闭（客户端断开）时，通知后台线程尽早停止
                cancelled.set()

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/platforms")
    def list_platforms() -> dict:
        from zhiji.platforms.bilibili_cookies import load_profile as load_bilibili_profile
        from zhiji.platforms.douyin_cookies import load_profile as load_douyin_profile
        from zhiji.platforms.xiaohongshu_cookies import load_profile as load_xiaohongshu_profile
        from zhiji.platforms.zhihu_cookies import load_profile as load_zhihu_profile

        enabled = config.get().settings.platforms
        cards = [
            {
                "key": "douyin",
                "name": "抖音",
                "desc": "短视频 → 音频拦截 + 本地转写",
                "placeholder": "https://v.douyin.com/xxxx/ 或视频页链接",
                "enabled": enabled.douyin,
                "cookie_configured": bool(load_douyin_profile()),
            },
            {
                "key": "bilibili",
                "name": "B 站",
                "desc": "官方字幕（可选登录）/ 音频转写笔记",
                "placeholder": "https://www.bilibili.com/video/BV…",
                "enabled": enabled.bilibili,
                "cookie_configured": bool(load_bilibili_profile()),
            },
            {
                "key": "zhihu",
                "name": "知乎",
                "desc": "文章 / 回答 → 图文笔记",
                "placeholder": "https://zhuanlan.zhihu.com/p/…",
                "enabled": enabled.zhihu,
                "cookie_configured": bool(load_zhihu_profile()),
            },
            {
                "key": "xiaohongshu",
                "name": "小红书",
                "desc": "图文 / 视频笔记 → 正文与媒体信息",
                "placeholder": "https://www.xiaohongshu.com/explore/… 或 https://xhslink.com/…",
                "enabled": enabled.xiaohongshu,
                "cookie_configured": bool(load_xiaohongshu_profile()),
            },
        ]
        return {"platforms": cards}

    @app.get("/api/cookies/douyin")
    def get_douyin_cookie() -> dict:
        from zhiji.platforms.douyin_cookies import load_profile
        profile = load_profile()
        if profile:
            return {
                "configured": True,
                "saved_at": profile.saved_at,
                "cookie_length": len(profile.cookie),
            }
        return {"configured": False}

    @app.post("/api/cookies/douyin")
    def save_douyin_cookie(payload: dict) -> dict:
        from zhiji.platforms.douyin_cookies import save_profile
        cookie = payload.get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=400, detail="Cookie 不能为空")
        save_profile(cookie, payload.get("user_agent", ""))
        return {"ok": True}

    @app.delete("/api/cookies/douyin")
    def delete_douyin_cookie() -> dict:
        from zhiji.platforms.douyin_cookies import profile_path
        path = profile_path()
        if path.exists():
            path.unlink()
        return {"ok": True}

    @app.get("/api/cookies/zhihu")
    def get_zhihu_cookie() -> dict:
        from zhiji.platforms.zhihu_cookies import load_profile
        profile = load_profile()
        if profile:
            return {
                "configured": True,
                "saved_at": profile.saved_at,
                "cookie_length": len(profile.cookie),
            }
        return {"configured": False}

    @app.post("/api/cookies/zhihu")
    def save_zhihu_cookie(payload: dict) -> dict:
        from zhiji.platforms.zhihu_cookies import save_profile
        cookie = payload.get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=400, detail="Cookie 不能为空")
        if "d_c0" not in cookie:
            raise HTTPException(status_code=400, detail="Cookie 需包含 d_c0 字段")
        save_profile(cookie, payload.get("user_agent", ""))
        return {"ok": True}

    @app.delete("/api/cookies/zhihu")
    def delete_zhihu_cookie() -> dict:
        from zhiji.platforms.zhihu_cookies import profile_path
        path = profile_path()
        if path.exists():
            path.unlink()
        return {"ok": True}

    @app.get("/api/cookies/bilibili")
    def get_bilibili_cookie() -> dict:
        from zhiji.platforms.bilibili_cookies import load_profile
        profile = load_profile()
        if profile:
            return {
                "configured": True,
                "saved_at": profile.saved_at,
                "cookie_length": len(profile.cookie),
            }
        return {"configured": False}

    @app.post("/api/cookies/bilibili")
    def save_bilibili_cookie(payload: dict) -> dict:
        from zhiji.platforms.bilibili_cookies import save_profile
        cookie = payload.get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=400, detail="Cookie 不能为空")
        if "SESSDATA" not in cookie:
            raise HTTPException(status_code=400, detail="Cookie 需包含 SESSDATA 字段")
        save_profile(cookie, payload.get("user_agent", ""))
        return {"ok": True}

    @app.delete("/api/cookies/bilibili")
    def delete_bilibili_cookie() -> dict:
        from zhiji.platforms.bilibili_cookies import profile_path
        path = profile_path()
        if path.exists():
            path.unlink()
        return {"ok": True}

    @app.get("/api/cookies/xiaohongshu")
    def get_xiaohongshu_cookie() -> dict:
        from zhiji.platforms.xiaohongshu_cookies import load_profile
        profile = load_profile()
        if profile:
            return {
                "configured": True,
                "saved_at": profile.saved_at,
                "cookie_length": len(profile.cookie),
            }
        return {"configured": False}

    @app.post("/api/cookies/xiaohongshu")
    def save_xiaohongshu_cookie(payload: dict) -> dict:
        from zhiji.platforms.xiaohongshu_cookies import save_profile
        cookie = payload.get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=400, detail="Cookie 不能为空")
        if "web_session=" not in cookie:
            raise HTTPException(status_code=400, detail="Cookie 需包含 web_session 字段")
        save_profile(cookie, payload.get("user_agent", ""))
        return {"ok": True}

    @app.delete("/api/cookies/xiaohongshu")
    def delete_xiaohongshu_cookie() -> dict:
        from zhiji.platforms.xiaohongshu_cookies import profile_path
        path = profile_path()
        if path.exists():
            path.unlink()
        return {"ok": True}

    @app.post("/api/chat")
    def chat(payload: ChatRequest) -> dict:
        conversation_id = payload.conversation_id
        _get_or_404(store, conversation_id)
        model = _resolve_model(config, payload.model_name)
        store.update(conversation_id, model_name=model.name)
        message = payload.message.strip()
        source_context = extract_supported_links(message)
        store.append_message(conversation_id, "user", message)
        conversation = store.get(conversation_id)
        reply = LLMClient(model).complete(build_chat_messages(conversation, source_context))
        conversation = store.append_message(conversation_id, "assistant", reply)
        return {"reply": reply, "conversation": _conv_public(conversation)}

    @app.post("/api/chat/stream")
    def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        conversation_id = payload.conversation_id
        _get_or_404(store, conversation_id)
        model = _resolve_model(config, payload.model_name)
        store.update(conversation_id, model_name=model.name)
        message = payload.message.strip()
        source_context = extract_supported_links(message)
        store.append_message(conversation_id, "user", message)
        messages = build_chat_messages(store.get(conversation_id), source_context)

        async def event_source():
            events: queue.Queue = queue.Queue()
            cancelled = threading.Event()
            loop = asyncio.get_running_loop()

            def work() -> None:
                collected: list[str] = []
                try:
                    events.put({"meta": {"conversation_id": conversation_id, "model": model.name}})
                    for chunk in LLMClient(model).stream_chunks(messages):
                        if cancelled.is_set():
                            break
                        collected.append(chunk)
                        events.put({"delta": chunk})
                except ZhijiError as exc:
                    events.put({"error": error_response(exc)["error"]})
                except Exception as exc:  # noqa: BLE001
                    events.put({"error": error_response(exc)["error"]})
                finally:
                    if collected:
                        store.append_message(conversation_id, "assistant", "".join(collected))
                    events.put({
                        "done": True,
                        "conversation": _conv_public(store.get(conversation_id)),
                    })
                    events.put(None)

            threading.Thread(target=work, daemon=True).start()
            try:
                while True:
                    if await request.is_disconnected():
                        cancelled.set()
                        break
                    item = await loop.run_in_executor(None, _queue_get, events, 0.5)
                    if item is _QUEUE_EMPTY:
                        continue
                    if item is None:
                        break
                    yield _sse(item)
            finally:
                cancelled.set()

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 任务 API（阶段 2.3）-------------------------------------

    @app.post("/api/tasks")
    def submit_task(payload: SubmitTaskRequest) -> dict:
        active = _resolve_worker()
        task_id = active.submit(
            task_type=payload.type,
            platform=payload.platform,
            payload=payload.payload,
            priority=payload.priority,
        )
        return {"task": _task_public(_load_task(active, task_id))}

    @app.get("/api/tasks")
    def list_tasks(
        status: str | None = None,
        platform: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        active = _resolve_worker()
        status_filter: TaskStatus | None = None
        if status:
            try:
                status_filter = TaskStatus(status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=f"未知的任务状态: {status}"
                ) from exc
        with active.database.session_scope() as session:
            rows = TaskRepository(session).list(
                status=status_filter,
                platform=platform,
                limit=max(1, min(limit, 200)),
                offset=max(0, offset),
            )
        return {"tasks": [_task_public(row) for row in rows]}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return {"task": _task_public(_load_task(_resolve_worker(), task_id))}

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> dict:
        active = _resolve_worker()
        try:
            row = active.cancel(task_id)
        except TaskNotFoundError as exc:
            raise _http_error(404, exc) from exc
        except TaskTransitionError as exc:
            raise _http_error(409, exc) from exc
        return {"task": _task_public(row)}

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: str, request: Request) -> StreamingResponse:
        active = _resolve_worker()
        _load_task(active, task_id)

        async def event_source():
            channel = active.bus.subscribe(task_id)
            loop = asyncio.get_running_loop()
            last: dict | None = None
            terminal = False
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    row = _load_task(active, task_id)
                    snapshot = _task_public(row)
                    if snapshot != last:
                        yield _sse({"task": snapshot})
                        last = snapshot

                    while True:
                        try:
                            item = channel.get_nowait()
                        except queue.Empty:
                            break
                        yield _sse(item)

                    if TaskStatus(row.status).is_terminal():
                        terminal = True
                        break
                    item = await loop.run_in_executor(None, _queue_get, channel, 0.3)
                    if item is _QUEUE_EMPTY:
                        continue
                    yield _sse(item)
            finally:
                active.bus.unsubscribe(task_id, channel, clear_history=terminal)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _public(model: LLMModelConfig) -> dict:
    data = model.model_dump(mode="json")
    data["api_key"] = mask_api_key(data.get("api_key", ""))
    return data
