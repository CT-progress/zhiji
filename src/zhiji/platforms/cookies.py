"""各平台登录态（Cookie + UA）持久化的共享实现。

阶段 4.3 起，登录态默认**加密入库**（``platform_credentials`` 表）：

- :func:`save_cookie_profile` 精简 Cookie 后整体加密写入数据库，并顺手删掉同目录下
  遗留的明文 ``*-profile.json``，避免明文继续躺在磁盘上；
- :func:`load_cookie_profile` 优先从库里解密；库里没有时回退读旧明文文件，读到就
  顺手加密入库并删除该文件——老数据不丢，明文文件清零；
- 显式传入 ``path`` 时仍然读写文件（测试与临时场景用），此时完全不碰数据库；
- 明文文件永远不是权威存储：删掉它不影响已入库的登录态。

各平台模块只保留自己的 Cookie 白名单与薄封装（``bilibili_cookies`` /
``douyin_cookies`` / ``zhihu_cookies`` / ``xiaohongshu_cookies``），通用逻辑集中
在这里：

- :func:`prune_cookie` 按白名单精简 Cookie，去重并剔除浏览器残留；
- :func:`save_cookie_profile` / :func:`load_cookie_profile` / :func:`delete_cookie_profile`
  统一读写入口；
- :func:`set_profile_database` 由 Web / CLI 启动时注入当前数据库。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from zhiji.errors import OutputWriteError, ZhijiError

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from zhiji.storage.db import Database
    from zhiji.storage.platform_credential_repository import PlatformCredentialRepository


@dataclass
class CookieProfile:
    """平台登录态的通用结构；各平台如需额外字段可继承扩展。"""

    cookie: str
    user_agent: str
    saved_at: str = ""
    verified: bool = True


ProfileT = TypeVar("ProfileT", bound=CookieProfile)

#: 登录态密文所在的数据库；None 表示用进程级默认库。
_profile_database: Database | None = None
_repository_cache: tuple[Database, PlatformCredentialRepository] | None = None


def set_profile_database(database: Database | None) -> None:
    """指定登录态密文所在的数据库；Web / CLI 启动时注入，None 表示用默认库。"""

    global _profile_database, _repository_cache
    _profile_database = database
    _repository_cache = None


def profile_repository() -> PlatformCredentialRepository:
    """取当前数据库的登录态仓储；同一个库只构造一次，省掉重复建表检查。"""

    global _repository_cache
    from zhiji.storage import get_database
    from zhiji.storage.platform_credential_repository import PlatformCredentialRepository

    database = _profile_database if _profile_database is not None else get_database()
    cached = _repository_cache
    if cached is None or cached[0] is not database:
        cached = (database, PlatformCredentialRepository(database))
        _repository_cache = cached
    return cached[1]


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


def uses_database(path: str | Path | None, platform: str | None) -> bool:
    """显式给 path 时永远走文件；否则配了 platform 就走数据库。"""

    return path is None and platform is not None


def save_cookie_profile(
    profile_cls: Callable[..., ProfileT],
    default_path: Path,
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    names: frozenset[str],
    label: str,
    platform: str | None = None,
    verified: bool = True,
) -> ProfileT:
    """按白名单精简 Cookie 后加密入库（或原子写入文件），返回保存的 profile。"""

    profile = profile_cls(
        cookie=prune_cookie(cookie, names),
        user_agent=user_agent,
        saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        verified=verified,
    )
    if uses_database(path, platform) and platform is not None:
        profile_repository().save(platform, asdict(profile))
        # 明文文件已无用途，删掉它才算真的「明文清零」。
        _remove_plaintext_file(default_path)
        return profile
    _write_profile_file(profile, resolve_profile_path(default_path, path), label=label)
    return profile


def load_cookie_profile(
    profile_cls: Callable[..., ProfileT],
    default_path: Path,
    path: str | Path | None = None,
    *,
    names: frozenset[str] | None = None,
    require: str | None = None,
    platform: str | None = None,
) -> ProfileT | None:
    """读取登录态：数据库优先，其次旧明文文件，都拿不到时返回 None。"""

    if uses_database(path, platform) and platform is not None:
        profile = _load_from_database(profile_cls, platform, names, require)
        if profile is not None:
            # 库里已经是权威存储：顺手清掉可能残留的明文文件，避免它长期躺在磁盘上。
            _remove_plaintext_file(resolve_profile_path(default_path, None))
            return profile
        return _import_legacy_profile(profile_cls, default_path, platform, names, require)
    return _read_profile_file(
        profile_cls, resolve_profile_path(default_path, path), names, require
    )


def delete_cookie_profile(
    default_path: Path, path: str | Path | None = None, *, platform: str | None = None
) -> bool:
    """删除登录态：库里的一行 + 遗留的明文文件；返回是否真的删掉了东西。"""

    removed = False
    if uses_database(path, platform) and platform is not None:
        try:
            removed = profile_repository().delete(platform)
        except ZhijiError:
            removed = False
    target = resolve_profile_path(default_path, path)
    if target.exists():
        try:
            target.unlink()
            removed = True
        except OSError:
            pass
    return removed


def _load_from_database(
    profile_cls: Callable[..., ProfileT],
    platform: str,
    names: frozenset[str] | None,
    require: str | None,
) -> ProfileT | None:
    """从密文表读取并解密；主密钥不可用或密文损坏时退化为「未配置」。"""

    try:
        payload = profile_repository().load(platform)
    except ZhijiError:
        return None
    if payload is None:
        return None
    return _profile_from_mapping(profile_cls, payload, names, require)


def _import_legacy_profile(
    profile_cls: Callable[..., ProfileT],
    default_path: Path,
    platform: str,
    names: frozenset[str] | None,
    require: str | None,
) -> ProfileT | None:
    """库里没有时的兼容路径：读旧明文文件，加密入库并删除明文文件。"""

    target = resolve_profile_path(default_path, None)
    legacy = _read_profile_file(profile_cls, target, names, require)
    if legacy is None:
        return None
    try:
        profile_repository().save(platform, asdict(legacy))
    except ZhijiError:
        # 入库失败也不能挡住本次使用，只是本次继续读明文文件。
        return legacy
    _remove_plaintext_file(target)
    return legacy


def _write_profile_file(profile: CookieProfile, target: Path, *, label: str) -> None:
    """临时文件 + 原子替换写入登录态文件。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(target)
    except OSError as exc:
        raise OutputWriteError(
            f"{label}登录态写入失败: {target}", hint="检查 data/ 目录权限"
        ) from exc


def _remove_plaintext_file(target: Path) -> None:
    """尽力删除遗留的明文登录态文件；删不掉也不影响主流程。"""

    try:
        if target.exists():
            target.unlink()
    except OSError:
        pass


def _read_profile_file(
    profile_cls: Callable[..., ProfileT],
    target: Path,
    names: frozenset[str] | None,
    require: str | None,
) -> ProfileT | None:
    """读登录态文件；文件缺失、损坏或缺少 ``require`` 关键字段时返回 None。"""

    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _profile_from_mapping(profile_cls, data, names, require)


def _profile_from_mapping(
    profile_cls: Callable[..., ProfileT],
    data: Mapping[str, Any],
    names: frozenset[str] | None,
    require: str | None,
) -> ProfileT | None:
    """把字典转成 profile 对象，并按需精简 / 校验关键字段。"""

    try:
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
