"""
Critical Slowing Detector — Mann-Kendall trend test for early warning signals.

Critical slowing is a physical signal that a system is approaching a phase boundary:
  - Recovery time monotonically increases (Mann-Kendall p < 0.05)
  - Means current governance variables are losing effectiveness
  - Triggers double-loop learning to modify the rules themselves

Pure Python stdlib — no external dependencies.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# Normal distribution helpers (no scipy dependency)
# ═══════════════════════════════════════════════════════════════

def _erf_approx(x: float) -> float:
    """Abramowitz & Stegun 7.1.26 rational approximation of erf."""
    if x < 0:
        return -_erf_approx(-x)
    # constants
    a1 =  0.254829592
    a2 = -0.284496736
    a3 =  1.421413741
    a4 = -1.453152027
    a5 =  1.061405429
    p  =  0.3275911
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return y


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + _erf_approx(x / math.sqrt(2.0)))


def _norm_sf(x: float) -> float:
    """Standard normal survival function (1 - CDF)."""
    return 1.0 - _norm_cdf(x)


# ═══════════════════════════════════════════════════════════════
# Mann-Kendall trend test
# ═══════════════════════════════════════════════════════════════

def mann_kendall(timeseries: List[float]) -> Dict[str, Any]:
    """Mann-Kendall monotonic trend test.

    Args:
        timeseries: List of float values ordered by time.

    Returns:
        dict with keys:
            trend: 1 (increasing), -1 (decreasing), 0 (no trend)
            tau: Kendall rank correlation coefficient
            p_value: two-sided p-value
            significant: True if p < 0.05
            S: raw S statistic
            n: sample count
    """
    n = len(timeseries)
    if n < 3:
        return {"trend": 0, "tau": 0.0, "p_value": 1.0, "significant": False, "S": 0, "n": n}

    # Compute S statistic
    S = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = timeseries[j] - timeseries[i]
            if diff > 0:
                S += 1
            elif diff < 0:
                S -= 1

    # Kendall tau
    tau = S / (0.5 * n * (n - 1))

    # Variance of S with tie correction
    # Count ties
    unique_vals: Dict[float, int] = {}
    for v in timeseries:
        unique_vals[v] = unique_vals.get(v, 0) + 1

    tie_correction = 0
    for count in unique_vals.values():
        if count > 1:
            tie_correction += count * (count - 1) * (2 * count + 5)

    var_S = (n * (n - 1) * (2 * n + 5) - tie_correction) / 18.0

    # Z-score
    if S > 0:
        Z = (S - 1) / math.sqrt(var_S) if var_S > 0 else 0.0
    elif S < 0:
        Z = (S + 1) / math.sqrt(var_S) if var_S > 0 else 0.0
    else:
        Z = 0.0

    # Two-sided p-value
    p_value = 2.0 * _norm_sf(abs(Z))

    # Trend direction
    if p_value < 0.05:
        trend = 1 if tau > 0 else (-1 if tau < 0 else 0)
    else:
        trend = 0

    significant = p_value < 0.05

    return {
        "trend": trend,
        "tau": tau,
        "p_value": p_value,
        "significant": significant,
        "S": S,
        "n": n,
    }


# ═══════════════════════════════════════════════════════════════
# CriticalSlowingSignal
# ═══════════════════════════════════════════════════════════════

@dataclass
class CriticalSlowingSignal:
    """Result of a critical slowing detection for one (error_type, gv_id) pair."""

    error_type: str = ""
    gv_id: str = ""
    sample_count: int = 0
    kendall_tau: float = 0.0
    p_value: float = 1.0
    trend: int = 0          # 1 = worsening, -1 = improving, 0 = no trend
    critical: bool = False   # p < threshold AND trend == 1
    recovery_times: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type,
            "gv_id": self.gv_id,
            "sample_count": self.sample_count,
            "kendall_tau": self.kendall_tau,
            "p_value": self.p_value,
            "trend": self.trend,
            "critical": self.critical,
            "recovery_times": self.recovery_times,
        }


# ═══════════════════════════════════════════════════════════════
# CriticalSlowingDetector
# ═══════════════════════════════════════════════════════════════

class CriticalSlowingDetector:
    """Detects critical slowing down in system recovery behavior.

    Tracks recovery times per (error_type, gv_id) pair and applies the
    Mann-Kendall trend test to detect when governance variables are
    losing effectiveness.

    Attributes:
        _history: dict keyed by "error_type:gv_id", each value is a list
                  of failure records with recovery_time_s and task_id.
    """

    def __init__(
        self,
        artifacts_dir: Optional[Path] = None,
        min_samples: int = 5,
        p_threshold: float = 0.05,
    ):
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else Path(".")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.min_samples = min_samples
        self.p_threshold = p_threshold
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._state_file = self.artifacts_dir / "csd_state.json"
        self._load_state()

    # ── persistence ──────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted failure history from disk."""
        if self._state_file.exists():
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    self._history = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._history = {}

    def _save_state(self) -> None:
        """Persist failure history to disk."""
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── recording ────────────────────────────────────────────

    def record_failure(
        self,
        error_type: str,
        gv_ids: List[str],
        recovery_time_s: float,
        task_id: str = "",
    ) -> None:
        """Record a failure event for one or more governance variables.

        Args:
            error_type: The error category (accuracy, completeness, compliance, etc.)
            gv_ids: List of governance variable IDs affected.
            recovery_time_s: Recovery time in seconds.
            task_id: Optional task identifier for traceability.
        """
        record = {
            "recovery_time_s": recovery_time_s,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for gv_id in gv_ids:
            key = f"{error_type}:{gv_id}"
            if key not in self._history:
                self._history[key] = []
            self._history[key].append(record)

        self._save_state()

    # ── detection ────────────────────────────────────────────

    def detect(self, error_type: str, gv_id: str) -> CriticalSlowingSignal:
        """Run Mann-Kendall test on recovery times for a specific (error_type, gv_id) pair.

        Returns:
            CriticalSlowingSignal with trend analysis and critical flag.
        """
        key = f"{error_type}:{gv_id}"
        records = self._history.get(key, [])
        recovery_times = [r["recovery_time_s"] for r in records]
        n = len(recovery_times)

        if n < self.min_samples:
            return CriticalSlowingSignal(
                error_type=error_type,
                gv_id=gv_id,
                sample_count=n,
                recovery_times=recovery_times,
            )

        mk = mann_kendall(recovery_times)
        critical = mk["significant"] and mk["trend"] == 1 and mk["p_value"] < self.p_threshold

        return CriticalSlowingSignal(
            error_type=error_type,
            gv_id=gv_id,
            sample_count=n,
            kendall_tau=mk["tau"],
            p_value=mk["p_value"],
            trend=mk["trend"],
            critical=critical,
            recovery_times=recovery_times,
        )

    def detect_any(self) -> List[CriticalSlowingSignal]:
        """Run detection on all tracked (error_type, gv_id) pairs.

        Returns:
            List of signals, filtered to those that are critical or have min_samples.
        """
        results: List[CriticalSlowingSignal] = []
        for key in self._history:
            parts = key.split(":", 1)
            if len(parts) == 2:
                error_type, gv_id = parts
                signal = self.detect(error_type, gv_id)
                if signal.critical or signal.sample_count >= self.min_samples:
                    results.append(signal)
        return results

    def get_recovery_times(self, error_type: str, gv_id: str) -> List[float]:
        """Return raw recovery times for a given pair."""
        key = f"{error_type}:{gv_id}"
        return [r["recovery_time_s"] for r in self._history.get(key, [])]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the detector state."""
        signals = self.detect_any()
        return {
            "tracked_pairs": len(self._history),
            "critical_count": sum(1 for s in signals if s.critical),
            "signals": [s.to_dict() for s in signals],
        }
