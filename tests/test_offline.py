from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from tests.conftest import event


class OfflineLLM:
    def __init__(self):
        self.calls = 0

    def __call__(self, system: str, user: str) -> str | None:
        if "日报" in system:
            return "测试日报"
        self.calls += 1
        rows = json.loads(user)
        return json.dumps([
            {
                "digest": row["digest"], "behavior": "睡眠", "purpose": "生活事务",
                "category": "睡眠", "topic": "", "description": "根据习惯推测为睡眠",
                "confidence": 0.9, "explanation": "匹配用户确认的工作日睡眠时段",
            }
            for row in rows if row.get("process") == "__offline__"
        ], ensure_ascii=False)


def make_client(tmp_path: Path, llm=None):
    app = create_app(
        db_path=tmp_path / "offline.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai", api_token="", agent_llm=llm, summarizer_llm=llm,
    )
    return TestClient(app), app


def test_confirmed_offline_activity_splits_gap_and_survives_ingest(tmp_path: Path, monkeypatch):
    for key in ("ACTIVITYWATCH_AGENT_BASE_URL", "ACTIVITYWATCH_AGENT_API_KEY", "ACTIVITYWATCH_AGENT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    client, _ = make_client(tmp_path)
    with client:
        saved = client.post("/api/v1/offline-activities", json={
            "start_time": "2026-09-04T23:00:00+08:00",
            "end_time": "2026-09-05T08:00:00+08:00",
            "category": "睡眠", "note": "夜间睡眠", "remember": True,
        })
        assert saved.status_code == 200
        report = client.get("/api/v1/daily/report?day=2026-09-05").json()
        sleep = next(item for item in report["summary"] if item["category"] == "睡眠")
        assert sleep["seconds"] == 8 * 3600
        segment = next(item for item in report["combined_segments"] if item["category"] == "睡眠")
        assert segment["classification"]["source"] == "manual"

        # A later device upload overlaps the annotation. Device activity wins in
        # the combined view, so offline time is not double counted.
        client.post("/api/v1/events/batch", json={"events": [
            event(1, "2026-09-04T23:00:00Z", duration_ms=60_000)
        ]})
        report = client.get("/api/v1/daily/report?day=2026-09-05").json()
        sleep = next(item for item in report["summary"] if item["category"] == "睡眠")
        assert sleep["seconds"] == 8 * 3600 - 60
        assert sum(item["seconds"] for item in report["summary"]) == 24 * 3600


def test_remembered_time_pattern_can_produce_marked_inference(tmp_path: Path, monkeypatch):
    for key in ("ACTIVITYWATCH_AGENT_BASE_URL", "ACTIVITYWATCH_AGENT_API_KEY", "ACTIVITYWATCH_AGENT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    llm = OfflineLLM()
    client, app = make_client(tmp_path, llm)
    with client:
        client.post("/api/v1/offline-activities", json={
            "start_time": "2026-09-03T00:00:00+08:00",
            "end_time": "2026-09-03T08:00:00+08:00",
            "category": "睡眠", "remember": True,
        })
        result = client.post("/api/v1/agent/enrich", json={"day": "2026-09-04"}).json()
        assert result["new"] > 0
        report = client.get("/api/v1/daily/report?day=2026-09-04").json()
        inferred = next(item for item in report["combined_segments"] if item["category"] == "睡眠")
        assert inferred["classification"]["inferred"] is True
        assert inferred["classification"]["source"] == "agent"
        assert "待确认" in inferred["description"]

        digest = inferred["classification"]["digest"]
        client.post(f"/api/v1/agent/evidence/{digest}/revoke")
        client.post("/api/v1/agent/enrich", json={"day": "2026-09-04"})
        report = client.get("/api/v1/daily/report?day=2026-09-04").json()
        reverted = next(item for item in report["combined_segments"] if item["start_time"] == inferred["start_time"])
        assert reverted["category"] == "无设备记录"
        assert reverted["classification"] is None
        row = app.state.database.evidence_map([digest], include_revoked=True)[digest]
        assert row["revoked"] == 1


def test_conflicting_habits_do_not_guess(tmp_path: Path, monkeypatch):
    for key in ("ACTIVITYWATCH_AGENT_BASE_URL", "ACTIVITYWATCH_AGENT_API_KEY", "ACTIVITYWATCH_AGENT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    llm = OfflineLLM()
    client, _ = make_client(tmp_path, llm)
    with client:
        for category in ("睡眠", "运动"):
            client.post("/api/v1/offline-activities", json={
                "start_time": "2026-09-03T00:00:00+08:00",
                "end_time": "2026-09-03T08:00:00+08:00",
                "category": category, "remember": True,
            })
        # Exact same habit interval supersedes the older fact; only the latest
        # explicit correction remains active and therefore no contradiction leaks.
        active = client.get("/api/v1/agent/memory").json()["active"]
        assert [item["category"] for item in active if item["scope"] == "offline"] == ["运动"]
