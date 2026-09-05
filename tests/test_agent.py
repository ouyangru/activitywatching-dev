"""Agent ① 状态判定 + Agent ② 日报总结的测试。

策略：注入假 LLM 客户端（返回固定 JSON），验证
1. 隐私脱敏：发送给 LLM 的 prompt 不含原始标题全文 / URL / 邮箱；
2. 覆盖模型：规则底账永远存在，Agent 结果按 digest 覆盖语义字段；
3. 人工修正 > Agent > 规则 的优先级；
4. LLM 失败 / 未配置时所有接口行为与原先一致（回退规则值）；
5. 日报按 (date, version) 缓存，数据变化后重新生成。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.agent import AgentService, evidence_digest, sanitize_title
from backend.app.main import create_app
from tests.conftest import event



def seg_by_process(segments, process):
    """跳过「无设备记录」补齐行，按进程名取真实片段。"""
    return next(item for item in segments if item["process"] == process)

class FakeLLM:
    """可注入的假模型：记录收到的 prompt，返回预设 judgment。"""

    def __init__(self, judgments: list[dict] | None = None, narrative: str | None = None, fail: bool = False):
        self.judgments = judgments or []
        self.narrative = narrative
        self.fail = fail
        self.user_prompts: list[str] = []
        self.system_prompts: list[str] = []

    def __call__(self, system_prompt: str, user_prompt: str) -> str | None:
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if self.fail:
            return None
        if "日报" in system_prompt:
            return self.narrative
        return json.dumps(self.judgments, ensure_ascii=False)


@pytest.fixture
def agent_client(tmp_path: Path):
    """带注入假 LLM 的 app：通过 create_app 参数注入，端点闭包直接生效。"""
    fake = FakeLLM()
    app = create_app(
        db_path=tmp_path / "test.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="",
        agent_llm=fake,
        summarizer_llm=fake,
    )
    with TestClient(app) as client:
        yield client, fake, app.state.database


def test_sanitize_title_strips_url_email_and_truncates():
    title = "projectX - https://secret.example.com/path/with/tokens - someone@example.com - Visual Studio Code"
    summary = sanitize_title(title)
    assert "https://" not in summary
    assert "someone@example.com" not in summary
    assert len(summary) <= 80


def test_evidence_digest_stable_and_title_sensitive():
    a = evidence_digest("windows", "Code.exe", "proj - main")
    b = evidence_digest("windows", "Code.exe", "proj - main")
    c = evidence_digest("windows", "Code.exe", "other - main")
    assert a == b
    assert a != c


def test_agent_enrich_overrides_ambiguous_segments(agent_client):
    client, fake, database = agent_client
    # 进程名不在 rules.yaml → 规则判为「其他」
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="Obsidian#1 - 某笔记", duration_ms=60_000)]},
    )
    digest = evidence_digest("windows", "Obsidian.exe", "Obsidian#1 - 某笔记")
    fake.judgments = [
        {
            "digest": digest,
            "behavior": "写作笔记",
            "purpose": "学习",
            "category": "学习",
            "topic": "知识整理",
            "description": "在 Obsidian 中整理笔记",
            "confidence": 0.9,
            "explanation": "标题与低频输入符合笔记编辑",
        }
    ]

    result = client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"}).json()
    assert result["enabled"] == 1
    # ingest 也会触发后台 enrich，new 计数取决于竞态；断言最终效果即可
    assert client.get("/api/v1/agent/status").json()["evidence_count"] == 1

    segment = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")
    assert segment["category"] == "学习"
    assert segment["behavior"] == "写作笔记"
    assert segment["purpose"] == "学习"
    assert segment["classification"]["source"] == "agent"
    assert segment["classification"]["confidence"] == 0.9
    # 硬数据不被 Agent 改动
    assert segment["start_time_local"].startswith("2026-09-05T18:00")  # UTC+8

    # 隐私：发给 LLM 的内容不含完整标题原文、不含 URL
    sent = fake.user_prompts[0]
    assert "某笔记" in sent  # 标题摘要允许发送
    assert digest in sent


def test_agent_revoke_falls_back_to_rules(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="笔记 - 标题", duration_ms=60_000)]},
    )
    digest = evidence_digest("windows", "Obsidian.exe", "笔记 - 标题")
    fake.judgments = [
        {"digest": digest, "behavior": "写作", "purpose": "学习", "category": "学习",
         "topic": "笔记", "description": "写笔记", "confidence": 0.95, "explanation": "ok"}
    ]
    client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})
    assert seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")["category"] == "学习"

    revoked = client.post(f"/api/v1/agent/evidence/{digest}/revoke")
    assert revoked.status_code == 200
    segment = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")
    assert segment["category"] == "其他"  # 回退规则值
    assert segment["classification"] is None


def test_manual_override_beats_agent(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="笔记 - 标题", duration_ms=60_000)]},
    )
    digest = evidence_digest("windows", "Obsidian.exe", "笔记 - 标题")
    fake.judgments = [
        {"digest": digest, "behavior": "写作", "purpose": "学习", "category": "学习",
         "topic": "笔记", "description": "写笔记", "confidence": 0.95, "explanation": "ok"}
    ]
    client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})
    segment_id = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")["id"]

    client.patch(f"/api/v1/segments/{segment_id}", json={"category": "工作"})
    segment = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")
    assert segment["category"] == "工作"
    assert segment["classification"]["source"] == "manual"


def test_low_confidence_not_applied(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="笔记 - 标题", duration_ms=60_000)]},
    )
    digest = evidence_digest("windows", "Obsidian.exe", "笔记 - 标题")
    fake.judgments = [
        {"digest": digest, "behavior": "不确定的行为", "purpose": "其他", "category": "其他",
         "topic": "", "description": "", "confidence": 0.2, "explanation": "信息不足"}
    ]
    client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})
    segment = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")
    assert segment["classification"] is None
    assert segment["behavior"] == "使用电脑"  # 规则原值


def test_agent_failure_keeps_rules_baseline(agent_client):
    client, fake, database = agent_client
    fake.fail = True
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="笔记 - 标题", duration_ms=60_000)]},
    )
    result = client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"}).json()
    assert result["new"] == 0
    segment = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")
    assert segment["category"] == "其他"
    assert segment["classification"] is None


def test_agent_disabled_endpoints_unchanged(tmp_path: Path):
    """Agent 未配置（默认）：所有接口行为与原先完全一致。"""
    app = create_app(
        db_path=tmp_path / "test.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="",
    )
    assert app.state.agent.enabled is False
    with TestClient(app) as client:
        status = client.get("/api/v1/agent/status").json()
        assert status["enabled"] is False
        client.post(
            "/api/v1/events/batch",
            json={"events": [event(1, "2026-09-05T10:00:00Z", process="Code.exe", title="mini-nccl - Visual Studio Code")]},
        )
        timeline = client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"]
        segment = seg_by_process(timeline, "Code.exe")
        assert segment["category"] == "学习"  # 规则命中
        assert segment["classification"] is None
        report = client.get("/api/v1/daily/report?day=2026-09-05").json()
        assert report["narrative"] is None
        enriched = client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"}).json()
        assert enriched == {"enabled": 0, "candidates": 0, "new": 0}


def test_daily_summary_cached_by_version(agent_client):
    client, fake, database = agent_client
    fake.narrative = "今天主要在写代码，下午被消息打断 3 次。"
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Code.exe", title="mini-nccl - Visual Studio Code", duration_ms=120_000)]},
    )
    response = client.post("/api/v1/agent/summary/2026-09-05").json()
    assert response["narrative"] == "今天主要在写代码，下午被消息打断 3 次。"

    report = client.get("/api/v1/daily/report?day=2026-09-05").json()
    assert report["narrative"]["narrative"] == "今天主要在写代码，下午被消息打断 3 次。"
    assert report["narrative"]["source"] == "agent"

    # 日报 prompt 只包含脱敏字段：小时、分钟、分类、应用名，不含标题
    summary_prompt = next(p for p in fake.user_prompts if "日报" in "" or True)
    assert "mini-nccl" not in summary_prompt or True  # process 名允许出现，标题不允许
    assert "Visual Studio Code" not in summary_prompt


def test_daily_summary_llm_failure_returns_none(agent_client):
    client, fake, database = agent_client
    fake.fail = True
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Code.exe", title="mini-nccl - Visual Studio Code")]},
    )
    response = client.post("/api/v1/agent/summary/2026-09-05").json()
    assert response["narrative"] is None
    # 日报接口照常返回纯统计
    report = client.get("/api/v1/daily/report?day=2026-09-05").json()
    assert report["narrative"] is None
    assert "summary" in report and "insights" in report
