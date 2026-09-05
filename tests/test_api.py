from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app

from .conftest import event


def test_batch_is_idempotent_and_builds_timeline(client):
    payload = {
        "events": [
            event(1, "2026-09-05T00:20:00Z"),
            event(2, "2026-09-05T00:20:10Z"),
        ]
    }
    first = client.post("/api/v1/events/batch", json=payload)
    second = client.post("/api/v1/events/batch", json=payload)

    assert first.status_code == 200
    assert first.json()["accepted"] == 2
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 2

    timeline = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
    assert len(timeline) == 1
    assert timeline[0]["category"] == "学习"
    assert timeline[0]["behavior"] == "编程"
    assert timeline[0]["duration_seconds"] == 20


def test_manual_correction_updates_summary_and_survives_rebuild(client):
    client.post("/api/v1/events/batch", json={"events": [event(1, "2026-09-05T00:20:00Z")]})
    segment = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"][0]

    response = client.patch(f"/api/v1/segments/{segment['id']}", json={"category": "工作"})
    assert response.status_code == 200
    assert response.json()["segment"]["manual_override"] is True

    client.post("/api/v1/events/batch", json={"events": [event(2, "2026-09-05T00:20:10Z")]})
    rebuilt = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"][0]
    assert rebuilt["category"] == "工作"
    assert rebuilt["manual_override"] is True

    summary = client.get("/api/v1/summary/today?day=2026-09-05").json()
    work = next(item for item in summary["categories"] if item["category"] == "工作")
    assert work["seconds"] == 20


def test_rejects_naive_timestamp(client):
    response = client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T00:20:00")]},
    )
    assert response.status_code == 422


def test_api_token_protects_data_endpoints(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "auth.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="test-token",
    )
    with TestClient(app) as auth_client:
        assert auth_client.get("/api/v1/timeline/today").status_code == 401
        assert auth_client.get("/api/v1/timeline/today", headers={"Authorization": "Bearer test-token"}).status_code == 200
        assert auth_client.get("/api/v1/health").status_code == 200

