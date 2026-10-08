import threading

from zhiji.chat import ChatStore


def test_concurrent_append_message_keeps_all_messages(tmp_path):
    """并发写入不应互相覆盖：所有消息都必须落盘。"""

    store = ChatStore(tmp_path)
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
