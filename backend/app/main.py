from __future__ import annotations

import json
import logging
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

from .agent import AgentService
from .analyzer import ActivityAnalyzer, build_insights, serialize_segment
from .database import Database, utc_iso
from .merger import combine_segments
from .schemas import AgentMemoryRequest, BatchRequest, HeartbeatRequest, SegmentCorrection
from .summarizer import DailySummarizer


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


class AgentEnrichRequest(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


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
        "purpose": "未知",
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


def _merge_overlap_seconds(rows: list[Any], dimension: str) -> dict[str, int]:
    """按时间区间合并跨设备重叠段后再累计时长，避免多设备并行时重复计算。

    同一维度的相邻重叠段直接延长；不同维度的重叠按「先开始者优先」切分。
    """
    intervals: list[tuple[datetime, datetime, str]] = []
    for row in rows:
        row_start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        row_end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
        if row_end > row_start:
            intervals.append((row_start, row_end, row[dimension]))
    intervals.sort(key=lambda item: (item[0], item[1]))
    merged: list[tuple[datetime, datetime, str]] = []
    for start, end, key in intervals:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end, prev_key = merged[-1]
            if key == prev_key:
                merged[-1] = (prev_start, max(prev_end, end), key)
            else:
                overlap_end = min(prev_end, end)
                if overlap_end > start:
                    merged[-1] = (prev_start, overlap_end, prev_key)
                    merged.append((overlap_end, max(prev_end, end), key if end > prev_end else prev_key))
                else:
                    merged.append((start, end, key))
        else:
            merged.append((start, end, key))
    seconds: dict[str, int] = defaultdict(int)
    for start, end, key in merged:
        seconds[key] += max(0, int((end - start).total_seconds()))
    return seconds


def create_app(
    db_path: str | Path | None = None,
    rules_path: str | Path | None = None,
    timezone_name: str | None = None,
    api_token: str | None = None,
    agent_llm=None,
    summarizer_llm=None,
) -> FastAPI:
    # 让 activitywatch.* 的 INFO 日志（agent/summarizer 输入输出）落到 stderr/journalctl
    logging.basicConfig(
        level=os.getenv("ACTIVITYWATCH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
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
    agent = AgentService(
        database,
        timezone_name or os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"),
        llm=agent_llm,
    )
    summarizer = DailySummarizer(
        database,
        agent,
        timezone_name or os.getenv("ACTIVITYWATCH_TIMEZONE", "Asia/Shanghai"),
        llm=summarizer_llm,
    )

    application = FastAPI(
        title="行迹 Activity Timeline",
        version="0.4.0",
        description="Privacy-first Windows and Android activity timeline",
    )
    application.state.database = database
    application.state.analyzer = analyzer
    application.state.agent = agent
    application.state.summarizer = summarizer
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
        combined_rebuilt = 0
        for day in sorted({day for day, _ in affected}):
            combined_rebuilt += database.replace_combined_segments(day, _build_combined(analyzer, database, day))
            # Agent ① 异步增强：只投递任务，绝不阻塞写入（规则底账已就绪）
            agent.request_enrich(day)
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "segments_rebuilt": rebuilt,
            "combined_rebuilt": combined_rebuilt,
        }

    def _day_segments_with_gaps(day: str, device_id: str | None = None) -> list[Any]:
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), device_id)
        coverage_rows = database.rows_between("feature_windows", utc_iso(start), utc_iso(end), device_id)
        return _with_no_device_periods(rows, coverage_rows, start, end)

    def _build_combined(analyzer: ActivityAnalyzer, database: Database, day: str) -> list[dict[str, Any]]:
        """Derive cross-device primary segments for one day; original rows stay untouched.

        Agent evidence is applied to the per-device rows BEFORE merging, so the
        combined timeline inherits agent semantics (behavior/purpose/topic).
        """
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), None)
        coverage_rows = database.rows_between("feature_windows", utc_iso(start), utc_iso(end), None)
        rows = _with_no_device_periods(rows, coverage_rows, start, end)
        return combine_segments(agent.apply_evidence(rows))

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
        rows = agent.apply_evidence(rows)
        return {
            "date": day or datetime.now(analyzer.timezone).date().isoformat(),
            "timezone": str(analyzer.timezone),
            "segments": [serialize_segment(row, analyzer.timezone) for row in rows],
        }

    @application.get("/api/v1/timeline/combined")
    def timeline_combined(
        _: None = Depends(require_auth),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        """Cross-device primary activity view (plan.md Phase 3).

        Original device segments are not modified; this is a derived timeline.
        """
        effective_day = day or datetime.now(analyzer.timezone).date().isoformat()
        rows = database.combined_for_day(effective_day)
        if not rows and day is None:
            # 今天的数据可能由更早的 ingest 生成，允许惰性重建一次
            database.replace_combined_segments(effective_day, _build_combined(analyzer, database, effective_day))
            rows = database.combined_for_day(effective_day)
        segments = []
        for row in rows:
            item = dict(row)
            start = datetime.fromisoformat(item["start_time"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(item["end_time"].replace("Z", "+00:00"))
            item["duration_seconds"] = max(0, int((end - start).total_seconds()))
            item["start_time_local"] = start.astimezone(analyzer.timezone).isoformat(timespec="seconds")
            item["end_time_local"] = end.astimezone(analyzer.timezone).isoformat(timespec="seconds")
            item["secondary"] = json.loads(item.pop("secondary_json", "[]"))
            segments.append(item)
        return {
            "date": effective_day,
            "timezone": str(analyzer.timezone),
            "segments": segments,
        }

    @application.get("/api/v1/summary/today")
    def summary_today(
        _: None = Depends(require_auth),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        device_id: str | None = None,
        dimension: str = Query(default="category", pattern=r"^(category|purpose|behavior)$"),
    ) -> dict[str, Any]:
        start, end = analyzer.local_day_bounds(day)
        rows = database.rows_between("activity_segments", utc_iso(start), utc_iso(end), device_id)
        coverage_rows = database.rows_between("feature_windows", utc_iso(start), utc_iso(end), device_id)
        rows = _with_no_device_periods(rows, coverage_rows, start, end)
        rows = agent.apply_evidence(rows)
        # 按时间区间合并跨设备重叠段后再累计，避免多设备并行时重复计算
        seconds = _merge_overlap_seconds(rows, dimension)
        total = sum(seconds.values())
        ordered_keys = list(SUMMARY_CATEGORIES)
        if dimension != "category":
            ordered_keys = []
        ordered_keys += [key for key in sorted(seconds) if key not in ordered_keys]
        items = [
            {
                "dimension": dimension,
                "category": key,
                "seconds": seconds[key],
                "percent": round(seconds[key] * 100 / total, 1) if total else 0,
            }
            for key in ordered_keys
        ]
        return {"total_seconds": total, "dimension": dimension, "categories": items}

    @application.get("/api/v1/insights/today")
    def insights_today(
        _: None = Depends(require_auth),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """App ranking, behavior ranking, focus streaks and switch stats (plan.md Phase 5)."""
        effective_day = day or datetime.now(analyzer.timezone).date().isoformat()
        rows = _day_segments_with_gaps(effective_day, device_id)
        rows = agent.apply_evidence(rows)
        rows = [row for row in rows if row["category"] != NO_DEVICE_CATEGORY]
        return {
            "date": effective_day,
            "timezone": str(analyzer.timezone),
            **build_insights(rows, analyzer.timezone),
        }

    @application.get("/api/v1/daily/report")
    def daily_report(
        _: None = Depends(require_auth),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        """One-stop daily report payload for /daily page."""
        effective_day = day or datetime.now(analyzer.timezone).date().isoformat()
        rows = _day_segments_with_gaps(effective_day, None)
        rows = agent.apply_evidence(rows)
        summary_seconds = _merge_overlap_seconds(rows, "category")
        total = sum(summary_seconds.values())

        database.replace_combined_segments(effective_day, _build_combined(analyzer, database, effective_day))
        combined_rows = database.combined_for_day(effective_day)
        combined_segments = []
        for row in combined_rows:
            item = dict(row)
            start = datetime.fromisoformat(item["start_time"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(item["end_time"].replace("Z", "+00:00"))
            item["duration_seconds"] = max(0, int((end - start).total_seconds()))
            item["start_time_local"] = start.astimezone(analyzer.timezone).isoformat(timespec="seconds")
            item["end_time_local"] = end.astimezone(analyzer.timezone).isoformat(timespec="seconds")
            item["secondary"] = json.loads(item.pop("secondary_json", "[]"))
            combined_segments.append(item)

        insights = build_insights([row for row in rows if row["category"] != NO_DEVICE_CATEGORY], analyzer.timezone)

        def _narrative_payload() -> dict[str, Any]:
            return {
                "summary": [
                    {"category": category, "seconds": summary_seconds.get(category, 0)}
                    for category in SUMMARY_CATEGORIES
                ],
                "combined_segments": combined_segments,
                "insights": insights,
            }

        # Agent ② 日报：读缓存，过期时后台重生成，请求本身永不等待 LLM
        narrative = (
            summarizer.narrative_for(effective_day, rows, _narrative_payload)
            if rows
            else None
        )
        return {
            "date": effective_day,
            "timezone": str(analyzer.timezone),
            "total_seconds": total,
            "summary": [
                {"category": category, "seconds": summary_seconds.get(category, 0)}
                for category in SUMMARY_CATEGORIES
            ],
            "combined_segments": combined_segments,
            "insights": insights,
            "narrative": narrative,
            "memories": [
                {
                    "kind": row["kind"],
                    "scope": row["scope"],
                    "content": row["content"],
                    "source": row["source"],
                    "hit_count": row["hit_count"],
                }
                for row in database.active_memories()
            ],
        }

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
        current = agent.apply_evidence([dict(row)])[0]
        return {
            "server_time": utc_iso(now),
            "fresh_for_seconds": STATUS_FRESH_SECONDS,
            "is_live": observed_seconds_ago <= STATUS_FRESH_SECONDS,
            "observed_seconds_ago": observed_seconds_ago,
            "current": serialize_segment(current, analyzer.timezone),
        }

    @application.patch("/api/v1/segments/{segment_id}")
    def patch_segment(segment_id: int, correction: SegmentCorrection, _: None = Depends(require_auth)) -> dict[str, Any]:
        updated = database.correct_segment(segment_id, correction.category, correction.purpose)
        if updated is None:
            raise HTTPException(status_code=404, detail="segment not found or empty correction")
        # 「以后都这样」：从单次纠正归纳出该应用的一般性记忆（新纠正永远赢，旧的 superseded）
        if correction.remember:
            process = (updated.get("process") or "").strip()
            if process:
                category = correction.category or updated["category"]
                content = f"用户纠正：{process} 的活动应归类为「{category}」"
                if correction.purpose:
                    content += f"（目的：{correction.purpose}）"
                if correction.memory_note:
                    content += f"。备注：{correction.memory_note}"
                database.supersede_memories(process, "correction", category)
                database.add_memory(
                    {
                        "kind": "correction",
                        "scope": process,
                        "category": category,
                        "content": content,
                        "source": "correction",
                        "confidence": 1.0,
                    }
                )
        return {"segment": serialize_segment(updated, analyzer.timezone)}

    @application.post("/api/v1/heartbeat")
    def report_heartbeat(payload: HeartbeatRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        """Collector heartbeat (plan.md Phase 2): liveness + collection quality metadata."""
        now = database.upsert_heartbeat(payload.device_id, payload.platform, payload.collector_version)
        return {"device_id": payload.device_id, "accepted_at": now, "online_threshold_seconds": 120}

    @application.get("/api/v1/agent/status")
    def agent_status(_: None = Depends(require_auth)) -> dict[str, Any]:
        """Agent 层配置与覆盖情况（Agent 未配置时全部接口照常，仅此处为 disabled）。"""
        return {
            "enabled": agent.enabled,
            "model": agent.model_name if agent.enabled else None,
            "confidence_threshold": agent.confidence_threshold,
            "evidence_count": database.count("classification_evidence"),
            "memory_count": database.count("agent_memory"),
        }

    @application.post("/api/v1/agent/enrich")
    def agent_enrich(payload: AgentEnrichRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        """同步触发某天的 Agent ① 增强（调试/演示用；常规由 ingest 自动触发）。"""
        return agent.enrich_day(payload.day)

    @application.post("/api/v1/agent/evidence/{digest}/revoke")
    def agent_evidence_revoke(digest: str, _: None = Depends(require_auth)) -> dict[str, Any]:
        """撤销一条 Agent 判断：对应片段立即回退规则值（plan.md：可撤销、不静默覆盖）。"""
        if not database.revoke_evidence(digest):
            raise HTTPException(status_code=404, detail="evidence not found")
        return {"digest": digest, "revoked": True}

    @application.post("/api/v1/agent/summary/{day}")
    def agent_summary_refresh(day: str, _: None = Depends(require_auth)) -> dict[str, Any]:
        """同步重新生成某天日报叙述（Agent ②）。失败时返回空 narrative。"""
        rows = agent.apply_evidence(_day_segments_with_gaps(day, None))
        database.replace_combined_segments(day, _build_combined(analyzer, database, day))
        combined = []
        for row in database.combined_for_day(day):
            item = dict(row)
            start = datetime.fromisoformat(item["start_time"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(item["end_time"].replace("Z", "+00:00"))
            item["duration_seconds"] = max(0, int((end - start).total_seconds()))
            item["start_time_local"] = start.astimezone(analyzer.timezone).isoformat(timespec="seconds")
            item["end_time_local"] = end.astimezone(analyzer.timezone).isoformat(timespec="seconds")
            item["secondary"] = json.loads(item.pop("secondary_json", "[]"))
            combined.append(item)
        summary_seconds: dict[str, int] = defaultdict(int)
        for row in rows:
            row_start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
            row_end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
            summary_seconds[row["category"]] += max(0, int((row_end - row_start).total_seconds()))
        insights = build_insights([row for row in rows if row["category"] != NO_DEVICE_CATEGORY], analyzer.timezone)
        payload = {
            "summary": [{"category": category, "seconds": summary_seconds.get(category, 0)} for category in SUMMARY_CATEGORIES],
            "combined_segments": combined,
            "insights": insights,
        }
        narrative = summarizer.refresh(day, summarizer.version_for(rows), payload)
        return {"day": day, "narrative": narrative, "source": "agent" if narrative else None}

    @application.get("/api/v1/agent/memory")
    def agent_memory_list(_: None = Depends(require_auth)) -> dict[str, Any]:
        """长期记忆透明展示：全部记忆（含 superseded/stale，按状态分组）。"""
        memories = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "scope": row["scope"],
                "category": row["category"],
                "content": row["content"],
                "source": row["source"],
                "confidence": round(float(row["confidence"]), 2),
                "status": row["status"],
                "hit_count": row["hit_count"],
                "last_seen_at": row["last_seen_at"],
                "created_at": row["created_at"],
            }
            for row in database.list_memories()
        ]
        return {
            "active": [item for item in memories if item["status"] == "active"],
            "archived": [item for item in memories if item["status"] != "active"],
        }

    @application.post("/api/v1/agent/memory")
    def agent_memory_add(payload: AgentMemoryRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
        """手动添加长期记忆（如"我最近在赶毕业设计 mini-nccl"）。"""
        memory_id = database.add_memory(
            {
                "kind": payload.kind,
                "scope": payload.scope,
                "category": "",
                "content": payload.content,
                "source": "manual",
                "confidence": payload.confidence,
            }
        )
        return {"id": memory_id, "scope": payload.scope.lower(), "content": payload.content}

    @application.delete("/api/v1/agent/memory/{memory_id}")
    def agent_memory_delete(memory_id: int, _: None = Depends(require_auth)) -> dict[str, Any]:
        """删除单条记忆（隐私：记忆可导出、可单删、可清空重建）。"""
        if not database.delete_memory(memory_id):
            raise HTTPException(status_code=404, detail="memory not found")
        return {"id": memory_id, "deleted": True}

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

    @application.get("/daily", include_in_schema=False)
    def daily(request: Request, token: str | None = Query(default=None)) -> Response:
        if production:
            try:
                require_auth(request, request.cookies.get("activity_token"))
            except HTTPException:
                return RedirectResponse("/login", status_code=303)
        redirect = token_redirect("/daily", token)
        if redirect:
            return redirect
        return FileResponse(STATIC_DIR / "daily.html")

    return application


app = create_app()
