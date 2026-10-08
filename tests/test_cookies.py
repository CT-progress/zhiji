from pathlib import Path

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
