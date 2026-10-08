"""OpenAI 兼容 LLM 客户端。"""

from __future__ import annotations

import json as _json
import os
from collections.abc import Iterable

import httpx

from zhiji.errors import LLMError
from zhiji.models import LLMModelConfig


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
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self._endpoint(), json=payload, headers=headers)
            if response.status_code >= 400:
                raise LLMError(f"LLM 接口返回 {response.status_code}: {response.text[:200]}")
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPError as exc:
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
        try:
            with (
                httpx.Client(timeout=self.timeout, follow_redirects=True) as client,
                client.stream("POST", self._endpoint(), json=payload, headers=headers) as response,
            ):
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")
                    raise LLMError(f"LLM 接口返回 {response.status_code}: {body[:200]}")
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = _json.loads(data)["choices"][0]["delta"].get("content", "")
                    except (KeyError, IndexError, _json.JSONDecodeError):
                        continue
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise LLMError(f"无法连接 LLM 服务: {exc}") from exc
