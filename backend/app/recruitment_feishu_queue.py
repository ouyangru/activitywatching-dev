from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from .recruitment_feishu import (
    _now_iso,
    canonical_mail_fields,
    ensure_recruitment_feishu_tables,
    infer_recruitment_stage,
)


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


def backfill_recruitment_feishu_proposals(db_path: Path) -> dict[str, int]:
    created = 0
    skipped = 0
    with _connect(db_path) as connection:
        ensure_recruitment_feishu_tables(connection)
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
        now = _now_iso()
        for row in rows:
            item = dict(row)
            subject = item.get("source_subject") or item.get("title") or ""
            context = "\n".join(
                part for part in (
                    item.get("title") or "",
                    item.get("extraction_note") or "",
                ) if part
            )
            stage = _infer_mail_stage(subject, context)
            if not stage:
                skipped += 1
                continue
            next_at = item.get("start_at") or item.get("deadline_at")
            latest_update = subject.strip() or item.get("title") or stage
            proposed_fields = canonical_mail_fields(item, subject, context, stage)
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
                    json.dumps(proposed_fields, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            if cursor.rowcount:
                created += 1
        connection.commit()
    return {"created": created, "skipped": skipped}


def build_recruitment_feishu_queue_router(db_path: Path, require_auth: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1/recruitment/feishu", dependencies=[Depends(require_auth)])

    @router.post("/backfill")
    def backfill() -> dict[str, Any]:
        return {"ok": True, **backfill_recruitment_feishu_proposals(db_path), "written": False}

    return router
