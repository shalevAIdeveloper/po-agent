"""Set the po_api password on a hosted project without sending it in plain text (A2, 0.5b).

Runs on the operator's machine only; never deployed. It asks (hidden input) for:
  1. the `postgres` session-pooler connection string of the project (Dashboard → Connect);
  2. the new po_api password, twice.
It computes the SCRAM-SHA-256 verifier locally and runs
`ALTER ROLE po_api PASSWORD '<verifier>'`, so neither the SQL editor's saved snippets
nor database logs ever see the password itself (sec M2, 0.5b). Postgres stores a
pre-hashed SCRAM verifier as is.

Usage (from apps/api, PowerShell):
    uv run python -m tools.set_api_role_password [--ca-file prod-ca-2021.crt]
Then check: uv run python -m tools.check_api_role
Runbook: docs/runbooks/supabase-project.md
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import re
import secrets
import ssl
import sys
from collections.abc import Callable
from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg

from tools.check_api_role import LOCAL_HOSTS, LOCAL_PASSWORD, WEAK_SSLMODES, ToolError, tls_context

SCRAM_ITERATIONS = 4096  # Postgres' default (scram_iterations)
MIN_LENGTH = 24
MAX_LENGTH = 72
VERIFIER = re.compile(r"^SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")
CONNECT_TIMEOUT_SECONDS = 15.0


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def scram_verifier(
    password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores (RFC 5802 / RFC 7677).

    Only printable ASCII passwords are accepted (see validate_password), for which
    SASLprep is the identity, so this matches what the server would compute.
    """
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"


def validate_password(password: str, confirm: str) -> None:
    if password != confirm:
        raise ToolError("The two passwords don't match.")
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise ToolError(f"Use a generated password of {MIN_LENGTH}-{MAX_LENGTH} characters.")
    # Printable ASCII only (no spaces): SASLprep is then a no-op, and the password is
    # safe in a connection string once percent-encoded.
    if not all(33 <= ord(ch) <= 126 for ch in password):
        raise ToolError(
            "Use printable ASCII characters only (letters, digits, symbols; no spaces)."
        )
    if password == LOCAL_PASSWORD:
        raise ToolError("That is the public local password; generate a new one.")


def parse_admin_url(url: str, ca_file: str | None) -> tuple[str, ssl.SSLContext | None]:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise ToolError("Expected a postgresql:// connection string.")
    if unquote(parts.username or "").split(".", 1)[0] != "postgres":
        raise ToolError("Use the project's `postgres` connection string (session pooler).")
    if not parts.password:
        raise ToolError("The connection string has no password.")
    sslmode = (parse_qs(parts.query).get("sslmode") or [""])[0].lower()
    if sslmode in WEAK_SSLMODES:
        raise ToolError(f"sslmode={sslmode} would allow an unencrypted connection; remove it.")
    context = None if parts.hostname in LOCAL_HOSTS else tls_context(ca_file)
    return url, context


async def apply_verifier(admin_url: str, ssl_context: ssl.SSLContext | None, verifier: str) -> None:
    if not VERIFIER.fullmatch(verifier):
        raise ToolError("Internal error: unexpected verifier format.")
    try:
        conn = await asyncpg.connect(
            admin_url,
            timeout=CONNECT_TIMEOUT_SECONDS,
            statement_cache_size=0,
            ssl=ssl_context if ssl_context is not None else False,
        )
    except Exception as error:  # reported by type only, never with details
        raise ToolError(f"Could not connect ({type(error).__name__}).") from None
    try:
        exists = await conn.fetchval("select 1 from pg_roles where rolname = 'po_api'")
        if not exists:
            raise ToolError("Role po_api doesn't exist yet: apply the migrations first.")
        # ALTER ROLE takes no bind parameters; the verifier is checked against VERIFIER
        # above (base64, digits and `$:` only), so it can't break out of the literal.
        await conn.execute(f"alter role po_api password '{verifier}'")
    except asyncpg.PostgresError as error:
        raise ToolError(f"ALTER ROLE failed ({type(error).__name__}).") from None
    finally:
        await conn.close()


def main(argv: list[str] | None = None, prompt: Callable[[str], str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.set_api_role_password")
    parser.add_argument("--ca-file", help="Supabase CA certificate for verify-full TLS")
    args = parser.parse_args(argv)
    ask = prompt if prompt is not None else (getpass.getpass if sys.stdin.isatty() else None)
    try:
        if ask is None:
            raise ToolError("Run this in an interactive terminal: it asks for the secrets.")
        admin_url, context = parse_admin_url(
            ask("postgres connection string (input hidden): "), args.ca_file
        )
        password = ask("new po_api password (input hidden): ")
        validate_password(password, ask("repeat the po_api password: "))
        asyncio.run(apply_verifier(admin_url, context, scram_verifier(password)))
    except ToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print("po_api password set (only a SCRAM verifier was sent).")
    print("Next: uv run python -m tools.check_api_role")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
