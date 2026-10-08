"""FastAPI 本地配置服务。"""

from __future__ import annotations

import json
import queue
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from zhiji.chat import (
    DEFAULT_TITLE,
    ChatNotFoundError,
    ChatStore,
    Conversation,
    build_chat_messages,
    fetch_link_context,
)
from zhiji.config import ConfigManager, mask_api_key
from zhiji.errors import ConfigError, LLMError, ZhijiError, error_response
from zhiji.llm.client import LLMClient
from zhiji.models import LLMModelConfig

CONFIG_HTML = Path(__file__).resolve().parent / "static" / "config.html"


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
) -> FastAPI:
    config = config_manager or ConfigManager()
    store = ChatStore(data_dir)
    app = FastAPI(title="知记本地配置", version="0.1.0", docs_url="/docs")

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

    @app.post("/api/note/generate/stream")
    def generate_note_stream(payload: dict) -> StreamingResponse:
        url = (payload.get("url") or "").strip()
        model_name = payload.get("model")
        conversation_id = payload.get("conversation_id")
        if not url:
            raise HTTPException(status_code=400, detail="URL 不能为空")

        def event_source():
            from zhiji.pipeline import NotePipeline

            events: queue.Queue = queue.Queue()

            def work() -> None:
                try:
                    pipeline = NotePipeline(config)
                    receipt = pipeline.run(
                        url,
                        model_name=model_name,
                        stream_callback=lambda chunk: events.put({"delta": chunk}),
                        progress_callback=lambda stage, message, status="start": events.put(
                            {"stage": stage, "status": status, "message": message}
                        ),
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
                    events.put({
                        "done": True,
                        "note": {
                            "title": receipt.title,
                            "path": str(receipt.note_path),
                            "markdown": note_body,
                            "transcript_path": str(receipt.transcript_path) if receipt.transcript_path else None,
                        },
                        "conversation": _conv_public(conversation),
                        "warnings": receipt.warnings,
                    })
                except Exception as exc:  # noqa: BLE001
                    events.put({"error": error_response(exc)["error"]})
                finally:
                    events.put(None)

            threading.Thread(target=work, daemon=True).start()
            while True:
                item = events.get()
                if item is None:
                    break
                yield _sse(item)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/platforms")
    def list_platforms() -> dict:
        from zhiji.platforms.bilibili_cookies import load_profile as load_bilibili_profile
        from zhiji.platforms.douyin_cookies import load_profile as load_douyin_profile

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
                "cookie_configured": None,
            },
            {
                "key": "xiaohongshu",
                "name": "小红书",
                "desc": "即将支持",
                "placeholder": "",
                "enabled": False,
                "cookie_configured": None,
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

    @app.post("/api/chat")
    def chat(payload: ChatRequest) -> dict:
        conversation_id = payload.conversation_id
        _get_or_404(store, conversation_id)
        model = _resolve_model(config, payload.model_name)
        store.update(conversation_id, model_name=model.name)
        message = payload.message.strip()
        source_context = fetch_link_context(message)
        store.append_message(conversation_id, "user", message)
        conversation = store.get(conversation_id)
        reply = LLMClient(model).complete(build_chat_messages(conversation, source_context))
        conversation = store.append_message(conversation_id, "assistant", reply)
        return {"reply": reply, "conversation": _conv_public(conversation)}

    @app.post("/api/chat/stream")
    def chat_stream(payload: ChatRequest) -> StreamingResponse:
        conversation_id = payload.conversation_id
        _get_or_404(store, conversation_id)
        model = _resolve_model(config, payload.model_name)
        store.update(conversation_id, model_name=model.name)
        message = payload.message.strip()
        source_context = fetch_link_context(message)
        store.append_message(conversation_id, "user", message)
        messages = build_chat_messages(store.get(conversation_id), source_context)
        collected: list[str] = []

        def event_source():
            yield _sse({"meta": {"conversation_id": conversation_id, "model": model.name}})
            try:
                for chunk in LLMClient(model).stream_chunks(messages):
                    collected.append(chunk)
                    yield _sse({"delta": chunk})
            except ZhijiError as exc:
                yield _sse({"error": error_response(exc)["error"]})
            finally:
                if collected:
                    store.append_message(conversation_id, "assistant", "".join(collected))
                yield _sse({"done": True, "conversation": _conv_public(store.get(conversation_id))})

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
