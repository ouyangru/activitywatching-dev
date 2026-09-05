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
        elif row["idle_ms"] >= self.idle_threshold_ms:
            category, behavior, description = "空闲", "离开电脑", "暂时离开电脑"
        else:
            behavior = "使用手机" if platform == "android" else "使用电脑"
            category, description = "其他", self._default_description(process, title, platform)
            for rule in self.rules:
                match = rule.get("match", {})
                platform_ok = not match.get("platform") or match["platform"] == platform
                process_ok = not match.get("process") or re.search(match["process"], process, re.IGNORECASE)
                title_ok = not match.get("title") or re.search(match["title"], title, re.IGNORECASE)
                if platform_ok and process_ok and title_ok:
                    category = rule["category"]
                    behavior = rule["behavior"]
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
