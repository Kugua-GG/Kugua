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
# Multivariate Early Warning Signal helpers (v0.3)
# (3D cross-validated: 物理FI发散, 系统多传感器冗余, 数学多尺度τ)
# ═══════════════════════════════════════════════════════════════

def fisher_info_approx(timeseries: List[float], sost: float = 0.5) -> float:
    """Approximate Fisher Information for early warning.

    Fisher Information measures dynamic order — it drops before regime shifts.
    FI ≈ 4 * Σ (√p_i - √p_{i+1})²  where p_i are binned state probabilities.

    Physics: FI diverges at critical points (Mastromatteo & Marsili 2011).
             Declining FI = loss of dynamic order = approaching tipping point.
    Cybernetic: like a multi-sensor fusion metric — captures system-wide order.
    Mathematical: FI defines the Fisher-Rao metric on the statistical manifold.

    Args:
        timeseries: Ordered time series values.
        sost: Size-of-states parameter (bin width in standard deviations).

    Returns:
        Fisher information approximation. Lower = more disorder = closer to tipping.
    """
    n = len(timeseries)
    if n < 3:
        return 0.0

    # Compute mean and std for binning
    mean_val = sum(timeseries) / n
    var_val = sum((x - mean_val) ** 2 for x in timeseries) / n
    std_val = math.sqrt(var_val) if var_val > 0 else 1.0

    # Bin the data
    bin_width = sost * std_val
    if bin_width <= 0:
        return 0.0

    bins: Dict[int, int] = {}
    for x in timeseries:
        idx = int((x - mean_val) / bin_width)
        bins[idx] = bins.get(idx, 0) + 1

    # Compute probability amplitudes and FI
    total = sum(bins.values())
    amplitudes = sorted(
        [math.sqrt(count / total) for count in bins.values()],
        reverse=True,
    )

    fi = 0.0
    for i in range(len(amplitudes) - 1):
        diff = amplitudes[i] - amplitudes[i + 1]
        fi += diff * diff
    fi *= 4.0

    # Normalize by theoretical maximum (4 * (1 - 0))
    return min(1.0, fi / 4.0)


def compute_variance(timeseries: List[float]) -> float:
    """Compute population variance of a time series."""
    n = len(timeseries)
    if n < 2:
        return 0.0
    mean_val = sum(timeseries) / n
    return sum((x - mean_val) ** 2 for x in timeseries) / n


def compute_ar1(timeseries: List[float]) -> float:
    """Compute lag-1 autocorrelation (AR1).

    AR1 increases before critical transitions — the system takes longer
    to recover, so successive values become more correlated.
    """
    n = len(timeseries)
    if n < 3:
        return 0.0
    mean_val = sum(timeseries) / n
    num = sum(
        (timeseries[i] - mean_val) * (timeseries[i + 1] - mean_val)
        for i in range(n - 1)
    )
    den = sum((x - mean_val) ** 2 for x in timeseries)
    return num / den if den > 0 else 0.0


def sliding_window_mk(
    timeseries: List[float],
    window_size: int = 5,
) -> Dict[str, Any]:
    """Multi-scale Mann-Kendall using sliding windows.

    Computes Kendall tau at multiple scales by sliding windows of
    size `window_size` across the time series, then computing the
    median tau across windows. More robust to local fluctuations.

    Inspired by Chen et al. (2021): multi-scale analysis improves
    reliability of CSD detection.
    """
    n = len(timeseries)
    if n < window_size or window_size < 3:
        if n >= 3:
            return mann_kendall(timeseries)
        return {"trend": 0, "tau": 0.0, "p_value": 1.0, "significant": False}

    taus = []
    for start in range(n - window_size + 1):
        window = timeseries[start:start + window_size]
        result = mann_kendall(window)
        taus.append(result["tau"])

    median_tau = _median(taus) if taus else 0.0
    # Significance based on full series
    full_result = mann_kendall(timeseries)

    return {
        "trend": full_result["trend"],
        "tau": full_result["tau"],
        "p_value": full_result["p_value"],
        "significant": full_result["significant"],
        "median_window_tau": median_tau,
        "window_size": window_size,
        "n_windows": len(taus),
        "tau_stability": 1.0 - (max(taus) - min(taus)) if taus and max(taus) > min(taus) else 1.0,
    }


def _median(values: List[float]) -> float:
    """Compute median of a list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


def compute_composite_csd_index(
    recovery_times: List[float],
    p_threshold: float = 0.05,
) -> Dict[str, Any]:
    """Compute a composite CSD index from multiple early warning dimensions.

    Three-dimensional EWS (cross-validated):
      1. Kendall tau of recovery times (physics: critical slowing)
      2. Fisher Information trend (math: loss of dynamic order)
      3. AR(1) trend (systems: growing temporal correlation)

    Composite: CSD_Index = α·I(tau) + β·I(fi_drop) + γ·I(ar1_rise)
    Where I(·) is an indicator function for each signal.

    Returns dict with all dimensions and composite score.
    """
    n = len(recovery_times)
    if n < 3:
        return {
            "composite": 0.0, "signal_count": 0,
            "tau_signal": False, "fi_signal": False, "ar1_signal": False,
            "summary": "Insufficient data (n < 3)",
        }

    # 1. Kendall tau
    mk = mann_kendall(recovery_times)
    tau_signal = mk["significant"] and mk["tau"] > 0

    # 2. Fisher Information (sliding window)
    fi_values = []
    window = max(3, n // 2)
    for start in range(max(1, n - window * 2)):
        chunk = recovery_times[start:start + window]
        if len(chunk) >= 3:
            fi_values.append(fisher_info_approx(chunk))
    fi_trend = mann_kendall(fi_values) if len(fi_values) >= 3 else {"tau": 0.0, "significant": False}
    fi_signal = fi_trend["significant"] and fi_trend["tau"] < 0  # FI dropping = warning

    # 3. AR(1) slope over sliding windows
    ar1_values = []
    for start in range(max(1, n - window * 2)):
        chunk = recovery_times[start:start + window]
        if len(chunk) >= 3:
            ar1_values.append(compute_ar1(chunk))
    ar1_trend = mann_kendall(ar1_values) if len(ar1_values) >= 3 else {"tau": 0.0, "significant": False}
    ar1_signal = ar1_trend["significant"] and ar1_trend["tau"] > 0  # AR1 rising = warning

    # Compose: each dimension contributes equally
    signal_count = sum([tau_signal, fi_signal, ar1_signal])
    composite = signal_count / 3.0

    summary_parts = []
    if tau_signal:
        summary_parts.append("τ↑ (recovery worsening)")
    if fi_signal:
        summary_parts.append("FI↓ (order loss)")
    if ar1_signal:
        summary_parts.append("AR1↑ (correlation growth)")
    if signal_count == 0:
        summary_parts.append("No CSD signals detected")

    return {
        "composite": composite,
        "signal_count": signal_count,
        "tau_signal": tau_signal,
        "tau_value": mk["tau"],
        "fi_signal": fi_signal,
        "fi_tau": fi_trend.get("tau", 0.0),
        "ar1_signal": ar1_signal,
        "ar1_tau": ar1_trend.get("tau", 0.0),
        "fi_values": fi_values[-5:] if fi_values else [],
        "ar1_values": ar1_values[-5:] if ar1_values else [],
        "summary": "; ".join(summary_parts) if summary_parts else "No CSD signals",
    }


# ═══════════════════════════════════════════════════════════════
# CriticalSlowingSignal
# ═══════════════════════════════════════════════════════════════

@dataclass
class CriticalSlowingSignal:
    """Result of a critical slowing detection for one (error_type, gv_id) pair.

    v0.3: Multivariate early warning — includes Fisher info, variance, AR(1),
    and composite CSD index for multi-dimensional cross-validation.
    """

    error_type: str = ""
    gv_id: str = ""
    sample_count: int = 0
    kendall_tau: float = 0.0
    p_value: float = 1.0
    trend: int = 0          # 1 = worsening, -1 = improving, 0 = no trend
    critical: bool = False   # p < threshold AND trend == 1
    significant: bool = False  # p < threshold (any direction)
    recovery_times: List[float] = field(default_factory=list)

    # v0.3: Multivariate EWS fields
    composite_index: float = 0.0       # 0-1 composite CSD index
    fis_approx: float = 0.0            # Fisher information approximation
    variance_trend: float = 0.0         # tau of variance trend
    ar1_trend: float = 0.0             # tau of AR(1) trend
    ews_dimensions: int = 0            # number of EWS dimensions signaling
    ews_summary: str = ""              # human-readable summary

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "error_type": self.error_type,
            "gv_id": self.gv_id,
            "sample_count": self.sample_count,
            "kendall_tau": self.kendall_tau,
            "p_value": self.p_value,
            "trend": self.trend,
            "critical": self.critical,
            "significant": self.significant,
            "recovery_times": self.recovery_times,
            "composite_index": self.composite_index,
            "fis_approx": self.fis_approx,
            "variance_trend": self.variance_trend,
            "ar1_trend": self.ar1_trend,
            "ews_dimensions": self.ews_dimensions,
            "ews_summary": self.ews_summary,
        }
        return d


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
        """Run multivariate CSD detection on recovery times.

        Computes single-variable Mann-Kendall AND multivariate composite index.
        The composite index uses 3 dimensions (tau + Fisher info + AR1),
        cross-validated across physics, systems, and math frameworks.

        Returns:
            CriticalSlowingSignal with trend, composite index, and EWS summary.
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
                ews_summary=f"Insufficient samples ({n}/{self.min_samples})",
            )

        mk = mann_kendall(recovery_times)
        critical = mk["significant"] and mk["trend"] == 1 and mk["p_value"] < self.p_threshold

        # Multivariate composite CSD index
        composite = compute_composite_csd_index(recovery_times, self.p_threshold)

        # Fisher Information (latest window)
        window = max(3, n // 2)
        latest_chunk = recovery_times[-window:]
        fi = fisher_info_approx(latest_chunk)

        # AR(1) from latest window
        ar1 = compute_ar1(latest_chunk)

        return CriticalSlowingSignal(
            error_type=error_type,
            gv_id=gv_id,
            sample_count=n,
            kendall_tau=mk["tau"],
            p_value=mk["p_value"],
            trend=mk["trend"],
            critical=critical,
            significant=mk["significant"],
            recovery_times=recovery_times,
            composite_index=composite["composite"],
            fis_approx=fi,
            variance_trend=composite.get("fi_tau", 0.0),
            ar1_trend=composite.get("ar1_tau", 0.0),
            ews_dimensions=composite["signal_count"],
            ews_summary=composite["summary"],
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
