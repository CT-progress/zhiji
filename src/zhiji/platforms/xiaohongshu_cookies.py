"""小红书登录态（Cookie + UA）的本地持久化。"""

from __future__ import annotations

from pathlib import Path

from zhiji.config import PROJECT_ROOT
from zhiji.platforms.cookies import (
    CookieProfile,
    load_cookie_profile,
    resolve_profile_path,
    save_cookie_profile,
)
from zhiji.platforms.cookies import prune_cookie as _prune_cookie

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "xiaohongshu-profile.json"

ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "a1",                 # 设备标识
        "web_session",        # 登录会话
        "webId",              # Web 用户标识
        "gid",                # 访客标识
        "xsecappid",          # 接口校验
        "websectiga",         # 风控签名
        "sec_poison_id",      # 风控标识
        "customerClientId",   # 客户端标识
    }
)

XiaohongshuProfile = CookieProfile


def prune_cookie(cookie: str) -> str:
    return _prune_cookie(cookie, ESSENTIAL_COOKIE_NAMES)


def profile_path(path: str | Path | None = None) -> Path:
    return resolve_profile_path(DEFAULT_PROFILE_PATH, path)


def save_profile(
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    verified: bool = True,
) -> XiaohongshuProfile:
    return save_cookie_profile(
        CookieProfile,
        DEFAULT_PROFILE_PATH,
        cookie,
        user_agent,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        label="小红书",
        verified=verified,
    )


def load_profile(path: str | Path | None = None) -> XiaohongshuProfile | None:
    return load_cookie_profile(
        CookieProfile,
        DEFAULT_PROFILE_PATH,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        require="web_session=",
    )
