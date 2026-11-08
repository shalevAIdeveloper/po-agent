from fastapi.testclient import TestClient

from app.config import AppEnv, Settings
from app.main import create_app

WEB = "http://localhost:3000"


def test_cors_exposes_retry_after_and_request_id() -> None:
    settings = Settings(_env_file=None, app_env=AppEnv.LOCAL, web_origin=WEB)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health", headers={"Origin": WEB})

    exposed = {
        h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")
    }
    assert {"retry-after", "x-request-id"} <= exposed
    assert response.headers["access-control-allow-origin"] == WEB


def test_cors_ignores_other_origins() -> None:
    settings = Settings(_env_file=None, app_env=AppEnv.LOCAL, web_origin=WEB)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers
