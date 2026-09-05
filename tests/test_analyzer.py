from .conftest import event


def test_absorbs_ten_second_interruption_between_same_activity(client):
    events = [
        event(1, "2026-09-05T00:20:00Z"),
        event(2, "2026-09-05T00:20:10Z", "WeChat.exe", "微信"),
        event(3, "2026-09-05T00:20:20Z"),
    ]
    client.post("/api/v1/events/batch", json={"events": events})
    segments = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]

    recorded = [segment for segment in segments if segment["id"] is not None]
    assert len(recorded) == 1
    assert recorded[0]["behavior"] == "编程"
    assert recorded[0]["duration_seconds"] == 30
    assert recorded[0]["interruptions"][0]["behavior"] == "沟通"


def test_idle_window_has_priority_over_process_rule(client):
    idle = event(1, "2026-09-05T00:20:00Z", idle_ms=300_000)
    client.post("/api/v1/events/batch", json={"events": [idle]})
    segment = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
        if item["id"] is not None
    )

    assert segment["category"] == "空闲"
    assert segment["behavior"] == "离开电脑"


def test_video_site_wins_when_title_also_contains_technical_keyword(client):
    video = event(
        1,
        "2026-09-05T00:20:00Z",
        process="chrome.exe",
        title="Mini-NCCL 原理讲解_哔哩哔哩_bilibili",
    )
    client.post("/api/v1/events/batch", json={"events": [video]})
    segment = next(
        item for item in client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
        if item["id"] is not None
    )

    assert segment["category"] == "娱乐"
    assert segment["behavior"] == "观看视频"
