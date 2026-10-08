"""各平台登录态（Cookie + UA）持久化的共享实现。

各平台模块只保留自己的 Cookie 白名单与薄封装（``bilibili_cookies`` /
``douyin_cookies`` / ``zhihu_cookies`` / ``xiaohongshu_cookies``），通用逻辑集中在这里：

- :func:`prune_cookie` 按白名单精简 Cookie，去重并剔除浏览器残留；
- :func:`save_cookie_profile` 以临时文件 + 原子替换写入；
- :func:`load_cookie_profile` 读取并在损坏 / 缺少关键字段时返回 None。

登录态文件位于已 gitignore 的 ``data/`` 目录，不会进入版本库。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from zhiji.errors import OutputWriteError


@dataclass
class CookieProfile:
    """平台登录态的通用结构；各平台如需额外字段可继承扩展。"""

    cookie: str
    user_agent: str
    saved_at: str = ""
    verified: bool = True


ProfileT = TypeVar("ProfileT", bound=CookieProfile)


def prune_cookie(cookie: str, names: frozenset[str]) -> str:
    """只保留白名单里的 Cookie，去掉重复项与浏览器残留。"""

    seen: set[str] = set()
    kept: list[str] = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        if name in names and name not in seen:
            seen.add(name)
            kept.append(part)
    return "; ".join(kept)


def resolve_profile_path(default: Path, path: str | Path | None = None) -> Path:
    return Path(path).expanduser() if path else default


def save_cookie_profile(
    profile_cls: Callable[..., ProfileT],
    default_path: Path,
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    names: frozenset[str],
    label: str,
    verified: bool = True,
) -> ProfileT:
    """按白名单精简 Cookie 后原子写入登录态文件，返回保存的 profile。"""

    profile = profile_cls(
        cookie=prune_cookie(cookie, names),
        user_agent=user_agent,
        saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        verified=verified,
    )
    target = resolve_profile_path(default_path, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise OutputWriteError(f"{label}登录态写入失败: {target}", hint="检查 data/ 目录权限") from exc
    return profile


def load_cookie_profile(
    profile_cls: Callable[..., ProfileT],
    default_path: Path,
    path: str | Path | None = None,
    *,
    names: frozenset[str] | None = None,
    require: str | None = None,
) -> ProfileT | None:
    """读取登录态；文件缺失、损坏或缺少 ``require`` 关键字段时返回 None。"""

    target = resolve_profile_path(default_path, path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        cookie = str(data.get("cookie", ""))
        if names is not None:
            cookie = prune_cookie(cookie, names)
        profile = profile_cls(
            cookie=cookie,
            user_agent=str(data.get("user_agent", "")),
            saved_at=str(data.get("saved_at", "")),
            verified=bool(data.get("verified", True)),
        )
    except (AttributeError, OSError, ValueError, TypeError):
        return None
    if require and require not in profile.cookie:
        return None
    return profile
