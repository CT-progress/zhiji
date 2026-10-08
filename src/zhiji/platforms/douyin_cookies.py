"""抖音登录态（Cookie + UA）的本地持久化。

保存路径 ``data/douyin-profile.json`` 属于已 gitignore 的 ``data/`` 目录，
不会进入版本库；通用读写逻辑见 :mod:`zhiji.platforms.cookies`。
"""

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

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "douyin-profile.json"

#: 抖音 Web API 需要的关键 Cookie 字段
ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "ttwid",                    # 设备标识
        "msToken",                  # 风控 token
        "odin_tt",                  # 用户标识
        "passport_csrf_token",      # CSRF
        "s_v_web_id",               # 会话标识
        "sessionid",                # 登录会话
        "sessionid_ss",             # 登录会话（安全）
        "sid_guard",                # 会话守卫
        "sid_tt",                   # 会话 token
        "uid_tt",                   # 用户 token
        "uid_tt_ss",                # 用户 token（安全）
        "passport_auth_status",     # 登录状态
        "passport_auth_status_ss",
    }
)

#: 通用结构，保留平台专属别名便于类型标注与外部引用。
DouyinProfile = CookieProfile


def prune_cookie(cookie: str) -> str:
    """只保留关键 Cookie，去掉重复项与浏览器残留。"""

    return _prune_cookie(cookie, ESSENTIAL_COOKIE_NAMES)


def profile_path(path: str | Path | None = None) -> Path:
    return resolve_profile_path(DEFAULT_PROFILE_PATH, path)


def save_profile(
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    verified: bool = True,
) -> DouyinProfile:
    """原子写入登录态文件，返回保存后的 DouyinProfile。"""

    return save_cookie_profile(
        CookieProfile,
        DEFAULT_PROFILE_PATH,
        cookie,
        user_agent,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        label="抖音",
        verified=verified,
    )


def load_profile(path: str | Path | None = None) -> DouyinProfile | None:
    """读取登录态；文件缺失或损坏时返回 None。"""

    return load_cookie_profile(CookieProfile, DEFAULT_PROFILE_PATH, path)
