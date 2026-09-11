from backend.app.debuglog import DebugLogBuffer, infer_level, infer_module


def test_http_module_inference():
    assert infer_module("http", {"path": "/api/v1/recruitment/feishu/records"}) == "feishu"
    assert infer_module("http", {"path": "/api/v1/recruitment/calendar/events"}) == "calendar"
    assert infer_module("http", {"path": "/api/v1/recruitment/items"}) == "recruitment"
    assert infer_module("http", {"path": "/api/v1/insights/today"}) == "activity"
    assert infer_module("agent_output", {}) == "agent"
    assert infer_module("event", {"module": "mail"}) == "mail"


def test_severity_inference(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_SLOW_REQUEST_MS", "200")
    assert infer_level("http", {"status": 200, "elapsed_ms": 50}) == "info"
    assert infer_level("http", {"status": 404, "elapsed_ms": 50}) == "warn"
    assert infer_level("http", {"status": 500, "elapsed_ms": 50}) == "error"
    assert infer_level("http", {"status": 200, "elapsed_ms": 800}) == "warn"
    assert infer_level("event", {"status": "error"}) == "error"
    assert infer_level("event", {"level": "warn"}) == "warn"


def test_buffer_supports_module_and_level_filters(monkeypatch):
    monkeypatch.delenv("ACTIVITYWATCH_ENV", raising=False)
    monkeypatch.delenv("ACTIVITYWATCH_DEBUG_VIEW", raising=False)
    buffer = DebugLogBuffer(max_entries=10)
    buffer.record("http", path="/api/v1/recruitment/feishu/records", status=200, elapsed_ms=20)
    buffer.record("http", path="/api/v1/recruitment/calendar/events", status=502, elapsed_ms=30)
    buffer.record("event", module="mail", action="auto_scan", status="ok")

    assert [entry["module"] for entry in buffer.entries(module="calendar")] == ["calendar"]
    assert [entry["level"] for entry in buffer.entries(level="error")] == ["error"]
    assert buffer.entries(module="mail")[0]["action"] == "auto_scan"
