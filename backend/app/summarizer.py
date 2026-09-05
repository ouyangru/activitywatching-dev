"""Agent ② 日报总结。

输入是脱敏后的当日统计（分类时长、跨设备主片段、洞察），输出叙述式日报。
按 (date, 数据版本) 缓存在 daily_summaries 表：数据没变直接命中缓存；
数据变了且模型可用时后台重新生成，请求本身永不等待 LLM。
模型不可用 / 未配置时 daily/report 照常返回纯统计，narrative 为 None。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .agent import SUMMARY_SYSTEM_PROMPT, AgentService, LLMClient
from .database import Database


class DailySummarizer:
    def __init__(
        self,
        database: Database,
        agent: AgentService,
        timezone_name: str = "Asia/Shanghai",
        llm: LLMClient | None = None,
    ):
        self.database = database
        self.agent = agent
        self.timezone = ZoneInfo(timezone_name)
        # 测试可注入独立的 summarizer 客户端；默认复用 Agent ① 的客户端
        self.llm = llm if llm is not None else agent.llm if agent.enabled else None
        self.model_name = agent.model_name if llm is None else "injected"
        self._generating: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 版本号：片段数量 + 最后结束时间，数据没变则缓存命中
    # ------------------------------------------------------------------
    def version_for(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "0"
        last_end = max(str(row.get("end_time") or "") for row in rows)
        return f"{len(rows)}:{last_end}"

    _version = version_for

    def narrative_for(
        self,
        day: str,
        rows: list[dict[str, Any]],
        payload_builder: "callable[[], dict[str, Any]]",
    ) -> dict[str, Any] | None:
        """读取缓存的日报；过期且模型可用时触发后台重生成（不阻塞）。"""
        cached = self.database.daily_summary(day)
        version = self._version(rows)
        if cached is not None and cached["version"] == version:
            return {"narrative": cached["narrative"], "source": "agent", "model": cached["model"]}

        if self.llm is not None:
            self._schedule_generation(day, version, payload_builder)
        if cached is not None:
            return {"narrative": cached["narrative"], "source": "agent-stale", "model": cached["model"]}
        return None

    def _schedule_generation(self, day: str, version: str, payload_builder: "callable[[], dict[str, Any]]") -> None:
        with self._lock:
            if day in self._generating:
                return
            self._generating.add(day)

        def target() -> None:
            try:
                self.generate(day, version, payload_builder())
            except Exception:  # 后台线程绝不向上抛
                pass
            finally:
                with self._lock:
                    self._generating.discard(day)

        threading.Thread(target=target, daemon=True, name=f"daily-summary-{day}").start()

    def generate(self, day: str, version: str, payload: dict[str, Any]) -> str | None:
        """同步生成并落盘；失败返回 None（daily/report 无感回退纯统计）。"""
        if self.llm is None:
            return None
        prompt = _build_prompt(day, payload, self.timezone)
        raw = self.llm(SUMMARY_SYSTEM_PROMPT, prompt)
        if not raw:
            return None
        narrative = raw.strip()
        if not narrative:
            return None
        self.database.save_daily_summary(day, version, narrative, self.model_name)
        return narrative

    def refresh(self, day: str, version: str, payload: dict[str, Any]) -> str | None:
        """手动触发同步重生成（测试/演示用）。"""
        return self.generate(day, version, payload)


def _build_prompt(day: str, payload: dict[str, Any], timezone: ZoneInfo) -> str:
    """把日报 payload 压缩成脱敏 prompt：不带标题原文，时间折算成当天小时。"""
    lines: list[str] = [f"日期：{day}", "分类时长（秒）："]
    for item in payload.get("summary", []):
        lines.append(f"  {item['category']}: {item['seconds']}")

    lines.append("主活动片段（开始小时, 时长分钟, 分类, 行为, 目的, 主题, 应用）：")
    for segment in payload.get("combined_segments", []):
        start_local = datetime.fromisoformat(segment["start_time_local"])
        duration_minutes = round(segment.get("duration_seconds", 0) / 60)
        lines.append(
            "  {hour:02d}:00, {minutes}分钟, {category}, {behavior}, {purpose}, {topic}, {app}".format(
                hour=start_local.hour,
                minutes=duration_minutes,
                category=segment.get("category", ""),
                behavior=segment.get("behavior", ""),
                purpose=segment.get("purpose", ""),
                topic=(segment.get("classification") or {}).get("topic", "") if isinstance(segment.get("classification"), dict) else "",
                app=segment.get("process", ""),
            )
        )

    insights = payload.get("insights") or {}
    apps = [
        f"{item['process']}({round(item['seconds'] / 60)}分钟)"
        for item in insights.get("apps", [])[:8]
    ]
    lines.append(f"应用排行：{'、'.join(apps) if apps else '无'}")
    focus = insights.get("focus") or {}
    lines.append(
        "专注：{sessions} 次，最长 {minutes} 分钟（{start} 起）".format(
            sessions=focus.get("sessions", 0),
            minutes=round(focus.get("longest_seconds", 0) / 60),
            start=(focus.get("longest_start_local") or "")[:16].replace("T", " "),
        )
    )
    switches = insights.get("switches") or {}
    if switches.get("interruptions"):
        sources = "、".join(f"{name} {count} 次" for name, count in switches.get("top_sources", [])[:5])
        lines.append(f"打断：共 {switches.get('interruptions')} 次，主要来源：{sources}")
    else:
        lines.append("打断：无")
    return "\n".join(lines)
