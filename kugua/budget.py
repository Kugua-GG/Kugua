"""
kugua — AdaptiveBudgetControl + RetryPolicy + TimeoutGuard
v0.3.0
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CallRecord:
    task_type: str = ""
    tokens: int = 0
    duration_ms: float = 0
    success: bool = True
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class BudgetObserver:
    """Silent budget observer with cold-start period."""

    def __init__(self, cold_start_seconds: float = 300):
        self.cold_start = cold_start_seconds
        self._records: list[CallRecord] = []
        self._tiers = {"low": 1024, "medium": 4096, "high": 8192}

    @property
    def is_ready(self) -> bool:
        if not self._records:
            return False
        elapsed = time.time() - self._records[0].timestamp
        return elapsed >= self.cold_start

    def observe(self, record: CallRecord):
        self._records.append(record)
        if len(self._records) > 1000:
            self._records = self._records[-500:]


class AdaptiveBudgetControl:
    """Adaptive token budget based on historical usage patterns."""

    def __init__(self, observer: BudgetObserver = None):
        self.observer = observer or BudgetObserver()
        self.is_ready = self.observer.is_ready

    def check(self, task_type: str, tokens: int) -> tuple[str, int]:
        if self.observer.is_ready and tokens > self.observer._tiers.get("high", 8192):
            return "WARN", self.observer._tiers["medium"]
        return "OK", tokens

    def observe(self, task_type: str = "", tokens: int = 0,
                duration_ms: float = 0, retries: int = 0, success: bool = True):
        self.observer.observe(CallRecord(
            task_type=task_type, tokens=tokens,
            duration_ms=duration_ms, success=success,
        ))


class RetryPolicy:
    """Exponential backoff retry with jitter."""

    def __init__(self, max_retries: int = 3, base_delay: float = 0.5):
        self.max_retries = max_retries
        self.base_delay = base_delay

    def delay(self, attempt: int, error_type: str = "") -> float:
        import random
        if error_type == "rate_limit":
            return self.base_delay * (2 ** attempt) + random.uniform(0, 0.5)
        elif error_type == "server_error":
            return self.base_delay * (1.5 ** attempt)
        return 0.0


class TimeoutGuard:
    """Global timeout guard for LLM calls."""

    def __init__(self, seconds: float = 300):
        self.deadline = time.time() + seconds

    @property
    def expired(self) -> bool:
        return time.time() > self.deadline
