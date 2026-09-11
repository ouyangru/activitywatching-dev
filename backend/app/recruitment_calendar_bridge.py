from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .recruitment import _connect, _now_iso
from .recruitment_calendar import _sync_one, init_recruitment_calendar_db
from .recruitment_feishu import FIELD_ALIASES, ensure_recruitment_feishu_tables


LOG = logging.getLogger("activitywatch.recruitment.calendar_bridge")

_DATE_META: dict[str, tuple[str, str]] = {
    "application_date": ("other", "投递"),
    "assessment_date": ("assessment", "测评"),
    "written_date": ("written_test", "笔试"),
    "first_interview_date": ("interview", "一面"),
    "second_interview_date": ("interview", "二面"),
    "third_interview_date": ("interview", "三面"),
}
_DATE_KEYS = tuple(_DATE_META)
_ITEM_PATH_RE = re.compile(r"^/api/v1/recruitment/items/(?P<item_id>\d+)(?:/complete)?$")
_APPROVE_PATH_RE = re.compile(r"^/api/v1/recruitment/feishu/proposals/(?P<proposal_id>\d+)/approve$")


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))


def _ensure_bridge_columns(db_path: Path) -> None:
    init_recruitment_calendar_db(db_path)
    with _connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recruitment_items)").fetchall()}
        if "feishu_record_id" not in columns:
            connection.execute("ALTER TABLE recruitment_items ADD COLUMN feishu_record_id TEXT")
        if "feishu_stage_key" not in columns:
            connection.execute("ALTER TABLE recruitment_items ADD COLUMN feishu_stage_key TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_recruitment_feishu_stage "
            "ON recruitment_items(feishu_record_id, feishu_stage_key)"
        )
        ensure_recruitment_feishu_tables(connection)
        connection.commit()


def _field_value(fields: dict[str, Any], logical_key: str) -> Any:
    for name in FIELD_ALIASES.get(logical_key, ()):  # canonical name is the first alias
        if name in fields:
            return fields[name]
    return None


def _field_present(fields: dict[str, Any], logical_key: str) -> bool:
    return any(name in fields for name in FIELD_ALIASES.get(logical_key, ()))


def _stage_key(stage: str | None) -> str | None:
    text = (stage or "").strip().lower()
    if not text:
        return None
    if "测评" in text or "assessment" in text:
        return "assessment_date"
    if "笔试" in text or "written" in text:
        return "written_date"
    if "三面" in text or "3面" in text or "第三" in text:
        return "third_interview_date"
    if "二面" in text or "2面" in text or "第二" in text or "复试" in text:
        return "second_interview_date"
    if "一面" in text or "1面" in text or "第一" in text or "初面" in text:
        return "first_interview_date"
    if "面试" in text or "interview" in text:
        return "first_interview_date"
    if "投递" in text or "网申" in text:
        return "application_date"
    return None


def _calendar_raw(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        try:
            parsed = datetime.fromtimestamp(numeric, _tz())
        except (ValueError, OSError, OverflowError):
            return None
        if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
            return parsed.date().isoformat()
        return parsed.isoformat(timespec="minutes")
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", raw) else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_tz())
    else:
        parsed = parsed.astimezone(_tz())
    if "T" not in raw or (parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0):
        return parsed.date().isoformat()
    return parsed.isoformat(timespec="minutes")


def _pending_or_done(raw: str) -> str:
    try:
        if "T" in raw:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=_tz())
            else:
                when = when.astimezone(_tz())
            return "done" if when < datetime.now(_tz()) else "pending"
        day = datetime.fromisoformat(raw[:10]).date()
        return "done" if day < datetime.now(_tz()).date() else "pending"
    except ValueError:
        return "pending"


def _metadata(fields: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for logical in ("position", "recruitment_type", "location", "priority"):
        value = _field_value(fields, logical)
        if value not in (None, ""):
            values[logical] = str(value)
    return values


def _update_linked_google_safe(db_path: Path, item_id: int) -> None:
    try:
        with _connect(db_path) as connection:
            row = connection.execute(
                "SELECT calendar_event_id FROM recruitment_items WHERE id=?", (item_id,)
            ).fetchone()
        if row and row["calendar_event_id"]:
            _sync_one(db_path, item_id)
    except Exception as exc:  # local mutation must not fail because Google is temporarily unavailable
        LOG.warning("calendar bridge Google re-sync failed item=%s: %s", item_id, exc)
        try:
            with _connect(db_path) as connection:
                connection.execute(
                    "UPDATE recruitment_items SET calendar_sync_error=? WHERE id=?",
                    (str(exc)[:1000], item_id),
                )
                connection.commit()
        except Exception:
            LOG.exception("failed to store calendar sync error item=%s", item_id)


def _sync_mail_proposal(connection, proposal, fields: dict[str, Any]) -> list[int]:
    item_id = proposal["recruitment_item_id"]
    if not item_id:
        return []
    item = connection.execute("SELECT * FROM recruitment_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        return []

    key = _stage_key(proposal["stage"])
    record_id = str(proposal["record_id"] or "") or None
    raw_date = _field_value(fields, key) if key else None
    date_value = _calendar_raw(raw_date) or _calendar_raw(proposal["next_at"])
    company = str(proposal["company"] or _field_value(fields, "company") or item["company"] or "")
    metadata = _metadata(fields)

    assignments: list[str] = ["company=?", "updated_at=?"]
    params: list[Any] = [company, _now_iso()]
    if key:
        item_type, _ = _DATE_META[key]
        assignments.extend(["item_type=?", "feishu_record_id=?", "feishu_stage_key=?"])
        params.extend([item_type, record_id, key])
    if date_value:
        mode = item["mode"] if item["mode"] in {"fixed_time", "deadline"} else "fixed_time"
        assignments.append("mode=?")
        params.append(mode)
        if mode == "deadline":
            assignments.extend(["deadline_at=?", "start_at=NULL"])
            params.append(date_value)
        else:
            assignments.extend(["start_at=?", "deadline_at=NULL"])
            params.append(date_value)
        if item["status"] == "uncertain":
            assignments.append("status='pending'")
    for column, value in metadata.items():
        assignments.append(f"{column}=?")
        params.append(value)
    params.append(int(item_id))
    connection.execute(
        f"UPDATE recruitment_items SET {', '.join(assignments)} WHERE id=?",
        params,
    )
    return [int(item_id)]


def _upsert_progress_stage(
    connection,
    *,
    record_id: str,
    key: str,
    raw_date: str,
    company: str,
    metadata: dict[str, str],
) -> int:
    item_type, stage_label = _DATE_META[key]
    existing = connection.execute(
        """
        SELECT * FROM recruitment_items
        WHERE feishu_record_id=? AND feishu_stage_key=?
        ORDER BY CASE WHEN message_id LIKE 'feishu-progress:%' THEN 1 ELSE 0 END, id
        LIMIT 1
        """,
        (record_id, key),
    ).fetchone()
    title = stage_label + (f" · {metadata['position']}" if metadata.get("position") else "")
    status = _pending_or_done(raw_date)
    now = _now_iso()
    if existing:
        assignments = [
            "company=?", "title=?", "item_type=?", "mode='fixed_time'", "status=?",
            "start_at=?", "end_at=NULL", "deadline_at=NULL", "updated_at=?",
        ]
        params: list[Any] = [company, title, item_type, status, raw_date, now]
        for column, value in metadata.items():
            assignments.append(f"{column}=?")
            params.append(value)
        params.append(int(existing["id"]))
        connection.execute(
            f"UPDATE recruitment_items SET {', '.join(assignments)} WHERE id=?",
            params,
        )
        return int(existing["id"])

    message_id = f"feishu-progress:{record_id}:{key}"
    connection.execute(
        """
        INSERT INTO recruitment_items(
            message_id, company, title, item_type, mode, status,
            start_at, end_at, deadline_at, deadline_precision, action_url,
            source_subject, extraction_note, position, recruitment_type, location, priority,
            feishu_record_id, feishu_stage_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'fixed_time', ?, ?, NULL, NULL, ?, NULL,
                  '飞书招聘进度', '由招聘进度审核写入自动同步到日历', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            company,
            title,
            item_type,
            status,
            raw_date,
            "day" if "T" not in raw_date else "minute",
            metadata.get("position"),
            metadata.get("recruitment_type"),
            metadata.get("location"),
            metadata.get("priority"),
            record_id,
            key,
            now,
            now,
        ),
    )
    row = connection.execute("SELECT id FROM recruitment_items WHERE message_id=?", (message_id,)).fetchone()
    return int(row["id"])


def _sync_manual_proposal(connection, proposal, fields: dict[str, Any]) -> list[int]:
    record_id = str(proposal["record_id"] or "")
    if not record_id:
        return []
    company = str(proposal["company"] or _field_value(fields, "company") or "")
    metadata = _metadata(fields)

    # Metadata edits should also refresh already materialized calendar rows for this Feishu record.
    assignments = ["company=?", "updated_at=?"]
    params: list[Any] = [company, _now_iso()]
    for column, value in metadata.items():
        assignments.append(f"{column}=?")
        params.append(value)
    params.append(record_id)
    connection.execute(
        f"UPDATE recruitment_items SET {', '.join(assignments)} WHERE feishu_record_id=?",
        params,
    )

    item_ids: list[int] = []
    for key in _DATE_KEYS:
        if not _field_present(fields, key):
            continue
        raw_date = _calendar_raw(_field_value(fields, key))
        if not raw_date:
            continue
        item_ids.append(
            _upsert_progress_stage(
                connection,
                record_id=record_id,
                key=key,
                raw_date=raw_date,
                company=company,
                metadata=metadata,
            )
        )
    return item_ids


def sync_feishu_proposal_to_calendar(db_path: Path, proposal_id: int) -> list[int]:
    _ensure_bridge_columns(db_path)
    with _connect(db_path) as connection:
        proposal = connection.execute(
            "SELECT * FROM recruitment_feishu_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if not proposal or proposal["status"] != "applied":
            return []
        try:
            fields = json.loads(proposal["fields_json"] or "{}")
        except json.JSONDecodeError:
            LOG.warning("invalid fields_json for proposal=%s", proposal_id)
            return []
        if proposal["source"] == "mail":
            item_ids = _sync_mail_proposal(connection, proposal, fields)
        else:
            item_ids = _sync_manual_proposal(connection, proposal, fields)
        connection.commit()
    return item_ids


class RecruitmentCalendarBridgeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, db_path: Path) -> None:
        super().__init__(app)
        self.db_path = db_path
        _ensure_bridge_columns(db_path)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code >= 400:
            return response

        path = request.url.path
        method = request.method.upper()
        approve = _APPROVE_PATH_RE.match(path) if method == "POST" else None
        local_item = _ITEM_PATH_RE.match(path) if method in {"PATCH", "POST"} else None

        if approve:
            proposal_id = int(approve.group("proposal_id"))
            try:
                item_ids = await asyncio.to_thread(sync_feishu_proposal_to_calendar, self.db_path, proposal_id)
            except Exception:
                LOG.exception("failed to bridge Feishu proposal=%s into local calendar", proposal_id)
                item_ids = []
            for item_id in item_ids:
                asyncio.create_task(asyncio.to_thread(_update_linked_google_safe, self.db_path, item_id))
        elif local_item:
            item_id = int(local_item.group("item_id"))
            asyncio.create_task(asyncio.to_thread(_update_linked_google_safe, self.db_path, item_id))

        return response


def install_recruitment_calendar_bridge(app: FastAPI, db_path: Path) -> None:
    app.add_middleware(RecruitmentCalendarBridgeMiddleware, db_path=db_path)
