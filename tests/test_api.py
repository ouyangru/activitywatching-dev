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
            "collector_version": None,
            "is_online": False,
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


def test_insights_returns_rankings_and_focus(client):
    events = [
        event(1, "2026-09-05T01:00:00Z"),
        event(2, "2026-09-05T01:00:10Z"),
        event(3, "2026-09-05T01:00:20Z"),
    ]
    client.post("/api/v1/events/batch", json={"events": events})

    response = client.get("/api/v1/insights/today?day=2026-09-05")
    assert response.status_code == 200
    insights = response.json()

    top_app = insights["apps"][0]
    assert top_app["process"] == "Code.exe"
    assert top_app["seconds"] == 30
    assert top_app["category"] == "学习"
    assert insights["behaviors"][0]["behavior"] == "编程"
    assert insights["focus"]["longest_seconds"] == 30
    assert insights["focus"]["sessions"] == 1


def test_insights_focus_merges_small_gaps(client):
    events = [
        event(1, "2026-09-05T01:00:00Z"),
        event(2, "2026-09-05T01:00:40Z"),  # 30 秒间隔 < 60 秒合并阈值
    ]
    client.post("/api/v1/events/batch", json={"events": events})

    insights = client.get("/api/v1/insights/today?day=2026-09-05").json()
    # 专注时段按时间跨度计算：01:00:00-01:00:50（含 30 秒小间隙）
    assert insights["focus"]["longest_seconds"] == 50


def test_summary_dedupes_cross_device_overlap(client):
    """跨设备同时使用时按时间区间并集统计，不重复计算。"""
    events = [
        event(1, "2026-09-05T01:00:00Z", device_id="pc-01"),
        event(2, "2026-09-05T01:00:05Z", device_id="pc-02"),  # 与 pc-01 重叠 5 秒
    ]
    client.post("/api/v1/events/batch", json={"events": events})

    summary = client.get("/api/v1/summary/today?day=2026-09-05").json()
    study = next(item for item in summary["categories"] if item["category"] == "学习")
    assert study["seconds"] == 15  # 并集 01:00:00-01:00:15，而非 10+10

    report = client.get("/api/v1/daily/report?day=2026-09-05").json()
    assert report["total_seconds"] == summary["total_seconds"]


def test_purpose_field_split_from_category(client):
    """behavior/purpose 两层语义：purpose 默认与 category 一致但独立存储。"""
    client.post("/api/v1/events/batch", json={"events": [event(1, "2026-09-05T00:20:00Z")]})

    segment = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
        if item["id"] is not None
    )
    assert segment["purpose"] == "学习"

    # 人工可单独修正 purpose 而不影响 category
    response = client.patch(f"/api/v1/segments/{segment['id']}", json={"purpose": "工作"})
    assert response.status_code == 200
    updated = response.json()["segment"]
    assert updated["category"] == "学习"
    assert updated["purpose"] == "工作"

    purpose_summary = client.get("/api/v1/summary/today?day=2026-09-05&dimension=purpose").json()
    work = next(item for item in purpose_summary["categories"] if item["category"] == "工作")
    assert work["seconds"] == 10


def test_combined_timeline_cross_device(client):
    """跨设备合并接口：重叠时间主活动唯一，总时长不翻倍。"""
    pc = [
        event(1, "2026-09-05T02:00:00Z", device_id="pc-01"),
        event(2, "2026-09-05T02:00:10Z", device_id="pc-01"),
        event(3, "2026-09-05T02:00:20Z", device_id="pc-01"),
    ]
    phone = [
        event(
            1,
            "2026-09-05T02:00:00Z",
            process="tv.danmaku.bili",
            title="哔哩哔哩",
            duration_ms=30_000,
            platform="android",
            device_id="phone-01",
        )
    ]
    client.post("/api/v1/events/batch", json={"events": pc + phone})

    response = client.get("/api/v1/timeline/combined?day=2026-09-05")
    assert response.status_code == 200
    segments = response.json()["segments"]

    overlapping = [item for item in segments if item["overlap_seconds"] > 0]
    assert len(overlapping) == 1
    assert overlapping[0]["main_device_id"] == "pc-01"
    assert overlapping[0]["main_platform"] == "windows"
    assert overlapping[0]["category"] == "学习"
    assert any(item["device_id"] == "phone-01" for item in overlapping[0]["secondary"])
    # 主活动总时长 = 电脑 30 秒（手机 30 秒被折叠为次要活动，不翻倍）
    total = sum(item["duration_seconds"] for item in segments if item["category"] != "无设备记录")
    assert total == 30


def test_heartbeat_reports_and_devices_show_online(client):
    response = client.post(
        "/api/v1/heartbeat",
        json={"device_id": "pc-01", "platform": "windows", "collector_version": "0.4.0"},
    )
    assert response.status_code == 200
    assert response.json()["device_id"] == "pc-01"

    client.post("/api/v1/events/batch", json={"events": [event(1, "2026-09-05T00:20:00Z")]})

    devices = client.get("/api/v1/devices").json()["devices"]
    assert devices[0]["device_id"] == "test-pc"
    assert devices[0]["is_online"] is False  # 有数据但从未心跳

    client.post("/api/v1/heartbeat", json={"device_id": "test-pc", "platform": "windows"})
    devices = client.get("/api/v1/devices").json()["devices"]
    assert devices[0]["is_online"] is True
    assert devices[0]["collector_version"] == ""


def test_heartbeat_requires_auth(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "hb-auth.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="hb-token",
    )
    with TestClient(app) as auth_client:
        assert auth_client.post("/api/v1/heartbeat", json={"device_id": "x"}).status_code == 401
        ok = auth_client.post(
            "/api/v1/heartbeat",
            json={"device_id": "x"},
            headers={"Authorization": "Bearer hb-token"},
        )
        assert ok.status_code == 200


def test_daily_report_aggregates_summary_insights_and_combined(client):
    client.post("/api/v1/events/batch", json={"events": [event(1, "2026-09-05T00:20:00Z")]})

    response = client.get("/api/v1/daily/report?day=2026-09-05")
    assert response.status_code == 200
    report = response.json()

    assert report["date"] == "2026-09-05"
    assert report["total_seconds"] > 0
    assert any(item["category"] == "学习" for item in report["summary"])
    assert report["insights"]["apps"]
    assert any(item["category"] != "无设备记录" for item in report["combined_segments"])


def test_daily_page_is_served(client):
    response = client.get("/daily")
    assert response.status_code == 200
    assert "日报" in response.text
    assert "/static/daily.js" in response.text


def test_segment_correction_requires_field(client):
    response = client.patch("/api/v1/segments/1", json={})
    assert response.status_code == 422
