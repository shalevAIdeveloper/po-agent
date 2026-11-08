"""Check the API's database role (po_api) on a hosted project after setup (A2, 0.5b).

Runs on the operator's machine only; never deployed. Connects with the API's own
connection string (the one Railway will use) and verifies that po_api:
  - logs in over TLS, on PostgreSQL >= 16;
  - can log in but is NOINHERIT, not superuser, no BYPASSRLS / CREATEROLE / CREATEDB /
    REPLICATION, and is a member of `authenticated` only (SET, never INHERIT);
  - cannot read a tenant table on its own (fail closed);
  - can switch to `authenticated` per transaction, where RLS shows it nothing;
  - carries its own statement / idle-in-transaction timeouts;
  - rejects the local well-known password (non-local targets only).
It also reports whether po_api can create temp tables (A4, informational).

Usage (from apps/api, PowerShell):
    uv run python -m tools.check_api_role [--ca-file prod-ca-2021.crt]
It asks for the connection string with hidden input, e.g. the Supavisor session-mode
URL postgresql://po_api.<ref>:<password>@<pooler-host>:5432/postgres
Runbook: docs/runbooks/supabase-project.md
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import ssl
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

import asyncpg

# From supabase/roles.sql (public, local-only); it must never work in the cloud.
LOCAL_PASSWORD = "po_api_local_only"  # noqa: S105
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WEAK_SSLMODES = frozenset({"disable", "allow", "prefer"})
CONNECT_TIMEOUT_SECONDS = 15.0

Status = Literal["PASS", "FAIL", "INFO", "SKIP"]


class ToolError(Exception):
    """A failure to report to the operator (exit code 1)."""


@dataclass(frozen=True, slots=True)
class Result:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True, slots=True)
class Target:
    url: str
    host: str
    user: str
    # None locally; for cloud targets, TLS is always on (verified when a CA file is given).
    ssl_context: ssl.SSLContext | None = field(default=None, compare=False)

    @property
    def is_local(self) -> bool:
        return self.host in LOCAL_HOSTS


def tls_context(ca_file: str | None) -> ssl.SSLContext:
    """TLS for cloud targets: verify-full with the project's CA, else encrypted-only."""
    if ca_file:
        context = ssl.create_default_context(cafile=ca_file)  # verifies chain + hostname
    else:
        context = ssl.create_default_context()
        # Without the Supabase CA the chain can't be verified: still encrypted, never
        # plain text (asyncpg's default `prefer` would fall back to no TLS at all).
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def parse_target(url: str, ca_file: str | None = None) -> Target:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ToolError("Expected a postgresql:// connection string.")
    user = unquote(parts.username or "")
    # Supavisor addresses a role as `po_api.<project-ref>`.
    if user.split(".", 1)[0] != "po_api":
        raise ToolError("This checks the API role: the user must be po_api (or po_api.<ref>).")
    if not parts.password:
        raise ToolError("The connection string has no password.")
    sslmode = (parse_qs(parts.query).get("sslmode") or [""])[0].lower()
    if sslmode in WEAK_SSLMODES:
        raise ToolError(f"sslmode={sslmode} would allow an unencrypted connection; remove it.")
    host = parts.hostname
    context = None if host in LOCAL_HOSTS else tls_context(ca_file)
    return Target(url=url, host=host, user=user, ssl_context=context)


def with_password(url: str, password: str) -> str:
    """The same connection string with another password (for the rejection check)."""
    parts = urlsplit(url)
    user = parts.username or ""
    # Keep host[:port] exactly as written (IPv6 literals keep their brackets).
    hostport = parts.netloc.rsplit("@", 1)[1]
    netloc = f"{user}:{quote(password, safe='')}@{hostport}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def is_auth_failure(error: BaseException) -> bool:
    """SQLSTATE class 28: invalid authorization (wrong password, unknown user)."""
    return str(getattr(error, "sqlstate", "") or "").startswith("28")


Connect = Callable[[str, "ssl.SSLContext | None"], Awaitable[Any]]


async def _connect(url: str, ssl_context: ssl.SSLContext | None) -> Any:
    # statement_cache_size=0: safe through any pooler mode (A2).
    return await asyncpg.connect(
        url,
        timeout=CONNECT_TIMEOUT_SECONDS,
        statement_cache_size=0,
        ssl=ssl_context if ssl_context is not None else False,
    )


def _error(name: str, error: BaseException) -> Result:
    sqlstate = getattr(error, "sqlstate", None)
    suffix = f", SQLSTATE {sqlstate}" if sqlstate else ""
    return Result(name, "FAIL", f"{type(error).__name__}{suffix}")


async def _role_checks(conn: Any) -> list[Result]:
    results: list[Result] = []

    current = await conn.fetchval("select current_user")
    results.append(
        Result(
            "current user", "PASS" if current == "po_api" else "FAIL", f"current_user = {current}"
        )
    )

    version = int(await conn.fetchval("show server_version_num"))
    results.append(
        Result("PostgreSQL >= 16", "PASS" if version >= 160000 else "FAIL", f"{version}")
    )

    attrs = await conn.fetchrow(
        "select rolcanlogin, rolinherit, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,"
        " rolreplication from pg_roles where rolname = current_user"
    )
    if attrs is None:
        results.append(Result("role attributes", "FAIL", "role not found"))
    else:
        powers = {k.removeprefix("rol"): v for k, v in attrs.items() if k != "rolcanlogin"}
        ok = attrs["rolcanlogin"] and not any(powers.values())
        on = [name for name, value in powers.items() if value]
        results.append(
            Result(
                "login only: NOINHERIT, no super/bypassrls/createrole/createdb/replication",
                "PASS" if ok else "FAIL",
                "nothing extra" if not on else "has: " + ", ".join(on),
            )
        )

    memberships = await conn.fetch(
        "select r.rolname, m.inherit_option, m.set_option from pg_auth_members m"
        " join pg_roles r on r.oid = m.roleid where m.member = current_user::regrole"
        " order by 1"
    )
    found = [(m["rolname"], m["inherit_option"], m["set_option"]) for m in memberships]
    results.append(
        Result(
            "member of authenticated only (SET, not INHERIT)",
            "PASS" if found == [("authenticated", False, True)] else "FAIL",
            ", ".join(f"{n}(inherit={i}, set={s})" for n, i, s in found) or "no memberships",
        )
    )

    try:
        await conn.fetchval("select count(*) from public.organizations")
        results.append(Result("no access of its own", "FAIL", "po_api read organizations directly"))
    except asyncpg.InsufficientPrivilegeError:
        results.append(
            Result("no access of its own", "PASS", "permission denied without a role switch")
        )

    async with conn.transaction():
        await conn.execute("select set_config('role', 'authenticated', true)")
        visible = await conn.fetchval("select count(*) from public.organizations")
    results.append(
        Result(
            "switch to authenticated (RLS applies)",
            "PASS" if visible == 0 else "FAIL",
            f"{visible} organizations visible without JWT claims",
        )
    )

    statement = await conn.fetchval("show statement_timeout")
    idle = await conn.fetchval("show idle_in_transaction_session_timeout")
    results.append(
        Result(
            "role timeouts",
            "PASS" if (statement, idle) == ("8s", "15s") else "FAIL",
            f"statement_timeout={statement} idle_in_transaction={idle}",
        )
    )

    temp = await conn.fetchval(
        "select has_database_privilege(current_user, current_database(), 'TEMP')"
    )
    results.append(Result("TEMP privilege (A4)", "INFO", f"can create temp tables: {temp}"))
    return results


async def run_checks(target: Target, connect: Connect = _connect) -> list[Result]:
    try:
        conn = await connect(target.url, target.ssl_context)
    except Exception as error:  # reported by type only, never with details
        return [_error("login", error)]

    tls = "TLS off (local)" if target.is_local else "TLS on"
    results = [Result("login", "PASS", f"connected to {target.host} as {target.user}, {tls}")]
    try:
        results += await _role_checks(conn)
    except asyncpg.PostgresError as error:
        results.append(_error("role checks stopped", error))
    finally:
        await conn.close()

    if target.is_local:
        results.append(Result("local password rejected", "SKIP", "local target uses that password"))
        return results
    try:
        other = await connect(with_password(target.url, LOCAL_PASSWORD), target.ssl_context)
    except Exception as error:  # only an auth refusal proves the password is rejected
        if is_auth_failure(error):
            results.append(Result("local password rejected", "PASS", "login refused (auth error)"))
        else:
            results.append(_error("local password rejected (inconclusive)", error))
    else:
        await other.close()
        results.append(
            Result("local password rejected", "FAIL", "the local well-known password works here")
        )
    return results


def main(argv: list[str] | None = None, prompt: Callable[[str], str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.check_api_role")
    parser.add_argument(
        "--ca-file", help="Supabase CA certificate (Dashboard → Database → SSL) for verify-full"
    )
    args = parser.parse_args(argv)
    # Hidden prompt only: no env var or argument, so the password never lands in
    # shell history (owner rule; sec L2, 0.5b).
    ask = prompt if prompt is not None else (getpass.getpass if sys.stdin.isatty() else None)
    try:
        if ask is None:
            raise ToolError(
                "Run this in an interactive terminal: it asks for the connection string."
            )
        target = parse_target(ask("po_api connection string (input hidden): "), args.ca_file)
        results = asyncio.run(run_checks(target))
    except ToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    width = max(len(r.name) for r in results)
    for result in results:
        print(f"{result.status:<4}  {result.name:<{width}}  {result.detail}")
    if not target.is_local and not args.ca_file:
        print("\nnote: TLS was encrypted, but the certificate wasn't verified (use --ca-file).")
    failed = [r for r in results if r.status == "FAIL"]
    print(f"\n{'FAILED' if failed else 'OK'}: {len(failed)} failing check(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
