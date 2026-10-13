import json
from pathlib import Path

import pytest

from zhiji.platforms.cookies import (
    CookieProfile,
    load_cookie_profile,
    prune_cookie,
    resolve_profile_path,
    save_cookie_profile,
)


def test_prune_cookie_keeps_first_whitelisted_values():
    names = frozenset({"sessionid", "ttwid"})
    raw = "junk=1; sessionid=first; ttwid=dev; sessionid=second; malformed"

    assert prune_cookie(raw, names) == "sessionid=first; ttwid=dev"


def test_cookie_profile_roundtrip_and_required_field(tmp_path):
    default = tmp_path / "profile.json"
    saved = save_cookie_profile(
        CookieProfile,
        default,
        "sessionid=abc; junk=1",
        "UA",
        names=frozenset({"sessionid"}),
        label="测试平台",
    )

    assert saved.cookie == "sessionid=abc"
    loaded = load_cookie_profile(
        CookieProfile,
        default,
        names=frozenset({"sessionid"}),
        require="sessionid",
    )
    assert loaded is not None
    assert loaded.cookie == "sessionid=abc"
    assert loaded.user_agent == "UA"

    missing = load_cookie_profile(CookieProfile, default, require="missing")
    assert missing is None


def test_cookie_profile_handles_corrupt_and_non_object_json(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text("{broken", encoding="utf-8")
    assert load_cookie_profile(CookieProfile, path) is None

    path.write_text("[]", encoding="utf-8")
    assert load_cookie_profile(CookieProfile, path) is None


def test_resolve_profile_path_uses_override_or_default(tmp_path):
    default = tmp_path / "default.json"
    override = tmp_path / "custom.json"

    assert resolve_profile_path(default) == default
    assert resolve_profile_path(default, override) == override
    assert isinstance(resolve_profile_path(default), Path)


# ---------------------------------------------------------------------------
# 阶段 4.3：默认走数据库的加密存储（显式给 path 时仍然读写文件）
# ---------------------------------------------------------------------------


@pytest.fixture()
def profile_db(tmp_path):
    """把登录态仓储指向一个临时库，避免碰到真实的 data/zhiji.db。"""

    from zhiji.platforms import cookies as cookies_module
    from zhiji.storage import Database

    db = Database(f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}")
    cookies_module.set_profile_database(db)
    yield db
    cookies_module.set_profile_database(None)
    db.dispose()


def _cookie_file(cookie: str) -> str:
    return json.dumps(
        {"cookie": cookie, "user_agent": "UA", "saved_at": "2026-01-01T00:00:00+00:00"},
        ensure_ascii=False,
    )


def test_database_mode_saves_without_writing_file(profile_db, tmp_path):
    default = tmp_path / "douyin-profile.json"
    saved = save_cookie_profile(
        CookieProfile,
        default,
        "sessionid=abc; junk=1",
        "UA",
        names=frozenset({"sessionid"}),
        label="抖音",
        platform="douyin",
    )

    assert saved.cookie == "sessionid=abc"
    assert not default.exists()

    from zhiji.platforms.cookies import profile_repository

    assert profile_repository().has("douyin") is True
    loaded = load_cookie_profile(CookieProfile, default, platform="douyin")
    assert loaded is not None
    assert loaded.cookie == "sessionid=abc"
    assert loaded.user_agent == "UA"


def test_database_mode_unconfigured_returns_none(profile_db, tmp_path):
    default = tmp_path / "douyin-profile.json"
    assert load_cookie_profile(CookieProfile, default, platform="douyin") is None


def test_database_mode_removes_leftover_plaintext_file(profile_db, tmp_path):
    default = tmp_path / "douyin-profile.json"
    default.write_text(_cookie_file("sessionid=old"), encoding="utf-8")

    save_cookie_profile(
        CookieProfile,
        default,
        "sessionid=new",
        "UA",
        names=frozenset({"sessionid"}),
        label="抖音",
        platform="douyin",
    )

    # 入库后明文文件必须消失，且读到的是新值
    assert not default.exists()
    loaded = load_cookie_profile(CookieProfile, default, platform="douyin")
    assert loaded is not None
    assert loaded.cookie == "sessionid=new"


def test_database_mode_imports_legacy_file_once(profile_db, tmp_path):
    default = tmp_path / "douyin-profile.json"
    default.write_text(_cookie_file("sessionid=legacy; junk=1"), encoding="utf-8")

    loaded = load_cookie_profile(
        CookieProfile,
        default,
        names=frozenset({"sessionid"}),
        require="sessionid",
        platform="douyin",
    )

    assert loaded is not None
    assert loaded.cookie == "sessionid=legacy"
    assert not default.exists()  # 明文文件被消费掉

    from zhiji.platforms.cookies import profile_repository

    assert profile_repository().has("douyin") is True
    again = load_cookie_profile(CookieProfile, default, platform="douyin")
    assert again is not None
    assert again.cookie == "sessionid=legacy"


def test_explicit_path_still_uses_file_only(profile_db, tmp_path):
    path = tmp_path / "custom.json"
    save_cookie_profile(
        CookieProfile,
        tmp_path / "default.json",
        "sessionid=abc",
        "UA",
        path,
        names=frozenset({"sessionid"}),
        label="抖音",
        platform="douyin",
    )

    from zhiji.platforms.cookies import profile_repository

    assert path.exists()
    assert profile_repository().is_empty() is True


def test_delete_cookie_profile_clears_database_and_file(profile_db, tmp_path):
    from zhiji.platforms.cookies import delete_cookie_profile, profile_repository

    default = tmp_path / "douyin-profile.json"
    save_cookie_profile(
        CookieProfile,
        default,
        "sessionid=abc",
        "UA",
        names=frozenset({"sessionid"}),
        label="抖音",
        platform="douyin",
    )
    default.write_text(_cookie_file("sessionid=stale"), encoding="utf-8")

    assert delete_cookie_profile(default, platform="douyin") is True
    assert not default.exists()
    assert profile_repository().has("douyin") is False
    assert delete_cookie_profile(default, platform="douyin") is False


def test_database_load_cleans_up_stale_plaintext_file(profile_db, tmp_path):
    """库里已有登录态时，读到它就该顺手清掉残留的明文文件。"""

    default = tmp_path / "douyin-profile.json"
    save_cookie_profile(
        CookieProfile,
        default,
        "sessionid=abc",
        "UA",
        names=frozenset({"sessionid"}),
        label="抖音",
        platform="douyin",
    )
    # 模拟「入库成功但文件没删掉」的残留状态
    default.write_text(_cookie_file("sessionid=stale-value"), encoding="utf-8")

    loaded = load_cookie_profile(CookieProfile, default, platform="douyin")
    assert loaded is not None
    assert loaded.cookie == "sessionid=abc"  # 以库里的为准
    assert not default.exists()
