"""知乎登录态（Cookie + UA）的持久化。

阶段 4.3 起默认加密存进数据库 ``platform_credentials`` 表；``data/zhihu-profile.json``
只在升级时作为迁移来源，被读到后立即加密入库并删除，明文不再长期驻留磁盘。
通用读写逻辑见 :mod:`zhiji.platforms.cookies`。

收割的原始 Cookie 会混入浏览器其他站点（Bing/微软等）的残留和知乎的
验证码票据，全量带上会让请求头超过 openresty 的 8KB 限制（HTTP 400）。
这里保存/读取时统一按白名单精简，只保留知乎接口真正需要的字段。
"""

from __future__ import annotations

from dataclasses import dataclass
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

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "zhihu-profile.json"

#: 知乎接口真正需要的 Cookie 白名单；其余（分析、广告、验证码票据、其他站点
#: 残留）一律不带，避免请求头超限或触发风控。
ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "d_c0",    # x-zse-96 签名必需
        "z_c0",    # 登录凭证
        "_xsrf",   # CSRF
        "q_c1",    # 会话引导
        "_zap",    # 知乎用户标识
        "ANON",    # 匿名访问标记
    }
)


@dataclass
class ZhihuProfile(CookieProfile):
    """从有头浏览器收割的知乎登录态。"""

    def d_c0(self) -> str:
        """从 Cookie 串里提取 d_c0（x-zse-96 签名必需）。"""

        for part in self.cookie.split(";"):
            part = part.strip()
            if part.startswith("d_c0="):
                return part[len("d_c0=") :]
        return ""


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
) -> ZhihuProfile:
    """精简 Cookie 后加密入库（显式给 path 时写入文件），返回保存的 profile。"""

    return save_cookie_profile(
        ZhihuProfile,
        DEFAULT_PROFILE_PATH,
        cookie,
        user_agent,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        label="知乎",
        platform="zhihu",
        verified=verified,
    )


def load_profile(path: str | Path | None = None) -> ZhihuProfile | None:
    """读取登录态；数据库优先，其次旧明文文件；都拿不到时返回 None。"""

    return load_cookie_profile(
        ZhihuProfile,
        DEFAULT_PROFILE_PATH,
        path,
        names=ESSENTIAL_COOKIE_NAMES,
        platform="zhihu",
    )


def delete_profile(path: str | Path | None = None) -> bool:
    """删除登录态：数据库里的一行 + 遗留的明文文件，返回是否真的删掉了东西。"""

    return delete_cookie_profile(DEFAULT_PROFILE_PATH, path, platform="zhihu")
