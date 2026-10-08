import os
import time
from pathlib import Path

from zhiji import tmpfiles


def _age(path: Path, seconds: float) -> None:
    timestamp = time.time() - seconds
    os.utime(path, (timestamp, timestamp))


def test_sweep_stale_audio_only_removes_old_files(monkeypatch, tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    stale = audio_dir / "stale.mp3"
    fresh = audio_dir / "fresh.mp3"
    nested = audio_dir / "nested"
    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")
    nested.mkdir()
    _age(stale, 120)
    _age(fresh, 5)

    monkeypatch.setattr(tmpfiles, "AUDIO_DIR", audio_dir)
    tmpfiles.sweep_stale_audio(max_age_seconds=60)

    assert not stale.exists()
    assert fresh.exists()
    assert nested.exists()


def test_sweep_stale_audio_missing_directory_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(tmpfiles, "AUDIO_DIR", tmp_path / "missing")
    tmpfiles.sweep_stale_audio()


def test_ensure_audio_dir_and_remove_quietly(monkeypatch, tmp_path):
    audio_dir = tmp_path / "audio"
    monkeypatch.setattr(tmpfiles, "AUDIO_DIR", audio_dir)

    assert tmpfiles.ensure_audio_dir() == audio_dir
    assert audio_dir.is_dir()

    path = audio_dir / "a.mp3"
    path.write_bytes(b"x")
    tmpfiles.remove_quietly(path)
    assert not path.exists()
    tmpfiles.remove_quietly(path)
    tmpfiles.remove_quietly(None)
