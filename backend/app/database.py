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
                        mouse_click_count, scroll_count, interruptions_json, manual_override, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def correct_segment(self, segment_id: int, new_category: str) -> dict[str, Any] | None:
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
                    new_category,
                    now,
                ),
            )
            connection.execute(
                "UPDATE activity_segments SET category = ?, manual_override = 1, updated_at = ? WHERE id = ?",
                (new_category, now, segment_id),
            )
            updated = dict(row)
            updated.update(category=new_category, manual_override=1, updated_at=now)
            return updated

    def count(self, table: str) -> int:
        if table not in {"feature_windows", "activity_segments", "user_corrections"}:
            raise ValueError("unsupported table")
        with self.connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def list_devices(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT device_id, platform, MAX(start_time) AS last_seen, COUNT(*) AS window_count
                FROM feature_windows
                GROUP BY device_id, platform
                ORDER BY last_seen DESC
                """
            )
            return [dict(row) for row in rows]
