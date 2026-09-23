"""统一错误类型与错误响应描述。"""

from __future__ import annotations


class ZhijiError(Exception):
    """项目基础异常，携带稳定错误码。"""

    code = "INTERNAL"

    def __init__(self, message: str, *, platform: str | None = None, hint: str | None = None) -> None:
        self.message = message
        self.platform = platform
        self.hint = hint
        super().__init__(message)


class InputUnsupportedError(ZhijiError):
    code = "INPUT_UNSUPPORTED_URL"


class PlatformFetchError(ZhijiError):
    code = "PLATFORM_FETCH_FAILED"


class TranscriptMissingError(ZhijiError):
    code = "TRANSCRIPT_MISSING"


class LLMError(ZhijiError):
    code = "LLM_FAILED"


class OutputWriteError(ZhijiError):
    code = "OUTPUT_WRITE_FAILED"


class DuplicateContentError(ZhijiError):
    code = "DUPLICATE_CONTENT"


class ConfigError(ZhijiError):
    code = "CONFIG_ERROR"


def error_response(exc: Exception) -> dict:
    """把异常转成统一的 API / CLI 错误结构。"""

    if isinstance(exc, ZhijiError):
        return {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "platform": exc.platform,
                "hint": exc.hint,
            }
        }
    return {
        "error": {
            "code": "INTERNAL",
            "message": str(exc),
            "platform": None,
            "hint": None,
        }
    }