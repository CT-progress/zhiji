"""临时文件目录与清理工具。

音频下载等中间产物统一放在项目内 ``.tmp/`` 下，并在这里集中提供
“用完即删 + 过期清扫”的能力，避免反复运行把磁盘塞满。
"""

from __future__ import annotations

import time
from contextlib import suppress
from pathlib import Path

from zhiji.config import PROJECT_ROOT

TMP_ROOT = PROJECT_ROOT / ".tmp"
#: 音频中间产物目录；转写完成后即可安全删除。
AUDIO_DIR = TMP_ROOT / "audio"
#: 超过该时长的残留音频会在下次运行时被清扫（秒）。
STALE_AUDIO_MAX_AGE = 6 * 60 * 60


def ensure_audio_dir() -> Path:
    """确保音频临时目录存在并返回它。"""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_DIR


def sweep_stale_audio(max_age_seconds: int = STALE_AUDIO_MAX_AGE) -> None:
    """删除 ``.tmp/audio`` 下超过 ``max_age_seconds`` 的残留文件。"""

    if not AUDIO_DIR.exists():
        return
    cutoff = time.time() - max_age_seconds
    for item in AUDIO_DIR.iterdir():
        # 文件可能正被其它进程占用；跳过，下次再清
        with suppress(OSError):
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink()


def remove_quietly(path: Path | None) -> None:
    """尽力删除临时文件；Windows 上文件被占用时忽略失败，不影响主流程。"""

    if path is None:
        return
    with suppress(OSError):
        path.unlink(missing_ok=True)
