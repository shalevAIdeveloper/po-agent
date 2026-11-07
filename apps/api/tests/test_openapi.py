from pathlib import Path

import pytest

from app.openapi_export import openapi_schema, render

COMMITTED = Path(__file__).resolve().parents[1] / "openapi.json"


def test_committed_openapi_is_up_to_date() -> None:
    """Regenerate with: uv run python -m app.openapi_export openapi.json"""
    assert COMMITTED.read_text(encoding="utf-8") == render()


def test_export_ignores_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # A prod-like environment with missing settings would fail Settings() validation.
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("SUPABASE_URL", "http://not-https.example")

    assert render() == COMMITTED.read_text(encoding="utf-8")


def test_protected_operations_declare_bearer_auth() -> None:
    schema = openapi_schema()

    assert "HTTPBearer" in schema["components"]["securitySchemes"]
    for path in ("/me", "/orgs", "/orgs/current"):
        for operation in schema["paths"][path].values():
            assert {"HTTPBearer": []} in operation["security"], path
    assert "security" not in schema["paths"]["/health"]["get"]
