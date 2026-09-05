import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from .conftest import event


def test_production_refuses_missing_token(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITYWATCH_ENV", "production")
    with pytest.raises(RuntimeError, match="Production requires"):
        create_app(db_path=tmp_path / "db.sqlite", api_token="")


def test_cloud_login_upload_and_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITYWATCH_ENV", "production")
    token = "test-secret-" + "a" * 40
    db_path = tmp_path / "cloud.sqlite"
    app = create_app(db_path=db_path, api_token=token)
    with TestClient(app, base_url="https://cloud.example") as client:
        assert client.get("/api/v1/status/current").status_code == 401
        assert client.get("/mobile?token=" + token, follow_redirects=False).headers["location"] == "/login"
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.post("/api/v1/auth/login", json={"token": "错误"}).status_code == 401
        response = client.post("/api/v1/auth/login", json={"token": token})
        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert client.get("/mobile").status_code == 200
        payload = {"events": [event(1, "2026-09-05T00:20:00Z")]}
        assert client.post("/api/v1/events/batch", json=payload).json()["accepted"] == 1
    restarted = create_app(db_path=db_path, api_token=token)
    with TestClient(restarted, base_url="https://cloud.example") as client:
        headers = {"Authorization": "Bearer " + token}
        assert client.post("/api/v1/events/batch", json=payload, headers=headers).json()["duplicates"] == 1
        response = client.get("/api/v1/status/current", headers=headers)
        assert response.json()["current"]["device_id"] == "test-pc"
        assert response.headers["cache-control"] == "no-store"
