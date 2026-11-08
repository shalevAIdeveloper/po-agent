import ssl
from typing import Any

import pytest

from tools import check_api_role as tool

CLOUD = "postgresql://po_api.abcdefghijklmnopqrst:hunter2@aws-0-us-east-1.pooler.supabase.com:5432/postgres"


@pytest.mark.parametrize(
    "url",
    ["postgresql://po_api:secret@127.0.0.1:54322/postgres", CLOUD, f"{CLOUD}?sslmode=require"],
)
def test_po_api_urls_are_accepted(url: str) -> None:
    target = tool.parse_target(f"  {url}  ")

    assert target.user.startswith("po_api")
    assert target.url == url


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("mysql://po_api:x@h/db", "postgresql://"),
        ("postgresql://postgres:x@h/postgres", "must be po_api"),
        ("postgresql://po_apix:x@h/postgres", "must be po_api"),
        ("postgresql://po_api@h/postgres", "no password"),
        (f"{CLOUD}?sslmode=disable", "sslmode=disable"),
        (f"{CLOUD}?sslmode=prefer", "sslmode=prefer"),
        (f"{CLOUD}?sslmode=ALLOW", "sslmode=allow"),
    ],
)
def test_other_urls_are_refused(url: str, message: str) -> None:
    with pytest.raises(tool.ToolError, match=message):
        tool.parse_target(url)


def test_local_targets_use_no_tls_and_cloud_targets_always_do() -> None:
    local = tool.parse_target("postgresql://po_api:x@localhost:54322/postgres")
    cloud = tool.parse_target(CLOUD)

    assert local.is_local and local.ssl_context is None
    assert not cloud.is_local
    assert isinstance(cloud.ssl_context, ssl.SSLContext)


def test_without_a_ca_file_tls_is_encrypted_but_unverified() -> None:
    context = tool.tls_context(None)

    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_with_a_ca_file_tls_is_verified(tmp_path: Any) -> None:
    # Any valid PEM bundle works for this check: use the system defaults' location if
    # present, else skip (the point is verify_mode + hostname checking, not the CA).
    paths = ssl.get_default_verify_paths()
    if not paths.cafile:
        pytest.skip("no default CA bundle file on this machine")

    context = tool.tls_context(paths.cafile)

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_with_password_swaps_only_the_password() -> None:
    url = "postgresql://po_api.ref:real%40pw@pooler.example.com:5432/postgres?sslmode=require"

    assert tool.with_password(url, "p@ss word") == (
        "postgresql://po_api.ref:p%40ss%20word@pooler.example.com:5432/postgres?sslmode=require"
    )


def test_with_password_keeps_ipv6_brackets() -> None:
    url = "postgresql://po_api:old@[2600:1f18::1]:5432/postgres"

    assert tool.with_password(url, "new") == "postgresql://po_api:new@[2600:1f18::1]:5432/postgres"


class _PgError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("message with secret-host and hunter2")
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "expected"), [("28P01", True), ("28000", True), ("08006", False), (None, False)]
)
def test_only_auth_errors_count_as_rejection(sqlstate: str | None, expected: bool) -> None:
    assert tool.is_auth_failure(_PgError(sqlstate)) is expected


class _Conn:
    """Answers the role checks like a correctly set-up cloud po_api."""

    async def fetchval(self, query: str) -> Any:
        answers = {
            "select current_user": "po_api",
            "show server_version_num": "170011",
            "show statement_timeout": "8s",
            "show idle_in_transaction_session_timeout": "15s",
        }
        if query in answers:
            return answers[query]
        if "has_database_privilege" in query:
            return True
        if "from public.organizations" in query and not getattr(self, "_switched", False):
            raise tool.asyncpg.InsufficientPrivilegeError("permission denied")
        return 0

    async def fetchrow(self, _query: str) -> dict[str, bool]:
        return {
            "rolcanlogin": True,
            "rolinherit": False,
            "rolsuper": False,
            "rolbypassrls": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolreplication": False,
        }

    async def fetch(self, _query: str) -> list[dict[str, Any]]:
        return [{"rolname": "authenticated", "inherit_option": False, "set_option": True}]

    async def execute(self, _query: str) -> None:
        self._switched = True

    def transaction(self) -> "_Conn":
        return self

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._switched = False

    async def close(self) -> None:
        return None


def _connector(rejection: BaseException | None) -> Any:
    calls: list[str] = []

    async def connect(url: str, _ctx: ssl.SSLContext | None) -> Any:
        calls.append(url)
        if len(calls) == 2 and rejection is not None:
            raise rejection
        return _Conn()

    return connect


@pytest.mark.parametrize(
    ("rejection", "status", "name"),
    [
        (_PgError("28P01"), "PASS", "local password rejected"),
        (_PgError("08006"), "FAIL", "local password rejected (inconclusive)"),
        (OSError("network down"), "FAIL", "local password rejected (inconclusive)"),
        (None, "FAIL", "local password rejected"),
    ],
)
async def test_local_password_probe(
    rejection: BaseException | None, status: str, name: str
) -> None:
    results = await tool.run_checks(tool.parse_target(CLOUD), connect=_connector(rejection))

    last = results[-1]
    assert (last.name, last.status) == (name, status)
    assert all(r.status in ("PASS", "INFO") for r in results[:-1]), results
    assert "hunter2" not in repr(results)


async def test_a_failed_login_is_reported_without_details() -> None:
    async def refuse(_url: str, _ctx: ssl.SSLContext | None) -> object:
        raise OSError("connection refused to secret-host with password hunter2")

    results = await tool.run_checks(tool.parse_target(CLOUD), connect=refuse)

    assert results == [tool.Result("login", "FAIL", "OSError")]


def test_cli_without_a_terminal_is_an_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert tool.main([]) == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_confirms_the_paste_without_content(capsys: pytest.CaptureFixture[str]) -> None:
    pasted = "aws-0-us-east-1.pooler.supabase.com\r\n"  # a wrong, partial paste

    assert tool.main([], prompt=lambda _msg: pasted) == 1

    out = capsys.readouterr()
    assert "received 35 chars, starts with postgresql://: no" in out.out
    assert "pooler.supabase.com" not in out.out


def test_cli_never_prints_the_password(capsys: pytest.CaptureFixture[str]) -> None:
    code = tool.main([], prompt=lambda _msg: "postgresql://postgres:hunter2@h/postgres")

    out = capsys.readouterr()
    assert code == 1
    assert "hunter2" not in out.out + out.err
