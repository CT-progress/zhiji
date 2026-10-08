"""LLM 客户端重试逻辑测试（全部 mock，无网络）。"""

from __future__ import annotations

from typing import Self

import httpx
import pytest

from zhiji.errors import LLMError
from zhiji.llm import client as client_module
from zhiji.llm.client import LLMClient
from zhiji.models import LLMModelConfig


def _model() -> LLMModelConfig:
    return LLMModelConfig(
        name="t",
        base_url="https://example.com/v1",
        model="m",
        api_key="sk-test",
    )


class _FakeStream:
    """模拟 httpx 的流式响应上下文管理器。"""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def iter_lines(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return b""


def test_complete_retries_transport_error(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)
    calls = {"n": 0}

    def fake_post(self, url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert LLMClient(_model()).complete([{"role": "user", "content": "hi"}]) == "ok"
    assert calls["n"] == 3


def test_complete_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)

    def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with pytest.raises(LLMError):
        LLMClient(_model()).complete([{"role": "user", "content": "hi"}])


def test_complete_retries_5xx(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)
    calls = {"n": 0}

    def fake_post(self, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert LLMClient(_model()).complete([{"role": "user", "content": "hi"}]) == "ok"
    assert calls["n"] == 2


def test_complete_does_not_retry_4xx(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)
    calls = {"n": 0}

    def fake_post(self, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with pytest.raises(LLMError):
        LLMClient(_model()).complete([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1


def test_stream_chunks_retries_open_error(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)
    calls = {"n": 0}

    def fake_stream(self, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return _FakeStream(
            [
                'data: {"choices":[{"delta":{"content":"hi"}}]}',
                "data: [DONE]",
            ]
        )

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)

    chunks = list(LLMClient(_model()).stream_chunks([{"role": "user", "content": "x"}]))
    assert chunks == ["hi"]
    assert calls["n"] == 2


def test_stream_chunks_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)

    def fake_stream(self, method, url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)

    with pytest.raises(LLMError):
        list(LLMClient(_model()).stream_chunks([{"role": "user", "content": "x"}]))


class _FailingAfterFirstLine(_FakeStream):
    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
        raise httpx.ReadError("stream interrupted")


def test_stream_chunks_does_not_retry_after_first_delta(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)
    calls = {"n": 0}

    def fake_stream(self, method, url, **kwargs):
        calls["n"] += 1
        return _FailingAfterFirstLine([])

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)

    stream = LLMClient(_model()).stream_chunks([{"role": "user", "content": "x"}])
    assert next(iter(stream)) == "hi"
    with pytest.raises(LLMError):
        next(stream)
    assert calls["n"] == 1


def test_stream_chunks_does_not_retry_non_retryable_status(monkeypatch):
    monkeypatch.setattr(client_module, "_sleep_backoff", lambda _attempt: None)
    calls = {"n": 0}

    def fake_stream(self, method, url, **kwargs):
        calls["n"] += 1
        return _FakeStream([], status_code=400)

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)

    with pytest.raises(LLMError):
        list(LLMClient(_model()).stream_chunks([{"role": "user", "content": "x"}]))
    assert calls["n"] == 1


def test_stream_chunks_ignores_malformed_events(monkeypatch):
    def fake_stream(self, method, url, **kwargs):
        return _FakeStream([
            "event: ping",
            "data: not-json",
            'data: {"choices":[]}',
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ])

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)

    assert list(LLMClient(_model()).stream_chunks([{"role": "user", "content": "x"}])) == ["ok"]
