from pathlib import Path

from backend.app.recruitment import _connect
from backend.app.recruitment_calendar import _google_event_body, init_recruitment_calendar_db


def test_calendar_migration_adds_sync_columns(tmp_path: Path):
    db_path = tmp_path / "activitywatch.db"
    init_recruitment_calendar_db(db_path)
    with _connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recruitment_items)").fetchall()}
    assert "calendar_event_id" in columns
    assert "calendar_synced_at" in columns
    assert "calendar_sync_error" in columns


def test_google_event_body_keeps_date_only_deadline_all_day(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    body = _google_event_body(
        {
            "id": 7,
            "company": "腾讯",
            "title": "在线测评",
            "item_type": "assessment",
            "mode": "deadline",
            "status": "pending",
            "deadline_at": "2026-09-15",
            "start_at": None,
            "end_at": None,
            "action_url": "https://example.com/test",
        }
    )
    assert body["summary"] == "腾讯 · 在线测评"
    assert body["start"] == {"date": "2026-09-15"}
    assert body["end"] == {"date": "2026-09-16"}
    assert body["extendedProperties"]["private"]["activitywatchRecruitmentId"] == "7"


def test_google_event_body_does_not_invent_long_duration(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    body = _google_event_body(
        {
            "id": 8,
            "company": "字节跳动",
            "title": "技术面试",
            "item_type": "interview",
            "mode": "fixed_time",
            "status": "pending",
            "start_at": "2026-09-16T14:00:00+08:00",
            "end_at": None,
            "deadline_at": None,
            "action_url": None,
        }
    )
    assert body["start"]["dateTime"].startswith("2026-09-16T14:00:00")
    assert body["end"]["dateTime"].startswith("2026-09-16T14:01:00")
