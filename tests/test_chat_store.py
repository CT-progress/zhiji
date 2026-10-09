"""会话存储测试：数据库后端（默认）与 JSON 回退后端。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from zhiji.chat import DB_BACKEND, JSON_BACKEND, ChatNotFoundError, ChatStore
from zhiji.errors import ZhijiError

BACKENDS = [DB_BACKEND, JSON_BACKEND]


@pytest.mark.parametrize("backend", BACKENDS)
def test_concurrent_append_message_keeps_all_messages(tmp_path: Path, backend: str) -> None:
    """并发写入不应互相覆盖：所有消息都必须落盘。"""

    store = ChatStore(tmp_path, backend=backend)
    conversation = store.create()

    n = 20
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        barrier.wait()
        store.append_message(conversation.id, "user", f"消息 {i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    final = store.get(conversation.id)
    assert len(final.messages) == n
    assert {m.content for m in final.messages} == {f"消息 {i}" for i in range(n)}


@pytest.mark.parametrize("backend", BACKENDS)
def test_store_lifecycle(tmp_path: Path, backend: str) -> None:
    store = ChatStore(tmp_path, backend=backend)
    conversation = store.create(title="我的会话", model_name="DeepSeek")
    assert conversation.title == "我的会话"

    store.append_message(conversation.id, "user", "你好")
    store.append_message(conversation.id, "assistant", "你好，我是知记")
    store.attach_note(
        conversation.id,
        platform="zhihu",
        source_url="https://www.zhihu.com/question/1/answer/2",
        note_content="笔记正文",
        note_path="output/notes/a.md",
        title="笔记标题",
    )

    loaded = store.get(conversation.id)
    assert [m.role for m in loaded.messages] == ["user", "assistant"]
    assert loaded.platform == "zhihu"
    assert loaded.note_content == "笔记正文"
    assert loaded.note_path == "output/notes/a.md"
    assert loaded.title == "笔记标题"
    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at >= loaded.created_at

    assert [c.id for c in store.list()] == [conversation.id]
    assert store.update(conversation.id, title="改名").title == "改名"
    assert store.get(conversation.id).title == "改名"

    store.delete(conversation.id)
    assert store.list() == []
    with pytest.raises(ChatNotFoundError):
        store.get(conversation.id)


@pytest.mark.parametrize("backend", BACKENDS)
def test_auto_title_only_on_first_user_message(tmp_path: Path, backend: str) -> None:
    store = ChatStore(tmp_path, backend=backend)
    conversation = store.create()

    titled = store.append_message(conversation.id, "user", "帮我总结这篇知乎回答")
    assert titled.title == "帮我总结这篇知乎回答"

    again = store.append_message(conversation.id, "user", "还有一个问题")
    assert again.title == "帮我总结这篇知乎回答"


def test_db_backend_persists_across_instances(tmp_path: Path) -> None:
    first = ChatStore(tmp_path)
    conversation = first.create(title="持久化")
    first.append_message(conversation.id, "user", "消息")

    second = ChatStore(tmp_path)
    loaded = second.get(conversation.id)
    assert loaded.title == "持久化"
    assert [m.content for m in loaded.messages] == ["消息"]


def test_db_backend_creates_sqlite_file_only(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    assert store.backend_name == DB_BACKEND
    store.create()
    assert (tmp_path / "zhiji.db").exists()
    assert not (tmp_path / "conversations.json").exists()


def test_json_backend_creates_json_file_only(tmp_path: Path) -> None:
    store = ChatStore(tmp_path, backend=JSON_BACKEND)
    store.create()
    assert (tmp_path / "conversations.json").exists()
    assert not (tmp_path / "zhiji.db").exists()


def test_backend_can_be_switched_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_CHAT_BACKEND", JSON_BACKEND)
    store = ChatStore(tmp_path)
    store.create()
    assert store.backend_name == JSON_BACKEND
    assert (tmp_path / "conversations.json").exists()


def test_unknown_backend_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ZhijiError):
        ChatStore(tmp_path, backend="redis")


def test_json_backend_reports_broken_payload(tmp_path: Path) -> None:
    store = ChatStore(tmp_path, backend=JSON_BACKEND)
    (tmp_path / "conversations.json").write_text("{不是合法 JSON", encoding="utf-8")
    with pytest.raises(ZhijiError):
        store.list()
