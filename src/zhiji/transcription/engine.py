"""字幕优先，无字幕时使用本地 faster-whisper 转写。"""

from __future__ import annotations

from pathlib import Path

from zhiji.config import PROJECT_ROOT
from zhiji.errors import TranscriptMissingError
from zhiji.models import AppSettings, ContentBundle, ContentType, TranscriptSegment

_TMP_AUDIO = PROJECT_ROOT / ".tmp" / "audio"


def ensure_segments(
    bundle: ContentBundle,
    settings: AppSettings,
    warnings: list[str],
) -> list[TranscriptSegment]:
    if bundle.segments:
        return bundle.segments
    if bundle.ref.content_type == ContentType.VIDEO:
        # 视频内容：简介文案（body_text）信息量远低于口播内容，
        # 即使非空也必须优先走音频转写，简介只作转写失败时的降级兜底。
        try:
            return transcribe_url(bundle.ref.source_url, settings)
        except TranscriptMissingError:
            if bundle.body_text and bundle.body_text.strip():
                warnings.append("音频下载/转写失败，降级使用视频简介文案")
                return []
            raise
    if bundle.body_text:
        warnings.append("该内容无音频，直接使用正文文本")
        return []
    return transcribe_url(bundle.ref.source_url, settings)


def transcribe_file(path: str | Path, settings: AppSettings) -> list[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptMissingError(
            "未安装 ASR 组件",
            hint="请执行 pip install -e .[asr] 后重试",
        ) from exc

    try:
        model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type="int8" if settings.whisper_device == "cpu" else "float16",
        )
        segments_iter, _ = model.transcribe(str(path), language="zh", vad_filter=True)
        segments = [
            TranscriptSegment(
                start=round(segment.start, 2),
                end=round(segment.end, 2),
                text=segment.text.strip(),
            )
            for segment in segments_iter
            if segment.text.strip()
        ]
    except Exception as exc:
        raise TranscriptMissingError(f"ASR 转写失败: {exc}") from exc
    return segments


def transcribe_url(url: str, settings: AppSettings) -> list[TranscriptSegment]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise TranscriptMissingError("未安装 yt-dlp", hint="请安装项目依赖") from exc

    _TMP_AUDIO.mkdir(parents=True, exist_ok=True)
    output_template = str(_TMP_AUDIO / "%(id)s.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            audio_path = Path(ydl.prepare_filename(info))
    except Exception as exc:
        raise TranscriptMissingError(f"音频下载失败: {exc}") from exc

    segments = transcribe_file(audio_path, settings)
    if not settings.keep_audio:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass
    return segments