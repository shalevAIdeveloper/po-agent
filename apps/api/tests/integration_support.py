"""Environment for integration tests against a local Supabase stack (ADR 0005)."""

import os

import pytest


def require_env(name: str) -> str:
    """Skips locally when unset, but fails in CI so integration tests never pass by skipping."""
    value = os.environ.get(name)
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail(f"{name} must be set when CI is set")
    pytest.skip(f"{name} not set (start local Supabase first)")
