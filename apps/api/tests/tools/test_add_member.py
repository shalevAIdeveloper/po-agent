import json
import re
from typing import Any

import httpx
import pytest

from tools import add_member as tool

SECRET = "sb_secret_unit_test_key"
LOCAL = {"ADMIN_SUPABASE_URL": "http://127.0.0.1:54321", "ADMIN_SUPABASE_SECRET_KEY": SECRET}
ROW = {
    "org_id": "feed0000-0000-4000-8000-0000000000a6",
    "org_name": "Acme",
    "user_id": "feed0000-0000-4000-8000-000000000062",
    "role": "reviewer",
}
ARGS = dict(email="b@acme.test", org_slug="acme", role="reviewer", actor="ops", reason="pilot")


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ----- target resolution -----


def test_local_target_needs_no_confirmation() -> None:
    target = tool.resolve_target(LOCAL, None)

    assert target == tool.Target(url="http://127.0.0.1:54321", secret_key=SECRET)


def test_missing_url_is_an_error() -> None:
    with pytest.raises(tool.ToolError, match="Set ADMIN_SUPABASE_URL"):
        tool.resolve_target({"ADMIN_SUPABASE_SECRET_KEY": SECRET}, None)


def test_missing_key_without_a_prompt_is_an_error() -> None:
    with pytest.raises(tool.ToolError, match="No secret key"):
        tool.resolve_target({"ADMIN_SUPABASE_URL": LOCAL["ADMIN_SUPABASE_URL"]}, None)


def test_missing_key_is_prompted_for_naming_the_project() -> None:
    asked: list[str] = []

    def prompt(message: str) -> str:
        asked.append(message)
        return f"  {SECRET}  "

    target = tool.resolve_target({"ADMIN_SUPABASE_URL": LOCAL["ADMIN_SUPABASE_URL"]}, None, prompt)

    assert target.secret_key == SECRET
    assert asked == ["Secret key for http://127.0.0.1:54321 (input hidden): "]


def test_env_key_wins_and_no_prompt_is_shown() -> None:
    def prompt(_message: str) -> str:
        raise AssertionError("no prompt expected")

    assert tool.resolve_target(LOCAL, None, prompt).secret_key == SECRET


def test_remote_target_requires_matching_confirm_host() -> None:
    env = {**LOCAL, "ADMIN_SUPABASE_URL": "https://abc.supabase.co"}

    with pytest.raises(tool.ToolError, match=re.escape("--confirm-host abc.supabase.co")):
        tool.resolve_target(env, None)
    with pytest.raises(tool.ToolError, match=re.escape("--confirm-host abc.supabase.co")):
        tool.resolve_target(env, "xyz.supabase.co")
    assert tool.resolve_target(env, "abc.supabase.co").url == "https://abc.supabase.co"


def test_remote_target_requires_https() -> None:
    env = {**LOCAL, "ADMIN_SUPABASE_URL": "http://abc.supabase.co"}

    with pytest.raises(tool.ToolError, match="https"):
        tool.resolve_target(env, "abc.supabase.co")


@pytest.mark.parametrize("key", ["sb_publishable_x", "eyJhbGciOiJIUzI1NiJ9.e30.x"])
def test_only_secret_keys_are_accepted(key: str) -> None:
    with pytest.raises(tool.ToolError, match="secret key"):
        tool.resolve_target({**LOCAL, "ADMIN_SUPABASE_SECRET_KEY": key}, None)


# ----- the RPC call -----


def test_calls_the_rpc_with_the_secret_key_and_all_arguments() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[ROW])

    added = tool.add_member(tool.resolve_target(LOCAL, None), **ARGS, http=_client(handler))

    assert added == tool.AddedMember(**ROW)
    request = seen[0]
    assert str(request.url) == "http://127.0.0.1:54321/rest/v1/rpc/admin_add_member"
    assert request.headers["apikey"] == SECRET
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(request.content) == {
        "p_email": "b@acme.test",
        "p_org_slug": "acme",
        "p_role": "reviewer",
        "p_actor": "ops",
        "p_reason": "pilot",
    }


def test_owner_role_is_refused_before_any_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    with pytest.raises(tool.ToolError, match="admin, reviewer"):
        tool.add_member(
            tool.resolve_target(LOCAL, None), **{**ARGS, "role": "owner"}, http=_client(handler)
        )


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            404,
            {"code": "P0002", "message": "no user with that email (invite them first)"},
            "Not found: no user",
        ),
        (409, {"code": "23505", "message": "x"}, "already a member"),
        (
            400,
            {"code": "22023", "message": "reason is required"},
            "Invalid input: reason is required",
        ),
        (401, {"code": "42501", "message": "permission denied"}, "secret (service-role) key"),
        (404, {"code": "PGRST202", "message": "Could not find the function"}, "not deployed"),
        (500, {"code": "XX000", "message": "internal ACME 123 Main St"}, "HTTP 500, code XX000"),
        (502, "<html>bad gateway</html>", "HTTP 502, code none"),
    ],
)
def test_errors_are_mapped_to_fixed_messages(status: int, body: Any, expected: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    with pytest.raises(tool.ToolError, match=re.escape(expected)) as excinfo:
        tool.add_member(tool.resolve_target(LOCAL, None), **ARGS, http=_client(handler))
    assert "ACME" not in str(excinfo.value)


def test_network_failure_is_reported_without_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(tool.ToolError, match=r"Could not reach .* \(ConnectError\)"):
        tool.add_member(tool.resolve_target(LOCAL, None), **ARGS, http=_client(handler))


def test_unexpected_success_shape_is_an_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with pytest.raises(tool.ToolError, match="Unexpected response"):
        tool.add_member(tool.resolve_target(LOCAL, None), **ARGS, http=_client(handler))


# ----- CLI -----


def test_cli_reports_errors_on_stderr_and_never_prints_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = {**LOCAL, "ADMIN_SUPABASE_URL": "https://abc.supabase.co"}
    argv = ["--email", "b@acme.test", "--org", "acme", "--role", "admin", "--actor", "ops",
            "--reason", "pilot"]  # fmt: skip

    code = tool.main(argv, env=env)

    out = capsys.readouterr()
    assert code == 1
    assert "--confirm-host abc.supabase.co" in out.err
    assert SECRET not in out.err + out.out


def test_cli_requires_actor_and_reason(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["--email", "b@acme.test", "--org", "acme", "--role", "admin"], env=LOCAL)

    assert excinfo.value.code == 2
    assert "--actor" in capsys.readouterr().err
