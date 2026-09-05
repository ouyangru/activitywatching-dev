from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .database import Database, utc_iso


@dataclass
class Segment:
    start: datetime
    end: datetime
    category: str
    behavior: str
    description: str
    process: str
    window_title: str
    platform: str = "windows"
    purpose: str = "其他"
    window_count: int = 1
    key_count: int = 0
    mouse_click_count: int = 0
    scroll_count: int = 0
    interruptions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def merge(self, other: "Segment") -> None:
        self.end = max(self.end, other.end)
        self.window_count += other.window_count
        self.key_count += other.key_count
        self.mouse_click_count += other.mouse_click_count
        self.scroll_count += other.scroll_count
        self.interruptions.extend(other.interruptions)

    def as_record(self) -> dict[str, Any]:
        return {
            "start_time": utc_iso(self.start),
            "end_time": utc_iso(self.end),
            "category": self.category,
            "behavior": self.behavior,
            "description": self.description,
            "process": self.process,
            "window_title": self.window_title,
            "platform": self.platform,
            "purpose": self.purpose,
            "window_count": self.window_count,
            "key_count": self.key_count,
            "mouse_click_count": self.mouse_click_count,
            "scroll_count": self.scroll_count,
            "interruptions": self.interruptions,
        }


class ActivityAnalyzer:
    def __init__(self, database: Database, rules_path: str | Path, timezone_name: str = "Asia/Shanghai"):
        self.database = database
        self.timezone = ZoneInfo(timezone_name)
        with Path(rules_path).open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        self.idle_threshold_ms = int(config.get("idle_threshold_ms", 60_000))
        self.short_switch_seconds = float(config.get("short_switch_seconds", 15))
        self.rules = config.get("rules", [])

    def local_day_bounds(self, day: str | None = None) -> tuple[datetime, datetime]:
        if day:
            local_start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=self.timezone)
        else:
            now = datetime.now(self.timezone)
            local_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return local_start.astimezone(timezone.utc), (local_start + timedelta(days=1)).astimezone(timezone.utc)

    def day_for(self, value: datetime) -> str:
        return value.astimezone(self.timezone).date().isoformat()

    def rebuild_day(self, day: str, device_id: str) -> int:
        start, end = self.local_day_bounds(day)
        rows = self.database.rows_between("feature_windows", utc_iso(start), utc_iso(end), device_id)
        segments = self.build_segments(rows)
        return self.database.replace_segments(
            utc_iso(start), utc_iso(end), [segment.as_record() for segment in segments], device_id
        )

    def build_segments(self, rows: list[Any]) -> list[Segment]:
        raw = [self._classify(row) for row in rows]
        merged: list[Segment] = []
        for segment in raw:
            if merged and self._same_activity(merged[-1], segment) and self._is_contiguous(merged[-1], segment):
                merged[-1].merge(segment)
            else:
                merged.append(segment)
        return self._absorb_short_switches(merged)

    @staticmethod
    def _same_activity(left: Segment, right: Segment) -> bool:
        return (
            left.platform == right.platform
            and left.category == right.category
            and left.behavior == right.behavior
            and left.process == right.process
        )

    @staticmethod
    def _is_contiguous(left: Segment, right: Segment) -> bool:
        return (right.start - left.end).total_seconds() <= 2.5

    def _absorb_short_switches(self, segments: list[Segment]) -> list[Segment]:
        index = 1
        while index < len(segments) - 1:
            previous, brief, following = segments[index - 1 : index + 2]
            neighbors_match = self._same_activity(previous, following)
            no_large_gap = self._is_contiguous(previous, brief) and self._is_contiguous(brief, following)
            if brief.duration_seconds < self.short_switch_seconds and neighbors_match and no_large_gap:
                previous.interruptions.append(
                    {
                        "start_time": utc_iso(brief.start),
                        "end_time": utc_iso(brief.end),
                        "behavior": brief.behavior,
                        "process": brief.process,
                    }
                )
                previous.merge(brief)
                previous.merge(following)
                del segments[index : index + 2]
                index = max(1, index - 1)
            else:
                index += 1
        return segments

    def _classify(self, row: Any) -> Segment:
        start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        end = start + timedelta(milliseconds=row["duration_ms"])
        process = row["process"] or "Unknown"
        title = row["window_title"] or ""
        platform = row["platform"] or "windows"

        if process == "__screen_off__":
            category, behavior, description = "空闲", "手机锁屏", "手机屏幕关闭"
            purpose = "其他"
        elif row["idle_ms"] >= self.idle_threshold_ms:
            category, behavior, description = "空闲", "离开电脑", "暂时离开电脑"
            purpose = "其他"
        else:
            behavior = "使用手机" if platform == "android" else "使用电脑"
            category, description = "其他", self._default_description(process, title, platform)
            purpose = "其他"
            for rule in self.rules:
                match = rule.get("match", {})
                platform_ok = not match.get("platform") or match["platform"] == platform
                process_ok = not match.get("process") or re.search(match["process"], process, re.IGNORECASE)
                title_ok = not match.get("title") or re.search(match["title"], title, re.IGNORECASE)
                if platform_ok and process_ok and title_ok:
                    category = rule["category"]
                    behavior = rule["behavior"]
                    # purpose 独立于 category：规则可声明不同的目的层语义，
                    # 未声明时与 category 保持一致（兼容字段策略，见 plan.md §3.1）。
                    purpose = rule.get("purpose", rule["category"])
                    description = self._description(rule, process, title, platform)
                    break

        return Segment(
            start=start,
            end=end,
            category=category,
            behavior=behavior,
            description=description,
            process=process,
            window_title=title,
            platform=platform,
            purpose=purpose,
            key_count=row["key_count"],
            mouse_click_count=row["mouse_click_count"],
            scroll_count=row["scroll_count"],
        )

    @staticmethod
    def _default_description(process: str, title: str = "", platform: str = "windows") -> str:
        if platform == "android":
            return f"使用 {title or process}"
        clean = re.sub(r"\.exe$", "", process, flags=re.IGNORECASE)
        return f"使用 {clean}"

    @staticmethod
    def _description(rule: dict[str, Any], process: str, title: str, platform: str = "windows") -> str:
        template = rule.get("description", rule["behavior"])
        project = title.split(" - ")[0].strip() if " - " in title else ""
        if not project or project.lower() in {"visual studio code", "new tab"}:
            project = "项目"
        app = title if platform == "android" and title else re.sub(r"\.exe$", "", process, flags=re.IGNORECASE)
        return template.format(project=project[:80], title=title[:120], app=app)


def serialize_segment(row: Any, local_timezone: ZoneInfo) -> dict[str, Any]:
    result = dict(row)
    start = datetime.fromisoformat(result["start_time"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(result["end_time"].replace("Z", "+00:00"))
    result["duration_seconds"] = max(0, int((end - start).total_seconds()))
    result["start_time_local"] = start.astimezone(local_timezone).isoformat(timespec="seconds")
    result["end_time_local"] = end.astimezone(local_timezone).isoformat(timespec="seconds")
    result["interruptions"] = json.loads(result.pop("interruptions_json", "[]"))
    result["manual_override"] = bool(result["manual_override"])
    return result


FOCUS_CATEGORIES = {"学习", "工作"}
FOCUS_MERGE_GAP_SECONDS = 60.0
TOP_APPS_LIMIT = 8


def build_insights(rows: list[Any], local_timezone: ZoneInfo) -> dict[str, Any]:
    """Compute daily insights: app ranking, behavior ranking, focus streaks and switches.

    ``rows`` are raw activity_segments rows (any devices). Pure function, no DB access.
    """
    apps: dict[tuple[str, str], dict[str, Any]] = {}
    behaviors: dict[str, int] = {}
    purposes: dict[str, int] = {}
    interruptions_total = 0
    switches_total = 0
    interruption_sources: dict[str, int] = {}
    focus_runs: list[dict[str, Any]] = []
    current_run: dict[str, Any] | None = None
    previous_by_device: dict[str, str] = {}

    ordered = sorted(
        rows,
        key=lambda item: (item["start_time"], dict(item).get("device_id") or ""),
    )
    for raw_row in ordered:
        row = dict(raw_row)
        start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
        seconds = max(0, int((end - start).total_seconds()))
        process = row["process"] or "Unknown"
        platform = row["platform"] or "windows"
        behavior = row["behavior"]
        purpose = row.get("purpose") or "其他"
        category = row["category"]

        app = apps.setdefault(
            (process, platform),
            {"process": process, "platform": platform, "seconds": 0, "segment_count": 0, "category": category},
        )
        app["seconds"] += seconds
        app["segment_count"] += 1
        behaviors[behavior] = behaviors.get(behavior, 0) + seconds
        purposes[purpose] = purposes.get(purpose, 0) + seconds

        interruptions = json.loads(row["interruptions_json"] or "[]")
        interruptions_total += len(interruptions)
        for item in interruptions:
            source = item.get("process") or item.get("behavior") or "未知"
            interruption_sources[source] = interruption_sources.get(source, 0) + 1

        device_id = row.get("device_id") or ""
        if device_id:
            previous_behavior = previous_by_device.get(device_id)
            if previous_behavior is not None and previous_behavior != behavior:
                switches_total += 1
            previous_by_device[device_id] = behavior

        if category in FOCUS_CATEGORIES:
            if (
                current_run is not None
                and current_run["device_id"] == device_id
                and (start - current_run["end"]).total_seconds() <= FOCUS_MERGE_GAP_SECONDS
            ):
                current_run["end"] = max(current_run["end"], end)
            else:
                if current_run is not None:
                    focus_runs.append(current_run)
                current_run = {"start": start, "end": end, "device_id": device_id}
        else:
            if current_run is not None:
                focus_runs.append(current_run)
                current_run = None
    if current_run is not None:
        focus_runs.append(current_run)

    ranked_apps = sorted(apps.values(), key=lambda item: item["seconds"], reverse=True)
    total_seconds = sum(item["seconds"] for item in ranked_apps)
    for item in ranked_apps:
        item["share"] = round(item["seconds"] * 100 / total_seconds, 1) if total_seconds else 0
        item["duration_text"] = _duration_text(item["seconds"])

    focus_runs.sort(key=lambda run: (run["end"] - run["start"]).total_seconds(), reverse=True)
    longest = focus_runs[0] if focus_runs else None

    return {
        "apps": ranked_apps[:TOP_APPS_LIMIT],
        "behaviors": [
            {"behavior": behavior, "seconds": seconds, "duration_text": _duration_text(seconds)}
            for behavior, seconds in sorted(behaviors.items(), key=lambda item: item[1], reverse=True)
        ],
        "purposes": [
            {"purpose": purpose, "seconds": seconds, "duration_text": _duration_text(seconds)}
            for purpose, seconds in sorted(purposes.items(), key=lambda item: item[1], reverse=True)
        ],
        "focus": {
            "sessions": len(focus_runs),
            "longest_seconds": int((longest["end"] - longest["start"]).total_seconds()) if longest else 0,
            "longest_start_local": longest["start"].astimezone(local_timezone).isoformat(timespec="seconds") if longest else None,
            "longest_end_local": longest["end"].astimezone(local_timezone).isoformat(timespec="seconds") if longest else None,
        },
        "switches": {
            "behavior_changes": switches_total,
            "interruptions": interruptions_total,
            "top_sources": sorted(interruption_sources.items(), key=lambda item: item[1], reverse=True)[:5],
        },
    }


def _duration_text(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours and minutes:
        return f"{hours} 时 {minutes} 分"
    if hours:
        return f"{hours} 时"
    if minutes:
        return f"{minutes} 分"
    return f"{seconds} 秒"
