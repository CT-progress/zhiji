"""任务状态机测试：合法流转、非法流转与失败原因落库。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from zhiji.errors import TaskTransitionError
from zhiji.models import TASK_TRANSITIONS, TaskStatus
from zhiji.storage import Database, TaskRepository


@pytest.fixture()
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


def _task_in_state(repo: TaskRepository, state: TaskStatus) -> str:
    """造一个处于指定状态的任务，返回 task_id。"""

    task_id = repo.create(task_type="note.generate").id
    if state is TaskStatus.PENDING:
        return task_id
    repo.update_status(task_id, TaskStatus.RUNNING)
    if state is TaskStatus.RUNNING:
        return task_id
    if state is TaskStatus.SUCCEEDED:
        repo.update_status(task_id, TaskStatus.SUCCEEDED, result={"note": "a.md"})
    elif state is TaskStatus.FAILED:
        repo.update_status(task_id, TaskStatus.FAILED, error="抓取失败")
    else:
        repo.update_status(task_id, TaskStatus.CANCELLED)
    return task_id


def test_transition_matrix_definition() -> None:
    assert TASK_TRANSITIONS[TaskStatus.PENDING] == {TaskStatus.RUNNING, TaskStatus.CANCELLED}
    assert TASK_TRANSITIONS[TaskStatus.RUNNING] == {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
    for terminal in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        assert TASK_TRANSITIONS[terminal] == frozenset()
        assert terminal.is_terminal() is True


def test_happy_path_sets_timestamps_and_result(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="note.generate").id

        repo.update_status(task_id, TaskStatus.RUNNING, progress=5)
        running = repo.get(task_id)
        assert running.status == TaskStatus.RUNNING.value
        assert running.started_at is not None
        assert running.finished_at is None

        repo.update_status(task_id, TaskStatus.SUCCEEDED, result={"note": "a.md"})
        done = repo.get(task_id)
        assert done.status == TaskStatus.SUCCEEDED.value
        assert done.finished_at is not None
        assert done.result_json is not None and "a.md" in done.result_json
        assert done.error is None


def test_pending_can_be_cancelled(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        repo.update_status(task_id, TaskStatus.CANCELLED)
        row = repo.get(task_id)
        assert row.status == TaskStatus.CANCELLED.value
        assert row.finished_at is not None
        assert row.started_at is None


def test_failure_reason_is_persisted(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        repo.update_status(task_id, TaskStatus.RUNNING)
        repo.update_status(task_id, TaskStatus.FAILED, error=" 抖音链接解析失败 ")
        row = repo.get(task_id)
        assert row.status == TaskStatus.FAILED.value
        assert row.error == " 抖音链接解析失败 "
        assert row.finished_at is not None


def test_failed_without_reason_is_rejected(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        repo.update_status(task_id, TaskStatus.RUNNING)
        with pytest.raises(TaskTransitionError):
            repo.update_status(task_id, TaskStatus.FAILED)
        with pytest.raises(TaskTransitionError):
            repo.update_status(task_id, TaskStatus.FAILED, error="   ")
        assert repo.get(task_id).status == TaskStatus.RUNNING.value


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.PENDING, TaskStatus.SUCCEEDED),
        (TaskStatus.PENDING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.RUNNING),
        (TaskStatus.SUCCEEDED, TaskStatus.RUNNING),
        (TaskStatus.SUCCEEDED, TaskStatus.CANCELLED),
        (TaskStatus.FAILED, TaskStatus.RUNNING),
        (TaskStatus.CANCELLED, TaskStatus.RUNNING),
    ],
)
def test_illegal_transitions_are_rejected(
    database: Database, current: TaskStatus, target: TaskStatus
) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = _task_in_state(repo, current)
        with pytest.raises(TaskTransitionError):
            repo.update_status(task_id, target)
        assert repo.get(task_id).status == current.value


def test_illegal_transition_does_not_mutate_timestamps(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        with pytest.raises(TaskTransitionError):
            repo.update_status(task_id, TaskStatus.SUCCEEDED)
        row = repo.get(task_id)
        assert row.status == TaskStatus.PENDING.value
        assert row.started_at is None
        assert row.finished_at is None


def test_terminal_states_have_no_exits(database: Database) -> None:
    for terminal in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        with database.session_scope() as session:
            repo = TaskRepository(session)
            task_id = _task_in_state(repo, terminal)
            for target in TaskStatus:
                assert not TaskStatus(terminal).can_transition_to(target)
                with pytest.raises(TaskTransitionError):
                    repo.update_status(task_id, target)


def test_update_progress_requires_running(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        with pytest.raises(TaskTransitionError):
            repo.update_progress(task_id, 10)

        repo.update_status(task_id, TaskStatus.RUNNING)
        assert repo.update_progress(task_id, 42).progress == 42
        assert repo.get(task_id).status == TaskStatus.RUNNING.value

        repo.update_status(task_id, TaskStatus.SUCCEEDED)
        with pytest.raises(TaskTransitionError):
            repo.update_progress(task_id, 99)


def test_progress_must_be_in_range(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        with pytest.raises(TaskTransitionError):
            repo.update_status(task_id, TaskStatus.RUNNING, progress=101)
        repo.update_status(task_id, TaskStatus.RUNNING)
        with pytest.raises(TaskTransitionError):
            repo.update_progress(task_id, -1)


def test_unknown_status_still_raises_value_error(database: Database) -> None:
    with database.session_scope() as session:
        repo = TaskRepository(session)
        task_id = repo.create(task_type="t").id
        with pytest.raises(ValueError):
            repo.update_status(task_id, "not-a-status")
