import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.recruitment import _connect
from backend.app.recruitment_calendar_bridge import (
    _ensure_bridge_columns,
    sync_feishu_proposal_to_calendar,
)
from backend.app.recruitment_feishu import ensure_recruitment_feishu_tables


def _millis(raw: str) -> int:
    dt = datetime.fromisoformat(raw).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(dt.timestamp() * 1000)


def test_mail_proposal_updates_existing_calendar_item(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    db = tmp_path / "calendar.db"
    _ensure_bridge_columns(db)
    with _connect(db) as connection:
        cursor = connection.execute(
            """
            INSERT INTO recruitment_items(
                message_id, company, title, item_type, mode, status,
                start_at, deadline_at, created_at, updated_at
            ) VALUES ('mail-1', 'Shopee', 'Shopee 笔试通知', 'written_test', 'fixed_time',
                      'pending', '2026-09-13T10:00:00+08:00', NULL, 'now', 'now')
            """
        )
        item_id = int(cursor.lastrowid)
        ensure_recruitment_feishu_tables(connection)
        proposal = connection.execute(
            """
            INSERT INTO recruitment_feishu_proposals(
                recruitment_item_id, source, company, stage, record_id, fields_json,
                status, created_at, updated_at
            ) VALUES (?, 'mail', 'Shopee', '笔试', 'rec-shopee', ?, 'applied', 'now', 'now')
            """,
            (
                item_id,
                json.dumps(
                    {
                        "投递公司": "Shopee",
                        "岗位": "嵌入式软件开发",
                        "笔试日期": _millis("2026-09-14T19:00:00"),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        proposal_id = int(proposal.lastrowid)
        connection.commit()

    assert sync_feishu_proposal_to_calendar(db, proposal_id) == [item_id]
    with _connect(db) as connection:
        row = connection.execute("SELECT * FROM recruitment_items WHERE id=?", (item_id,)).fetchone()
    assert row["feishu_record_id"] == "rec-shopee"
    assert row["feishu_stage_key"] == "written_date"
    assert row["position"] == "嵌入式软件开发"
    assert row["start_at"].startswith("2026-09-14T19:00")


def test_manual_progress_edit_reuses_linked_mail_item(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    db = tmp_path / "calendar.db"
    _ensure_bridge_columns(db)
    with _connect(db) as connection:
        cursor = connection.execute(
            """
            INSERT INTO recruitment_items(
                message_id, company, title, item_type, mode, status, start_at,
                feishu_record_id, feishu_stage_key, created_at, updated_at
            ) VALUES ('mail-2', 'Shopee', 'Shopee 笔试通知', 'written_test', 'fixed_time',
                      'pending', '2026-09-14T19:00:00+08:00', 'rec-shopee', 'written_date', 'now', 'now')
            """
        )
        item_id = int(cursor.lastrowid)
        ensure_recruitment_feishu_tables(connection)
        proposal = connection.execute(
            """
            INSERT INTO recruitment_feishu_proposals(
                source, company, stage, record_id, fields_json,
                status, created_at, updated_at
            ) VALUES ('manual', 'Shopee', NULL, 'rec-shopee', ?, 'applied', 'now', 'now')
            """,
            (json.dumps({"笔试日期": _millis("2026-09-15T20:00:00")}, ensure_ascii=False),),
        )
        proposal_id = int(proposal.lastrowid)
        connection.commit()

    assert sync_feishu_proposal_to_calendar(db, proposal_id) == [item_id]
    with _connect(db) as connection:
        rows = connection.execute(
            "SELECT * FROM recruitment_items WHERE feishu_record_id='rec-shopee' AND feishu_stage_key='written_date'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["start_at"].startswith("2026-09-15T20:00")


def test_manual_progress_stage_creates_calendar_item(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai")
    db = tmp_path / "calendar.db"
    _ensure_bridge_columns(db)
    with _connect(db) as connection:
        ensure_recruitment_feishu_tables(connection)
        proposal = connection.execute(
            """
            INSERT INTO recruitment_feishu_proposals(
                source, company, stage, record_id, fields_json,
                status, created_at, updated_at
            ) VALUES ('manual', '小鹏', NULL, 'rec-xpeng', ?, 'applied', 'now', 'now')
            """,
            (
                json.dumps(
                    {
                        "岗位": "系统软件工程师",
                        "一面日期": _millis("2026-09-20T14:30:00"),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        proposal_id = int(proposal.lastrowid)
        connection.commit()

    ids = sync_feishu_proposal_to_calendar(db, proposal_id)
    assert len(ids) == 1
    with _connect(db) as connection:
        row = connection.execute("SELECT * FROM recruitment_items WHERE id=?", (ids[0],)).fetchone()
    assert row["message_id"] == "feishu-progress:rec-xpeng:first_interview_date"
    assert row["company"] == "小鹏"
    assert row["item_type"] == "interview"
    assert row["position"] == "系统软件工程师"
    assert row["start_at"].startswith("2026-09-20T14:30")
