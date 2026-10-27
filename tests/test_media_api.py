"""阶段 C · 工作区⑤：媒体工坊 API 与异步处理测试。"""

from __future__ import annotations

import base64
import math
import struct
import threading
import time
import wave
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import av
import pytest
from fastapi.testclient import TestClient

from zhiji import media_service
from zhiji.config import ConfigManager
from zhiji.models import TranscriptSegment
from zhiji.web.app import create_app

PASSWORD = "password123"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TERMINAL = {"succeeded", "failed", "cancelled"}


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIJI_BCRYPT_ROUNDS", "4")


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    cm = ConfigManager(tmp_path / "zhiji.json")
    app = create_app(
        cm,
        data_dir=tmp_path / "data",
        secret_key="test-secret",
        require_auth=True,
    )
    with TestClient(app) as value:
        yield value


def _register_and_login(client: TestClient, username: str) -> tuple[str, str]:
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": PASSWORD},
    )
    assert registered.status_code == 200, registered.text
    logged_in = client.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    payload = logged_in.json()
    client.cookies.clear()
    return payload["user"]["id"], payload["token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


def make_video(path: Path, *, duration: float = 0.8, rate: int = 10) -> Path:
    width = height = 48
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


def upload(
    client: TestClient,
    token: str,
    filename: str,
    content: bytes,
    *,
    title: str | None = None,
) -> dict:
    params = {"filename": filename}
    if title is not None:
        params["title"] = title
    response = client.post(
        "/api/media/upload",
        headers=_headers(token),
        params=params,
        content=content,
    )
    assert response.status_code == 200, response.text
    return response.json()["media"]


def create_task(client: TestClient, token: str, media_id: str, **body) -> dict:
    response = client.post(
        f"/api/media/{media_id}/tasks",
        headers=_headers(token),
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def wait_task(client: TestClient, token: str, media_id: str, task_id: str) -> dict:
    deadline = time.monotonic() + 20
    snapshot = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/media/{media_id}", headers=_headers(token))
        assert response.status_code == 200, response.text
        snapshot = response.json()
        task = next((item for item in snapshot["tasks"] if item["id"] == task_id), None)
        if task is not None and task["status"] in TERMINAL:
            return snapshot
        time.sleep(0.04)
    raise AssertionError(f"等待媒体任务超时: {snapshot}")


def test_media_crud_preview_download_search_and_stats(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    media = upload(
        client,
        token,
        "../../封面.png",
        PNG_BYTES,
        title="发布封面",
    )
    assert media["title"] == "发布封面"
    assert media["filename"] == "封面.png"
    assert media["media_type"] == "image"
    assert media["width"] == 1
    assert media["height"] == 1

    preview = client.get(media["preview_url"], headers=headers)
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert preview.content == PNG_BYTES

    download = client.get(media["download_url"], headers=headers)
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert "filename*=UTF-8''" in download.headers["content-disposition"]

    listed = client.get("/api/media", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["media"][0]["artifact_count"] == 0
    searched = client.get("/api/media", headers=headers, params={"q": "发布"})
    assert [item["id"] for item in searched.json()["media"]] == [media["id"]]
    image_only = client.get("/api/media", headers=headers, params={"media_type": "image"})
    assert image_only.json()["count"] == 1
    audio_only = client.get("/api/media", headers=headers, params={"media_type": "audio"})
    assert audio_only.json()["count"] == 0

    stats = client.get("/api/media/stats", headers=headers)
    assert stats.json()["stats"]["items"] == 1
    assert stats.json()["stats"]["images"] == 1
    assert stats.json()["stats"]["added_today"] == 1

    renamed = client.patch(
        f"/api/media/{media['id']}",
        headers=headers,
        json={"title": "发布封面 · 定稿"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["media"]["title"] == "发布封面 · 定稿"

    deleted = client.delete(f"/api/media/{media['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/media/{media['id']}", headers=headers).status_code == 404
    assert client.get("/api/media", headers=headers).json()["count"] == 0


def test_audio_jobs_cover_extract_trim_and_subtitles(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    wav = make_wav(tmp_path / "tone.wav", duration=1.0)
    media = upload(client, token, wav.name, wav.read_bytes(), title="口播测试")
    media_id = media["id"]

    extract = create_task(client, token, media_id, action="extract_audio")
    snapshot = wait_task(client, token, media_id, extract["id"])
    assert snapshot["media"]["status"] == "ready"
    audio = next(item for item in snapshot["artifacts"] if item["kind"] == "audio")
    assert audio["mime_type"] == "audio/mpeg"
    assert client.get(audio["file_url"], headers=headers).status_code == 200
    audio_download = client.get(audio["download_url"], headers=headers)
    assert audio_download.status_code == 200
    assert "attachment" in audio_download.headers["content-disposition"]

    def fake_transcribe(path, settings):  # type: ignore[no-untyped-def]
        assert Path(path).is_file()
        assert settings.whisper_model
        return [
            TranscriptSegment(start=0.0, end=1.1, text="这是一段测试字幕。"),
            TranscriptSegment(start=1.1, end=1.4, text="第二条。"),
        ]

    monkeypatch.setattr(media_service, "transcribe_file", fake_transcribe)
    subtitle_task = create_task(client, token, media_id, action="transcribe")
    snapshot = wait_task(client, token, media_id, subtitle_task["id"])
    subtitle = next(item for item in snapshot["artifacts"] if item["kind"] == "subtitle")
    subtitle_file = client.get(subtitle["file_url"], headers=headers)
    assert subtitle_file.status_code == 200
    assert "这是一段测试字幕。" in subtitle_file.text
    assert "00:00:00,000 --> 00:00:01,100" in subtitle_file.text

    trim = create_task(
        client,
        token,
        media_id,
        action="trim",
        start_ms=100,
        end_ms=450,
    )
    snapshot = wait_task(client, token, media_id, trim["id"])
    clip = next(item for item in snapshot["artifacts"] if item["kind"] == "clip")
    assert clip["metadata"]["start_ms"] == 100
    assert clip["metadata"]["end_ms"] == 450

    deleted = client.delete(f"/api/media/artifacts/{audio['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(audio["file_url"], headers=headers).status_code == 404
    assert client.post(
        f"/api/media/{media_id}/tasks",
        headers=headers,
        json={"action": "transcode"},
    ).status_code == 400


def test_video_transcode_and_trim_through_api(
    client: TestClient, tmp_path: Path
) -> None:
    _, token = _register_and_login(client, "alice")
    video_path = make_video(tmp_path / "clip.mp4")
    media = upload(client, token, video_path.name, video_path.read_bytes(), title="横版视频")
    assert media["media_type"] == "video"
    assert media["video_codec"] == "h264"

    transcode = create_task(client, token, media["id"], action="transcode")
    snapshot = wait_task(client, token, media["id"], transcode["id"])
    assert snapshot["tasks"][0]["status"] == "succeeded"
    artifact = next(item for item in snapshot["artifacts"] if item["kind"] == "video")
    assert artifact["filename"].endswith("-转码.mp4")

    trim = create_task(
        client,
        token,
        media["id"],
        action="trim",
        start_ms=150,
        end_ms=600,
    )
    snapshot = wait_task(client, token, media["id"], trim["id"])
    assert snapshot["tasks"][0]["status"] == "succeeded"
    assert any(item["kind"] == "clip" for item in snapshot["artifacts"])


def test_running_task_blocks_delete_and_cancel_restores_media(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)
    wav = make_wav(tmp_path / "cancel.wav", duration=0.5)
    media = upload(client, token, wav.name, wav.read_bytes())
    started = threading.Event()
    release = threading.Event()

    def blocking_transcribe(path, settings):  # type: ignore[no-untyped-def]
        started.set()
        assert release.wait(timeout=10)
        return [TranscriptSegment(start=0, end=0.5, text="完成")]

    monkeypatch.setattr(media_service, "transcribe_file", blocking_transcribe)
    task = create_task(client, token, media["id"], action="transcribe")
    assert started.wait(timeout=5)
    try:
        blocked = client.delete(f"/api/media/{media['id']}", headers=headers)
        assert blocked.status_code == 409
        assert "处理任务" in blocked.json()["detail"]

        cancelled = client.post(
            f"/api/media/tasks/{task['id']}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["task"]["status"] == "cancelled"
        detail = client.get(f"/api/media/{media['id']}", headers=headers)
        assert detail.json()["media"]["status"] == "ready"
        second_cancel = client.post(
            f"/api/media/tasks/{task['id']}/cancel",
            headers=headers,
        )
        assert second_cancel.status_code == 409
    finally:
        release.set()

    assert client.delete(f"/api/media/{media['id']}", headers=headers).status_code == 200


def test_media_is_isolated_between_users(client: TestClient) -> None:
    _, alice_token = _register_and_login(client, "alice")
    _, bob_token = _register_and_login(client, "bob")
    alice_headers = _headers(alice_token)
    bob_headers = _headers(bob_token)
    bob_media = upload(client, bob_token, "bob.png", PNG_BYTES, title="鲍勃封面")

    assert client.get("/api/media", headers=alice_headers).json()["media"] == []
    assert client.get("/api/media/stats", headers=alice_headers).json()["stats"]["items"] == 0
    assert client.get(f"/api/media/{bob_media['id']}", headers=alice_headers).status_code == 404
    assert client.get(bob_media["preview_url"], headers=alice_headers).status_code == 404
    assert client.get(bob_media["download_url"], headers=alice_headers).status_code == 404
    assert client.patch(
        f"/api/media/{bob_media['id']}",
        headers=alice_headers,
        json={"title": "越权"},
    ).status_code == 404
    assert client.post(
        f"/api/media/{bob_media['id']}/tasks",
        headers=alice_headers,
        json={"action": "transcribe"},
    ).status_code == 404
    assert client.delete(f"/api/media/{bob_media['id']}", headers=alice_headers).status_code == 404
    assert client.get(f"/api/media/{bob_media['id']}", headers=bob_headers).status_code == 200


def test_upload_validation_rejects_bad_files(client: TestClient) -> None:
    _, token = _register_and_login(client, "alice")
    headers = _headers(token)

    unsupported = client.post(
        "/api/media/upload",
        headers=headers,
        params={"filename": "notes.txt"},
        content=b"plain text",
    )
    assert unsupported.status_code == 400
    assert "不支持的媒体格式" in unsupported.json()["detail"]

    empty = client.post(
        "/api/media/upload",
        headers=headers,
        params={"filename": "empty.png"},
        content=b"",
    )
    assert empty.status_code == 400
    assert "不能为空" in empty.json()["detail"]

    corrupt = client.post(
        "/api/media/upload",
        headers=headers,
        params={"filename": "fake.mp4"},
        content=b"not a video",
    )
    assert corrupt.status_code == 400
    assert "无法解析媒体文件" in corrupt.json()["detail"]


def test_upload_size_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zhiji.web.app as app_module

    original = app_module.create_media_router

    def limited_router(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["max_upload_bytes"] = 32
        return original(*args, **kwargs)

    monkeypatch.setattr(app_module, "create_media_router", limited_router)
    app = create_app(
        ConfigManager(tmp_path / "zhiji.json"),
        data_dir=tmp_path / "limited-data",
        secret_key="test-secret",
        require_auth=True,
    )
    with TestClient(app) as client:
        _, token = _register_and_login(client, "alice")
        response = client.post(
            "/api/media/upload",
            headers=_headers(token),
            params={"filename": "large.png"},
            content=b"x" * 33,
        )
        assert response.status_code == 413
        assert "上传上限" in response.json()["detail"]
