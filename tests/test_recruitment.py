import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.app.recruitment import (
    _summarize_recruitment_items,
    extract_recruitment_item,
    init_recruitment_db,
)


def test_extract_absolute_deadline(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    item = extract_recruitment_item(
        "【腾讯】2027届校园招聘在线测评邀请",
        "Tencent Campus <campus@example.com>",
        "请于2026年9月15日 23:59前完成测评。https://exam.example.com/start",
        datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert item is not None
    assert item["item_type"] == "assessment"
    assert item["mode"] == "deadline"
    assert item["status"] == "pending"
    assert item["deadline_at"].startswith("2026-09-15T23:59")
    assert item["action_url"] == "https://exam.example.com/start"


def test_extract_fixed_interview_time(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    item = extract_recruitment_item(
        "字节跳动面试通知",
        "recruit@example.com",
        "面试时间：9月16日 14:00，请提前进入会议。",
        datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert item is not None
    assert item["item_type"] == "interview"
    assert item["mode"] == "fixed_time"
    assert item["start_at"].startswith("2026-09-16T14:00")
    assert item["end_at"] is None


def test_extract_relative_deadline(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    received = datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    item = extract_recruitment_item(
        "小米校园招聘在线测评",
        "campus@example.com",
        "请在收到邮件后3天内完成测评。",
        received,
    )
    assert item is not None
    assert item["mode"] == "deadline"
    assert item["deadline_precision"] == "derived"
    assert item["deadline_at"].startswith("2026-09-14T10:00")


def test_unknown_time_goes_to_confirmation(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    item = extract_recruitment_item(
        "OPPO秋招笔试通知",
        "campus@example.com",
        "恭喜进入笔试环节，请登录招聘系统查看安排。",
        datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert item is not None
    assert item["mode"] == "uncertain"
    assert item["status"] == "uncertain"


def test_extract_pipeline_metadata_from_shopee_mail(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    item = extract_recruitment_item(
        "Shopee-嵌入式软件开发-2027届校招笔试通知",
        "Shopee Campus <campus@example.com>",
        "职位：嵌入式软件开发\n工作地点：深圳\n笔试时间：2026年9月20日 19:00\nhttps://careers.example.com/shopee",
        datetime(2026, 9, 11, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert item is not None
    assert item["company"] == "Shopee"
    assert item["position"] == "嵌入式软件开发"
    assert item["recruitment_type"] == "校招"
    assert item["location"] == "深圳"
    assert item["item_type"] == "written_test"
    assert item["start_at"].startswith("2026-09-20T19:00")


def test_summary_three_days_means_next_three_days_only():
    tz = ZoneInfo("Asia/Shanghai")
    items = [
        {
            "id": 1,
            "status": "pending",
            "item_type": "assessment",
            "mode": "deadline",
            "deadline_at": "2026-09-11T23:59:00+08:00",
            "start_at": None,
        },
        {
            "id": 2,
            "status": "pending",
            "item_type": "written_test",
            "mode": "deadline",
            "deadline_at": "2026-09-12",
            "start_at": None,
        },
        {
            "id": 3,
            "status": "pending",
            "item_type": "interview",
            "mode": "fixed_time",
            "deadline_at": None,
            "start_at": "2026-09-14T14:00:00+08:00",
        },
        {
            "id": 4,
            "status": "pending",
            "item_type": "assessment",
            "mode": "deadline",
            "deadline_at": "2026-09-15",
            "start_at": None,
        },
        {
            "id": 5,
            "status": "uncertain",
            "item_type": "assessment",
            "mode": "uncertain",
            "deadline_at": "2026-09-12",
            "start_at": None,
        },
        {
            "id": 6,
            "status": "pending",
            "item_type": "other",
            "mode": "deadline",
            "deadline_at": "2026-09-13",
            "start_at": None,
        },
        {
            "id": 7,
            "status": "done",
            "item_type": "interview",
            "mode": "fixed_time",
            "deadline_at": None,
            "start_at": "2026-09-13T10:00:00+08:00",
        },
    ]

    summary = _summarize_recruitment_items(items, date(2026, 9, 11), tz)

    assert summary["today"] == 1
    assert summary["three_days"] == 2
    assert summary["uncertain"] == 1


def test_recruitment_tables_initialize_with_pipeline_columns(tmp_path):
    db_path = tmp_path / "activitywatch.db"
    init_recruitment_db(db_path)
    assert db_path.exists()
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recruitment_items)")}
    assert {"position", "recruitment_type", "location", "priority"}.issubset(columns)
