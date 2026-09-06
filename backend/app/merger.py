"""Cross-device primary activity merger.

Implements the deterministic priority model from plan.md §Phase 3:

    同一时间段内：
    1. 明确前台且有持续交互的设备活动
    2. 明确媒体播放中的活动
    3. 有前台但长时间无交互的活动
    4. 锁屏/息屏
    5. 无任何设备窗口

The module is pure: it takes activity segment rows and returns combined
segment records without touching the database. Original device segments are
never modified; the combined result is a derived view only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .database import utc_iso

# 交互频率（次/分钟）达到该值即视为“持续交互”档位
INTERACTION_ACTIVE_PER_MINUTE = 3.0
SCORE_INTERACTIVE_BASE = 1.0
SCORE_INTERACTIVE_BONUS_MAX = 0.5
SCORE_MEDIA = 0.75
SCORE_FOREGROUND_PASSIVE = 0.45
SCORE_IDLE = 0.15
SCORE_NO_DEVICE = 0.0

REASON_INTERACTIVE = "持续键鼠交互"
REASON_MEDIA = "媒体播放中"
REASON_PASSIVE = "前台但无交互"
REASON_IDLE = "锁屏或空闲"
REASON_NO_DEVICE = "无任何设备上报"
REASON_SOLO = "唯一活动设备"

MEDIA_BEHAVIORS = {"观看视频"}
IDLE_CATEGORIES = {"空闲"}
NO_DEVICE_CATEGORY = "无设备记录"


@dataclass
class _Active:
    """A device segment clipped to an atomic interval."""

    row: dict[str, Any]
    score: float
    reason: str

    @property
    def device_id(self) -> str:
        return self.row.get("device_id") or ""

    @property
    def platform(self) -> str:
        return self.row.get("platform") or "none"


@dataclass
class _Combined:
    start: datetime
    end: datetime
    main: _Active
    reason: str
    secondary: list[dict[str, Any]] = field(default_factory=list)
    overlap_seconds: float = 0.0

    def as_record(self) -> dict[str, Any]:
        row = self.main.row
        return {
            "start_time": utc_iso(self.start),
            "end_time": utc_iso(self.end),
            "main_device_id": self.main.device_id,
            "main_platform": self.main.platform,
            "category": row["category"],
            "purpose": row.get("purpose") or row["category"],
            "behavior": row["behavior"],
            "description": row["description"],
            "process": row.get("process") or "",
            "topic": row.get("topic", ""),
            "classification": row.get("classification"),
            "offline_annotation_id": row.get("offline_annotation_id"),
            "engagement_score": round(self.main.score, 3),
            "overlap_seconds": int(self.overlap_seconds),
            "secondary": self.secondary,
            "reason": self.reason,
        }


def _parse_row(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["_start"] = datetime.fromisoformat(record["start_time"].replace("Z", "+00:00"))
    record["_end"] = datetime.fromisoformat(record["end_time"].replace("Z", "+00:00"))
    return record


def _engagement(row: dict[str, Any]) -> tuple[float, str]:
    """Deterministic engagement score + human readable reason."""
    category = row.get("observation_category", row["category"])
    behavior = row["behavior"]
    if category == NO_DEVICE_CATEGORY or row.get("platform") == "none":
        return SCORE_NO_DEVICE, REASON_NO_DEVICE
    if category in IDLE_CATEGORIES:
        return SCORE_IDLE, REASON_IDLE

    seconds = max(1.0, (row["_end"] - row["_start"]).total_seconds())
    interactions = row.get("key_count", 0) + row.get("mouse_click_count", 0) + row.get("scroll_count", 0)
    per_minute = interactions * 60.0 / seconds
    if per_minute >= INTERACTION_ACTIVE_PER_MINUTE:
        bonus = min(per_minute / 60.0, 1.0) * SCORE_INTERACTIVE_BONUS_MAX
        return SCORE_INTERACTIVE_BASE + bonus, REASON_INTERACTIVE
    if behavior in MEDIA_BEHAVIORS:
        return SCORE_MEDIA, REASON_MEDIA
    return SCORE_FOREGROUND_PASSIVE, REASON_PASSIVE


def _rank_key(active: _Active) -> tuple[float, int, int, str]:
    """Sort key: score desc, then windows-first, then longer segment, then stable id."""
    row = active.row
    duration = (row["_end"] - row["_start"]).total_seconds()
    platform_rank = 1 if active.platform == "windows" else (2 if active.platform == "android" else 3)
    return (-active.score, platform_rank, -duration, row.get("device_id") or "")


def _activity_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Identity of a device activity for merging adjacent atomic intervals."""
    return (
        row.get("device_id") or "",
        row["category"],
        row["behavior"],
        row.get("process") or "",
        row.get("purpose") or "",
        row.get("topic") or "",
        row.get("description") or "",
        str(row.get("classification") or ""),
        str(row.get("offline_annotation_id") or ""),
    )


def combine_segments(rows: list[Any]) -> list[dict[str, Any]]:
    """Merge per-device activity segments into cross-device primary activity segments.

    The day's covered time is split into atomic intervals at every segment
    boundary; each interval elects one primary activity by engagement score.
    Adjacent intervals electing the same source segment are merged back.
    """
    parsed = [_parse_row(row) for row in rows]
    parsed = [row for row in parsed if row["_end"] > row["_start"]]
    if not parsed:
        return []

    boundaries: set[datetime] = set()
    for row in parsed:
        boundaries.add(row["_start"])
        boundaries.add(row["_end"])
    points = sorted(boundaries)

    actives_by_row_id: dict[int, _Active] = {}
    combined: list[_Combined] = []

    for interval_start, interval_end in zip(points, points[1:]):
        overlapping = [row for row in parsed if row["_start"] < interval_end and row["_end"] > interval_start]
        if not overlapping:
            continue
        active_list: list[_Active] = []
        for row in overlapping:
            row_id = id(row)
            if row_id not in actives_by_row_id:
                score, reason = _engagement(row)
                actives_by_row_id[row_id] = _Active(row=row, score=score, reason=reason)
            active_list.append(actives_by_row_id[row_id])

        real_devices = [item for item in active_list if item.platform in {"windows", "android"}]
        winner = min(active_list, key=_rank_key)
        others = [item for item in real_devices if item is not winner]

        piece_reason = winner.reason
        if len(real_devices) == 1 and winner.reason == REASON_PASSIVE:
            piece_reason = REASON_SOLO

        piece = _Combined(start=interval_start, end=interval_end, main=winner, reason=piece_reason)
        if others:
            piece.overlap_seconds = (interval_end - interval_start).total_seconds()
            piece.secondary = [
                {
                    "device_id": item.device_id,
                    "platform": item.platform,
                    "category": item.row["category"],
                    "behavior": item.row["behavior"],
                    "description": item.row["description"],
                    "process": item.row.get("process") or "",
                }
                for item in others
            ]

        previous = combined[-1] if combined else None
        if (
            previous is not None
            and _activity_key(previous.main.row) == _activity_key(winner.row)
            and previous.end == interval_start
            and previous.reason == piece.reason
        ):
            previous.end = interval_end
            previous.overlap_seconds += piece.overlap_seconds
            existing = {(item["device_id"], item["behavior"]) for item in previous.secondary}
            for item in piece.secondary:
                if (item["device_id"], item["behavior"]) not in existing:
                    previous.secondary.append(item)
                    existing.add((item["device_id"], item["behavior"]))
        else:
            combined.append(piece)

    return [piece.as_record() for piece in combined if piece.end > piece.start]
