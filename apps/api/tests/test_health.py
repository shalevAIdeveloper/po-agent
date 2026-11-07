from fastapi.testclient import TestClient

from app import __version__


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "env": "local", "version": __version__}


def test_health_sets_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["x-request-id"]) == 32


def test_health_echoes_well_formed_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"x-request-id": "abc-123"})

    assert response.headers["x-request-id"] == "abc-123"


def test_health_replaces_malformed_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"x-request-id": "bad id\nforged=1"})

    assert response.headers["x-request-id"] != "bad id\nforged=1"
    assert len(response.headers["x-request-id"]) == 32
