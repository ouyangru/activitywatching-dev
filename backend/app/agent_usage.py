from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import deque
from typing import Any, Callable

from . import debuglog


def _float_env(name: str) -> float:
    try:
        return max(0.0, float(os.getenv(name, "0") or 0))
    except ValueError:
        return 0.0


def _estimate_tokens(text: str) -> int:
    """Cheap local estimate: CJK chars ~1 token; latin/digits/punctuation ~4 chars/token.

    This is intentionally labeled as an estimate. Exact provider usage can differ.
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
    other = max(0, len(text) - cjk)
    return cjk + math.ceil(other / 4)


def _classify_kind(system_prompt: str) -> str:
    if "日报助手" in system_prompt:
        return "summary"
    if "行为判定助手" in system_prompt:
        return "classify"
    return "unknown"


def _batch_size(user_prompt: str, kind: str) -> int | None:
    if kind != "classify":
        return None
    try:
        payload = json.loads(user_prompt)
        return len(payload) if isinstance(payload, list) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


class AgentUsageMonitor:
    """Wrap an Agent LLM callable and expose process-local usage diagnostics."""

    def __init__(self, llm: Callable[[str, str], str | None], model: str):
        self._llm = llm
        self.model = model
        self._lock = threading.Lock()
        self._recent: deque[tuple[float, str]] = deque(maxlen=200)
        self.calls = 0
        self.failed_calls = 0
        self.possible_waste_calls = 0
        self.estimated_input_tokens = 0
        self.estimated_output_tokens = 0
        self.estimated_cost_usd = 0.0

    def cooldown_seconds(self) -> float:
        getter = getattr(self._llm, "cooldown_seconds", None)
        if not callable(getter):
            return 0.0
        try:
            return max(0.0, float(getter()))
        except (TypeError, ValueError):
            return 0.0

    def __call__(self, system_prompt: str, user_prompt: str) -> str | None:
        started = time.monotonic()
        kind = _classify_kind(system_prompt)
        batch_size = _batch_size(user_prompt, kind)
        prompt_hash = hashlib.sha256((system_prompt + "\n" + user_prompt).encode("utf-8")).hexdigest()[:12]
        input_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)

        now = time.monotonic()
        repeat_window = max(60.0, _float_env("ACTIVITYWATCH_AGENT_REPEAT_WINDOW_SECONDS") or 900.0)
        with self._lock:
            while self._recent and now - self._recent[0][0] > repeat_window:
                self._recent.popleft()
            duplicate_count = sum(1 for _, fingerprint in self._recent if fingerprint == prompt_hash)
            self._recent.append((now, prompt_hash))

        raw = self._llm(system_prompt, user_prompt)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        output_tokens = _estimate_tokens(raw or "")

        input_rate = _float_env("ACTIVITYWATCH_AGENT_INPUT_USD_PER_1M")
        output_rate = _float_env("ACTIVITYWATCH_AGENT_OUTPUT_USD_PER_1M")
        estimated_cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

        possible_waste = bool(duplicate_count > 0 or not raw)
        waste_reasons: list[str] = []
        efficiency_flags: list[str] = []
        if duplicate_count > 0:
            waste_reasons.append(f"same_prompt_repeated_{duplicate_count + 1}x")
        if not raw:
            waste_reasons.append("empty_or_failed_output")
        if batch_size is not None and batch_size <= 1:
            efficiency_flags.append("tiny_batch")

        with self._lock:
            self.calls += 1
            self.failed_calls += int(not raw)
            self.possible_waste_calls += int(possible_waste)
            self.estimated_input_tokens += input_tokens
            self.estimated_output_tokens += output_tokens
            self.estimated_cost_usd += estimated_cost
            totals = self.snapshot()

        debuglog.record(
            "event",
            module="agent",
            action="llm_usage",
            status="warn" if possible_waste else "ok",
            level="warn" if possible_waste else "info",
            model=self.model,
            llm_kind=kind,
            prompt_hash=prompt_hash,
            batch_size=batch_size,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_total_tokens=input_tokens + output_tokens,
            estimated_cost_usd=round(estimated_cost, 8),
            pricing_configured=bool(input_rate or output_rate),
            elapsed_ms=elapsed_ms,
            possible_waste=possible_waste,
            waste_reasons=waste_reasons,
            efficiency_flags=efficiency_flags,
            repeat_window_seconds=int(repeat_window),
            total_calls=totals["calls"],
            total_possible_waste_calls=totals["possible_waste_calls"],
            total_estimated_cost_usd=totals["estimated_cost_usd"],
        )
        return raw

    def snapshot(self) -> dict[str, Any]:
        # Caller may already hold _lock; values are primitive reads only.
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "possible_waste_calls": self.possible_waste_calls,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_total_tokens": self.estimated_input_tokens + self.estimated_output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "pricing_configured": bool(
                _float_env("ACTIVITYWATCH_AGENT_INPUT_USD_PER_1M")
                or _float_env("ACTIVITYWATCH_AGENT_OUTPUT_USD_PER_1M")
            ),
        }


def install_agent_usage_monitor(agent: Any) -> AgentUsageMonitor | None:
    llm = getattr(agent, "llm", None)
    if llm is None:
        return None
    if isinstance(llm, AgentUsageMonitor):
        return llm
    monitor = AgentUsageMonitor(llm, getattr(agent, "model_name", "") or "unknown")
    agent.llm = monitor
    return monitor
