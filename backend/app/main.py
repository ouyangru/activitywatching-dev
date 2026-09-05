from __future__ import annotations

import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .analyzer import ActivityAnalyzer, serialize_segment
from .database import Database, utc_iso
from .schemas import BatchRequest, SegmentCorrection


BACKEND_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BACKEND_DIR / "static"
DEFAULT_DB = BACKEND_DIR / "data" / "activitywatch.db"
DEFAULT_RULES = BACKEND_DIR / "config" / "rules.yaml"
CATEGORIES = ["学习", "工作", "娱乐", "空闲", "其他"]
NO_DEVICE_CATEGORY = "无设备记录"
SUMMARY_CATEGORIES = [*CATEGORIES, NO_DEVICE_CATEGORY]
STATUS_FRESH_SECONDS = 120
NO_DEVICE_MIN_SECONDS = 300
load_dotenv(BACKEND_DIR / ".env")


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def _with_no_device_periods(
    rows: list[Any], coverage_rows: list[Any], start: datetime, end: datetime
) -> list[Any]:
    """Add substantial gaps where no selected device reported a raw window."""
    now = datetime.now(timezone.utc)
    effective_end = min(end, now)
    if effective_end <= start:
        return list(rows)

    covered: list[tuple[datetime, datetime]] = []
    for row in coverage_rows:
        row_start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        row_end = row_start + timedelta(milliseconds=row["duration_ms"])
        clipped_start = max(start, row_start)
        clipped_end = min(effective_end, row_end)
        if clipped_start < clipped_end:
            covered.append((clipped_start, clipped_end))

    covered.sort()
    merged: list[list[datetime]] = []
    for covered_start, covered_end in covered:
        if merged and covered_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], covered_end)
        else:
            merged.append([covered_start, covered_end])

    gaps: list[dict[str, Any]] = []
    cursor = start
    for covered_start, covered_end in merged:
        if (covered_start - cursor).total_seconds() >= NO_DEVICE_MIN_SECONDS:
            gaps.append(_no_device_row(cursor, covered_start))
        cursor = max(cursor, covered_end)
    if (effective_end - cursor).total_seconds() >= NO_DEVICE_MIN_SECONDS:
        gaps.append(_no_device_row(cursor, effective_end))

    return sorted([*rows, *gaps], key=lambda row: row["start_time"])


def _no_device_row(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "id": None,
        "device_id": None,
        "platform": "none",
        "start_time": utc_iso(start),
        "end_time": utc_iso(end),
        "category": NO_DEVICE_CATEGORY,
        "base_category": NO_DEVICE_CATEGORY,
        "behavior": "无设备活动",
        "description": "可能在运动、睡觉或进行不需要设备的活动",
        "process": "",
        "window_title": "",
        "window_count": 0,
        "key_count": 0,
        "mouse_click_count": 0,
        "scroll_count": 0,
        "interruptions_json": "[]",
        "manual_override": 0,
    }


def create_app(
    db_path: str | Path | None = None,
    rules_path: str | Path | None = None,
    timezone_name: str | None = None,
    api_token: str | None = None,
) -> FastAPI:
    production = os.getenv("ACTIVITYWATCH_ENV", "development") == "production"
    configured_token = api_token if api_token is not None else os.getenv("ACTIVITYWATCH_API_TOKEN", "")
    if production and (len(configured_token) < 32 or configured_token.startswith("replace-with")):
        raise RuntimeError("Production requires a random ACTIVITYWATCH_API_TOKEN of at least 32 characters")
    database = Database(db_path or os.getenv("ACTIVITYWATCH_DB_PATH", str(DEFAULT_DB)))
    analyzer = ActivityAnalyzer(
        database,
        rules_path or os.getenv("ACTIVITYWATCH_RULES_PATH", str(DEFAULT_RULES)),
        timezone_name or os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"),
    )

    application = FastAPI(
        title="行迹 Activity Timeline",
        version="0.3.1",
        description="Privacy-first Windows and Android activity timeline",
    )
    application.state.database = database
    application.state.analyzer = analyzer
    application.state.api_token = configured_token

    @application.middleware("http")
    async def privacy_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_auth(request: Request, activity_token: str | None = Cookie(default=None)) -> None:
        configured_token = application.state.api_token
        if not configured_token:
            return
        authorization = request.headers.get("authorization", "")
        bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        header_token = request.headers.get("x-activity-token", "")
        if not any(
            secrets.compare_digest(candidate.encode(), configured_token.encode())
            for candidate in (bearer, header_token, activity_token or "")
            if candidate
        ):
            raise HTTPException(status_code=401, detail="authentication required")

    @application.get("/api/v1/health")
    def health() -> dict[str, Any]:
        if production:
            return {"status": "ok"}
        return {
            "status": "ok",
            "feature_windows": database.count("feature_windows"),
            "activity_segments": database.count("activity_segments"),
        }

    @application.post("/api/v1/events/batch")
    def ingest_batch(payload: BatchRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        accepted, duplicates = database.insert_windows(payload.events)
        affected = sorted({(analyzer.day_for(event.start_time), event.device_id) for event in payload.events})
        rebuilt = sum(analyzer.rebuild_day(day, device_id) for day, device_id in affected)
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "segments_rebuilt": rebuilt,
        }

    @application.get("/api/v1/timeline/today")
    def timeline_today(
        _: None = Depends(require_auth),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        device_id: str | None = None,
    ) -> dict[str, Any]:
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), device_id)
        coverage_rows = database.rows_between("feature_windows", utc_iso(start), utc_iso(end), device_id)
        rows = _with_no_device_periods(rows, coverage_rows, start, end)
        return {
            "date": day or datetime.now(analyzer.timezone).date().isoformat(),
            "timezone": str(analyzer.timezone),
            "segments": [serialize_segment(row, analyzer.timezone) for row in rows],
        }

    @application.get("/api/v1/summary/today")
    def summary_today(
        _: None = Depends(require_auth),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        device_id: str | None = None,
    ) -> dict[str, Any]:
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), device_id)
        coverage_rows = database.rows_between("feature_windows", utc_iso(start), utc_iso(end), device_id)
        rows = _with_no_device_periods(rows, coverage_rows, start, end)
        seconds: dict[str, int] = defaultdict(int)
        for row in rows:
            row_start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
            row_end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
            seconds[row["category"]] += max(0, int((row_end - row_start).total_seconds()))
        total = sum(seconds.values())
        items = [
            {
                "category": category,
                "seconds": seconds[category],
                "percent": round(seconds[category] * 100 / total, 1) if total else 0,
            }
            for category in SUMMARY_CATEGORIES
        ]
        return {"total_seconds": total, "categories": items}

    @application.get("/api/v1/devices")
    def devices(_: None = Depends(require_auth)) -> dict[str, Any]:
        return {"devices": database.list_devices()}

    @application.get("/api/v1/status/current")
    def current_status(response: Response, _: None = Depends(require_auth)) -> dict[str, Any]:
        """Return the latest real activity segment and whether it is still live."""
        response.headers["Cache-Control"] = "no-store"
        now = datetime.now(timezone.utc)
        row = database.latest_segment()
        if row is None:
            return {
                "server_time": utc_iso(now),
                "fresh_for_seconds": STATUS_FRESH_SECONDS,
                "is_live": False,
                "observed_seconds_ago": None,
                "current": None,
            }

        end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
        observed_seconds_ago = max(0, int((now - end).total_seconds()))
        return {
            "server_time": utc_iso(now),
            "fresh_for_seconds": STATUS_FRESH_SECONDS,
            "is_live": observed_seconds_ago <= STATUS_FRESH_SECONDS,
            "observed_seconds_ago": observed_seconds_ago,
            "current": serialize_segment(row, analyzer.timezone),
        }

    @application.patch("/api/v1/segments/{segment_id}")
    def patch_segment(segment_id: int, correction: SegmentCorrection, _: None = Depends(require_auth)) -> dict[str, Any]:
        updated = database.correct_segment(segment_id, correction.category)
        if updated is None:
            raise HTTPException(status_code=404, detail="segment not found")
        return {"segment": serialize_segment(updated, analyzer.timezone)}

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/login", include_in_schema=False)
    def login_page() -> Response:
        return FileResponse(STATIC_DIR / "login.html")

    @application.post("/api/v1/auth/login")
    def login(payload: LoginRequest) -> Response:
        if not configured_token or not secrets.compare_digest(payload.token.encode(), configured_token.encode()):
            raise HTTPException(status_code=401, detail="Invalid token")
        response = Response(status_code=204)
        response.set_cookie("activity_token", configured_token, httponly=True,
                            secure=production, samesite="strict", max_age=604800)
        return response

    def token_redirect(path: str, token: str | None) -> Response | None:
        if production:
            return None
        if token and application.state.api_token and secrets.compare_digest(token, application.state.api_token):
            redirect = RedirectResponse(url=path, status_code=303)
            redirect.set_cookie("activity_token", token, httponly=True, samesite="lax")
            return redirect

    @application.get("/", include_in_schema=False)
    def index(request: Request, token: str | None = Query(default=None)) -> Response:
        if production:
            try:
                require_auth(request, request.cookies.get("activity_token"))
            except HTTPException:
                return RedirectResponse("/login", status_code=303)
        redirect = token_redirect("/", token)
        if redirect:
            return redirect
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/mobile", include_in_schema=False)
    def mobile(request: Request, token: str | None = Query(default=None)) -> Response:
        if production:
            try:
                require_auth(request, request.cookies.get("activity_token"))
            except HTTPException:
                return RedirectResponse("/login", status_code=303)
        redirect = token_redirect("/mobile", token)
        if redirect:
            return redirect
        return FileResponse(STATIC_DIR / "mobile.html")

    return application


app = create_app()
