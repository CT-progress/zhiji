"""媒体服务测试：文件校验、受控存储、PyAV 探测与任务产物。"""

from __future__ import annotations

import math
import shutil
import struct
import wave
from fractions import Fraction
from pathlib import Path

import av
import pytest

from zhiji.config import ConfigManager
from zhiji.media_service import (
    MEDIA_TASK_EXTRACT_AUDIO,
    MEDIA_TASK_TRANSCODE,
    MEDIA_TASK_TRIM,
    MediaJobService,
    MediaStorage,
    MediaValidationError,
    clean_filename,
    media_type_for_extension,
    probe_media,
)
from zhiji.models import TaskStatus
from zhiji.storage import Database, MediaRepository, TaskRepository
from zhiji.tasks import TaskWorker


def make_wav(path: Path, *, duration: float = 1.0, rate: int = 8000) -> Path:
    samples = [
        int(12000 * math.sin(2 * math.pi * 440 * index / rate))
        for index in range(round(duration * rate))
    ]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", value) for value in samples))
    return path


def make_video(path: Path, *, duration: float = 1.2, rate: int = 10) -> Path:
    width = height = 64
    with av.open(str(path), mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=rate)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for index in range(round(duration * rate)):
            frame = av.VideoFrame(width, height, "rgb24")
            frame.planes[0].update(bytes(frame.planes[0].buffer_size))
            frame.pts = index
            frame.time_base = Fraction(1, rate)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path


@pytest.fixture()
def database(tmp_path: Path):
    db = Database(f"sqlite:///{(tmp_path / 'media.db').as_posix()}")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture()
def worker(database: Database):
    value = TaskWorker(database=database, poll_interval=0.01)
    yield value
    value.stop()


@pytest.fixture()
def media_setup(database: Database, worker: TaskWorker, tmp_path: Path):
    storage = MediaStorage(tmp_path / "media")
    config = ConfigManager(tmp_path / "zhiji.json")
    service = MediaJobService(database, storage, config)
    service.install(worker)
    return database, worker, storage, service


def add_media(database: Database, storage: MediaStorage, source: Path, extension: str) -> str:
    media_id = source.stem
    destination = storage.source_path(None, media_id, extension)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    probe = probe_media(destination, extension)
    with database.session_scope() as session:
        MediaRepository(session).create(
            media_id=media_id,
            title=source.stem,
            filename=source.name,
            media_type=probe.media_type,
            mime_type=probe.mime_type,
            extension=extension,
            size=destination.stat().st_size,
            sha256="0" * 64,
            duration_ms=probe.duration_ms,
            width=probe.width,
            height=probe.height,
            video_codec=probe.video_codec,
            audio_codec=probe.audio_codec,
            storage_path=storage.relative(destination),
        )
    return media_id


def run_task(worker: TaskWorker, task_type: str, media_id: str, **payload) -> str:
    task_id = worker.submit(
        task_type=task_type,
        payload={"media_id": media_id, **payload},
    )
    assert worker.run_once() is True
    return task_id


def task_row(database: Database, task_id: str):
    with database.session_scope() as session:
        return TaskRepository(session).get(task_id)


def artifacts_for(database: Database, media_id: str):
    with database.session_scope() as session:
        return MediaRepository(session).list_artifacts(media_id)


def test_clean_filename_and_storage_paths_are_controlled(tmp_path: Path) -> None:
    filename, extension = clean_filename("../../发布素材.MP4")
    assert filename == "发布素材.mp4"
    assert extension == ".mp4"
    assert media_type_for_extension(extension) == "video"

    with pytest.raises(MediaValidationError, match="不支持"):
        clean_filename("notes.txt")
    with pytest.raises(MediaValidationError, match="不能为空"):
        clean_filename("   ")

    storage = MediaStorage(tmp_path / "media")
    source = storage.source_path("alice", "media-1", ".wav")
    assert source.parent == storage.root / "alice" / "media-1"
    assert storage.resolve(storage.relative(source)) == source
    with pytest.raises(MediaValidationError, match="越界"):
        storage.resolve("../outside.wav")
    artifact = storage.artifact_path("alice", "media-1", "../../escape.mp3")
    assert artifact.name == "escape.mp3"
    assert storage.root in artifact.parents


def test_probe_rejects_non_media_and_reports_audio(
    tmp_path: Path, media_setup
) -> None:
    database, _, storage, _ = media_setup
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"not a wave file")
    with pytest.raises(MediaValidationError, match="无法解析媒体文件"):
        probe_media(empty, ".wav")

    source = make_wav(tmp_path / "tone.wav", duration=0.8)
    probe = probe_media(source, ".wav")
    assert probe.media_type == "audio"
    assert probe.audio_codec == "pcm_s16le"
    assert 700 <= (probe.duration_ms or 0) <= 900

    media_id = add_media(database, storage, source, ".wav")
    task_id = run_task(worker=media_setup[1], task_type=MEDIA_TASK_EXTRACT_AUDIO, media_id=media_id)
    assert task_row(database, task_id).status == TaskStatus.SUCCEEDED.value
    artifacts = artifacts_for(database, media_id)
    assert len(artifacts) == 1
    assert artifacts[0].kind == "audio"
    mp3_path = storage.resolve(artifacts[0].storage_path)
    assert mp3_path.is_file()
    assert str(probe_media(mp3_path, ".mp3").audio_codec or "").startswith("mp3")


def test_video_transcode_and_trim_create_playable_artifacts(
    tmp_path: Path, media_setup
) -> None:
    database, worker, storage, _ = media_setup
    source = make_video(tmp_path / "clip.mp4", duration=1.2)
    media_id = add_media(database, storage, source, ".mp4")

    transcode_id = run_task(worker, MEDIA_TASK_TRANSCODE, media_id)
    assert task_row(database, transcode_id).status == TaskStatus.SUCCEEDED.value
    transcoded = artifacts_for(database, media_id)[0]
    transcoded_probe = probe_media(storage.resolve(transcoded.storage_path), ".mp4")
    assert transcoded.kind == "video"
    assert transcoded_probe.width == 64
    assert transcoded_probe.height == 64
    assert transcoded_probe.video_codec == "h264"

    trim_id = run_task(
        worker,
        MEDIA_TASK_TRIM,
        media_id,
        start_ms=200,
        end_ms=700,
    )
    assert task_row(database, trim_id).status == TaskStatus.SUCCEEDED.value
    trimmed = next(item for item in artifacts_for(database, media_id) if item.kind == "clip")
    trimmed_probe = probe_media(storage.resolve(trimmed.storage_path), ".mp4")
    assert 300 <= (trimmed_probe.duration_ms or 0) <= 900
    assert trimmed.metadata_json is not None


def test_silent_video_rejects_audio_extraction_and_restores_ready(
    tmp_path: Path, media_setup
) -> None:
    database, worker, storage, _ = media_setup
    source = make_video(tmp_path / "silent.mp4", duration=0.5)
    media_id = add_media(database, storage, source, ".mp4")

    task_id = run_task(worker, MEDIA_TASK_EXTRACT_AUDIO, media_id)
    row = task_row(database, task_id)
    assert row.status == TaskStatus.FAILED.value
    assert row.error is not None and "音轨" in row.error
    with database.session_scope() as session:
        media = MediaRepository(session).get(media_id)
        assert media is not None
        assert media.status == "ready"
        assert "音轨" in media.error
    assert artifacts_for(database, media_id) == []
