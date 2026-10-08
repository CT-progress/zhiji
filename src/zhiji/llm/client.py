"""OpenAI 兼容 LLM 客户端。"""

from __future__ import annotations

import json as _json
import os
import random
import time
from collections.abc import Iterable

import httpx

from zhiji.errors import LLMError
from zhiji.models import LLMModelConfig

# 连接/超时错误与 429、5xx 视为可重试；退避采用“指数 + 抖动”
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS or status_code >= 500


def _sleep_backoff(attempt: int) -> None:
    """指数退避并加入抖动，attempt 从 1 开始计数。"""

    delay = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    delay += random.uniform(0, _BACKOFF_BASE_SECONDS)
    time.sleep(delay)


class LLMClient:
    """针对单个模型配置的 OpenAI 兼容 chat/completions 客户端。"""

    def __init__(self, model: LLMModelConfig, timeout: float = 120.0) -> None:
        self.model = model
        self.timeout = timeout

    @property
    def api_key(self) -> str:
        return self.model.api_key or os.getenv("LLM_API_KEY", "")

    def _endpoint(self) -> str:
        return self.model.base_url.rstrip("/") + "/chat/completions"

    def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8000,
        response_format: dict | None = None,
    ) -> str:
        if not self.api_key:
            raise LLMError("模型 api_key 未配置", hint="请在 Web 配置页或 .env 中填写")
        payload = {
            "model": self.model.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        attempt = 0
        while True:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(self._endpoint(), json=payload, headers=headers)
                if response.status_code >= 400:
                    if _is_retryable_status(response.status_code) and attempt < _MAX_RETRIES:
                        attempt += 1
                        _sleep_backoff(attempt)
                        continue
                    raise LLMError(
                        f"LLM 接口返回 {response.status_code}: {response.text[:200]}"
                    )
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
            except httpx.HTTPError as exc:
                if attempt < _MAX_RETRIES:
                    attempt += 1
                    _sleep_backoff(attempt)
                    continue
                raise LLMError(f"无法连接 LLM 服务: {exc}") from exc
            except (KeyError, IndexError, _json.JSONDecodeError) as exc:
                raise LLMError(f"LLM 响应格式异常: {exc}") from exc

    def test_connection(self) -> str:
        return self.complete(
            [{"role": "user", "content": "请回复 OK"}],
            temperature=0,
            max_tokens=8,
        )

    def stream_chunks(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8000,
        response_format: dict | None = None,
    ) -> Iterable[str]:
        """流式返回 OpenAI 兼容接口的增量文本。"""

        if not self.api_key:
            raise LLMError("模型 api_key 未配置", hint="请在 Web 配置页或 .env 中填写")
        payload = {
            "model": self.model.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        attempt = 0
        while True:
            # 已开始向调用方吐出内容后不再重试，避免重复输出
            started = False
            try:
                with (
                    httpx.Client(timeout=self.timeout, follow_redirects=True) as client,
                    client.stream(
                        "POST", self._endpoint(), json=payload, headers=headers
                    ) as response,
                ):
                    if response.status_code >= 400:
                        body = response.read().decode("utf-8", errors="replace")
                        if _is_retryable_status(response.status_code) and attempt < _MAX_RETRIES:
                            attempt += 1
                            _sleep_backoff(attempt)
                            continue
                        raise LLMError(f"LLM 接口返回 {response.status_code}: {body[:200]}")
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            delta = _json.loads(data)["choices"][0]["delta"].get("content", "")
                        except (KeyError, IndexError, _json.JSONDecodeError):
                            continue
                        if delta:
                            started = True
                            yield delta
                    return
            except httpx.HTTPError as exc:
                if not started and attempt < _MAX_RETRIES:
                    attempt += 1
                    _sleep_backoff(attempt)
                    continue
                raise LLMError(f"无法连接 LLM 服务: {exc}") from exc
