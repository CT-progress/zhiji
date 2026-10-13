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


class StorageError(ZhijiError):
    code = "STORAGE_ERROR"


class TaskNotFoundError(ZhijiError):
    code = "TASK_NOT_FOUND"


class TaskTransitionError(ZhijiError):
    """任务状态机非法流转（含缺少失败原因等约束）。"""

    code = "TASK_TRANSITION_INVALID"


class UserNotFoundError(ZhijiError):
    code = "USER_NOT_FOUND"


class DuplicateUserError(ZhijiError):
    code = "USER_ALREADY_EXISTS"


class AuthError(ZhijiError):
    """注册 / 登录 / 会话校验失败。"""

    code = "AUTH_FAILED"


class CryptoError(ZhijiError):
    """加解密失败：密钥不匹配、密文被篡改或格式损坏。"""

    code = "CRYPTO_FAILED"


class MasterKeyError(ZhijiError):
    """主密钥缺失 / 格式非法 / 无法落盘。"""

    code = "MASTER_KEY_UNAVAILABLE"


class GenerationCancelledError(ZhijiError):
    """调用方主动取消（如 SSE 断连）导致的生成中断，不算失败。"""

    code = "GENERATION_CANCELLED"


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