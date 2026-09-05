import sqlite3

from backend.app.database import Database


def test_existing_database_is_migrated_with_platform_columns(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE feature_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
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
            CREATE TABLE activity_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
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
            CREATE TABLE user_corrections (
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
        )

    database = Database(path)
    with database.connect() as connection:
        feature_columns = {row[1] for row in connection.execute("PRAGMA table_info(feature_windows)")}
        segment_columns = {row[1] for row in connection.execute("PRAGMA table_info(activity_segments)")}

    assert "platform" in feature_columns
    assert "platform" in segment_columns
