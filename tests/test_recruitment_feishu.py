import json
import sqlite3

from backend.app.recruitment import init_recruitment_db
from backend.app.recruitment_feishu import (
    _serialize_proposal,
    infer_recruitment_stage,
    match_company_record,
    resolve_field_mapping,
)
from backend.app.recruitment_feishu_queue import (
    _infer_mail_stage,
    backfill_recruitment_feishu_proposals,
)
from backend.app.recruitment_pipeline import PIPELINE_FIELDS, build_mail_pipeline_fields


def live_fields():
    return [
        {"field_name": "投递公司", "type": 1},
        {"field_name": "网申链接", "type": 15},
        {"field_name": "岗位", "type": 1},
        {"field_name": "类型", "type": 3},
        {"field_name": "工作地点", "type": 1},
        {"field_name": "投递状态", "type": 3},
        {"field_name": "优先级", "type": 3},
        {"field_name": "投递日期", "type": 5},
        {"field_name": "测评日期", "type": 5},
        {"field_name": "笔试日期", "type": 5},
        {"field_name": "一面日期", "type": 5},
        {"field_name": "二面日期", "type": 5},
        {"field_name": "三面日期", "type": 5},
        {"field_name": "备注", "type": 1},
    ]


def test_infer_recruitment_stage_prefers_specific_round():
    assert infer_recruitment_stage("腾讯二面邀请") == "2面"
    assert infer_recruitment_stage("字节跳动 HR 面试通知") == "HR面"
    assert infer_recruitment_stage("在线测评邀请") == "测评"
    assert infer_recruitment_stage("招聘流程终止通知") == "流程结束"
    assert infer_recruitment_stage("某公司第4轮面试邀请") == "4面"
    assert _infer_mail_stage("某公司第4轮面试邀请", "") == "4面"


def test_company_match_requires_unique_record():
    records = [
        {"record_id": "rec-a", "fields": {"投递公司": "腾讯"}},
        {"record_id": "rec-b", "fields": {"投递公司": "字节跳动有限公司"}},
    ]
    matched = match_company_record(records, "投递公司", "字节跳动")
    assert matched["status"] == "matched"
    assert matched["record"]["record_id"] == "rec-b"

    ambiguous = match_company_record(
        [
            {"record_id": "rec-1", "fields": {"投递公司": "小米科技"}},
            {"record_id": "rec-2", "fields": {"投递公司": "小米集团"}},
        ],
        "投递公司",
        "小米",
    )
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["record"] is None


def test_field_mapping_falls_back_from_stale_old_env_to_live_columns(monkeypatch):
    monkeypatch.setenv("FEISHU_RECRUITMENT_COMPANY_FIELD", "公司")
    monkeypatch.setenv("FEISHU_RECRUITMENT_STAGE_FIELD", "招聘进度")
    monkeypatch.setenv("FEISHU_RECRUITMENT_LATEST_FIELD", "最新动态")
    mapping = resolve_field_mapping(live_fields())
    assert mapping == PIPELINE_FIELDS


def test_build_mail_pipeline_fields_maps_stage_date():
    fields = build_mail_pipeline_fields(
        {
            "company": "Shopee",
            "action_url": "https://careers.example.com/shopee",
            "position": "嵌入式软件开发",
            "recruitment_type": "校招",
            "location": "深圳",
            "priority": "高",
            "start_at": "2026-09-20T19:00:00+08:00",
            "deadline_at": None,
            "extraction_note": "根据笔试时间识别固定时间",
        },
        "笔试",
        "Shopee 笔试通知",
    )
    assert fields["投递公司"] == "Shopee"
    assert fields["岗位"] == "嵌入式软件开发"
    assert fields["类型"] == "校招"
    assert fields["工作地点"] == "深圳"
    assert fields["投递状态"] == "笔试"
    assert fields["笔试日期"] == "2026-09-20T19:00:00+08:00"
    assert "一面日期" not in fields


def test_backfill_enriches_pending_review_without_writing(tmp_path):
    db_path = tmp_path / "activitywatch.db"
    init_recruitment_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO recruitment_items(
                message_id, company, title, item_type, mode, status, start_at,
                position, recruitment_type, location,
                source_subject, extraction_note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mail-1",
                "腾讯",
                "腾讯二面邀请",
                "interview",
                "fixed_time",
                "pending",
                "2026-09-15T14:00:00+08:00",
                "Linux系统软件工程师",
                "校招",
                "深圳",
                "腾讯二面邀请",
                "面试时间：9月15日 14:00",
                "2026-09-11T12:00:00Z",
                "2026-09-11T12:00:00Z",
            ),
        )
        connection.commit()

    first = backfill_recruitment_feishu_proposals(db_path)
    second = backfill_recruitment_feishu_proposals(db_path)

    assert first["created"] == 1
    assert second["created"] == 0
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT source, company, stage, status, record_id, fields_json FROM recruitment_feishu_proposals"
        ).fetchone()
    assert row[:5] == ("mail", "腾讯", "2面", "pending", None)
    fields = json.loads(row[5])
    assert fields["投递公司"] == "腾讯"
    assert fields["岗位"] == "Linux系统软件工程师"
    assert fields["投递状态"] == "2面"
    assert fields["二面日期"] == "2026-09-15T14:00:00+08:00"


def test_unmatched_mail_proposal_can_create_new_feishu_record(tmp_path):
    db_path = tmp_path / "activitywatch.db"
    init_recruitment_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE recruitment_feishu_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recruitment_item_id INTEGER,
                source TEXT NOT NULL DEFAULT 'mail',
                company TEXT NOT NULL DEFAULT '',
                stage TEXT,
                latest_update TEXT,
                next_at TEXT,
                source_title TEXT,
                record_id TEXT,
                fields_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(recruitment_item_id, source)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO recruitment_feishu_proposals(
                recruitment_item_id, source, company, stage, latest_update, next_at,
                source_title, fields_json, status, created_at, updated_at
            ) VALUES (1, 'mail', 'Shopee', '笔试', 'Shopee 笔试通知',
                      '2026-09-20T19:00:00+08:00', 'Shopee 笔试通知', ?,
                      'pending', '2026-09-11T12:00:00Z', '2026-09-11T12:00:00Z')
            """,
            (json.dumps({"投递公司": "Shopee", "投递状态": "笔试", "笔试日期": "2026-09-20T19:00:00+08:00"}, ensure_ascii=False),),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM recruitment_feishu_proposals").fetchone()

    fields = live_fields()
    mapping = resolve_field_mapping(fields)
    serialized = _serialize_proposal(row, fields=fields, records=[], mapping=mapping)
    assert serialized["match_status"] == "unmatched"
    assert serialized["write_mode"] == "create"
    assert serialized["can_approve"] is True
    assert serialized["proposed_fields"]["投递公司"] == "Shopee"


def test_canonical_types_repair_generic_subjects_and_preserve_review_states(tmp_path):
    from backend.app.recruitment_feishu import queue_recruitment_feishu_proposal

    db_path = tmp_path / "recruitment.db"
    init_recruitment_db(db_path)
    with sqlite3.connect(db_path) as connection:
        for item_id, item_type, status in [
            (1, "written_test", "pending"), (2, "assessment", "uncertain"),
            (3, "written_test", "done"), (4, "assessment", "cancelled"),
        ]:
            connection.execute(
                "INSERT INTO recruitment_items(id,message_id,company,title,item_type,status,deadline_at,created_at,updated_at) "
                "VALUES(?,?, '腾讯','招聘安排',?,?, '2026-09-20', 'now','now')",
                (item_id, str(item_id), item_type, status),
            )
    assert backfill_recruitment_feishu_proposals(db_path)["created"] == 3
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT recruitment_item_id,stage,fields_json FROM recruitment_feishu_proposals ORDER BY recruitment_item_id"
        ).fetchall()
        assert [row[1] for row in rows] == ["笔试", "测评", "笔试"]
        assert json.loads(rows[0][2])["笔试日期"] == "2026-09-20"
        assert json.loads(rows[1][2])["测评日期"] == "2026-09-20"
        connection.execute("UPDATE recruitment_feishu_proposals SET status='rejected' WHERE recruitment_item_id=1")
        connection.execute("UPDATE recruitment_feishu_proposals SET status='applied' WHERE recruitment_item_id=2")
        assert not queue_recruitment_feishu_proposal(
            connection, 4, {"status": "cancelled", "item_type": "assessment"}, "招聘安排"
        )
    assert backfill_recruitment_feishu_proposals(db_path)["created"] == 0
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT status FROM recruitment_feishu_proposals ORDER BY recruitment_item_id"
        ).fetchall() == [("rejected",), ("applied",), ("pending",)]
        assert connection.execute("SELECT status FROM recruitment_items ORDER BY id").fetchall() == [
            ("pending",), ("uncertain",), ("done",), ("cancelled",)
        ]


def test_qq_scan_queues_body_only_written_test_atomically(tmp_path, monkeypatch):
    from email.message import EmailMessage
    from backend.app.recruitment import scan_qq_mail

    mail = EmailMessage()
    mail["Subject"] = "【腾讯】校园招聘安排"
    mail["From"] = "campus@example.com"
    mail["Message-ID"] = "<body-only-test@example.com>"
    mail["Date"] = "Tue, 15 Sep 2026 10:00:00 +0800"
    mail.set_content("请参加在线考试，考试时间：2026年9月20日 19:00。")

    class FakeIMAP:
        def __init__(self, *args): pass
        def login(self, *args): pass
        def select(self, *args, **kwargs): return "OK", []
        def uid(self, command, *args):
            if command == "search": return "OK", [b"1"]
            return "OK", [(b"1", mail.as_bytes())]
        def logout(self): pass

    monkeypatch.setenv("QQ_EMAIL", "test@example.com")
    monkeypatch.setenv("QQ_EMAIL_AUTH_CODE", "test-code")
    monkeypatch.setattr("backend.app.recruitment.imaplib.IMAP4_SSL", FakeIMAP)
    db_path = tmp_path / "recruitment.db"
    assert scan_qq_mail(db_path)["imported"] == 1
    assert scan_qq_mail(db_path)["imported"] == 0
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT r.item_type,p.stage,p.status,p.fields_json FROM recruitment_items r "
            "JOIN recruitment_feishu_proposals p ON p.recruitment_item_id=r.id"
        ).fetchone()
        assert row[:3] == ("written_test", "笔试", "pending")
        assert json.loads(row[3])["笔试日期"].startswith("2026-09-20T19:00")
        assert connection.execute("SELECT COUNT(*) FROM recruitment_feishu_proposals").fetchone()[0] == 1


def test_proposal_read_repairs_history_and_does_not_truncate_pending(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.recruitment_feishu import FeishuBitableClient, build_recruitment_feishu_router

    db_path = tmp_path / "recruitment.db"
    init_recruitment_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            "INSERT INTO recruitment_items(message_id,company,title,item_type,status,created_at,updated_at) "
            "VALUES(?,'腾讯','招聘安排','assessment','pending','now','now')",
            [(str(i),) for i in range(151)],
        )
    monkeypatch.setattr(FeishuBitableClient, "configured", property(lambda self: False))
    app = FastAPI()
    app.include_router(build_recruitment_feishu_router(db_path, lambda: None))
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/api/v1/recruitment/feishu/proposals?limit=150")
            assert response.status_code == 200
            proposals = response.json()["proposals"]
            assert len(proposals) == 151
            assert all(p["stage"] == "测评" and p["status"] == "pending" for p in proposals)


def test_terminal_progress_is_not_overridden_by_past_test_type():
    from backend.app.recruitment_feishu_queue import _proposal_payload

    assert _proposal_payload({"item_type": "written_test", "title": "笔试未通过通知"})[0] == "流程结束"
    assert _proposal_payload({"item_type": "assessment", "title": "测评通过，录用通知"})[0] == "Offer"
