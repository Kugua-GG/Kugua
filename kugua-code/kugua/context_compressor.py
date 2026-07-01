"""
kugua — ContextCompressor + ObserverWeight
v0.2.1

ContextCompressor: reduces context inflation in the main model to lower hallucination rates.
ObserverWeight: dynamic veto weight based on observer confidence and model tier ratio.
"""
from __future__ import annotations

import re
from typing import Optional


class ContextCompressor:
    """Compress injected context to stay within token budgets.

    Usage:
        cc = ContextCompressor(max_tokens=2000)
        compressed = cc.compress_kb_entries(entries, context_lines)
    """

    def __init__(self, max_tokens: int = 2000, max_l2_entries: int = 5,
                 max_l1_entries: int = 3, l1_summary_chars: int = 80):
        self.max_tokens = max_tokens
        self.max_l2_entries = max_l2_entries
        self.max_l1_entries = max_l1_entries
        self.l1_summary_chars = l1_summary_chars

    def compress_kb_entries(self, entries: list, context_lines: list[str]) -> str:
        if not entries:
            return ""
        l3 = [(e, line) for e, line in zip(entries, context_lines) if getattr(e, 'level', '') == "L3"]
        l2 = [(e, line) for e, line in zip(entries, context_lines) if getattr(e, 'level', '') == "L2"]
        l1 = [(e, line) for e, line in zip(entries, context_lines) if getattr(e, 'level', '') == "L1"]
        result_lines = []
        for _, line in l3:
            result_lines.append(line)
        l2_sorted = sorted(l2, key=lambda x: getattr(x[0], 'confidence', 0), reverse=True)
        for _, line in l2_sorted[:self.max_l2_entries]:
            result_lines.append(line)
        l1_sorted = sorted(l1, key=lambda x: getattr(x[0], 'confidence', 0), reverse=True)
        for entry, _ in l1_sorted[:self.max_l1_entries]:
            summary = self._summarize(getattr(entry, 'content', ''), self.l1_summary_chars)
            key = getattr(entry, 'key', '?')
            level = getattr(entry, 'level', 'L1')
            conf = getattr(entry, 'confidence', 0)
            result_lines.append(f"[KB:{key}|{level}|c={conf:.1f}] {summary}")
        joined = "\n".join(result_lines)
        if self._estimate_tokens(joined) <= self.max_tokens:
            return joined
        return self._trim_to_budget(result_lines)

    def compress_l2_history(self, entries: list[dict], max_entries: int = 20) -> list[dict]:
        if len(entries) <= max_entries:
            return entries
        keep = entries[-(max_entries - 1):]
        old = entries[:-(max_entries - 1)]
        summary = self._summarize_history(old)
        return [{"role": "system", "content": f"[History summary · {len(old)} compressed] {summary}"}] + keep

    def _trim_to_budget(self, lines: list[str]) -> str:
        result = list(lines)
        while self._estimate_tokens("\n".join(result)) > self.max_tokens and result:
            result.pop()
        return "\n".join(result)

    @staticmethod
    def _summarize(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        for sep in ["。", ".", "；", ";"]:
            last = truncated.rfind(sep)
            if last > max_chars * 0.5:
                return truncated[:last + 1]
        return truncated.rsplit(" ", 1)[0] + "..."

    @staticmethod
    def _summarize_history(entries: list[dict]) -> str:
        if not entries:
            return ""
        events = []
        for e in entries:
            content = e.get("content", "")
            if "EMERGENCY" in content: events.append("emergency-stop")
            elif "DoubleLoop" in content: events.append("double-loop")
            elif "PASS" in content: events.append("review-pass")
            elif "FAIL" in content: events.append("review-fail")
        unique = list(dict.fromkeys(events))
        return f"{len(entries)} entries. Key events: {', '.join(unique[:5])}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        chinese = len(re.findall(r'[一-鿿]', text))
        other = len(text) - chinese
        return int((chinese / 1.3 + other / 3.5) * 1.3)


class ObserverWeight:
    """Dynamic veto weight based on observer confidence.

    Three tiers:
      HIGH (>=0.7): full-weight veto
      MEDIUM (0.5-0.7): warn, log, escalate to human
      LOW (<0.5): log only, don't block
    """

    HIGH_CONFIDENCE = 0.7
    MEDIUM_CONFIDENCE = 0.5

    def __init__(self, main_model_tier: str = "", observer_model_tier: str = ""):
        self.main_tier = main_model_tier
        self.observer_tier = observer_model_tier
        self.veto_count = 0
        self.soft_warn_count = 0
        self.observation_count = 0

    def evaluate(self, confidence: float, calibrated: float = None) -> str:
        effective = calibrated if calibrated is not None else confidence
        self.observation_count += 1
        if effective >= self.HIGH_CONFIDENCE:
            self.veto_count += 1
            return "VETO"
        elif effective >= self.MEDIUM_CONFIDENCE:
            self.soft_warn_count += 1
            return "WARN"
        else:
            return "LOG"

    def to_dict(self) -> dict:
        return {
            "main_model": self.main_tier, "observer_model": self.observer_tier,
            "veto_count": self.veto_count, "soft_warn_count": self.soft_warn_count,
            "log_count": self.observation_count - self.veto_count - self.soft_warn_count,
            "total_observations": self.observation_count,
        }
