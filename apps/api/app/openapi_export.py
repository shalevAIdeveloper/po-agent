"""Writes the API's OpenAPI schema for the web client's generated types (ADR 0006).

Usage (from apps/api): uv run python -m app.openapi_export openapi.json
"""

import json
import sys
from pathlib import Path
from typing import Any

from app.config import AppEnv, Settings
from app.main import create_app


def openapi_schema() -> dict[str, Any]:
    # Explicit values override the environment (and no .env is read): the schema must
    # not depend on where it is generated, and must work even with APP_ENV=prod set.
    settings = Settings(
        _env_file=None,
        app_env=AppEnv.LOCAL,
        log_level="INFO",
        database_url=None,
        supabase_url=None,
        web_origin=None,
    )
    return create_app(settings).openapi()


def render() -> str:
    return json.dumps(openapi_schema(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m app.openapi_export <output.json>")
    Path(argv[1]).write_text(render(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv)
