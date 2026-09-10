"""开发者视图调试日志（内存环形缓冲）。

目的：在网页上直接看到「设备采集上传 / API 请求 / Agent 输入输出 / 记忆注入」，
便于排查提示词注入与分类行为，不用 SSH 翻 journalctl。

边界（与 payload 日志一致的生产策略）：
- 只存内存环形缓冲（默认 200 条），重启即清空，不落盘；
- 记录的 prompt 本就是脱敏特征（进程名 + sanitize_title 摘要 + 交互频率）；
- HTTP 条目只记 method/path/status，不记 query string（防 token 泄露）；
- 生产环境（ACTIVITYWATCH_ENV=production）默认关闭，显式设
  ACTIVITYWATCH_DEBUG_VIEW=1 开启、=0 强制关闭。
"""

from __future__ import annotations

import itertools
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

MAX_ENTRIES = 200
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


class DebugLogBuffer:
    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._lock = threading.Lock()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._ids = itertools.count(1)

    def record(self, kind: str, **fields: Any) -> None:
        if not debug_view_enabled():
            return
        entry = {
            "id": next(self._ids),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "kind": str(kind),
            **_clip(fields),
        }
        with self._lock:
            self._entries.append(entry)

    def entries(self, kind: str | None = None, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            snapshot = list(self._entries)
        selected = [
            entry for entry in snapshot
            if (not kind or entry["kind"] == kind) and entry["id"] > after_id
        ]
        return sorted(selected, key=lambda entry: entry["id"], reverse=True)[:limit]

    def latest_id(self) -> int:
        with self._lock:
            return self._entries[-1]["id"] if self._entries else 0

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def reset(self) -> None:
        """测试用：清空并重置 id 计数。"""
        with self._lock:
            self._entries.clear()
            self._ids = itertools.count(1)


_buffer = DebugLogBuffer()


def record(kind: str, **fields: Any) -> None:
    _buffer.record(kind, **fields)


def buffer() -> DebugLogBuffer:
    return _buffer
