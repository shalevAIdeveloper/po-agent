import pytest
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings
from app.main import create_app
from tests.ca import make_ca_pem

CLOUD = {
    "database_url": "postgresql://u:p@db.example:5432/postgres",
    "database_ca_cert": make_ca_pem(),
    "supabase_url": "https://example.supabase.co",
    "web_origin": "https://app.example.com",
}


def test_health_returns_only_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    # L8: no env or version on an unauthenticated endpoint.
    assert response.json() == {"status": "ok"}


def test_health_sets_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["x-request-id"]) == 32


def test_caller_cannot_choose_the_request_id(client: TestClient) -> None:
    # L7: a well-formed id from the caller is not adopted as ours.
    response = client.get("/health", headers={"x-request-id": "abc-123"})

    assert response.headers["x-request-id"] != "abc-123"
    assert len(response.headers["x-request-id"]) == 32


def test_malformed_request_id_is_ignored(client: TestClient) -> None:
    response = client.get("/health", headers={"x-request-id": "bad id\nforged=1"})

    assert response.headers["x-request-id"] != "bad id\nforged=1"
    assert len(response.headers["x-request-id"]) == 32


@pytest.mark.parametrize("env", [AppEnv.DEV, AppEnv.PROD])
@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc"])
def test_docs_and_schema_are_hidden_outside_local(env: AppEnv, path: str) -> None:
    # L5: deployed dev must not publish the API surface either.
    settings = Settings(_env_file=None, app_env=env, **CLOUD)  # type: ignore[arg-type]

    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_and_schema_are_served_locally(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_trailing_slash_is_not_redirected(client: TestClient) -> None:
    # Behind Railway's proxy a redirect would point at http:// (0.5c `sec` L4).
    response = client.get("/health/", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers
