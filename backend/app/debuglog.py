"""开发者视图调试日志（内存环形缓冲）。

目的：在网页上直接看到「设备采集上传 / API 请求 / Agent 输入输出 / 招聘与日历链路」，
便于定位异常，不用 SSH 翻 journalctl。

边界：
- 只存内存环形缓冲，重启即清空，不落盘；
- 记录的 prompt 本就是脱敏特征；
- HTTP 条目只记 method/path/status，不记 query string；
- 不记录 token、App Secret、邮件正文等敏感内容；
- 生产环境默认关闭，显式设 ACTIVITYWATCH_DEBUG_VIEW=1 开启。
"""

from __future__ import annotations

import itertools
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

MAX_ENTRIES = 300
MAX_TEXT_CHARS = 20_000


def debug_view_enabled() -> bool:
    configured = os.getenv("ACTIVITYWATCH_DEBUG_VIEW")
    if configured is not None:
        return configured != "0"
    return os.getenv("ACTIVITYWATCH_ENV", "development") != "production"


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "…(截断)"
    if isinstance(value, list):
        return [_clip(item) for item in value]
    if isinstance(value, dict):
        return {key: _clip(item) for key, item in value.items()}
    return value


def infer_module(kind: str, fields: dict[str, Any]) -> str:
    explicit = str(fields.get("module") or "").strip()
    if explicit:
        return explicit
    if kind.startswith("agent_"):
        return "agent"
    if kind == "ingest":
        return "ingest"
    path = str(fields.get("path") or "")
    if "/api/v1/recruitment/feishu" in path:
        return "feishu"
    if "/api/v1/recruitment/calendar" in path:
        return "calendar"
    if "/api/v1/recruitment" in path:
        return "recruitment"
    if "/api/v1/agent" in path:
        return "agent"
    if path.startswith(("/api/v1/events", "/api/v1/heartbeat", "/api/v1/devices")):
        return "ingest"
    if path.startswith((
        "/api/v1/timeline", "/api/v1/summary", "/api/v1/insights",
        "/api/v1/daily", "/api/v1/offline-activities", "/api/v1/segments",
    )):
        return "activity"
    if path.startswith("/api/v1/debug"):
        return "system"
    return "system"


def infer_level(kind: str, fields: dict[str, Any]) -> str:
    explicit = str(fields.get("level") or "").strip().lower()
    if explicit in {"info", "warn", "error"}:
        return explicit
    if fields.get("error") or fields.get("exception"):
        return "error"
    status = fields.get("status")
    if isinstance(status, int):
        if status >= 500:
            return "error"
        if status >= 400:
            return "warn"
    if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
        return "error"
    elapsed = fields.get("elapsed_ms")
    try:
        threshold = max(0.0, float(os.getenv("ACTIVITYWATCH_SLOW_REQUEST_MS", "200")))
        if elapsed is not None and float(elapsed) >= threshold:
            return "warn"
    except (TypeError, ValueError):
        pass
    return "info"


class DebugLogBuffer:
    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._lock = threading.Lock()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._ids = itertools.count(1)

    def record(self, kind: str, **fields: Any) -> None:
        if not debug_view_enabled():
            return
        kind = str(kind)
        module = infer_module(kind, fields)
        level = infer_level(kind, fields)
        entry = {
            "id": next(self._ids),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "kind": kind,
            "module": module,
            "level": level,
            **_clip(fields),
        }
        entry["module"] = module
        entry["level"] = level
        with self._lock:
            self._entries.append(entry)

    def entries(
        self,
        kind: str | None = None,
        after_id: int = 0,
        limit: int = 100,
        module: str | None = None,
        level: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = list(self._entries)
        selected = [
            entry for entry in snapshot
            if (not kind or entry["kind"] == kind)
            and (not module or entry.get("module") == module)
            and (not level or entry.get("level") == level)
            and entry["id"] > after_id
        ]
        return sorted(selected, key=lambda entry: entry["id"], reverse=True)[:limit]

    def latest_id(self) -> int:
        with self._lock:
            return self._entries[-1]["id"] if self._entries else 0

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self._ids = itertools.count(1)


_buffer = DebugLogBuffer()


def record(kind: str, **fields: Any) -> None:
    _buffer.record(kind, **fields)


def buffer() -> DebugLogBuffer:
    return _buffer
