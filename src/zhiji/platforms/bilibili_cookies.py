"""B 站登录态（Cookie + UA）的本地持久化。

保存路径 ``data/bilibili-profile.json`` 属于已 gitignore 的 ``data/`` 目录，
不会进入版本库；通用读写逻辑见 :mod:`zhiji.platforms.cookies`。

B 站字幕接口（x/player/wbi/v2）未登录时不返回字幕列表；保存 SESSDATA 后
适配器会优先走官方字幕，缺失或失效时自动降级到音频转写。
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

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "bilibili-profile.json"

#: B 站接口真正需要的 Cookie 白名单；其余（分析、广告、其他站点残留）一律不带。
ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "SESSDATA",           # 登录凭证（字幕接口必需）
        "bili_jct",           # CSRF
        "buvid3",             # 设备标识（搜索接口风控需要）
        "DedeUserID",         # 用户标识
        "DedeUserID__ckMd5",  # 用户标识校验
        "sid",                # 会话标识
    }
)

#: 通用结构，保留平台专属别名便于类型标注与外部引用。
BilibiliProfile = CookieProfile


def prune_cookie(cookie: str) -> str:
    """只保留白名单 Cookie，去掉重复项与浏览器残留。"""

    return _prune_cookie(cookie, ESSENTIAL_COOKIE_NAMES)


def profile_path(path: str | Path | None = None) -> Path:
    return resolve_profile_path(DEFAULT_PROFILE_PATH, path)


def save_profile(
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    verified: bool = True,
) -> BilibiliProfile:
    """原子写入登录态文件，返回保存后的 BilibiliProfile。"""

    return save_cookie_profile(
        CookieProfile,
        DEFAULT_PROFILE_PATH,
        cookie,
        user_agent,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        label="B 站",
        verified=verified,
    )


def load_profile(path: str | Path | None = None) -> BilibiliProfile | None:
    """读取登录态；文件缺失、损坏或未包含 SESSDATA 时返回 None。"""

    return load_cookie_profile(
        CookieProfile,
        DEFAULT_PROFILE_PATH,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        require="SESSDATA=",
    )
