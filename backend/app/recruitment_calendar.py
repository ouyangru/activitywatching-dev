from __future__ import annotations

import json
import os
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .recruitment import _connect, _now_iso, init_recruitment_db

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.events"


class ManualRecruitmentItem(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    company: str = Field(default="", max_length=128)
    item_type: str = Field(default="other", max_length=64)
    mode: str = Field(default="fixed_time", max_length=64)
    start_at: str | None = None
    end_at: str | None = None
    deadline_at: str | None = None
    action_url: str | None = Field(default=None, max_length=2048)


class GoogleEventPatch(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    start: str
    end: str | None = None
    all_day: bool = False
    description: str | None = Field(default=None, max_length=8000)


def _calendar_config() -> dict[str, str]:
    return {
        "client_id": os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip(),
        "calendar_id": os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
        "redirect_uri": os.getenv("GOOGLE_CALENDAR_REDIRECT_URI", "").strip(),
    }


def _calendar_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(recruitment_items)").fetchall()}


def init_recruitment_calendar_db(path: Path) -> None:
    init_recruitment_db(path)
    with _connect(path) as connection:
        columns = _calendar_columns(connection)
        if "calendar_event_id" not in columns:
            connection.execute("ALTER TABLE recruitment_items ADD COLUMN calendar_event_id TEXT")
        if "calendar_synced_at" not in columns:
            connection.execute("ALTER TABLE recruitment_items ADD COLUMN calendar_synced_at TEXT")
        if "calendar_sync_error" not in columns:
            connection.execute("ALTER TABLE recruitment_items ADD COLUMN calendar_sync_error TEXT")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS recruitment_google_auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at TEXT,
                scope TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recruitment_google_oauth_state (
                state TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.commit()


def _json_request(
    method: str,
    url: str,
    *,
    access_token: str | None = None,
    json_body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    headers: dict[str, str] = {"Accept": "application/json"}
    data: bytes | None = None
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    elif form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if not body:
                return {}
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            detail = payload.get("error_description") or payload.get("error", {}).get("message") or payload.get("error")
        except Exception:
            detail = raw
        raise RuntimeError(f"Google API {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 Google Calendar：{exc.reason}") from exc


def _redirect_uri(request: Request) -> str:
    configured = _calendar_config()["redirect_uri"]
    if configured:
        return configured
    return str(request.base_url).rstrip("/") + "/api/v1/recruitment/calendar/google/callback"


def _auth_row(db_path: Path) -> sqlite3.Row | None:
    with _connect(db_path) as connection:
        return connection.execute("SELECT * FROM recruitment_google_auth WHERE id=1").fetchone()


def _save_tokens(db_path: Path, payload: dict[str, Any], previous_refresh: str | None = None) -> None:
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Google OAuth 未返回 access_token")
    refresh_token = payload.get("refresh_token") or previous_refresh
    expires_in = int(payload.get("expires_in") or 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 30))).isoformat()
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO recruitment_google_auth(id, access_token, refresh_token, expires_at, scope, updated_at)
            VALUES(1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              access_token=excluded.access_token,
              refresh_token=excluded.refresh_token,
              expires_at=excluded.expires_at,
              scope=excluded.scope,
              updated_at=excluded.updated_at
            """,
            (access_token, refresh_token, expires_at, payload.get("scope") or GOOGLE_SCOPE, _now_iso()),
        )
        connection.commit()


def _access_token(db_path: Path) -> str:
    row = _auth_row(db_path)
    if not row:
        raise RuntimeError("Google 日历尚未连接")
    expires_at = row["expires_at"]
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry > datetime.now(timezone.utc) + timedelta(seconds=30):
                return row["access_token"]
        except ValueError:
            pass
    refresh_token = row["refresh_token"]
    config = _calendar_config()
    if not refresh_token or not config["client_id"] or not config["client_secret"]:
        raise RuntimeError("Google 日历授权已过期，请重新连接")
    payload = _json_request(
        "POST",
        GOOGLE_TOKEN_URL,
        form={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    _save_tokens(db_path, payload, refresh_token)
    return str(payload["access_token"])


def _google_url(calendar_id: str, suffix: str = "") -> str:
    calendar = urllib.parse.quote(calendar_id, safe="")
    return f"{GOOGLE_CALENDAR_API}/calendars/{calendar}/events{suffix}"


def _google_request(db_path: Path, method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return _json_request(method, url, access_token=_access_token(db_path), json_body=body)


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"))


def _parse_local_datetime(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=_local_tz())
    return value


def _day_for_raw(raw: str) -> str:
    if "T" not in raw:
        return raw[:10]
    return _parse_local_datetime(raw).astimezone(_local_tz()).date().isoformat()


def _google_event_body(item: dict[str, Any]) -> dict[str, Any]:
    mode = item.get("mode") or "fixed_time"
    raw = item.get("deadline_at") if mode == "deadline" else item.get("start_at")
    if not raw:
        raise RuntimeError("该事项没有可同步的确定时间")
    company = (item.get("company") or "").strip()
    title = (item.get("title") or "秋招事项").strip()
    summary = f"{company} · {title}" if company else title
    body: dict[str, Any] = {
        "summary": summary,
        "description": "\n".join(
            part for part in [
                "来自 ActivityWatching 秋招事务",
                f"状态：{item.get('status') or 'pending'}",
                f"类型：{item.get('item_type') or 'other'}",
                item.get("action_url") or "",
            ] if part
        ),
        "extendedProperties": {"private": {"activitywatchRecruitmentId": str(item["id"]) }},
    }
    if "T" not in raw:
        start_day = datetime.fromisoformat(raw[:10]).date()
        body["start"] = {"date": start_day.isoformat()}
        body["end"] = {"date": (start_day + timedelta(days=1)).isoformat()}
        return body
    start = _parse_local_datetime(raw)
    end_raw = item.get("end_at") if mode == "fixed_time" else None
    end = _parse_local_datetime(end_raw) if end_raw else start + timedelta(minutes=1)
    if end <= start:
        end = start + timedelta(minutes=1)
    body["start"] = {"dateTime": start.isoformat(), "timeZone": str(_local_tz())}
    body["end"] = {"dateTime": end.isoformat(), "timeZone": str(_local_tz())}
    return body


def _sync_one(db_path: Path, item_id: int) -> dict[str, Any]:
    config = _calendar_config()
    if not config["client_id"] or not config["client_secret"]:
        raise RuntimeError("服务器尚未配置 Google Calendar OAuth")
    with _connect(db_path) as connection:
        row = connection.execute("SELECT * FROM recruitment_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise RuntimeError("事项不存在")
    item = dict(row)
    event_id = item.get("calendar_event_id")
    if item.get("status") == "cancelled":
        if event_id:
            try:
                _google_request(db_path, "DELETE", _google_url(config["calendar_id"], "/" + urllib.parse.quote(event_id, safe="")))
            except RuntimeError as exc:
                if "404" not in str(exc) and "410" not in str(exc):
                    raise
        with _connect(db_path) as connection:
            connection.execute(
                "UPDATE recruitment_items SET calendar_synced=0, calendar_event_id=NULL, calendar_synced_at=?, calendar_sync_error=NULL WHERE id=?",
                (_now_iso(), item_id),
            )
            connection.commit()
        return {"ok": True, "deleted": True, "event_id": None}

    body = _google_event_body(item)
    if event_id:
        url = _google_url(config["calendar_id"], "/" + urllib.parse.quote(event_id, safe=""))
        result = _google_request(db_path, "PATCH", url, body)
    else:
        result = _google_request(db_path, "POST", _google_url(config["calendar_id"]), body)
        event_id = result.get("id")
    if not event_id:
        raise RuntimeError("Google Calendar 未返回事件 ID")
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE recruitment_items SET calendar_synced=1, calendar_event_id=?, calendar_synced_at=?, calendar_sync_error=NULL WHERE id=?",
            (event_id, _now_iso(), item_id),
        )
        connection.commit()
    return {"ok": True, "event_id": event_id, "html_link": result.get("htmlLink")}


def _serialize_local_event(item: dict[str, Any]) -> dict[str, Any] | None:
    mode = item.get("mode") or "fixed_time"
    raw = item.get("deadline_at") if mode == "deadline" else item.get("start_at")
    if not raw:
        return None
    return {
        "key": f"local:{item['id']}",
        "source": "manual" if str(item.get("message_id") or "").startswith("manual-") else "recruitment",
        "local_id": item["id"],
        "google_event_id": item.get("calendar_event_id"),
        "synced": bool(item.get("calendar_event_id")),
        "company": item.get("company") or "",
        "title": item.get("title") or "秋招事项",
        "item_type": item.get("item_type") or "other",
        "mode": mode,
        "status": item.get("status") or "pending",
        "start": raw,
        "end": item.get("end_at"),
        "all_day": "T" not in raw,
        "day": _day_for_raw(raw),
        "action_url": item.get("action_url"),
    }


def _google_events(db_path: Path, start: str, end: str) -> list[dict[str, Any]]:
    config = _calendar_config()
    tz = _local_tz()
    start_dt = datetime.fromisoformat(start).replace(tzinfo=tz)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=tz)
    params = urllib.parse.urlencode(
        {
            "timeMin": start_dt.isoformat(),
            "timeMax": end_dt.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
        }
    )
    payload = _google_request(db_path, "GET", _google_url(config["calendar_id"]) + "?" + params)
    events: list[dict[str, Any]] = []
    for event in payload.get("items", []):
        if event.get("status") == "cancelled":
            continue
        start_info = event.get("start") or {}
        end_info = event.get("end") or {}
        raw_start = start_info.get("dateTime") or start_info.get("date")
        if not raw_start:
            continue
        private = ((event.get("extendedProperties") or {}).get("private") or {})
        events.append(
            {
                "key": f"google:{event.get('id')}",
                "source": "google",
                "google_event_id": event.get("id"),
                "linked_local_id": private.get("activitywatchRecruitmentId"),
                "title": event.get("summary") or "Google 日程",
                "company": "",
                "status": "confirmed",
                "mode": "fixed_time",
                "start": raw_start,
                "end": end_info.get("dateTime") or end_info.get("date"),
                "all_day": "date" in start_info,
                "day": _day_for_raw(raw_start),
                "description": event.get("description") or "",
                "html_link": event.get("htmlLink"),
            }
        )
    return events


def build_recruitment_calendar_router(db_path: Path, require_auth: Any) -> APIRouter:
    init_recruitment_calendar_db(db_path)
    router = APIRouter(prefix="/api/v1/recruitment/calendar", dependencies=[Depends(require_auth)])

    @router.get("/google/status")
    def google_status(request: Request) -> dict[str, Any]:
        config = _calendar_config()
        row = _auth_row(db_path)
        return {
            "configured": bool(config["client_id"] and config["client_secret"]),
            "connected": bool(row and row["refresh_token"]),
            "calendar_id": config["calendar_id"],
            "redirect_uri": _redirect_uri(request),
        }

    @router.post("/google/connect")
    def google_connect(request: Request) -> dict[str, str]:
        config = _calendar_config()
        if not config["client_id"] or not config["client_secret"]:
            raise HTTPException(status_code=503, detail="请先配置 GOOGLE_CALENDAR_CLIENT_ID / GOOGLE_CALENDAR_CLIENT_SECRET")
        state = secrets.token_urlsafe(32)
        with _connect(db_path) as connection:
            connection.execute("DELETE FROM recruitment_google_oauth_state WHERE created_at < ?", ((datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),))
            connection.execute("INSERT INTO recruitment_google_oauth_state(state, created_at) VALUES(?, ?)", (state, _now_iso()))
            connection.commit()
        query = urllib.parse.urlencode(
            {
                "client_id": config["client_id"],
                "redirect_uri": _redirect_uri(request),
                "response_type": "code",
                "scope": GOOGLE_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )
        return {"authorization_url": GOOGLE_AUTH_URL + "?" + query}

    @router.get("/google/callback", response_class=HTMLResponse)
    def google_callback(request: Request, state: str = Query(...), code: str = Query(...)) -> HTMLResponse:
        config = _calendar_config()
        with _connect(db_path) as connection:
            row = connection.execute("SELECT * FROM recruitment_google_oauth_state WHERE state=?", (state,)).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="Google OAuth state 无效或已过期")
            connection.execute("DELETE FROM recruitment_google_oauth_state WHERE state=?", (state,))
            connection.commit()
        payload = _json_request(
            "POST",
            GOOGLE_TOKEN_URL,
            form={
                "code": code,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": _redirect_uri(request),
                "grant_type": "authorization_code",
            },
        )
        previous = _auth_row(db_path)
        _save_tokens(db_path, payload, previous["refresh_token"] if previous else None)
        return HTMLResponse(
            """<!doctype html><meta charset='utf-8'><title>Google Calendar 已连接</title>
            <style>body{background:#131a22;color:#e7eef5;font-family:system-ui;padding:40px}p{color:#a5b5c4}</style>
            <h2>Google Calendar 已连接</h2><p>此窗口可以关闭。</p>
            <script>if(window.opener){window.opener.postMessage('activitywatch-google-calendar-connected', location.origin);setTimeout(()=>window.close(),500)}</script>"""
        )

    @router.post("/google/disconnect")
    def google_disconnect() -> dict[str, bool]:
        with _connect(db_path) as connection:
            connection.execute("DELETE FROM recruitment_google_auth WHERE id=1")
            connection.commit()
        return {"ok": True}

    @router.get("/events")
    def calendar_events(start: str, end: str, include_google: bool = True) -> dict[str, Any]:
        try:
            start_day = datetime.fromisoformat(start).date()
            end_day = datetime.fromisoformat(end).date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="start/end 必须是 YYYY-MM-DD") from exc
        with _connect(db_path) as connection:
            rows = connection.execute("SELECT * FROM recruitment_items WHERE status != 'cancelled'").fetchall()
        local_events: list[dict[str, Any]] = []
        linked_ids: set[str] = set()
        for row in rows:
            event = _serialize_local_event(dict(row))
            if not event:
                continue
            day = datetime.fromisoformat(event["day"]).date()
            if start_day <= day < end_day:
                local_events.append(event)
            if event.get("google_event_id"):
                linked_ids.add(str(event["google_event_id"]))

        google_events: list[dict[str, Any]] = []
        google_error = None
        if include_google and _auth_row(db_path):
            try:
                google_events = [event for event in _google_events(db_path, start, end) if str(event.get("google_event_id")) not in linked_ids]
            except Exception as exc:
                google_error = str(exc)
        return {"events": local_events + google_events, "google_error": google_error}

    @router.post("/items")
    def create_manual_item(payload: ManualRecruitmentItem) -> dict[str, Any]:
        if payload.mode == "fixed_time" and not payload.start_at:
            raise HTTPException(status_code=400, detail="固定时间日程必须填写开始时间")
        if payload.mode == "deadline" and not payload.deadline_at:
            raise HTTPException(status_code=400, detail="截止事项必须填写截止日期或时间")
        now = _now_iso()
        message_id = "manual-" + uuid4().hex
        precision = None
        if payload.deadline_at:
            precision = "minute" if "T" in payload.deadline_at else "day"
        with _connect(db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO recruitment_items(
                    message_id, company, title, item_type, mode, status,
                    start_at, end_at, deadline_at, deadline_precision, action_url,
                    source_subject, extraction_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, '手动创建日程', ?, ?)
                """,
                (
                    message_id,
                    payload.company,
                    payload.title,
                    payload.item_type,
                    payload.mode,
                    payload.start_at,
                    payload.end_at,
                    payload.deadline_at,
                    precision,
                    payload.action_url,
                    payload.title,
                    now,
                    now,
                ),
            )
            item_id = int(cursor.lastrowid)
            connection.commit()
            row = connection.execute("SELECT * FROM recruitment_items WHERE id=?", (item_id,)).fetchone()
        return {"item": dict(row)}

    @router.post("/items/{item_id}/sync")
    def sync_item(item_id: int) -> dict[str, Any]:
        try:
            return _sync_one(db_path, item_id)
        except Exception as exc:
            with _connect(db_path) as connection:
                connection.execute("UPDATE recruitment_items SET calendar_sync_error=? WHERE id=?", (str(exc), item_id))
                connection.commit()
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/sync-all")
    def sync_all() -> dict[str, Any]:
        with _connect(db_path) as connection:
            rows = connection.execute(
                "SELECT id FROM recruitment_items WHERE status='pending' AND (start_at IS NOT NULL OR deadline_at IS NOT NULL) ORDER BY id"
            ).fetchall()
        synced = 0
        failed: list[dict[str, Any]] = []
        for row in rows:
            try:
                _sync_one(db_path, int(row["id"]))
                synced += 1
            except Exception as exc:
                failed.append({"id": int(row["id"]), "error": str(exc)})
        return {"ok": not failed, "synced": synced, "failed": failed}

    @router.delete("/items/{item_id}/google")
    def unlink_google(item_id: int) -> dict[str, bool]:
        config = _calendar_config()
        with _connect(db_path) as connection:
            row = connection.execute("SELECT calendar_event_id FROM recruitment_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="事项不存在")
        event_id = row["calendar_event_id"]
        if event_id:
            try:
                _google_request(db_path, "DELETE", _google_url(config["calendar_id"], "/" + urllib.parse.quote(event_id, safe="")))
            except RuntimeError as exc:
                if "404" not in str(exc) and "410" not in str(exc):
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
        with _connect(db_path) as connection:
            connection.execute(
                "UPDATE recruitment_items SET calendar_synced=0, calendar_event_id=NULL, calendar_synced_at=?, calendar_sync_error=NULL WHERE id=?",
                (_now_iso(), item_id),
            )
            connection.commit()
        return {"ok": True}

    @router.patch("/google-events/{event_id}")
    def patch_google_event(event_id: str, payload: GoogleEventPatch) -> dict[str, Any]:
        config = _calendar_config()
        if payload.all_day:
            start_day = datetime.fromisoformat(payload.start[:10]).date()
            end_day = datetime.fromisoformat(payload.end[:10]).date() if payload.end else start_day + timedelta(days=1)
            body = {
                "summary": payload.title,
                "description": payload.description or "",
                "start": {"date": start_day.isoformat()},
                "end": {"date": max(end_day, start_day + timedelta(days=1)).isoformat()},
            }
        else:
            start_dt = _parse_local_datetime(payload.start)
            end_dt = _parse_local_datetime(payload.end) if payload.end else start_dt + timedelta(minutes=30)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(minutes=1)
            body = {
                "summary": payload.title,
                "description": payload.description or "",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": str(_local_tz())},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": str(_local_tz())},
            }
        try:
            result = _google_request(
                db_path,
                "PATCH",
                _google_url(config["calendar_id"], "/" + urllib.parse.quote(event_id, safe="")),
                body,
            )
            return {"event": result}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.delete("/google-events/{event_id}")
    def delete_google_event(event_id: str) -> dict[str, bool]:
        config = _calendar_config()
        try:
            _google_request(db_path, "DELETE", _google_url(config["calendar_id"], "/" + urllib.parse.quote(event_id, safe="")))
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router
