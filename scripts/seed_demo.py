"""Send 36 minutes of realistic demo FeatureWindows to a running backend."""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen


SCENES = [
    (18 * 60, "Code.exe", "mini-nccl - Visual Studio Code", 38, 4, 1),
    (7 * 60, "chrome.exe", "CUDA C++ Programming Guide - Google Chrome", 4, 2, 7),
    (6 * 60, "chrome.exe", "Mini-NCCL 原理讲解_哔哩哔哩_bilibili", 1, 1, 0),
    (5 * 60, "WeChat.exe", "微信", 12, 3, 1),
]


def main() -> None:
    random.seed(7)
    total_seconds = sum(scene[0] for scene in SCENES)
    cursor = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=total_seconds)
    sequence = int(time.time()) * 100
    events = []
    for seconds, process, title, keys, clicks, scrolls in SCENES:
        for _ in range(seconds // 10):
            events.append(
                {
                    "device_id": "demo-windows-pc",
                    "sequence": sequence,
                    "start_time": cursor.isoformat().replace("+00:00", "Z"),
                    "duration_ms": 10_000,
                    "context": {"process": process, "window_title": title},
                    "interaction": {
                        "key_count": max(0, keys + random.randint(-4, 4)),
                        "mouse_click_count": max(0, clicks + random.randint(-1, 1)),
                        "scroll_count": max(0, scrolls + random.randint(-1, 1)),
                        "idle_ms": 0,
                        "clipboard_copy_count": 0,
                        "clipboard_paste_count": 0,
                        "clipboard_events": [],
                    },
                }
            )
            sequence += 1
            cursor += timedelta(seconds=10)

    request = Request(
        "http://localhost:8765/api/v1/events/batch",
        data=json.dumps({"events": events}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()

