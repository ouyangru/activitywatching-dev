from backend.app.agent_usage import AgentUsageMonitor


def test_usage_monitor_counts_calls_and_flags_exact_repeat(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_AGENT_INPUT_USD_PER_1M", "1")
    monkeypatch.setenv("ACTIVITYWATCH_AGENT_OUTPUT_USD_PER_1M", "2")

    monitor = AgentUsageMonitor(lambda system, user: '[{"ok":true}]', "test-model")
    system = "你是一个本机活动追踪系统的行为判定助手。"
    user = '[{"digest":"abc"}]'

    assert monitor(system, user)
    first = monitor.snapshot()
    assert first["calls"] == 1
    assert first["possible_waste_calls"] == 0
    assert first["estimated_total_tokens"] > 0
    assert first["estimated_cost_usd"] > 0

    assert monitor(system, user)
    second = monitor.snapshot()
    assert second["calls"] == 2
    assert second["possible_waste_calls"] == 1


def test_usage_monitor_marks_empty_output_as_possible_waste():
    monitor = AgentUsageMonitor(lambda system, user: None, "test-model")
    assert monitor("system", "user") is None
    stats = monitor.snapshot()
    assert stats["calls"] == 1
    assert stats["failed_calls"] == 1
    assert stats["possible_waste_calls"] == 1
