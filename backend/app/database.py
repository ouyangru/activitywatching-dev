from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .schemas import FeatureWindow


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS feature_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'windows',
    sequence INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    process TEXT NOT NULL,
    window_title TEXT NOT NULL,
    key_count INTEGER NOT NULL DEFAULT 0,
    mouse_click_count INTEGER NOT NULL DEFAULT 0,
    scroll_count INTEGER NOT NULL DEFAULT 0,
    idle_ms INTEGER NOT NULL DEFAULT 0,
    clipboard_copy_count INTEGER NOT NULL DEFAULT 0,
    clipboard_paste_count INTEGER NOT NULL DEFAULT 0,
    clipboard_events_json TEXT NOT NULL DEFAULT '[]',
    received_at TEXT NOT NULL,
    UNIQUE(device_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_feature_windows_time
ON feature_windows(start_time);

CREATE TABLE IF NOT EXISTS activity_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'windows',
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    category TEXT NOT NULL,
    base_category TEXT NOT NULL,
    behavior TEXT NOT NULL,
    description TEXT NOT NULL,
    process TEXT NOT NULL,
    window_title TEXT NOT NULL,
    window_count INTEGER NOT NULL,
    key_count INTEGER NOT NULL,
    mouse_click_count INTEGER NOT NULL,
    scroll_count INTEGER NOT NULL,
    interruptions_json TEXT NOT NULL DEFAULT '[]',
    manual_override INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_segments_time
ON activity_segments(start_time, end_time);

CREATE TABLE IF NOT EXISTS combined_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    main_device_id TEXT NOT NULL DEFAULT '',
    main_platform TEXT NOT NULL DEFAULT 'none',
    category TEXT NOT NULL,
    purpose TEXT NOT NULL,
    behavior TEXT NOT NULL,
    description TEXT NOT NULL,
    process TEXT NOT NULL DEFAULT '',
    engagement_score REAL NOT NULL DEFAULT 0,
    overlap_seconds INTEGER NOT NULL DEFAULT 0,
    secondary_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_combined_segments_day
ON combined_segments(day, start_time);
CREATE TABLE IF NOT EXISTS collector_heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL DEFAULT 'windows',
    collector_version TEXT NOT NULL DEFAULT '',
    last_heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER,
    device_id TEXT NOT NULL,
    segment_start_time TEXT NOT NULL,
    segment_end_time TEXT NOT NULL,
    previous_category TEXT NOT NULL,
    new_category TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classification_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    process TEXT NOT NULL,
    title_summary TEXT NOT NULL DEFAULT '',
    behavior TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    explanation TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_json TEXT NOT NULL DEFAULT '{}',
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_classification_evidence_digest
ON classification_evidence(digest);

CREATE TABLE IF NOT EXISTS daily_summaries (
    day TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    narrative TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'project_fact',
    scope TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_scope
ON agent_memory(scope, status);

CREATE TABLE IF NOT EXISTS offline_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    category TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_column(connection, "feature_windows", "platform", "TEXT NOT NULL DEFAULT 'windows'")
            self._ensure_column(connection, "activity_segments", "platform", "TEXT NOT NULL DEFAULT 'windows'")
            self._ensure_column(connection, "activity_segments", "purpose", "TEXT NOT NULL DEFAULT '其他'")
            self._ensure_column(connection, "combined_segments", "topic", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "classification_evidence", "hit_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "classification_evidence", "context_version", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "agent_memory", "context_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(connection, "agent_memory", "annotation_id", "INTEGER")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def insert_windows(self, events: list[FeatureWindow]) -> tuple[int, int]:
        accepted = 0
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            for event in events:
                interaction = event.interaction
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO feature_windows (
                        device_id, platform, sequence, start_time, duration_ms, process, window_title,
                        key_count, mouse_click_count, scroll_count, idle_ms,
                        clipboard_copy_count, clipboard_paste_count, clipboard_events_json,
                        received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.device_id,
                        event.platform,
                        event.sequence,
                        utc_iso(event.start_time),
                        event.duration_ms,
                        event.context.process,
                        event.context.window_title,
                        interaction.key_count,
                        interaction.mouse_click_count,
                        interaction.scroll_count,
                        interaction.idle_ms,
                        interaction.clipboard_copy_count,
                        interaction.clipboard_paste_count,
                        json.dumps(
                            [item.model_dump(mode="json") for item in interaction.clipboard_events],
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                accepted += int(cursor.rowcount == 1)
        return accepted, len(events) - accepted

    def rows_between(self, table: str, start: str, end: str, device_id: str | None = None) -> list[sqlite3.Row]:
        if table not in {"feature_windows", "activity_segments"}:
            raise ValueError("unsupported table")
        where = "start_time >= ? AND start_time < ?"
        params: list[Any] = [start, end]
        if device_id:
            where += " AND device_id = ?"
            params.append(device_id)
        order = "start_time ASC, sequence ASC" if table == "feature_windows" else "start_time ASC"
        with self.connect() as connection:
            return list(connection.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order}", params))

    def replace_segments(self, start: str, end: str, segments: list[dict[str, Any]], device_id: str) -> int:
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            old_manual = list(
                connection.execute(
                    """SELECT * FROM activity_segments
                    WHERE device_id = ? AND start_time >= ? AND start_time < ? AND manual_override = 1""",
                    (device_id, start, end),
                )
            )
            connection.execute(
                "DELETE FROM activity_segments WHERE device_id = ? AND start_time >= ? AND start_time < ?",
                (device_id, start, end),
            )
            for segment in segments:
                corrected = self._matching_manual(segment, old_manual)
                category = corrected["category"] if corrected else segment["category"]
                connection.execute(
                    """
                    INSERT INTO activity_segments (
                        device_id, platform, start_time, end_time, category, base_category, behavior,
                        description, process, window_title, window_count, key_count,
                        mouse_click_count, scroll_count, interruptions_json, manual_override,
                        purpose, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        segment["platform"],
                        segment["start_time"],
                        segment["end_time"],
                        category,
                        segment["category"],
                        segment["behavior"],
                        segment["description"],
                        segment["process"],
                        segment["window_title"],
                        segment["window_count"],
                        segment["key_count"],
                        segment["mouse_click_count"],
                        segment["scroll_count"],
                        json.dumps(segment["interruptions"], ensure_ascii=False),
                        int(corrected is not None),
                        corrected["purpose"] if corrected else segment.get("purpose", "其他"),
                        now,
                    ),
                )
        return len(segments)

    @staticmethod
    def _matching_manual(segment: dict[str, Any], old_rows: list[sqlite3.Row]) -> sqlite3.Row | None:
        new_start = datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
        new_end = datetime.fromisoformat(segment["end_time"].replace("Z", "+00:00"))
        best: tuple[float, sqlite3.Row] | None = None
        for row in old_rows:
            if row["behavior"] != segment["behavior"]:
                continue
            old_start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
            old_end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
            overlap = max(0.0, (min(new_end, old_end) - max(new_start, old_start)).total_seconds())
            old_duration = max(0.001, (old_end - old_start).total_seconds())
            score = overlap / old_duration
            if score >= 0.5 and (best is None or score > best[0]):
                best = (score, row)
        return best[1] if best else None

    def correct_segment(
        self, segment_id: int, new_category: str | None = None, new_purpose: str | None = None
    ) -> dict[str, Any] | None:
        if new_category is None and new_purpose is None:
            return None
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            row = connection.execute("SELECT * FROM activity_segments WHERE id = ?", (segment_id,)).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                INSERT INTO user_corrections (
                    segment_id, device_id, segment_start_time, segment_end_time,
                    previous_category, new_category, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    row["device_id"],
                    row["start_time"],
                    row["end_time"],
                    row["category"],
                    new_purpose if new_category is None else new_category,
                    now,
                ),
            )
            connection.execute(
                "UPDATE activity_segments SET category = COALESCE(?, category), purpose = COALESCE(?, purpose), manual_override = 1, updated_at = ? WHERE id = ?",
                (new_category, new_purpose, now, segment_id),
            )
            updated = dict(row)
            updated.update(
                category=new_category or row["category"],
                purpose=new_purpose or row["purpose"],
                manual_override=1,
                updated_at=now,
            )
            return updated

    def count(self, table: str) -> int:
        if table not in {
            "feature_windows",
            "activity_segments",
            "user_corrections",
            "combined_segments",
            "classification_evidence",
            "agent_memory",
        }:
            raise ValueError("unsupported table")
        with self.connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def replace_combined_segments(self, day: str, segments: list[dict[str, Any]]) -> int:
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            connection.execute("DELETE FROM combined_segments WHERE day = ?", (day,))
            for segment in segments:
                connection.execute(
                    """
                    INSERT INTO combined_segments (
                        day, start_time, end_time, main_device_id, main_platform, category, purpose,
                        behavior, description, process, engagement_score, overlap_seconds,
                        secondary_json, reason, topic, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        day,
                        segment["start_time"],
                        segment["end_time"],
                        segment.get("main_device_id", ""),
                        segment.get("main_platform", "none"),
                        segment["category"],
                        segment.get("purpose", "其他"),
                        segment.get("behavior", ""),
                        segment.get("description", ""),
                        segment.get("process", ""),
                        segment.get("engagement_score", 0.0),
                        int(segment.get("overlap_seconds", 0)),
                        json.dumps(segment.get("secondary", []), ensure_ascii=False),
                        segment.get("reason", ""),
                        segment.get("topic", ""),
                        now,
                    ),
                )
        return len(segments)

    def combined_for_day(self, day: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM combined_segments WHERE day = ? ORDER BY start_time ASC",
                    (day,),
                )
            )

    def upsert_heartbeat(self, device_id: str, platform: str, collector_version: str) -> str:
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO collector_heartbeats (device_id, platform, collector_version, last_heartbeat_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    platform = excluded.platform,
                    collector_version = excluded.collector_version,
                    last_heartbeat_at = excluded.last_heartbeat_at
                """,
                (device_id, platform, collector_version, now),
            )
        return now

    def list_devices(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT f.device_id, f.platform, MAX(f.start_time) AS last_seen, COUNT(*) AS window_count,
                       h.collector_version, h.last_heartbeat_at
                FROM feature_windows f
                LEFT JOIN collector_heartbeats h ON h.device_id = f.device_id
                GROUP BY f.device_id, f.platform, h.collector_version, h.last_heartbeat_at
                ORDER BY last_seen DESC
                """
            )
            devices = []
            for row in rows:
                record = dict(row)
                reference = record.pop("last_heartbeat_at")
                if reference:
                    heartbeat_at = datetime.fromisoformat(reference.replace("Z", "+00:00"))
                    record["is_online"] = (now - heartbeat_at).total_seconds() <= 120
                else:
                    record["is_online"] = False
                devices.append(record)
            return devices

    def latest_segment(self) -> sqlite3.Row | None:
        """Return the activity segment that ended most recently."""
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM activity_segments
                ORDER BY end_time DESC, start_time DESC, id DESC
                LIMIT 1
                """
            ).fetchone()

    def evidence_map(self, digests: list[str], include_revoked: bool = False) -> dict[str, sqlite3.Row]:
        """Return non-revoked agent evidence rows keyed by digest."""
        if not digests:
            return {}
        digests = list(set(digests))
        with self.connect() as connection:
            result = {}
            for offset in range(0, len(digests), 500):
                chunk = digests[offset:offset + 500]
                placeholders = ",".join("?" * len(chunk))
                suffix = "" if include_revoked else " AND revoked = 0"
                rows = connection.execute(
                    f"SELECT * FROM classification_evidence WHERE digest IN ({placeholders}){suffix}", chunk
                )
                result.update({row["digest"]: row for row in rows})
            return result

    def upsert_evidence(self, record: dict[str, Any]) -> None:
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO classification_evidence (
                    digest, platform, process, title_summary, behavior, purpose, category, topic,
                    description, confidence, explanation, model, input_json, created_at, context_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(digest) DO UPDATE SET
                    behavior = excluded.behavior,
                    purpose = excluded.purpose,
                    category = excluded.category,
                    topic = excluded.topic,
                    description = excluded.description,
                    confidence = excluded.confidence,
                    explanation = excluded.explanation,
                    model = excluded.model,
                    input_json = excluded.input_json,
                    context_version = excluded.context_version,
                    created_at = excluded.created_at
                WHERE classification_evidence.revoked = 0
                """,
                (
                    record["digest"],
                    record["platform"],
                    record["process"],
                    record.get("title_summary", ""),
                    record.get("behavior", ""),
                    record.get("purpose", ""),
                    record.get("category", ""),
                    record.get("topic", ""),
                    record.get("description", ""),
                    float(record.get("confidence", 0.0)),
                    record.get("explanation", ""),
                    record.get("model", ""),
                    json.dumps(record.get("input", {}), ensure_ascii=False),
                    now,
                    record.get("context_version", ""),
                ),
            )

    def revoke_evidence(self, digest: str) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "UPDATE classification_evidence SET revoked = 1 WHERE digest = ?", (digest,)
            )
            return cursor.rowcount == 1

    def save_daily_summary(self, day: str, version: str, narrative: str, model: str) -> None:
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_summaries (day, version, narrative, model, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    version = excluded.version,
                    narrative = excluded.narrative,
                    model = excluded.model,
                    created_at = excluded.created_at
                """,
                (day, version, narrative, model, now),
            )

    def daily_summary(self, day: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM daily_summaries WHERE day = ?", (day,)
            ).fetchone()

    def bump_evidence_hits(self, digest: str) -> int:
        """evidence 命中计数 +1（每个 digest 每天最多一次，由调用方去重）；返回当前值。"""
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "UPDATE classification_evidence SET hit_count = hit_count + 1 WHERE digest = ? AND revoked = 0",
                (digest,),
            )
            if cursor.rowcount == 0:
                return 0
            row = connection.execute(
                "SELECT hit_count FROM classification_evidence WHERE digest = ?", (digest,)
            ).fetchone()
            return int(row[0]) if row else 0

    def add_memory(self, record: dict[str, Any]) -> int:
        """新增一条长期记忆；kind/scope/source 由调用方约束，content 截断到 280 字符。"""
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO agent_memory (
                    kind, scope, category, content, source, confidence, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    str(record.get("kind") or "project_fact")[:32],
                    str(record.get("scope") or "").strip().lower()[:128],
                    str(record.get("category") or "")[:16],
                    str(record.get("content") or "").strip()[:280],
                    str(record.get("source") or "manual")[:16],
                    max(0.0, min(1.0, float(record.get("confidence", 1.0)))),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def memory_for(self, scope: str) -> list[sqlite3.Row]:
        """按进程名/主题精确匹配取 active 记忆（大小写不敏感）。"""
        key = (scope or "").strip().lower()
        if not key:
            return []
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM agent_memory
                    WHERE scope = ? AND status = 'active'
                    ORDER BY CASE source WHEN 'correction' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END, confidence DESC, id DESC
                    LIMIT 20
                    """,
                    (key,),
                )
            )

    def active_memories(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM agent_memory WHERE status = 'active' ORDER BY kind, id DESC LIMIT 50"
                )
            )

    def list_memories(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM agent_memory ORDER BY status ASC, id DESC LIMIT 200"
                )
            )

    def delete_memory(self, memory_id: int) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute("DELETE FROM agent_memory WHERE id = ?", (memory_id,))
            return cursor.rowcount == 1

    def supersede_memories(self, scope: str, kind: str, keep_category: str) -> int:
        """同 scope 同 kind 且 category 不同的旧记忆标记 superseded（新纠正永远赢，旧的归档可追溯）。"""
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_memory SET status = 'superseded', updated_at = ?
                WHERE scope = ? AND kind = ? AND status = 'active' AND category != ?
                """,
                (utc_iso(datetime.now(timezone.utc)), (scope or "").strip().lower(), kind, keep_category or ""),
            )
            return cursor.rowcount

    def touch_memories(self, memory_ids: list[int]) -> None:
        """记忆被注入 prompt 时更新命中计数与最后使用时间。"""
        if not memory_ids:
            return
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            connection.executemany(
                "UPDATE agent_memory SET hit_count = hit_count + 1, last_seen_at = ? WHERE id = ?",
                [(now, memory_id) for memory_id in memory_ids],
            )

    def memory_version(self) -> str:
        """Semantic revision excludes read counters; edits invalidate cached judgments."""
        import hashlib
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, kind, scope, category, content, confidence, context_json FROM agent_memory WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return hashlib.sha256(json.dumps([tuple(row) for row in rows], ensure_ascii=False).encode()).hexdigest() if rows else ""

    def offline_memories(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM agent_memory WHERE scope = 'offline' AND status = 'active' AND source = 'correction' ORDER BY id DESC"
            )]

    def offline_annotations(self, start: str, end: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM offline_annotations WHERE start_time < ? AND end_time > ? ORDER BY id", (end, start)
            )]

    def add_offline_annotation(self, start: str, end: str, category: str, note: str, patterns: list[dict]) -> int:
        now = utc_iso(datetime.now(timezone.utc))
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO offline_annotations (start_time, end_time, category, note, created_at) VALUES (?, ?, ?, ?, ?)",
                (start, end, category, note, now),
            )
            annotation_id = cursor.lastrowid
            for pattern in patterns:
                context = json.dumps(pattern, sort_keys=True)
                # The latest explicit habit for precisely this interval replaces the old one.
                connection.execute(
                    "UPDATE agent_memory SET status = 'superseded', updated_at = ? WHERE scope = 'offline' AND context_json = ? AND status = 'active'",
                    (now, context),
                )
                start_min, end_min = pattern['start_minute'], pattern['end_minute']
                label = '工作日' if pattern['day_type'] == 'weekday' else '周末'
                content = f"用户确认的时段习惯：{label} {start_min // 60:02d}:{start_min % 60:02d}—{end_min // 60:02d}:{end_min % 60:02d} 通常为{category}；仅作推测依据，不代表每天必然如此。"
                connection.execute(
                    "INSERT INTO agent_memory (kind, scope, category, content, source, confidence, status, created_at, updated_at, context_json, annotation_id) VALUES ('pattern', 'offline', ?, ?, 'correction', 1, 'active', ?, ?, ?, ?)",
                    (category, content, now, now, context, annotation_id),
                )
            return int(annotation_id)

    def delete_offline_annotation(self, annotation_id: int) -> bool:
        with self._write_lock, self.connect() as connection:
            cursor = connection.execute("DELETE FROM offline_annotations WHERE id = ?", (annotation_id,))
            connection.execute("DELETE FROM agent_memory WHERE annotation_id = ?", (annotation_id,))
            return cursor.rowcount > 0
