from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
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
load_dotenv(BACKEND_DIR / ".env")


def create_app(
    db_path: str | Path | None = None,
    rules_path: str | Path | None = None,
    timezone_name: str | None = None,
) -> FastAPI:
    database = Database(db_path or os.getenv("ACTIVITYWATCH_DB_PATH", str(DEFAULT_DB)))
    analyzer = ActivityAnalyzer(
        database,
        rules_path or os.getenv("ACTIVITYWATCH_RULES_PATH", str(DEFAULT_RULES)),
        timezone_name or os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"),
    )

    application = FastAPI(
        title="行迹 Activity Timeline",
        version="0.1.0",
        description="Privacy-first Windows activity timeline demo",
    )
    application.state.database = database
    application.state.analyzer = analyzer

    @application.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "feature_windows": database.count("feature_windows"),
            "activity_segments": database.count("activity_segments"),
        }

    @application.post("/api/v1/events/batch")
    def ingest_batch(payload: BatchRequest) -> dict[str, Any]:
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
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        device_id: str | None = None,
    ) -> dict[str, Any]:
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), device_id)
        return {
            "date": day or datetime.now(analyzer.timezone).date().isoformat(),
            "timezone": str(analyzer.timezone),
            "segments": [serialize_segment(row, analyzer.timezone) for row in rows],
        }

    @application.get("/api/v1/summary/today")
    def summary_today(
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        device_id: str | None = None,
    ) -> dict[str, Any]:
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), device_id)
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
            for category in CATEGORIES
        ]
        return {"total_seconds": total, "categories": items}

    @application.patch("/api/v1/segments/{segment_id}")
    def patch_segment(segment_id: int, correction: SegmentCorrection) -> dict[str, Any]:
        updated = database.correct_segment(segment_id, correction.category)
        if updated is None:
            raise HTTPException(status_code=404, detail="segment not found")
        return {"segment": serialize_segment(updated, analyzer.timezone)}

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return application


app = create_app()
