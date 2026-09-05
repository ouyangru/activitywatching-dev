"""Agent ① 状态判定（语义覆盖层）。

设计原则（见 plan.md Phase 6 与 ineed.md）：

1. 规则永远先跑出完整底账（analyzer.py），本模块只做异步增强，不阻塞写入。
2. 判断按「内容摘要」寻址缓存（digest = 平台 + 进程 + 脱敏标题），同一应用/标题
   只调用一次模型，采集器每 30 秒重建片段不会产生重复调用。
3. 只发送脱敏特征：进程名、标题摘要（截断、去 URL/邮箱原文）、分钟级交互频率；
   默认不发送原始标题全文，不发送任何键鼠内容。
4. 结果带置信度、解释、可撤销状态，不静默覆盖规则：读取层覆盖行为由
   ``AgentService.apply_evidence`` 完成，人工修正（manual_override）永远优先。
5. 模型不可用 / 未配置 / 返回不合法时静默回退规则值，核心功能不受影响。

环境变量配置（OpenAI 兼容 /chat/completions 接口）：

    ACTIVITYWATCH_AGENT_ENABLED    默认 1；设为 0 强制关闭
    ACTIVITYWATCH_AGENT_BASE_URL   例如 https://api.example.com/v1
    ACTIVITYWATCH_AGENT_API_KEY
    ACTIVITYWATCH_AGENT_MODEL      例如 deepseek-chat / gpt-4o-mini

三者齐全才启用；否则服务处于 disabled 状态，所有读取接口行为与原先完全一致。
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .database import Database, utc_iso


TITLE_MAX_CHARS = 80
CONFIDENCE_THRESHOLD = 0.55
MAX_DIGESTS_PER_CALL = 20
LLM_TIMEOUT_SECONDS = 45

CLASSIFY_SYSTEM_PROMPT = """你是一个本机活动追踪系统的行为判定助手。
输入是若干条"脱敏活动片段摘要"：进程名、窗口标题摘要、分钟级交互频率。
请为每一条判断：用户当时最可能在做什么（behavior，如 编程/阅读文档/看视频/聊天/浏览资讯），
这件事的目的（purpose，只能是 学习/工作/娱乐/生活事务/其他 之一），
对应的粗分类（category，只能是 学习/工作/娱乐/空闲/其他 之一），
主题（topic，不超过 12 个字，例如 "CUDA / mini-nccl"），
一句人类可读的描述（description，不超过 30 个字），
以及 0 到 1 的置信度（confidence）和一句话解释（explanation）。
只依据给定信息推断，不要编造不存在的细节。
严格输出 JSON 数组，每个元素包含字段 digest, behavior, purpose, category, topic, description, confidence, explanation，
不要输出任何其他文字。"""

SUMMARY_SYSTEM_PROMPT = """你是一个本机活动追踪系统的日报助手。
输入是某一天的脱敏统计：各分类时长、跨设备主活动片段序列（小时、时长、分类、行为、目的、主题、应用名）、
应用排行、专注情况、打断统计。请写一份 200 字以内的中文日报，回答：
1. 今天时间主要花在哪里；
2. 主要完成了什么、被什么打断；
3. 哪些记录置信度较低、建议人工确认。
只依据给定数据，不要编造。直接输出日报正文，不要客套和前后缀。"""


LLMClient = Callable[[str, str], str | None]
"""llm(system_prompt, user_prompt) -> 原始回复文本或 None（失败）。"""


def sanitize_title(title: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """标题脱敏摘要：截断、折叠空白、替换 URL 和邮箱原文。"""
    text = (title or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in text.split(" - ") if part.strip()]
    # 优先保留「项目名 - 主窗口」前两段，丢弃后面的编辑器/浏览器后缀
    if len(parts) >= 2:
        text = " - ".join(parts[:2])
    text = re.sub(r"https?://\S+", "[链接]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[邮箱]", text)
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars]


def evidence_digest(platform: str, process: str, title: str) -> str:
    """内容寻址缓存键：平台 + 进程 + 脱敏标题摘要。"""
    import hashlib

    payload = f"{(platform or '').lower()}|{(process or '').lower()}|{sanitize_title(title)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_llm_client_from_env() -> tuple[LLMClient | None, str]:
    """从环境变量构造 OpenAI 兼容客户端；未配置时返回 (None, 原因)。"""
    base_url = os.getenv("ACTIVITYWATCH_AGENT_BASE_URL", "").rstrip("/")
    api_key = os.getenv("ACTIVITYWATCH_AGENT_API_KEY", "")
    model = os.getenv("ACTIVITYWATCH_AGENT_MODEL", "")
    if not (base_url and api_key and model):
        return None, "missing base_url/api_key/model"
    if os.getenv("ACTIVITYWATCH_AGENT_ENABLED", "1") == "0":
        return None, "disabled by env"

    def call(system_prompt: str, user_prompt: str) -> str | None:
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, json.JSONDecodeError):
            return None

    return call, model


def _interaction_profile(row: dict[str, Any]) -> dict[str, Any]:
    """分钟级交互频率（脱敏），辅助判断阅读/编程/观看等模式。"""
    start = datetime.fromisoformat(row["start_time"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(row["end_time"].replace("Z", "+00:00"))
    minutes = max(0.5, (end - start).total_seconds() / 60)
    return {
        "keys_per_min": round((row.get("key_count") or 0) / minutes, 1),
        "clicks_per_min": round((row.get("mouse_click_count") or 0) / minutes, 1),
        "scrolls_per_min": round((row.get("scroll_count") or 0) / minutes, 1),
        "duration_minutes": round(minutes, 1),
    }


class AgentService:
    """规则底账之上的异步语义增强层（Agent ①）。"""

    def __init__(
        self,
        database: Database,
        timezone_name: str = "Asia/Shanghai",
        llm: LLMClient | None = None,
        model_name: str = "",
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ):
        self.database = database
        self.timezone = ZoneInfo(timezone_name)
        self.confidence_threshold = confidence_threshold
        if llm is not None:
            self.llm = llm
            self.model_name = model_name or "injected"
            self.enabled = True
        else:
            client, model = default_llm_client_from_env()
            self.llm = client
            self.model_name = model
            self.enabled = client is not None
        self._queue: queue.Queue[str] = queue.Queue()
        self._pending: set[str] = set()
        self._pending_lock = threading.Lock()
        self._worker_started = False

    # ------------------------------------------------------------------
    # 异步触发（写路径只投递，不等待）
    # ------------------------------------------------------------------
    def request_enrich(self, day: str) -> bool:
        """请求后台增强某天，已排队则跳过。永不抛错、永不阻塞。"""
        if not self.enabled:
            return False
        with self._pending_lock:
            if day in self._pending:
                return False
            self._pending.add(day)
            if not self._worker_started:
                threading.Thread(target=self._worker, daemon=True, name="agent-enrich").start()
                self._worker_started = True
        self._queue.put(day)
        return True

    def _worker(self) -> None:
        while True:
            day = self._queue.get()
            try:
                self.enrich_day(day)
            except Exception:  # 后台线程绝不向上抛
                pass
            finally:
                with self._pending_lock:
                    self._pending.discard(day)

    # ------------------------------------------------------------------
    # 增强（Agent ① 主体）
    # ------------------------------------------------------------------
    def enrich_day(self, day: str) -> dict[str, int]:
        """挑出规则判为「其他」且未人工修正的片段，按 digest 去重后批量判定。"""
        if not self.enabled:
            return {"enabled": 0, "candidates": 0, "new": 0}
        local_start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=self.timezone)
        start = local_start.astimezone(timezone.utc)
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        rows = self.database.rows_between("activity_segments", utc_iso(start), utc_iso(end), None)

        candidates: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            if item.get("manual_override"):
                continue
            if item.get("category") != "其他":
                continue
            if not item.get("device_id"):
                continue
            process = item.get("process") or ""
            if not process or process == "__screen_off__":
                continue
            platform = item.get("platform") or "windows"
            title = item.get("window_title") or ""
            digest = evidence_digest(platform, process, title)
            if digest not in candidates:
                candidates[digest] = {
                    "digest": digest,
                    "platform": platform,
                    "process": process,
                    "title_summary": sanitize_title(title),
                    **_interaction_profile(item),
                }

        if not candidates:
            return {"enabled": 1, "candidates": 0, "new": 0}

        known = self.database.evidence_map(list(candidates))
        pending = [item for digest, item in candidates.items() if digest not in known]
        created = 0
        for chunk_start in range(0, len(pending), MAX_DIGESTS_PER_CALL):
            chunk = pending[chunk_start : chunk_start + MAX_DIGESTS_PER_CALL]
            created += self._classify_chunk(chunk)
        return {"enabled": 1, "candidates": len(candidates), "new": created}

    def _classify_chunk(self, chunk: list[dict[str, Any]]) -> int:
        user_prompt = json.dumps(chunk, ensure_ascii=False)
        raw = self.llm(CLASSIFY_SYSTEM_PROMPT, user_prompt) if self.llm else None
        if not raw:
            return 0
        try:
            parsed = _extract_json_array(raw)
        except ValueError:
            return 0
        created = 0
        by_digest = {item["digest"]: item for item in chunk}
        for judgment in parsed:
            if not isinstance(judgment, dict):
                continue
            digest = judgment.get("digest")
            source = by_digest.get(digest)
            if source is None:
                continue
            confidence = _clamp_confidence(judgment.get("confidence"))
            self.database.upsert_evidence(
                {
                    "digest": digest,
                    "platform": source["platform"],
                    "process": source["process"],
                    "title_summary": source["title_summary"],
                    "behavior": str(judgment.get("behavior") or "")[:64],
                    "purpose": str(judgment.get("purpose") or "")[:32],
                    "category": str(judgment.get("category") or "")[:16],
                    "topic": str(judgment.get("topic") or "")[:64],
                    "description": str(judgment.get("description") or "")[:120],
                    "confidence": confidence,
                    "explanation": str(judgment.get("explanation") or "")[:280],
                    "model": self.model_name,
                    "input": source,
                }
            )
            created += 1
        return created

    # ------------------------------------------------------------------
    # 读取层覆盖（展示时 Agent 优先，无结果即规则值）
    # ------------------------------------------------------------------
    def apply_evidence(self, rows: list[Any]) -> list[dict[str, Any]]:
        """返回应用了 Agent 判断后的行列表；硬数据（时间/时长/交互计数）不动。"""
        items = [dict(row) if not isinstance(row, dict) else dict(row) for row in rows]
        if not items:
            return items
        digests = [
            evidence_digest(item.get("platform") or "windows", item.get("process") or "", item.get("window_title") or "")
            for item in items
        ]
        evidence = self.database.evidence_map(digests) if self.enabled else {}
        for item, digest in zip(items, digests):
            item.setdefault("classification", None)
            if item.get("manual_override"):
                item["classification"] = {"source": "manual", "confidence": 1.0}
                continue
            row = evidence.get(digest)
            if row is None:
                continue
            if float(row["confidence"]) < self.confidence_threshold:
                continue
            for field in ("behavior", "purpose", "category", "topic"):
                value = (row[field] or "").strip()
                if value and (field != "category" or value in {"学习", "工作", "娱乐", "空闲", "其他"}):
                    item[field] = value
            if (row["description"] or "").strip():
                item["description"] = row["description"]
            item["classification"] = {
                "source": "agent",
                "confidence": round(float(row["confidence"]), 2),
                "explanation": row["explanation"],
                "topic": row["topic"],
            }
        return items


def _extract_json_array(raw: str) -> list[Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array in response")
    return json.loads(text[start : end + 1])


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
