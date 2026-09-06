"""Human interval annotations and time-of-day evidence, applied only to idle/gaps.

Never writes device observations. Splitting preserves time coverage and the sum
of interaction counters. Habits are explicit user facts, not inferred memories.
"""
from datetime import datetime, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from .activities import NO_DEVICE_CATEGORY
from .database import Database, utc_iso


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def day_type(value: datetime) -> str:
    return "weekday" if value.weekday() < 5 else "weekend"


def habit_patterns(start: datetime, end: datetime, timezone: ZoneInfo) -> list[dict]:
    cursor = start.astimezone(timezone)
    end = end.astimezone(timezone)
    patterns = []
    while cursor < end:
        midnight = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        stop = min(end, midnight + timedelta(days=1))
        patterns.append({"day_type": day_type(cursor),
                         "start_minute": cursor.hour * 60 + cursor.minute,
                         "end_minute": 1440 if stop.date() != cursor.date() else stop.hour * 60 + stop.minute})
        cursor = stop
    return patterns


def apply_offline(database: Database, rows: list, timezone: ZoneInfo) -> list[dict]:
    if not rows:
        return []
    annotations = database.offline_annotations(min(row["start_time"] for row in rows), max(row["end_time"] for row in rows))
    memories = database.offline_memories()
    result = []
    for source in rows:
        row = dict(source)
        if row.get("_offline_context") or row.get("manual_override") or row["category"] not in {"空闲", NO_DEVICE_CATEGORY}:
            result.append(row)
            continue
        start, end = parse(row["start_time"]), parse(row["end_time"])
        if end <= start:
            result.append(row)
            continue
        relevant = [item for item in annotations if parse(item["start_time"]) < end and parse(item["end_time"]) > start]
        if not relevant and not memories:
            result.append(row)
            continue
        boundaries = {start, end}
        for item in relevant:
            boundaries.update((max(start, parse(item["start_time"])), min(end, parse(item["end_time"]))))
        local_day = start.astimezone(timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        while local_day < end:
            for hour in range(25):
                point = local_day + timedelta(hours=hour)
                if start < point < end:
                    boundaries.add(point)
            for memory in memories:
                context = json.loads(memory["context_json"])
                if context.get("day_type") == day_type(local_day):
                    for key in ("start_minute", "end_minute"):
                        point = local_day + timedelta(minutes=context[key])
                        if start < point < end:
                            boundaries.add(point)
            local_day += timedelta(days=1)
        points = sorted(boundaries)
        for left, right in zip(points, points[1:]):
            piece = dict(row)
            piece.update(start_time=utc_iso(left), end_time=utc_iso(right), observation_category=row["category"])
            if len(points) > 2:
                piece.update(id=None, source_segment_id=row.get("id"))
            # Allocate counts by cumulative fractions so no interactions are invented or lost.
            total = (end - start).total_seconds()
            for key in ("window_count", "key_count", "mouse_click_count", "scroll_count"):
                count = row.get(key, 0) or 0
                piece[key] = round(count * (right - start).total_seconds() / total) - round(count * (left - start).total_seconds() / total)
            piece['interruptions_json'] = json.dumps([
                item for item in json.loads(row.get('interruptions_json') or '[]')
                if left <= parse(item['start_time']) < right
            ], ensure_ascii=False)
            local = left.astimezone(timezone)
            minutes = local.hour * 60 + local.minute
            stop_minutes = minutes + (right - left).total_seconds() / 60
            matches = []
            for memory in memories:
                context = json.loads(memory["context_json"])
                if context.get("day_type") == day_type(local) and context.get("start_minute", 1440) <= minutes and context.get("end_minute", 0) >= stop_minutes:
                    matches.append(memory)
            # Contradictory overlapping habits are not enough to make an automatic judgment.
            if len({item["category"] for item in matches}) != 1:
                matches = []
            context = {"day_type": day_type(local), "start_hour": local.hour,
                       "duration_minutes": round((right - left).total_seconds() / 60, 1),
                       "observation": row["category"], "known_facts": [item["content"] for item in matches]}
            key = f"offline|{row.get('platform')}|{row.get('device_id')}|{piece['start_time']}|{piece['end_time']}"
            piece["_offline_digest"] = hashlib.sha256(key.encode()).hexdigest()
            piece["_offline_context"] = context
            piece["_offline_allowed_categories"] = list({item["category"] for item in matches})
            confirmed = [item for item in relevant if parse(item["start_time"]) <= left and parse(item["end_time"]) >= right]
            if confirmed:
                annotation = confirmed[-1]
                piece.update(category=annotation["category"], behavior=annotation["category"], purpose="生活事务",
                             description=f"人工确认：{annotation['category']}", manual_override=1,
                             offline_annotation_id=annotation["id"], classification={"source": "manual", "confidence": 1.0})
            result.append(piece)
    return result
