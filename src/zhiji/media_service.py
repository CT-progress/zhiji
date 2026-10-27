"""媒体工坊服务：受控文件布局、PyAV 探测/转码与异步任务处理。"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import av

from zhiji.config import ConfigManager
from zhiji.errors import GenerationCancelledError
from zhiji.storage import Database
from zhiji.storage.media_repository import MediaRepository, artifact_to_dict
from zhiji.tasks import TaskContext, TaskWorker
from zhiji.transcription.engine import transcribe_file

MEDIA_TASK_EXTRACT_AUDIO = "media.extract_audio"
MEDIA_TASK_TRANSCODE = "media.transcode"
MEDIA_TASK_TRIM = "media.trim"
MEDIA_TASK_TRANSCRIBE = "media.transcribe"
MEDIA_TASK_TYPES = (
    MEDIA_TASK_EXTRACT_AUDIO,
    MEDIA_TASK_TRANSCODE,
    MEDIA_TASK_TRIM,
    MEDIA_TASK_TRANSCRIBE,
)
DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024

MEDIA_MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
}
MEDIA_TYPE_BY_EXTENSION = {
    **{ext: "image" for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")},
    **{ext: "audio" for ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac")},
    **{ext: "video" for ext in (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv")},
}
_SAFE_SCOPE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class MediaValidationError(ValueError):
    """上传或处理参数不满足媒体工坊约束。"""


@dataclass(frozen=True)
class MediaProbe:
    media_type: str
    mime_type: str
    extension: str
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None


@dataclass(frozen=True)
class OutputSpec:
    kind: str
    extension: str
    mime_type: str


def clean_filename(value: str) -> tuple[str, str]:
    """返回安全的展示文件名与扩展名；原文件名绝不进入磁盘路径。"""

    candidate = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(ch for ch in candidate if ch >= " " and ch != "\x7f").strip()
    if not candidate:
        raise MediaValidationError("文件名不能为空")
    suffix = Path(candidate).suffix.lower()
    if suffix not in MEDIA_MIME_BY_EXTENSION:
        allowed = "、".join(sorted(MEDIA_MIME_BY_EXTENSION))
        raise MediaValidationError(f"不支持的媒体格式，可用扩展名：{allowed}")
    stem = Path(candidate).stem.strip(" .") or "未命名媒体"
    filename = f"{stem[:220]}{suffix}"
    return filename, suffix


def media_type_for_extension(extension: str) -> str:
    try:
        return MEDIA_TYPE_BY_EXTENSION[extension]
    except KeyError as exc:
        raise MediaValidationError(f"不支持的媒体扩展名: {extension}") from exc


def probe_media(path: str | Path, extension: str) -> MediaProbe:
    """用 PyAV 读取容器信息；不依赖系统 ffmpeg 可执行文件。"""

    try:
        with av.open(str(path)) as container:
            video_stream = next(
                (stream for stream in container.streams if stream.type == "video"), None
            )
            audio_stream = next(
                (stream for stream in container.streams if stream.type == "audio"), None
            )
            if video_stream is None and audio_stream is None:
                raise MediaValidationError("文件中没有可识别的音视频流")
            duration = 0.0
            if container.duration is not None:
                duration = float(container.duration) / 1_000_000
            if duration <= 0:
                for stream in container.streams:
                    if stream.duration is not None and stream.time_base is not None:
                        duration = max(duration, float(stream.duration * stream.time_base))
            media_type = media_type_for_extension(extension)
            width = getattr(video_stream, "width", None) if video_stream else None
            height = getattr(video_stream, "height", None) if video_stream else None
            video_codec = (
                getattr(video_stream.codec_context, "name", None) if video_stream else None
            )
            audio_codec = (
                getattr(audio_stream.codec_context, "name", None) if audio_stream else None
            )
    except MediaValidationError:
        raise
    except (av.FFmpegError, OSError, ValueError) as exc:
        raise MediaValidationError(f"无法解析媒体文件: {exc}") from exc
    return MediaProbe(
        media_type=media_type,
        mime_type=MEDIA_MIME_BY_EXTENSION[extension],
        extension=extension,
        duration_ms=max(0, round(duration * 1000)) if media_type != "image" else None,
        width=int(width) if width else None,
        height=int(height) if height else None,
        video_codec=str(video_codec) if video_codec else None,
        audio_codec=str(audio_codec) if audio_codec else None,
    )


class MediaStorage:
    """按用户与媒体 ID 生成路径；数据库只保存相对 root 的 POSIX 路径。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def scope_key(self, user_id: str | None) -> str:
        raw = str(user_id or "local")
        safe = _SAFE_SCOPE_RE.sub("_", raw).strip("._") or "local"
        return safe[:80]

    def media_dir(self, user_id: str | None, media_id: str) -> Path:
        return self.root / self.scope_key(user_id) / media_id

    def source_path(self, user_id: str | None, media_id: str, extension: str) -> Path:
        return self.media_dir(user_id, media_id) / f"source{extension}"

    def artifact_path(self, user_id: str | None, media_id: str, filename: str) -> Path:
        safe_name = Path(filename).name
        return self.media_dir(user_id, media_id) / "artifacts" / safe_name

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise MediaValidationError("媒体存储路径越界") from exc
        return candidate

    def remove_media(self, user_id: str | None, media_id: str) -> None:
        target = self.media_dir(user_id, media_id).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return
        shutil.rmtree(target, ignore_errors=True)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_name(path: Path, suffix: str) -> str:
    stem = Path(path.stem).name.strip() or "media"
    return f"{stem[:180]}{suffix}"


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_srt(path: Path, segments) -> None:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = " ".join(str(segment.text or "").split())
        if not text:
            continue
        blocks.append(
            f"{index}\n{_srt_timestamp(segment.start)} --> {_srt_timestamp(segment.end)}\n{text}"
        )
    if not blocks:
        raise MediaValidationError("转写结果为空，未生成字幕")
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _source_seconds(container) -> float:
    if container.duration is not None:
        return max(0.0, float(container.duration) / 1_000_000)
    durations = [
        float(stream.duration * stream.time_base)
        for stream in container.streams
        if stream.duration is not None and stream.time_base is not None
    ]
    return max(durations, default=0.0)


def _convert_media(
    source: Path,
    destination: Path,
    *,
    include_video: bool,
    include_audio: bool,
    start_ms: int = 0,
    end_ms: int | None = None,
    progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """用 PyAV 重编码为目标格式；音频输出 MP3，视频输出 H.264/AAC MP4。"""

    if not include_video and not include_audio:
        raise MediaValidationError("没有可转换的媒体流")
    report = progress or (lambda _value: None)
    is_cancelled = cancelled or (lambda: False)
    with av.open(str(source)) as input_container:
        duration = _source_seconds(input_container)
        video_stream = next(
            (stream for stream in input_container.streams if stream.type == "video"),
            None,
        )
        audio_stream = next(
            (stream for stream in input_container.streams if stream.type == "audio"),
            None,
        )
        if include_video and video_stream is None:
            raise MediaValidationError("源文件没有视频流")
        if include_audio and audio_stream is None:
            raise MediaValidationError("源文件没有音频流")
        start = max(0.0, start_ms / 1000)
        end = duration if end_ms is None else min(duration, end_ms / 1000)
        if end <= start:
            raise MediaValidationError("裁剪结束时间必须晚于开始时间")

        output_format = "mp4" if include_video else "mp3"
        options = {"movflags": "+faststart"} if include_video else None
        output_video_rate = (
            Fraction(video_stream.average_rate or 25)
            if include_video and video_stream is not None
            else None
        )
        with av.open(str(destination), mode="w", format=output_format, options=options) as output:
            video_out = None
            audio_out = None
            if include_video and video_stream is not None:
                rate = video_stream.average_rate or 25
                video_out = output.add_stream("libx264", rate=rate)
                source_width = int(video_stream.codec_context.width)
                source_height = int(video_stream.codec_context.height)
                target_width = max(2, source_width - source_width % 2)
                target_height = max(2, source_height - source_height % 2)
                video_out.width = target_width
                video_out.height = target_height
                video_out.pix_fmt = "yuv420p"
                video_out.options = {"crf": "23", "preset": "veryfast"}
            if include_audio and audio_stream is not None:
                rate = int(audio_stream.codec_context.sample_rate or 44100)
                audio_out = output.add_stream(
                    "libmp3lame" if not include_video else "aac", rate=rate
                )

            video_pts = 0
            audio_pts = 0
            decode_args: dict[str, int] = {}
            if include_video and video_stream is not None:
                decode_args["video"] = 0
            if include_audio and audio_stream is not None:
                decode_args["audio"] = 0
            if not decode_args:
                raise MediaValidationError("没有可解码的媒体流")

            for frame in input_container.decode(**decode_args):
                if is_cancelled():
                    raise GenerationCancelledError("媒体处理已取消")
                frame_time = None
                if frame.pts is not None and frame.time_base is not None:
                    frame_time = float(frame.pts * frame.time_base)
                if frame_time is not None and frame_time < start:
                    continue
                if frame_time is not None and frame_time >= end:
                    continue
                if isinstance(frame, av.VideoFrame) and video_out is not None:
                    encoded_frame = frame.reformat(
                        format="yuv420p",
                        width=video_out.width,
                        height=video_out.height,
                    )
                    encoded_frame.pts = video_pts
                    encoded_frame.time_base = Fraction(1, 1) / output_video_rate
                    video_pts += 1
                    for packet in video_out.encode(encoded_frame):
                        output.mux(packet)
                elif isinstance(frame, av.AudioFrame) and audio_out is not None:
                    frame.pts = audio_pts
                    frame.time_base = Fraction(1, rate)
                    audio_pts += frame.samples
                    for packet in audio_out.encode(frame):
                        output.mux(packet)
                if duration > 0 and frame_time is not None:
                    report(max(1, min(94, round((frame_time - start) / (end - start) * 94))))

            if video_out is not None:
                for packet in video_out.encode(None):
                    output.mux(packet)
            if audio_out is not None:
                for packet in audio_out.encode(None):
                    output.mux(packet)
    report(96)


class MediaJobService:
    """把媒体处理动作注册进现有 TaskWorker，产物与元数据落在同一数据库。"""

    def __init__(
        self,
        database: Database,
        storage: MediaStorage,
        config: ConfigManager | None = None,
    ) -> None:
        self.database = database
        self.storage = storage
        self.config = config or ConfigManager()

    def install(self, worker: TaskWorker) -> None:
        for task_type in MEDIA_TASK_TYPES:
            worker.handlers.setdefault(task_type, self.handle)

    def handle(self, context: TaskContext) -> dict[str, Any]:
        media_id = str(context.payload.get("media_id") or "").strip()
        if not media_id:
            raise MediaValidationError("任务缺少 media_id")

        artifact_id = str(uuid4())
        destination: Path | None = None
        partial: Path | None = None
        try:
            with self.database.session_scope() as session:
                repo = MediaRepository(session)
                media = repo.get(media_id, user_id=context.user_id)
                if media is None:
                    raise MediaValidationError("媒体不存在或无权访问")
                source = self.storage.resolve(media.storage_path)
                media_type = media.media_type
                media_title = media.title
                duration_ms = media.duration_ms
                media_audio_codec = media.audio_codec
                if not source.exists():
                    raise MediaValidationError("媒体原文件已丢失")

            output_spec = self._output_spec(
                context.task_type, media_type, bool(media_audio_codec)
            )
            output_name = f"{artifact_id}{output_spec.extension}"
            destination = self.storage.artifact_path(context.user_id, media_id, output_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(
                f".{artifact_id}.part{output_spec.extension}"
            )
            context.report(2)
            context.emit({"stage": "prepare", "message": "已打开媒体文件"})

            if context.task_type == MEDIA_TASK_TRANSCRIBE:
                segments = transcribe_file(source, self.config.get().settings)
                if context.is_cancelled():
                    raise GenerationCancelledError("字幕生成已取消")
                context.report(88)
                _write_srt(partial, segments)
                metadata = {
                    "segments": len(segments),
                    "language": "zh",
                    "duration_ms": duration_ms,
                }
            else:
                include_video = context.task_type != MEDIA_TASK_EXTRACT_AUDIO
                include_audio = bool(media_audio_codec)
                start_ms = self._int_payload(context.payload, "start_ms", 0)
                end_ms = self._optional_int_payload(context.payload, "end_ms")
                if context.task_type != MEDIA_TASK_TRIM:
                    start_ms, end_ms = 0, None
                if duration_ms and end_ms is not None and end_ms > duration_ms + 1000:
                    raise MediaValidationError("裁剪结束时间超出媒体时长")
                _convert_media(
                    source,
                    partial,
                    include_video=include_video and media_type == "video",
                    include_audio=include_audio,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    progress=context.report,
                    cancelled=context.is_cancelled,
                )
                metadata = {
                    "source_duration_ms": duration_ms,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            if context.is_cancelled():
                raise GenerationCancelledError("媒体处理已取消")
            partial.replace(destination)
            display_name = self._display_name(
                media_title, context.task_type, output_spec.extension
            )
            with self.database.session_scope() as session:
                repo = MediaRepository(session)
                row = repo.create_artifact(
                    artifact_id=artifact_id,
                    media_id=media_id,
                    kind=output_spec.kind,
                    filename=display_name,
                    mime_type=output_spec.mime_type,
                    size=destination.stat().st_size,
                    storage_path=self.storage.relative(destination),
                    user_id=context.user_id,
                    task_id=context.task_id,
                    metadata=metadata,
                )
                repo.update(media_id, user_id=context.user_id, status="ready", error="")
                artifact = artifact_to_dict(row)
            context.report(100)
            context.emit({"stage": "done", "message": "产物已生成"})
            return {"artifact": artifact}
        except Exception as exc:
            if partial is not None:
                partial.unlink(missing_ok=True)
            if destination is not None:
                destination.unlink(missing_ok=True)
            message = "处理已取消" if isinstance(exc, GenerationCancelledError) else str(exc)
            with self.database.session_scope() as session:
                MediaRepository(session).update(
                    media_id,
                    user_id=context.user_id,
                    status="ready",
                    error=message[:1000],
                )
            raise

    def _output_spec(
        self, task_type: str, media_type: str, has_audio: bool
    ) -> OutputSpec:
        if task_type == MEDIA_TASK_EXTRACT_AUDIO:
            if media_type == "image" or not has_audio:
                raise MediaValidationError("媒体没有可提取的音轨")
            return OutputSpec("audio", ".mp3", "audio/mpeg")
        if task_type == MEDIA_TASK_TRANSCODE:
            if media_type != "video":
                raise MediaValidationError("转码 MP4 仅支持视频素材")
            return OutputSpec("video", ".mp4", "video/mp4")
        if task_type == MEDIA_TASK_TRIM:
            if media_type == "image":
                raise MediaValidationError("图片不支持时间裁剪")
            if media_type == "video":
                return OutputSpec("clip", ".mp4", "video/mp4")
            return OutputSpec("clip", ".mp3", "audio/mpeg")
        if task_type == MEDIA_TASK_TRANSCRIBE:
            if media_type == "image" or not has_audio:
                raise MediaValidationError("媒体没有可用于字幕的音轨")
            return OutputSpec("subtitle", ".srt", "application/x-subrip")
        raise MediaValidationError(f"未知媒体任务: {task_type}")

    def _display_name(self, title: str, task_type: str, extension: str) -> str:
        stem = " ".join(str(title or "媒体").split())[:180] or "媒体"
        suffixes = {
            MEDIA_TASK_EXTRACT_AUDIO: "音轨",
            MEDIA_TASK_TRANSCODE: "转码",
            MEDIA_TASK_TRIM: "裁剪",
            MEDIA_TASK_TRANSCRIBE: "字幕",
        }
        return f"{stem}-{suffixes.get(task_type, '产物')}{extension}"

    def _int_payload(self, payload: dict[str, Any], key: str, default: int) -> int:
        value = payload.get(key, default)
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise MediaValidationError(f"{key} 必须是整数") from exc
        if result < 0:
            raise MediaValidationError(f"{key} 不能小于 0")
        return result

    def _optional_int_payload(self, payload: dict[str, Any], key: str) -> int | None:
        value = payload.get(key)
        if value in (None, ""):
            return None
        return self._int_payload(payload, key, 0)

