"""Fixed-sample tests for the cross-device primary activity merger (plan.md Phase 3)."""

from datetime import datetime, timedelta, timezone

from backend.app.merger import combine_segments


def seg(
    device_id: str,
    start: str,
    end: str,
    *,
    platform: str = "windows",
    category: str = "学习",
    behavior: str = "编程",
    process: str = "Code.exe",
    description: str = "使用 VS Code 编写代码",
    key_count: int = 120,
    mouse_click_count: int = 10,
    scroll_count: int = 20,
) -> dict:
    return {
        "device_id": device_id,
        "platform": platform,
        "start_time": start,
        "end_time": end,
        "category": category,
        "behavior": behavior,
        "description": description,
        "process": process,
        "key_count": key_count,
        "mouse_click_count": mouse_click_count,
        "scroll_count": scroll_count,
        "interruptions_json": "[]",
    }


OVERLAP_START = "2026-09-05T02:00:00.000Z"
OVERLAP_END = "2026-09-05T02:30:00.000Z"


def test_interactive_pc_beats_media_phone_when_overlapping():
    """电脑看课程（持续交互）+ 手机刷视频：主活动是电脑，重叠 30 分钟不翻倍。"""
    rows = [
        seg("pc-01", OVERLAP_START, OVERLAP_END, key_count=600, mouse_click_count=80, scroll_count=200),
        seg(
            "phone-01",
            OVERLAP_START,
            OVERLAP_END,
            platform="android",
            category="娱乐",
            behavior="观看视频",
            process="tv.danmaku.bili",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
    ]

    combined = combine_segments(rows)

    assert len(combined) == 1
    result = combined[0]
    assert result["main_device_id"] == "pc-01"
    assert result["category"] == "学习"
    assert result["reason"] == "持续键鼠交互"
    assert result["overlap_seconds"] == 1800
    assert len(result["secondary"]) == 1
    assert result["secondary"][0]["device_id"] == "phone-01"


def test_media_phone_wins_when_pc_has_no_interaction():
    """电脑前台但无键鼠、手机在播放视频：按优先级模型手机（媒体播放）为主活动。"""
    rows = [
        seg(
            "pc-01",
            OVERLAP_START,
            OVERLAP_END,
            category="娱乐",
            behavior="观看视频",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
        seg(
            "phone-01",
            OVERLAP_START,
            OVERLAP_END,
            platform="android",
            category="娱乐",
            behavior="观看视频",
            process="tv.danmaku.bili",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
    ]

    combined = combine_segments(rows)

    assert len(combined) == 1
    # 两台都在播放视频：分数相同时优先 Windows 平台
    assert combined[0]["main_device_id"] == "pc-01"


def test_idle_ranks_below_any_foreground_activity():
    rows = [
        seg(
            "pc-01",
            OVERLAP_START,
            OVERLAP_END,
            category="空闲",
            behavior="离开电脑",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
        seg(
            "phone-01",
            OVERLAP_START,
            OVERLAP_END,
            platform="android",
            category="工作",
            behavior="沟通",
            process="com.tencent.mm",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
    ]

    combined = combine_segments(rows)

    assert combined[0]["main_device_id"] == "phone-01"
    assert combined[0]["reason"] == "前台但无交互"


def test_main_activity_total_never_exceeds_day():
    """验收标准：全天主活动时长之和不超过当天有效时间（覆盖区间内）。"""
    rows = [
        seg("pc-01", "2026-09-05T01:00:00.000Z", "2026-09-05T02:30:00.000Z"),
        seg(
            "phone-01",
            "2026-09-05T02:00:00.000Z",
            "2026-09-05T04:00:00.000Z",
            platform="android",
            category="娱乐",
            behavior="观看视频",
            process="tv.danmaku.bili",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
        seg("pc-01", "2026-09-05T04:30:00.000Z", "2026-09-05T05:00:00.000Z"),
    ]

    combined = combine_segments(rows)

    durations = []
    for item in combined:
        start = datetime.fromisoformat(item["start_time"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(item["end_time"].replace("Z", "+00:00"))
        durations.append((end - start).total_seconds())
    assert sum(durations) == 3.5 * 3600  # 覆盖区间总长 01:00-05:00 去掉 30 分钟空档
    assert sum(durations) <= 24 * 3600


def test_merge_is_deterministic_and_repeatable():
    rows = [
        seg("pc-01", OVERLAP_START, OVERLAP_END),
        seg(
            "phone-01",
            OVERLAP_START,
            OVERLAP_END,
            platform="android",
            category="娱乐",
            behavior="观看视频",
            process="tv.danmaku.bili",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
    ]
    assert combine_segments(rows) == combine_segments(rows)


def test_adjacent_intervals_with_same_main_are_merged():
    """相邻原子区间若主活动来源相同，应合并为一个片段。"""
    rows = [
        seg("pc-01", "2026-09-05T02:00:00.000Z", "2026-09-05T02:10:00.000Z"),
        seg("pc-01", "2026-09-05T02:10:00.000Z", "2026-09-05T02:20:00.000Z"),
    ]

    combined = combine_segments(rows)

    assert len(combined) == 1
    assert combined[0]["start_time"] == "2026-09-05T02:00:00.000Z"
    assert combined[0]["end_time"] == "2026-09-05T02:20:00.000Z"


def test_no_device_rows_rank_lowest_and_are_kept():
    rows = [
        {
            "device_id": None,
            "platform": "none",
            "start_time": OVERLAP_START,
            "end_time": OVERLAP_END,
            "category": "无设备记录",
            "behavior": "无设备活动",
            "description": "可能在运动、睡觉或进行不需要设备的活动",
            "process": "",
            "key_count": 0,
            "mouse_click_count": 0,
            "scroll_count": 0,
            "interruptions_json": "[]",
        }
    ]

    combined = combine_segments(rows)

    assert len(combined) == 1
    assert combined[0]["category"] == "无设备记录"
    assert combined[0]["reason"] == "无任何设备上报"
    assert combined[0]["engagement_score"] == 0.0


def test_time_splitting_handles_partial_overlap():
    """部分重叠：重叠段选主活动，非重叠段各自成为主活动。"""
    rows = [
        seg("pc-01", "2026-09-05T02:00:00.000Z", "2026-09-05T02:30:00.000Z"),
        seg(
            "phone-01",
            "2026-09-05T02:15:00.000Z",
            "2026-09-05T02:45:00.000Z",
            platform="android",
            category="娱乐",
            behavior="观看视频",
            process="tv.danmaku.bili",
            key_count=0,
            mouse_click_count=0,
            scroll_count=0,
        ),
    ]

    combined = combine_segments(rows)

    assert len(combined) == 2
    first, second = combined
    # 02:00-02:30 电脑连续为主活动：前 15 分钟独占，后 15 分钟与手机重叠（相邻合并）
    assert first["main_device_id"] == "pc-01"
    assert first["start_time"] == "2026-09-05T02:00:00.000Z"
    assert first["end_time"] == "2026-09-05T02:30:00.000Z"
    assert first["overlap_seconds"] == 900
    # 02:30-02:45 只剩手机，成为主活动
    assert second["main_device_id"] == "phone-01"
    assert second["overlap_seconds"] == 0


def test_acceptance_total_with_day_boundary():
    start = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(96):  # 全天 96 个 15 分钟片段，电脑全程交互
        segment_start = start + timedelta(minutes=15 * index)
        segment_end = segment_start + timedelta(minutes=15)
        rows.append(seg("pc-01", segment_start.isoformat(), segment_end.isoformat()))
    combined = combine_segments(rows)
    assert sum(
        (datetime.fromisoformat(item["end_time"].replace("Z", "+00:00"))
         - datetime.fromisoformat(item["start_time"].replace("Z", "+00:00"))).total_seconds()
        for item in combined
    ) == 24 * 3600
