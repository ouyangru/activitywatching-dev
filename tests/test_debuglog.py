"""开发者视图（debuglog）：环境开关矩阵、环形缓冲语义、端到端接线。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import debuglog
from backend.app.debuglog import DebugLogBuffer, debug_view_enabled
from backend.app.main import create_app
from tests.conftest import event
from tests.test_agent import FakeLLM


@pytest.fixture(autouse=True)
def isolated_buffer(monkeypatch):
    """每个测试独立缓冲 + 开发环境默认开。"""
    monkeypatch.delenv("ACTIVITYWATCH_ENV", raising=False)
    monkeypatch.delenv("ACTIVITYWATCH_DEBUG_VIEW", raising=False)
    debuglog.buffer().reset()
    yield
    debuglog.buffer().reset()


def test_debug_view_enabled_env_matrix(monkeypatch):
    assert debug_view_enabled() is True  # 开发环境默认开
    monkeypatch.setenv("ACTIVITYWATCH_ENV", "production")
    assert debug_view_enabled() is False  # 生产默认关
    monkeypatch.setenv("ACTIVITYWATCH_DEBUG_VIEW", "1")
    assert debug_view_enabled() is True  # 生产显式开启
    monkeypatch.setenv("ACTIVITYWATCH_DEBUG_VIEW", "0")
    assert debug_view_enabled() is False  # 显式关闭优先


def test_debug_view_disabled_records_nothing(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_ENV", "production")
    debuglog.record("http", method="GET", path="/x")
    assert debuglog.buffer().entries() == []


def test_ring_buffer_caps_kind_filter_and_pagination():
    buffer = DebugLogBuffer(max_entries=5)
    for index in range(8):
        buffer.record("http" if index % 2 == 0 else "ingest", path=f"/{index}")
    entries = buffer.entries()
    assert [entry["id"] for entry in entries] == [8, 7, 6, 5, 4]  # 淘汰最旧，新在前
    assert all(entry["id"] > 3 for entry in entries)

    only_http = buffer.entries(kind="http")
    assert {entry["kind"] for entry in only_http} == {"http"}
    assert len(only_http) == 2

    incremental = buffer.entries(after_id=6)
    assert [entry["id"] for entry in incremental] == [8, 7]

    buffer.clear()
    assert buffer.entries() == [] and buffer.latest_id() == 0


def test_text_fields_clipped():
    buffer = DebugLogBuffer()
    buffer.record("agent_input", system="s" * 25_000, user={"nested": "u" * 25_000})
    entry = buffer.entries()[0]
    assert len(entry["system"]) <= 20_010 and entry["system"].endswith("…(截断)")
    assert len(entry["user"]["nested"]) <= 20_010


def test_api_and_agent_flow_recorded(tmp_path: Path):
    fake = FakeLLM(judgments=[{
        "digest": "x", "behavior": "编程", "purpose": "学习", "category": "学习",
        "topic": "t", "description": "d", "confidence": 0.9, "explanation": "e",
    }])
    app = create_app(
        db_path=tmp_path / "debug.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="",
        agent_llm=fake,
        summarizer_llm=fake,
    )
    with TestClient(app) as client:
        client.post(
            "/api/v1/agent/memory",
            json={"kind": "project_fact", "scope": "mini-nccl", "content": "毕业设计项目，算学习"},
        )
        response = client.post(
            "/api/v1/events/batch",
            json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="mini-nccl 设计笔记 - Obsidian", duration_ms=60_000)]},
        )
        assert response.status_code == 200
        client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})

        data = client.get("/api/v1/debug/logs").json()
        assert data["enabled"] is True
        kinds = {entry["kind"] for entry in data["entries"]}
        assert {"http", "ingest", "agent_input", "agent_output", "agent_inject"} <= kinds

        ingest = next(entry for entry in data["entries"] if entry["kind"] == "ingest")
        assert ingest["accepted"] == 1 and ingest["devices"] == [["test-pc", "windows"]]

        inject = next(entry for entry in data["entries"] if entry["kind"] == "agent_inject")
        assert inject["day"] == "2026-09-05"
        assert inject["items"][0]["process"] == "Obsidian.exe"
        assert inject["items"][0]["project_facts"] == ["mini-nccl"]

        agent_input = next(entry for entry in data["entries"] if entry["kind"] == "agent_input")
        assert agent_input["llm_kind"] == "classify"
        assert "毕业设计项目" in agent_input["user"]

        agent_output = next(entry for entry in data["entries"] if entry["kind"] == "agent_output")
        assert agent_output["status"] == "ok"
        assert "digest" in agent_output["output"]

        http_entry = next(entry for entry in data["entries"] if entry["kind"] == "http" and entry["path"] == "/api/v1/events/batch")
        assert http_entry["method"] == "POST" and http_entry["status"] == 200

        # kind 过滤 + after_id 增量 + 无效 kind 拒绝
        only_ingest = client.get("/api/v1/debug/logs?kind=ingest").json()
        assert {entry["kind"] for entry in only_ingest["entries"]} == {"ingest"}
        assert client.get("/api/v1/debug/logs?kind=bogus").status_code == 422
        incremental = client.get(f"/api/v1/debug/logs?after_id={data['latest_id']}").json()
        assert incremental["entries"] == []


def test_debug_endpoint_not_self_logged(tmp_path: Path):
    app = create_app(
        db_path=tmp_path / "selflog.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        api_token="",
    )
    with TestClient(app) as client:
        client.get("/api/v1/debug/logs")
        client.delete("/api/v1/debug/logs")
        client.get("/api/v1/health")  # 对照：普通端点会进日志环
        entries = debuglog.buffer().entries()
        assert [entry["kind"] for entry in entries] == ["http"]
        assert entries[0]["path"] == "/api/v1/health"


def test_debug_logs_disabled_in_production(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_ENV", "production")
    monkeypatch.setenv("ACTIVITYWATCH_API_TOKEN", "t" * 40)
    app = create_app(
        db_path=tmp_path / "prod.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        api_token="t" * 40,
    )
    with TestClient(app) as client:
        client.post(
            "/api/v1/events/batch",
            json={"events": [event(1, "2026-09-05T10:00:00Z")]},
            headers={"Authorization": "Bearer " + "t" * 40},
        )
        data = client.get("/api/v1/debug/logs", headers={"Authorization": "Bearer " + "t" * 40}).json()
        assert data == {"enabled": False, "latest_id": 0, "entries": []}
