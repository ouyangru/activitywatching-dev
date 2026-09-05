"""Seed a temp DB with cross-device demo data for smoke testing, then start the server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from backend.app.main import create_app

app = create_app(
    db_path=Path(__file__).parent / "smoke.db",
    rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
    timezone_name="Asia/Shanghai",
    api_token="",
)

def win_event(seq, start, process="Code.exe", title="demo - Visual Studio Code", keys=40, idle=0, device="pc-01"):
    return {
        "platform": "windows", "device_id": device, "sequence": seq,
        "start_time": start, "duration_ms": 10_000,
        "context": {"process": process, "window_title": title},
        "interaction": {"key_count": keys, "mouse_click_count": 4, "scroll_count": 6, "idle_ms": idle,
                        "clipboard_copy_count": 0, "clipboard_paste_count": 0, "clipboard_events": []},
    }

def phone_event(seq, start, process="tv.danmaku.bili", title="哔哩哔哩", device="phone-01"):
    return {
        "platform": "android", "device_id": device, "sequence": seq,
        "start_time": start, "duration_ms": 30_000,
        "context": {"process": process, "window_title": title},
        "interaction": {"key_count": 0, "mouse_click_count": 0, "scroll_count": 0, "idle_ms": 0,
                        "clipboard_copy_count": 0, "clipboard_paste_count": 0, "clipboard_events": []},
    }

with TestClient(app) as client:
    events = []
    # 08:00-08:05 电脑编程（本地 08:00 = UTC 00:00）
    for i in range(30):
        events.append(win_event(i + 1, f"2026-09-06T00:0{i % 10}:{i * 10 % 60:02d}:00Z".replace("00:0", "00:0")))
    # 简化：直接用秒级时间戳生成连续事件
    events = []
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 6, 0, 0, tzinfo=timezone.utc)
    for i in range(30):  # 08:00-08:05 本地，电脑编程
        events.append(win_event(i + 1, (base + timedelta(seconds=10 * i)).isoformat()))
    for i in range(20):  # 08:00-08:10 手机看视频（与电脑重叠 5 分钟）
        events.append(phone_event(i + 1, (base + timedelta(seconds=30 * i)).isoformat()))
    for i in range(30, 60):  # 08:05-08:10 电脑继续编程
        events.append(win_event(i + 1, (base + timedelta(seconds=10 * i)).isoformat()))
    response = client.post("/api/v1/events/batch", json={"events": events})
    print("ingest:", response.json())

    client.post("/api/v1/heartbeat", json={"device_id": "pc-01", "platform": "windows", "collector_version": "0.4.0"})
    print("devices:", client.get("/api/v1/devices").json())

    insights = client.get("/api/v1/insights/today?day=2026-09-06").json()
    print("focus:", insights["focus"])
    print("apps:", [(a["process"], a["seconds"]) for a in insights["apps"]])

    combined = client.get("/api/v1/timeline/combined?day=2026-09-06").json()["segments"]
    for item in combined:
        if item["category"] != "无设备记录":
            print("combined:", item["start_time_local"], item["main_device_id"], item["category"],
                  item["behavior"], "overlap:", item["overlap_seconds"], "reason:", item["reason"],
                  "secondary:", [s["device_id"] for s in item["secondary"]])

    purpose = client.get("/api/v1/summary/today?day=2026-09-06&dimension=purpose").json()
    print("purpose summary:", [(c["category"], c["seconds"]) for c in purpose["categories"] if c["seconds"]])

    report = client.get("/api/v1/daily/report?day=2026-09-06").json()
    print("daily total:", report["total_seconds"], "headline apps:", report["insights"]["apps"][0]["process"])
    print("page /:", client.get("/").status_code, " /daily:", client.get("/daily").status_code)
print("SMOKE OK")
