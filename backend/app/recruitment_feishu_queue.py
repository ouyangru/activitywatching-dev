from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from .recruitment_feishu import (
    _now_iso,
    ensure_recruitment_feishu_tables,
    infer_recruitment_stage,
)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


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
            stage = infer_recruitment_stage(subject, context)
            if not stage:
                skipped += 1
                continue
            next_at = item.get("start_at") or item.get("deadline_at")
            latest_update = subject.strip() or item.get("title") or stage
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO recruitment_feishu_proposals(
                    recruitment_item_id, source, company, stage, latest_update, next_at,
                    source_title, fields_json, status, created_at, updated_at
                ) VALUES (?, 'mail', ?, ?, ?, ?, ?, '{}', 'pending', ?, ?)
                """,
                (
                    item["id"],
                    item.get("company") or "",
                    stage,
                    latest_update[:512],
                    next_at,
                    subject[:512],
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
