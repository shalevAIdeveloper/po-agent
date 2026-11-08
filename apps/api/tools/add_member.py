"""Add an invited user to an existing organization (stop-gap until F7).

Runs on the operator's machine only; it is not part of the API or the worker and
is never deployed. Calls `public.admin_add_member` (service-role-only RPC), which
adds the membership and writes `private.admin_audit_log` in one transaction.
Runbook: docs/runbooks/invite-pilot-user.md.

Usage (from apps/api, PowerShell):
    $env:ADMIN_SUPABASE_URL = "http://127.0.0.1:54321"
    uv run python -m tools.add_member --email buyer@acme.com --org acme `
        --role reviewer --actor you@company.com --reason "Pilot: second buyer at Acme"
    # Prompts for the secret key without echoing it. Don't put the key in an
    # $env: assignment: PowerShell 5.1 saves typed lines to its history file.

Any project that is not on localhost also needs `--confirm-host <project host>`,
so a command copied from local can't silently run against prod.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

ROLES = ("admin", "reviewer")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
TIMEOUT_SECONDS = 10.0

# Fixed messages for the RPC's error codes; anything else is reported by code only.
ERROR_MESSAGES = {
    "P0002": "Not found: {detail}",
    "23505": "The user is already a member of this organization.",
    "21000": "Several users match that email; resolve it in the dashboard first.",
    "22023": "Invalid input: {detail}",
    "42501": "Permission denied: use the project's secret (service-role) key.",
    "PGRST202": "admin_add_member is not deployed on this project (migrations not applied?).",
}


class ToolError(Exception):
    """A failure to report to the operator (exit code 1)."""


@dataclass(frozen=True, slots=True)
class Target:
    url: str
    secret_key: str


@dataclass(frozen=True, slots=True)
class AddedMember:
    org_id: str
    org_name: str
    user_id: str
    role: str


def resolve_target(
    env: dict[str, str],
    confirm_host: str | None,
    prompt_secret: Callable[[str], str] | None = None,
) -> Target:
    url = env.get("ADMIN_SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise ToolError("Set ADMIN_SUPABASE_URL.")
    key = env.get("ADMIN_SUPABASE_SECRET_KEY", "").strip()
    if not key and prompt_secret is not None:
        # Asked after the URL so the operator sees which project the key is for.
        key = prompt_secret(f"Secret key for {url} (input hidden): ").strip()
    if not key:
        raise ToolError("No secret key: run interactively, or set ADMIN_SUPABASE_SECRET_KEY.")
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        raise ToolError("ADMIN_SUPABASE_URL must be an http(s) origin.")
    if host not in LOCAL_HOSTS:
        if parts.scheme != "https":
            raise ToolError("Non-local projects must use https.")
        if confirm_host != host:
            raise ToolError(
                f"Target is {host}. Re-run with --confirm-host {host} to confirm the project."
            )
    if not key.startswith("sb_secret_"):
        # Publishable keys can't call the RPC; legacy JWT keys are not used in this project.
        raise ToolError("ADMIN_SUPABASE_SECRET_KEY must be a secret key (sb_secret_...).")
    return Target(url=url, secret_key=key)


def _error_from(response: httpx.Response) -> ToolError:
    code = ""
    detail = ""
    try:
        body: Any = response.json()
        if isinstance(body, dict):
            code = str(body.get("code") or "")
            # Our RPC's messages are fixed strings (no data); keep them short anyway.
            detail = str(body.get("message") or "")[:200]
    except ValueError:
        pass
    template = ERROR_MESSAGES.get(code)
    if template is None:
        return ToolError(f"Request failed (HTTP {response.status_code}, code {code or 'none'}).")
    return ToolError(template.format(detail=detail))


def add_member(
    target: Target,
    *,
    email: str,
    org_slug: str,
    role: str,
    actor: str,
    reason: str,
    http: httpx.Client | None = None,
) -> AddedMember:
    if role not in ROLES:
        raise ToolError(f"--role must be one of: {', '.join(ROLES)}.")
    client = http or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = client.post(
            f"{target.url}/rest/v1/rpc/admin_add_member",
            headers={
                "apikey": target.secret_key,
                "authorization": f"Bearer {target.secret_key}",
                "content-type": "application/json",
                "accept": "application/json",
            },
            json={
                "p_email": email,
                "p_org_slug": org_slug,
                "p_role": role,
                "p_actor": actor,
                "p_reason": reason,
            },
        )
    except httpx.HTTPError as error:
        raise ToolError(f"Could not reach {target.url} ({type(error).__name__}).") from None
    finally:
        if http is None:
            client.close()

    if response.status_code != 200:
        raise _error_from(response)
    rows = response.json()
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ToolError("Unexpected response from admin_add_member.")
    row = rows[0]
    return AddedMember(
        org_id=str(row["org_id"]),
        org_name=str(row["org_name"]),
        user_id=str(row["user_id"]),
        role=str(row["role"]),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.add_member",
        description="Add an invited user to an existing organization (audited).",
    )
    parser.add_argument("--email", required=True, help="the invited user's email")
    parser.add_argument("--org", required=True, dest="org_slug", help="organization slug")
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--actor", required=True, help="who is running this (your email)")
    parser.add_argument("--reason", required=True, help="why (stored in the audit log)")
    parser.add_argument("--confirm-host", help="required for non-local projects")
    return parser


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prompt = getpass.getpass if env is None and sys.stdin.isatty() else None
        target = resolve_target(dict(os.environ) if env is None else env, args.confirm_host, prompt)
        added = add_member(
            target,
            email=args.email,
            org_slug=args.org_slug,
            role=args.role,
            actor=args.actor,
            reason=args.reason,
        )
    except ToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"Added user {added.user_id} to '{added.org_name}' ({added.org_id}) as {added.role}. "
        "Audit row written."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
