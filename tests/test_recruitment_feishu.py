import sqlite3

from backend.app.recruitment import init_recruitment_db
from backend.app.recruitment_feishu import (
    infer_recruitment_stage,
    match_company_record,
    resolve_field_mapping,
)
from backend.app.recruitment_feishu_queue import backfill_recruitment_feishu_proposals


def test_infer_recruitment_stage_prefers_specific_round():
    assert infer_recruitment_stage("腾讯二面邀请") == "二面"
    assert infer_recruitment_stage("字节跳动 HR 面试通知") == "HR面"
    assert infer_recruitment_stage("在线测评邀请") == "测评"
    assert infer_recruitment_stage("招聘流程终止通知") == "流程结束"


def test_company_match_requires_unique_record():
    records = [
        {"record_id": "rec-a", "fields": {"公司": "腾讯"}},
        {"record_id": "rec-b", "fields": {"公司": "字节跳动有限公司"}},
    ]
    matched = match_company_record(records, "公司", "字节跳动")
    assert matched["status"] == "matched"
    assert matched["record"]["record_id"] == "rec-b"

    ambiguous = match_company_record(
        [
            {"record_id": "rec-1", "fields": {"公司": "小米科技"}},
            {"record_id": "rec-2", "fields": {"公司": "小米集团"}},
        ],
        "公司",
        "小米",
    )
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["record"] is None


def test_field_mapping_only_uses_unique_alias(monkeypatch):
    monkeypatch.delenv("FEISHU_RECRUITMENT_COMPANY_FIELD", raising=False)
    monkeypatch.delenv("FEISHU_RECRUITMENT_STAGE_FIELD", raising=False)
    monkeypatch.delenv("FEISHU_RECRUITMENT_LATEST_FIELD", raising=False)
    monkeypatch.delenv("FEISHU_RECRUITMENT_NEXT_FIELD", raising=False)
    fields = [
        {"field_name": "公司名称", "type": 1},
        {"field_name": "当前阶段", "type": 3},
        {"field_name": "最近进展", "type": 1},
        {"field_name": "下次安排", "type": 5},
    ]
    assert resolve_field_mapping(fields) == {
        "company": "公司名称",
        "stage": "当前阶段",
        "latest": "最近进展",
        "next": "下次安排",
    }


def test_backfill_creates_pending_review_without_writing(tmp_path):
    db_path = tmp_path / "activitywatch.db"
    init_recruitment_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO recruitment_items(
                message_id, company, title, item_type, mode, status, start_at,
                source_subject, extraction_note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mail-1",
                "腾讯",
                "腾讯二面邀请",
                "interview",
                "fixed_time",
                "pending",
                "2026-09-15T14:00:00+08:00",
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
            "SELECT source, company, stage, status, record_id FROM recruitment_feishu_proposals"
        ).fetchone()
    assert row == ("mail", "腾讯", "二面", "pending", None)
