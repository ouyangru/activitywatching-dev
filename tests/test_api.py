from datetime import datetime, timedelta, timezone
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
    recorded = [segment for segment in timeline if segment["category"] == "学习"]
    assert len(recorded) == 1
    assert recorded[0]["behavior"] == "编程"
    assert recorded[0]["duration_seconds"] == 20


def test_timeline_marks_gaps_without_device_records(client):
    client.post("/api/v1/events/batch", json={"events": [event(1, "2026-09-05T00:20:00Z")]})

    timeline = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
    missing = [segment for segment in timeline if segment["category"] == "无设备记录"]

    assert len(missing) == 2
    assert missing[0]["start_time"] == "2026-09-04T16:00:00.000Z"
    assert missing[0]["end_time"] == "2026-09-05T00:20:00.000Z"
    assert missing[0]["id"] is None
    assert missing[0]["platform"] == "none"


def test_short_raw_collection_gap_is_not_marked_as_no_device(client):
    events = [
        event(1, "2026-09-05T00:20:00Z", duration_ms=10_000),
        event(2, "2026-09-05T00:21:00Z", duration_ms=10_000),
    ]
    client.post("/api/v1/events/batch", json={"events": events})

    timeline = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]

    assert all(segment["category"] != "无设备记录" or segment["duration_seconds"] >= 300 for segment in timeline)


def test_manual_correction_updates_summary_and_survives_rebuild(client):
    client.post("/api/v1/events/batch", json={"events": [event(1, "2026-09-05T00:20:00Z")]})
    segment = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
        if item["id"] is not None
    )

    response = client.patch(f"/api/v1/segments/{segment['id']}", json={"category": "工作"})
    assert response.status_code == 200
    assert response.json()["segment"]["manual_override"] is True

    client.post("/api/v1/events/batch", json={"events": [event(2, "2026-09-05T00:20:10Z")]})
    rebuilt = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
        if item["id"] is not None
    )
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


def test_current_status_returns_latest_activity(client):
    start = datetime.now(timezone.utc) - timedelta(seconds=20)
    payload = event(1, start.isoformat(), duration_ms=10_000)
    client.post("/api/v1/events/batch", json={"events": [payload]})

    response = client.get("/api/v1/status/current")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    status = response.json()
    assert status["is_live"] is True
    assert status["observed_seconds_ago"] >= 0
    assert status["current"]["device_id"] == "test-pc"
    assert status["current"]["behavior"] == "编程"


def test_mobile_token_link_sets_auth_cookie(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "mobile-auth.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="mobile-secret",
    )
    with TestClient(app) as auth_client:
        page = auth_client.get("/mobile?token=mobile-secret")
        assert page.status_code == 200
        assert "当前状态" in page.text
        assert auth_client.get("/api/v1/status/current").status_code == 200


def test_android_event_is_classified_and_exposed_as_device(client):
    android = event(
        1,
        "2026-09-05T00:20:00Z",
        process="com.tencent.mm",
        title="微信",
        duration_ms=120_000,
        platform="android",
        device_id="android-phone-01",
    )

    response = client.post("/api/v1/events/batch", json={"events": [android]})
    assert response.status_code == 200

    timeline = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05&device_id=android-phone-01").json()["segments"]
        if item["category"] == "工作"
    )
    assert timeline["platform"] == "android"
    assert timeline["behavior"] == "沟通"
    assert timeline["category"] == "工作"
    assert timeline["description"] == "使用 微信 沟通"

    devices = client.get("/api/v1/devices").json()["devices"]
    assert devices == [
        {
            "device_id": "android-phone-01",
            "platform": "android",
            "last_seen": "2026-09-05T00:20:00.000Z",
            "window_count": 1,
        }
    ]


def test_android_screen_off_is_idle_even_when_shorter_than_idle_threshold(client):
    locked = event(
        1,
        "2026-09-05T00:20:00Z",
        process="__screen_off__",
        title="手机屏幕关闭",
        duration_ms=30_000,
        idle_ms=30_000,
        platform="android",
        device_id="android-phone-01",
    )
    client.post("/api/v1/events/batch", json={"events": [locked]})

    segment = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05&device_id=android-phone-01").json()["segments"]
        if item["category"] == "空闲"
    )
    assert segment["category"] == "空闲"
    assert segment["behavior"] == "手机锁屏"
