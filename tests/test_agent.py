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
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.agent import (
    AgentService,
    OpenAICompatibleLLM,
    evidence_digest,
    payload_logging_enabled,
    sanitize_title,
)
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.summarizer import _build_prompt
from tests.conftest import event
from zoneinfo import ZoneInfo

AGENT_ENV_KEYS = (
    "ACTIVITYWATCH_AGENT_BASE_URL",
    "ACTIVITYWATCH_AGENT_API_KEY",
    "ACTIVITYWATCH_AGENT_MODEL",
    "ACTIVITYWATCH_AGENT_ENABLED",
    "ACTIVITYWATCH_AGENT_LOG_PAYLOADS",
)


@pytest.fixture(autouse=True)
def _hermetic_agent_env(monkeypatch):
    """隔离本地 backend/.env：测试的"未配置"场景不受开发机真实 Key 影响。"""
    for key in AGENT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield



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


def test_production_payload_logging_defaults_off_but_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("ACTIVITYWATCH_ENV", "production")
    assert payload_logging_enabled() is False

    monkeypatch.setenv("ACTIVITYWATCH_AGENT_LOG_PAYLOADS", "1")
    assert payload_logging_enabled() is True


def test_http_402_opens_llm_circuit_and_suppresses_retries(monkeypatch):
    attempts = 0

    def reject(_request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError("https://llm.invalid", 402, "Payment Required", {}, None)

    monkeypatch.setattr("backend.app.agent.urllib.request.urlopen", reject)
    llm = OpenAICompatibleLLM("https://llm.invalid/v1", "secret", "test-model")

    assert llm("system", "user") is None
    assert llm.cooldown_seconds() > 3500
    assert llm("system", "user") is None
    assert attempts == 1


def test_invalid_model_result_is_not_retried_on_every_enrich(tmp_path):
    fake = FakeLLM(judgments=[])
    agent = AgentService(Database(tmp_path / "invalid-result.db"), llm=fake)
    agent.rows_provider = lambda _day: [
        {
            "device_id": "test-pc",
            "platform": "windows",
            "start_time": "2026-09-05T10:00:00.000Z",
            "end_time": "2026-09-05T10:01:00.000Z",
            "process": "Unknown.exe",
            "window_title": "无法判断的窗口",
            "category": "其他",
            "manual_override": False,
            "key_count": 0,
            "mouse_click_count": 0,
            "scroll_count": 0,
        }
    ]

    first = agent.enrich_day("2026-09-05")
    second = agent.enrich_day("2026-09-05")

    assert first == {"enabled": 1, "candidates": 1, "new": 0}
    assert second == {"enabled": 1, "candidates": 1, "new": 0}
    assert len(fake.user_prompts) == 1


def test_daily_prompt_includes_secondary_android_activity():
    prompt = _build_prompt(
        "2026-09-05",
        {
            "summary": [],
            "combined_segments": [{
                "start_time_local": "2026-09-05T10:00:00+08:00",
                "duration_seconds": 600,
                "category": "工作",
                "behavior": "编程",
                "purpose": "工作",
                "topic": "项目",
                "process": "Code.exe",
                "secondary": [{
                    "device_id": "android-phone",
                    "platform": "android",
                    "category": "生活事务",
                    "behavior": "沟通",
                    "process": "com.tencent.mm",
                }],
            }],
            "insights": {},
        },
        ZoneInfo("Asia/Shanghai"),
    )

    assert "主活动用于计时" in prompt
    assert "同时设备：android/android-phone, 生活事务, 沟通, com.tencent.mm" in prompt


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
    summary_prompt = next(p for system, p in zip(fake.system_prompts, fake.user_prompts) if "日报" in system)
    assert "mini-nccl" not in summary_prompt
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


# ----------------------------------------------------------------------
# 长期记忆（agent_memory）
# ----------------------------------------------------------------------


def test_correction_remember_creates_and_supersedes_memory(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="笔记 - 标题", duration_ms=60_000)]},
    )
    segment_id = seg_by_process(client.get("/api/v1/timeline/today?day=2026-09-05").json()["segments"], "Obsidian.exe")["id"]

    # 修正 + 「以后都这样」→ 归纳出泛化记忆
    client.patch(
        f"/api/v1/segments/{segment_id}",
        json={"category": "工作", "purpose": "写文档", "remember": True, "memory_note": "上班用它写周报"},
    )
    memories = client.get("/api/v1/agent/memory").json()
    assert len(memories["active"]) == 1
    memory = memories["active"][0]
    assert memory["scope"] == "obsidian.exe"
    assert memory["kind"] == "correction"
    assert memory["source"] == "correction"
    assert "工作" in memory["content"]
    assert "周报" in memory["content"]

    # 同一应用再次纠正为不同分类 → 新纠正永远赢，旧的 superseded（归档不删除）
    client.patch(
        f"/api/v1/segments/{segment_id}",
        json={"category": "学习", "remember": True},
    )
    memories = client.get("/api/v1/agent/memory").json()
    assert len(memories["active"]) == 1
    assert memories["active"][0]["category"] == "学习"
    assert len(memories["archived"]) == 1
    assert memories["archived"][0]["category"] == "工作"


def test_memory_manual_add_list_delete(agent_client):
    client, fake, database = agent_client
    created = client.post(
        "/api/v1/agent/memory",
        json={"kind": "project_fact", "scope": "mini-nccl", "content": "mini-nccl 是我的毕业设计项目，相关活动算学习"},
    ).json()
    assert created["scope"] == "mini-nccl"

    memories = client.get("/api/v1/agent/memory").json()
    assert [item["scope"] for item in memories["active"]] == ["mini-nccl"]
    assert memories["active"][0]["source"] == "manual"

    deleted = client.delete(f"/api/v1/agent/memory/{created['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/v1/agent/memory").json()["active"] == []
    assert client.delete(f"/api/v1/agent/memory/{created['id']}").status_code == 404


def test_memory_injected_into_classify_prompt(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/agent/memory",
        json={"kind": "correction", "scope": "Obsidian.exe", "content": "用户纠正：Obsidian.exe 的活动应归类为「学习」（写笔记）"},
    )
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="其他 - 标题", duration_ms=60_000)]},
    )
    client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})

    # 发给模型的条目里附带 known_facts，判断参考长期记忆
    classify_prompt = next(p for p in fake.user_prompts if "known_facts" in p or "digest" in p)
    assert "known_facts" in classify_prompt
    assert "应归类为「学习」" in classify_prompt

    # 注入后记忆命中计数更新
    memory = client.get("/api/v1/agent/memory").json()["active"][0]
    assert memory["hit_count"] >= 1
    assert memory["last_seen_at"]


def test_auto_promotion_after_repeated_hits(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="笔记 - 标题", duration_ms=60_000)]},
    )
    digest = evidence_digest("windows", "Obsidian.exe", "笔记 - 标题")
    fake.judgments = [
        {"digest": digest, "behavior": "写作", "purpose": "学习", "category": "学习",
         "topic": "笔记", "description": "写笔记", "confidence": 0.9, "explanation": "ok"}
    ]
    client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})
    assert client.get("/api/v1/agent/status").json()["evidence_count"] == 1
    # 阈值未到：还没沉淀
    assert client.get("/api/v1/agent/memory").json()["active"] == []

    # 累计命中到 4 次，再 enrich 一次 → 第 5 次触发自动沉淀
    while database.bump_evidence_hits(digest) < 4:
        pass
    fresh = AgentService(database, "Asia/Shanghai", llm=fake, model_name="fake")
    fresh.enrich_day("2026-09-05")

    memories = client.get("/api/v1/agent/memory").json()["active"]
    promoted = next(item for item in memories if item["scope"] == "obsidian.exe")
    assert promoted["source"] == "auto"
    assert promoted["kind"] == "app_fact"
    assert "写作" in promoted["content"]


def test_summary_prompt_includes_memory_and_week_context(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/agent/memory",
        json={"kind": "project_fact", "scope": "mini-nccl", "content": "mini-nccl 是我的毕业设计项目，相关活动算学习"},
    )
    fake.narrative = "今天主要在推进毕业设计。"
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Code.exe", title="mini-nccl - Visual Studio Code", duration_ms=120_000)]},
    )
    client.post("/api/v1/agent/summary/2026-09-05")

    summary_prompt = next(p for p in fake.user_prompts if "长期记忆" in p or "近几天" in p)
    assert "长期记忆" in summary_prompt
    assert "毕业设计项目" in summary_prompt

    # 日报接口带出记忆（/daily 页面透明展示）
    report = client.get("/api/v1/daily/report?day=2026-09-05").json()
    assert [item["scope"] for item in report["memories"]] == ["mini-nccl"]


def test_memory_endpoints_work_when_agent_disabled(tmp_path: Path):
    """Agent 未配置：记忆管理仍可用（纯本地数据库操作），核心接口不受影响。"""
    app = create_app(
        db_path=tmp_path / "test.db",
        rules_path=Path(__file__).parents[1] / "backend" / "config" / "rules.yaml",
        timezone_name="Asia/Shanghai",
        api_token="",
    )
    with TestClient(app) as client:
        client.post(
            "/api/v1/events/batch",
            json={"events": [event(1, "2026-09-05T10:00:00Z", process="Code.exe", title="mini-nccl - Visual Studio Code")]},
        )
        client.post("/api/v1/agent/memory", json={"scope": "code.exe", "content": "写代码用的"})
        report = client.get("/api/v1/daily/report?day=2026-09-05").json()
        assert len(report["memories"]) == 1
        status = client.get("/api/v1/agent/status").json()
        assert status["enabled"] is False
        assert status["memory_count"] == 1


# ----------------------------------------------------------------------
# 知识库优化：scope 归一化 / stale 生命周期 / project_fact 注入
# ----------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from backend.app.database import normalize_scope, utc_iso


def test_memory_scope_normalization_matches_variants(tmp_path: Path):
    database = Database(tmp_path / "scope.db")
    assert normalize_scope("C:\\Tools\\Code.EXE") == "code.exe"
    assert normalize_scope("  code  ") == "code"
    assert normalize_scope("/usr/bin/code") == "code"

    database.add_memory({"kind": "app_fact", "scope": "code.exe", "content": "写代码"})
    # 存 code.exe：带路径、大小写、短形态都能查到
    assert len(database.memory_for("C:\\Tools\\Code.EXE")) == 1
    assert len(database.memory_for("code")) == 1
    assert len(database.memory_for("CODE.EXE")) == 1
    # Android 包名绝不能按 . 切分
    database.add_memory({"kind": "app_fact", "scope": "com.tencent.mm", "content": "微信"})
    assert [row["scope"] for row in database.memory_for("com.tencent.mm")] == ["com.tencent.mm"]
    assert database.memory_for("com.tencent") == []

    # 历史短形态（存了 obsidian）：查 obsidian.exe 也能命中
    database.add_memory({"kind": "app_fact", "scope": "obsidian", "content": "旧数据"})
    assert len(database.memory_for("Obsidian.exe")) == 1


def test_supersede_matches_legacy_scope_forms(tmp_path: Path):
    database = Database(tmp_path / "supersede.db")
    database.add_memory({"kind": "correction", "scope": "obsidian", "category": "工作", "content": "旧纠正"})
    # 新纠正写成 obsidian.exe，历史短形态同样被归档
    assert database.supersede_memories("Obsidian.exe", "correction", "学习") == 1
    memories = database.list_memories()
    assert memories[0]["status"] == "superseded"


def test_stale_memories_expire_and_correction_exempt(agent_client):
    client, fake, database = agent_client
    database.add_memory({"kind": "app_fact", "scope": "oldapp.exe", "content": "旧应用事实"})
    database.add_memory({"kind": "project_fact", "scope": "old-project", "content": "旧项目"})
    database.add_memory({"kind": "correction", "scope": "keep.exe", "category": "学习", "content": "用户纠正"})
    version_before = database.memory_version()

    # 把前两条的 last_seen_at 改成 8 个月前（correction 保持 created_at 为现在）
    stale_time = utc_iso(datetime.now(timezone.utc) - timedelta(days=240))
    with database.connect() as connection:
        connection.execute(
            "UPDATE agent_memory SET last_seen_at = ? WHERE scope IN ('oldapp.exe', 'old-project')",
            (stale_time,),
        )
    database._invalidate_memory_version()

    expired = database.expire_stale_memories({"app_fact": 45, "project_fact": 30})
    assert expired == 2

    # stale 不再注入检索；correction 豁免仍然 active
    assert database.memory_for("oldapp.exe") == []
    assert database.memory_version() != version_before
    assert len(database.memory_for("keep.exe")) == 1

    # API 层：stale 落入 archived，用户可见可追溯
    memories = client.get("/api/v1/agent/memory").json()
    assert {item["status"] for item in memories["archived"]} == {"stale"}
    assert all(item["status"] == "active" for item in memories["active"])


def test_stale_sweep_wired_into_enrich(agent_client):
    client, fake, database = agent_client
    database.add_memory({"kind": "app_fact", "scope": "gone.exe", "content": "过期事实"})
    stale_time = utc_iso(datetime.now(timezone.utc) - timedelta(days=240))
    with database.connect() as connection:
        connection.execute("UPDATE agent_memory SET last_seen_at = ? WHERE scope = 'gone.exe'", (stale_time,))
    database._invalidate_memory_version()

    fresh = AgentService(database, "Asia/Shanghai", llm=fake, model_name="fake")
    fresh.enrich_day("2026-09-05")  # 无候选也走清扫
    assert [row["status"] for row in database.list_memories()] == ["stale"]


def test_project_fact_injected_into_classify(agent_client):
    client, fake, database = agent_client
    client.post(
        "/api/v1/agent/memory",
        json={"kind": "project_fact", "scope": "mini-nccl", "content": "mini-nccl 是毕业设计项目，相关活动算学习"},
    )
    client.post(
        "/api/v1/agent/memory",
        json={"kind": "project_fact", "scope": "unrelated-topic", "content": "无关项目不应被注入"},
    )
    client.post(
        "/api/v1/events/batch",
        json={"events": [event(1, "2026-09-05T10:00:00Z", process="Obsidian.exe", title="mini-nccl 设计笔记 - Obsidian", duration_ms=60_000)]},
    )
    fake.judgments = []
    client.post("/api/v1/agent/enrich", json={"day": "2026-09-05"})

    classify_prompt = next(p for p in fake.user_prompts if "digest" in p)
    assert "毕业设计项目" in classify_prompt
    assert "[project_fact]" in classify_prompt
    assert "unrelated-topic" not in classify_prompt

    # 项目记忆命中被 touch：hit_count 更新（stale 生命周期的依据）
    memory = next(item for item in client.get("/api/v1/agent/memory").json()["active"] if item["scope"] == "mini-nccl")
    assert memory["hit_count"] >= 1
    assert memory["last_seen_at"]
