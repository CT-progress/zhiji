"""抖音登录态（Cookie + UA）的本地持久化。

保存路径 ``data/douyin-profile.json`` 属于已 gitignore 的 ``data/`` 目录，
不会进入版本库。写入采用临时文件 + 原子替换，避免并发读写出错。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from zhiji.config import PROJECT_ROOT
from zhiji.errors import OutputWriteError

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "douyin-profile.json"

#: 抖音 Web API 需要的关键 Cookie 字段
ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "ttwid",           # 设备标识
        "msToken",         # 风控 token
        "odin_tt",         # 用户标识
        "passport_csrf_token",  # CSRF
        "s_v_web_id",      # 会话标识
        "sessionid",       # 登录会话
        "sessionid_ss",    # 登录会话（安全）
        "sid_guard",       # 会话守卫
        "sid_tt",          # 会话 token
        "uid_tt",          # 用户 token
        "uid_tt_ss",       # 用户 token（安全）
        "passport_auth_status",  # 登录状态
        "passport_auth_status_ss",
    }
)


def prune_cookie(cookie: str) -> str:
    """只保留关键 Cookie，去掉重复项与浏览器残留。"""

    seen: set[str] = set()
    kept: list[str] = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        if name in ESSENTIAL_COOKIE_NAMES and name not in seen:
            seen.add(name)
            kept.append(part)
    return "; ".join(kept)


@dataclass
class DouyinProfile:
    """从浏览器收割的抖音登录态。"""

    cookie: str
    user_agent: str
    saved_at: str = ""
    verified: bool = True


def profile_path(path: str | Path | None = None) -> Path:
    return Path(path).expanduser() if path else DEFAULT_PROFILE_PATH


def save_profile(
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    verified: bool = True,
) -> DouyinProfile:
    """原子写入登录态文件，返回保存后的 DouyinProfile。"""

    profile = DouyinProfile(
        cookie=prune_cookie(cookie),
        user_agent=user_agent,
        saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        verified=verified,
    )
    target = profile_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise OutputWriteError(f"抖音登录态写入失败: {target}", hint="检查 data/ 目录权限") from exc
    return profile


def load_profile(path: str | Path | None = None) -> DouyinProfile | None:
    """读取登录态；文件缺失或损坏时返回 None。"""

    target = profile_path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return DouyinProfile(
            cookie=str(data.get("cookie", "")),
            user_agent=str(data.get("user_agent", "")),
            saved_at=str(data.get("saved_at", "")),
            verified=bool(data.get("verified", True)),
        )
    except (OSError, ValueError, TypeError):
        return None
