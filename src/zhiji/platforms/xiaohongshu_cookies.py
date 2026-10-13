"""小红书登录态（Cookie + UA）的持久化。

阶段 4.3 起默认加密存进数据库 ``platform_credentials`` 表；``data/xiaohongshu-profile.json``
只在升级时作为迁移来源，被读到后立即加密入库并删除，明文不再长期驻留磁盘。
"""

from __future__ import annotations

from pathlib import Path

from zhiji.config import PROJECT_ROOT
from zhiji.platforms.cookies import (
    CookieProfile,
    delete_cookie_profile,
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
        platform="xiaohongshu",
        verified=verified,
    )


def load_profile(path: str | Path | None = None) -> XiaohongshuProfile | None:
    """读取登录态；数据库优先，其次旧明文文件；未包含 web_session 时返回 None。"""

    return load_cookie_profile(
        CookieProfile,
        DEFAULT_PROFILE_PATH,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        require="web_session=",
        platform="xiaohongshu",
    )


def delete_profile(path: str | Path | None = None) -> bool:
    """删除登录态：数据库里的一行 + 遗留的明文文件，返回是否真的删掉了东西。"""

    return delete_cookie_profile(DEFAULT_PROFILE_PATH, path, platform="xiaohongshu")
