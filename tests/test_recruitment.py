from datetime import datetime
from zoneinfo import ZoneInfo

from backend.app.recruitment import extract_recruitment_item, init_recruitment_db


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


def test_recruitment_tables_initialize(tmp_path):
    db_path = tmp_path / "activitywatch.db"
    init_recruitment_db(db_path)
    assert db_path.exists()
