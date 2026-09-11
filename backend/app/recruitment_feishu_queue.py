from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from .recruitment_feishu import (
    _now_iso,
    ensure_recruitment_feishu_tables,
    infer_recruitment_stage,
)
from .recruitment_pipeline import build_mail_pipeline_fields


ROUND_RE = re.compile(r"第?\s*([1-9一二三四五六七八九])\s*(?:轮)?\s*面")
ROUND_NUMBER = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _infer_mail_stage(subject: str, context: str) -> str | None:
    text = f"{subject}\n{context}".lower()
    match = ROUND_RE.search(text)
    if match:
        value = match.group(1)
        number = int(value) if value.isdigit() else ROUND_NUMBER.get(value, 0)
        if number:
            return f"{number}面"
    return infer_recruitment_stage(subject, context)


def _proposal_payload(item: dict[str, Any]) -> tuple[str | None, dict[str, Any], str, str | None]:
    subject = item.get("source_subject") or item.get("title") or ""
    context = "\n".join(
        part for part in (
            item.get("title") or "",
            item.get("extraction_note") or "",
        ) if part
    )
    stage = _infer_mail_stage(subject, context)
    if not stage:
        return None, {}, subject, None
    next_at = item.get("start_at") or item.get("deadline_at")
    fields = build_mail_pipeline_fields(item, stage, subject)
    return stage, fields, subject, next_at


def backfill_recruitment_feishu_proposals(db_path: Path) -> dict[str, int]:
    created = 0
    enriched = 0
    skipped = 0
    with _connect(db_path) as connection:
        ensure_recruitment_feishu_tables(connection)
        now = _now_iso()

        # 先升级已经存在的旧版 pending proposal。旧版本 fields_json 为空，
        # 如果不补齐，部署新代码后用户仍然看不到岗位/地点/各阶段日期。
        legacy_rows = connection.execute(
            """
            SELECT p.id AS proposal_id, r.*
            FROM recruitment_feishu_proposals p
            JOIN recruitment_items r ON r.id = p.recruitment_item_id
            WHERE p.source='mail' AND p.status='pending'
              AND (p.fields_json IS NULL OR p.fields_json='' OR p.fields_json='{}')
            ORDER BY p.id ASC
            """
        ).fetchall()
        for legacy in legacy_rows:
            item = dict(legacy)
            stage, fields, subject, next_at = _proposal_payload(item)
            if not stage:
                continue
            connection.execute(
                """
                UPDATE recruitment_feishu_proposals
                SET company=?, stage=?, latest_update=?, next_at=?, source_title=?,
                    fields_json=?, error=NULL, updated_at=?
                WHERE id=?
                """,
                (
                    item.get("company") or "",
                    stage,
                    subject.strip() or item.get("title") or stage,
                    next_at,
                    subject[:512],
                    json.dumps(fields, ensure_ascii=False),
                    now,
                    item["proposal_id"],
                ),
            )
            enriched += 1

        rows = connection.execute(
            """
            SELECT r.*
            FROM recruitment_items r
            LEFT JOIN recruitment_feishu_proposals p
              ON p.recruitment_item_id = r.id AND p.source = 'mail'
            WHERE p.id IS NULL AND r.status != 'cancelled'
            ORDER BY r.id ASC
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            stage, fields, subject, next_at = _proposal_payload(item)
            if not stage:
                skipped += 1
                continue
            latest_update = subject.strip() or item.get("title") or stage
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO recruitment_feishu_proposals(
                    recruitment_item_id, source, company, stage, latest_update, next_at,
                    source_title, fields_json, status, created_at, updated_at
                ) VALUES (?, 'mail', ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    item["id"],
                    item.get("company") or "",
                    stage,
                    latest_update[:512],
                    next_at,
                    subject[:512],
                    json.dumps(fields, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            if cursor.rowcount:
                created += 1
        connection.commit()
    return {"created": created, "enriched": enriched, "skipped": skipped}


def build_recruitment_feishu_queue_router(db_path: Path, require_auth: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1/recruitment/feishu", dependencies=[Depends(require_auth)])

    @router.post("/backfill")
    def backfill() -> dict[str, Any]:
        return {"ok": True, **backfill_recruitment_feishu_proposals(db_path), "written": False}

    return router
