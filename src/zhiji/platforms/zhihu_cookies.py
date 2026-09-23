"""知乎登录态（Cookie + UA）的本地持久化。

保存路径 ``data/zhihu-profile.json`` 属于已 gitignore 的 ``data/`` 目录，
不会进入版本库。写入采用临时文件 + 原子替换，避免并发读写出错。

收割的原始 Cookie 会混入浏览器其他站点（Bing/微软等）的残留和知乎的
验证码票据，全量带上会让请求头超过 openresty 的 8KB 限制（HTTP 400）。
这里保存/读取时统一按白名单精简，只保留知乎接口真正需要的字段。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from zhiji.config import PROJECT_ROOT
from zhiji.errors import OutputWriteError

DEFAULT_PROFILE_PATH = PROJECT_ROOT / "data" / "zhihu-profile.json"

#: 知乎接口真正需要的 Cookie 白名单；其余（分析、广告、验证码票据、其他站点
#: 残留）一律不带，避免请求头超限或触发风控。
ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "d_c0",  # x-zse-96 签名必需
        "z_c0",  # 登录凭证
        "_xsrf",  # CSRF
        "q_c1",  # 会话引导
        "_zap",  # 知乎用户标识
        "ANON",  # 匿名访问标记
    }
)


def prune_cookie(cookie: str) -> str:
    """只保留白名单 Cookie，去掉重复项与浏览器残留。

    Cookie 名区分大小写；重复的字段只保留第一个。
    """

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
class ZhihuProfile:
    """从有头浏览器收割的知乎登录态。"""

    cookie: str
    user_agent: str
    saved_at: str = ""
    verified: bool = True

    def d_c0(self) -> str:
        """从 Cookie 串里提取 d_c0（x-zse-96 签名必需）。"""

        for part in self.cookie.split(";"):
            part = part.strip()
            if part.startswith("d_c0="):
                return part[len("d_c0=") :]
        return ""


def profile_path(path: str | Path | None = None) -> Path:
    return Path(path).expanduser() if path else DEFAULT_PROFILE_PATH


def save_profile(
    cookie: str,
    user_agent: str,
    path: str | Path | None = None,
    *,
    verified: bool = True,
) -> ZhihuProfile:
    """原子写入登录态文件，返回保存后的 ZhihuProfile。"""

    profile = ZhihuProfile(
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
        raise OutputWriteError(f"知乎登录态写入失败: {target}", hint="检查 data/ 目录权限") from exc
    return profile


def load_profile(path: str | Path | None = None) -> ZhihuProfile | None:
    """读取登录态；文件缺失或损坏时返回 None（由调用方引导重新登录）。"""

    target = profile_path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return ZhihuProfile(
            cookie=prune_cookie(str(data.get("cookie", ""))),
            user_agent=str(data.get("user_agent", "")),
            saved_at=str(data.get("saved_at", "")),
            verified=bool(data.get("verified", True)),
        )
    except (OSError, ValueError, TypeError):
        return None
