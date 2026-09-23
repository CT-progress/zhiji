"""B 站登录态（Cookie + UA）的本地持久化。

保存路径 ``data/bilibili-profile.json`` 属于已 gitignore 的 ``data/`` 目录，
不会进入版本库。写入采用临时文件 + 原子替换，避免并发读写出错。

B 站字幕接口（x/player/wbi/v2）未登录时不返回字幕列表；保存 SESSDATA 后
适配器会优先走官方字幕，缺失或失效时自动降级到音频转写。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from zhiji.config import PROJECT_ROOT
from zhiji.errors import OutputWriteError

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


def prune_cookie(cookie: str) -> str:
    """只保留白名单 Cookie，去掉重复项与浏览器残留。"""

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
class BilibiliProfile:
    """从浏览器收割或用户手动粘贴的 B 站登录态。"""

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
) -> BilibiliProfile:
    """原子写入登录态文件，返回保存后的 BilibiliProfile。"""

    profile = BilibiliProfile(
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
        raise OutputWriteError(f"B 站登录态写入失败: {target}", hint="检查 data/ 目录权限") from exc
    return profile


def load_profile(path: str | Path | None = None) -> BilibiliProfile | None:
    """读取登录态；文件缺失、损坏或未包含 SESSDATA 时返回 None。"""

    target = profile_path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        profile = BilibiliProfile(
            cookie=prune_cookie(str(data.get("cookie", ""))),
            user_agent=str(data.get("user_agent", "")),
            saved_at=str(data.get("saved_at", "")),
            verified=bool(data.get("verified", True)),
        )
    except (OSError, ValueError, TypeError):
        return None
    if "SESSDATA=" not in profile.cookie:
        return None
    return profile
