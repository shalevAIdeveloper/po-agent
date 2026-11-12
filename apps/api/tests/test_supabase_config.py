"""`supabase/config.toml` is what `supabase config push` sends to the hosted projects (A14).

The base values are pushed as they are, so the Auth hardening (D5/D6) must hold there;
each `[remotes.<env>]` block must point email links at the deployed web only (sec M4).
"""

import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONFIG: dict[str, Any] = tomllib.loads(
    (ROOT / "supabase" / "config.toml").read_text(encoding="utf-8")
)
REMOTES: dict[str, dict[str, Any]] = CONFIG.get("remotes", {})
_PROJECT_REF = "ynscpsuhflxyklsfhdei"
_DEV_WEB_ORIGIN = "https://web-dev-3fce.up.railway.app"


def _deployed_origin(url: object) -> bool:
    # An exact https origin: no localhost, no wildcard, no path/query/fragment.
    if not isinstance(url, str) or "*" in url:
        return False
    parts = urlsplit(url)
    host = parts.hostname or ""
    return (
        parts.scheme == "https"
        and host not in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}  # noqa: S104 (a denylist, not a bind)
        and not host.endswith(".localhost")
        and parts.path == ""
        and not parts.query
        and not parts.fragment
        and parts.username is None
    )


def test_base_auth_hardening_holds() -> None:
    auth = CONFIG["auth"]
    email = auth["email"]
    assert auth["enable_signup"] is False
    assert auth["enable_anonymous_sign_ins"] is False
    assert auth["minimum_password_length"] == 12
    assert email["enable_confirmations"] is True
    assert email["secure_password_change"] is True
    assert email["otp_length"] == 8
    assert email["otp_expiry"] == 3600  # A21: 1 h, decided
    assert auth["mfa"]["totp"]["enroll_enabled"] is True
    assert auth["mfa"]["totp"]["verify_enabled"] is True
    assert email["double_confirm_changes"] is True
    assert auth["enable_manual_linking"] is False
    assert auth["enable_refresh_token_rotation"] is True
    assert auth["jwt_expiry"] <= 3600
    for name in ("invite", "recovery", "magic_link"):
        template = email["template"][name]
        assert (ROOT / template["content_path"].removeprefix("./")).is_file()


def test_base_config_leaves_dashboard_smtp_alone() -> None:
    # SMTP is set in the dashboard. An `[auth.email.smtp]` table here would be pushed:
    # `enabled = false` sends an empty host (disables it), `true` overwrites it with
    # whatever placeholders are in the file (sec, A14 M2).
    assert "smtp" not in CONFIG["auth"]["email"]


def test_base_config_enables_no_other_sign_in_path() -> None:
    auth = CONFIG["auth"]
    assert "hook" not in auth
    for group in ("external", "third_party", "web3"):
        for name, provider in auth.get(group, {}).items():
            assert provider.get("enabled") is False, f"auth.{group}.{name}"
    assert auth["sms"]["enable_signup"] is False
    assert auth["mfa"]["phone"]["enroll_enabled"] is False
    assert auth["mfa"]["phone"]["verify_enabled"] is False
    assert auth["oauth_server"]["enabled"] is False


def test_dev_remote_targets_the_dev_project() -> None:
    dev = REMOTES["dev"]
    assert dev["project_id"] == _PROJECT_REF
    # Exact value, so a typo or lookalike host can't pass the generic origin check.
    assert dev["auth"]["site_url"] == _DEV_WEB_ORIGIN
    assert dev["auth"]["additional_redirect_urls"] == [_DEV_WEB_ORIGIN]


@pytest.mark.parametrize("env", sorted(REMOTES))
def test_remote_links_go_to_the_deployed_web_only(env: str) -> None:
    auth = REMOTES[env]["auth"]
    site_url = auth["site_url"]
    redirects = auth["additional_redirect_urls"]

    assert _deployed_origin(site_url)
    assert redirects, "an empty list would leave the base localhost URLs in place"
    assert all(_deployed_origin(url) for url in redirects)
    assert site_url in redirects


@pytest.mark.parametrize("env", sorted(REMOTES))
def test_remote_email_frequency_is_deliberate(env: str) -> None:
    # The base 1s is for local e2e; pushed as-is it'd let one user spam resets.
    assert REMOTES[env]["auth"]["email"]["max_frequency"] == "60s"


@pytest.mark.parametrize("env", sorted(REMOTES))
def test_remotes_do_not_weaken_auth(env: str) -> None:
    # Overrides may only set URLs and the email frequency; signup, confirmations,
    # password rules and OTP settings come from the base config pinned above.
    # `rate_limit.email_sent` isn't allowed either: without `[auth.email.smtp]` the CLI
    # doesn't push it, so it would only look enforced (sec, A14 M1).
    auth = REMOTES[env]["auth"]
    assert set(auth) <= {"site_url", "additional_redirect_urls", "email"}
    assert set(auth["email"]) <= {"max_frequency"}
    assert set(REMOTES[env]) <= {"project_id", "auth"}


@pytest.mark.parametrize(
    "url",
    [
        "http://web-dev-3fce.up.railway.app",
        "https://localhost:3000",
        "https://127.0.0.1",
        "https://*.up.railway.app",
        "https://web-dev-3fce.up.railway.app/",
        "https://web-dev-3fce.up.railway.app/auth/confirm",
        "https://web-dev-3fce.up.railway.app?x=1",
        "https://user@web-dev-3fce.up.railway.app",
        "",
        None,
    ],
)
def test_deployed_origin_rejects(url: object) -> None:
    assert not _deployed_origin(url)


def test_deployed_origin_accepts_an_exact_origin() -> None:
    assert _deployed_origin("https://web-dev-3fce.up.railway.app")
