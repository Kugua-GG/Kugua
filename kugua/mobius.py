"""
Mobius Loop — unifies single-loop and double-loop learning into a continuous spectrum.

The key insight: single-loop (correct behavior under same rules) and double-loop
(modify rules themselves) are not binary states but a continuous spectrum, like a
Mobius strip where the "inside" and "outside" are one continuous surface.

Five correction levels mapped to intensity thresholds:
  L0_HINT      (< 0.2)   — lightweight prompt hint (single-loop side)
  L1_BIAS      (< 0.4)   — CorrectionBias injected into Worker context
  L2_OVERRIDE  (< 0.6)   — temporary rule override (twist point zone)
  L3_CANDIDATE (< 0.85)  — KB candidate, pending validation
  L4_COMMIT    (>= 0.85) — full double-loop: modify the rule itself

Reference: DSpark Confidence-Scheduled Verification — continuous gating
replaces discrete state machines.

Pure Python stdlib — no external dependencies.
"""

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# CorrectionBias
# ═══════════════════════════════════════════════════════════════

@dataclass
class CorrectionBias:
    """A single correction hint injected into the Worker context.

    Each bias nudges the system toward a correction. When biases accumulate
    for the same (error_type, gv_id), the CorrectionSpectrum intensity rises,
    eventually triggering double-loop learning.
    """

    error_location: str = ""
    error_type: str = ""         # accuracy, completeness, compliance, etc.
    correction_hint: str = ""
    confidence: float = 0.0
    gv_id: str = ""              # governance variable ID (KB entry key)
    bias_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.bias_id:
            self.bias_id = f"bias_{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_prompt_fragment(self) -> str:
        """Generate a prompt fragment for injection into Worker context.

        Returns empty string if no hint is set (empty bias).
        """
        if not self.correction_hint:
            return ""
        parts = []
        if self.error_type:
            parts.append(f"[{self.error_type.upper()}]")
        if self.error_location:
            parts.append(f"@{self.error_location}")
        parts.append(self.correction_hint)
        return " ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bias_id": self.bias_id,
            "error_location": self.error_location,
            "error_type": self.error_type,
            "correction_hint": self.correction_hint,
            "confidence": self.confidence,
            "gv_id": self.gv_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CorrectionBias":
        return cls(
            error_location=d.get("error_location", ""),
            error_type=d.get("error_type", ""),
            correction_hint=d.get("correction_hint", ""),
            confidence=d.get("confidence", 0.0),
            gv_id=d.get("gv_id", ""),
            bias_id=d.get("bias_id", ""),
            timestamp=d.get("timestamp", ""),
        )


# ═══════════════════════════════════════════════════════════════
# CorrectionSpectrum
# ═══════════════════════════════════════════════════════════════

# Level boundaries
LEVEL_L0_MAX = 0.2        # L0_HINT:     [0.0, 0.2)
LEVEL_L1_MAX = 0.4        # L1_BIAS:     [0.2, 0.4)
LEVEL_L2_MAX = 0.6        # L2_OVERRIDE: [0.4, 0.6)
LEVEL_L3_MAX = 0.85       # L3_CANDIDATE:[0.6, 0.85)
                           # L4_COMMIT:   [0.85, 1.0]

TWIST_LOW = 0.4           # Twist point zone lower bound
TWIST_HIGH = 0.85         # Twist point zone upper bound (exclusive)

@dataclass
class CorrectionSpectrum:
    """Tracks the accumulation of CorrectionBias for one (error_type, gv_id) pair.

    The intensity rises with each bias and decays over time. When intensity
    crosses thresholds, the system moves from single-loop hints to double-loop
    rule modification.

    Args:
        gv_id: Governance variable ID.
        error_type: Error category.
        bias_weight_per_instance: Base intensity added per bias.
        time_decay_gamma: Decay rate per day (lower = slower decay).
        confidence_threshold: Minimum confidence for bonus contribution.
    """

    gv_id: str
    error_type: str
    biases: List[CorrectionBias] = field(default_factory=list)
    intensity: float = 0.0
    bias_weight_per_instance: float = 0.15
    time_decay_gamma: float = 7.0
    confidence_threshold: float = 0.6
    _last_decay_time: float = 0.0

    def __post_init__(self):
        self._last_decay_time = time.time()

    # ── intensity computation ─────────────────────────────────

    def add_bias(self, bias: CorrectionBias) -> None:
        """Add a correction bias, applying time decay then incrementing intensity.

        The increment formula:
          delta = w * (1 + confidence_bonus + concentration_bonus)

        where:
          w = bias_weight_per_instance
          confidence_bonus = (confidence - threshold) / (1 - threshold) if above threshold
          concentration_bonus = 0.5 if any prior bias shares the same location
        """
        self.apply_decay()

        w = self.bias_weight_per_instance

        # Confidence bonus: proportional to how far above threshold
        confidence_bonus = 0.0
        if bias.confidence > self.confidence_threshold:
            confidence_bonus = (bias.confidence - self.confidence_threshold) / (
                1.0 - self.confidence_threshold
            )

        # Concentration bonus: biases clustering at the same location
        concentration_bonus = 0.0
        if bias.error_location:
            same_loc_count = sum(
                1 for b in self.biases
                if b.error_location == bias.error_location
            )
            if same_loc_count > 0:
                # Ratio of same-location biases signals concentration
                ratio = same_loc_count / max(len(self.biases), 1)
                concentration_bonus = 0.5 * ratio

        delta = w * (1.0 + confidence_bonus + concentration_bonus)
        self.intensity = min(1.0, self.intensity + delta)

        self.biases.append(bias)
        self._last_decay_time = time.time()

    def apply_decay(self) -> None:
        """Apply exponential time decay to current intensity.

        intensity *= exp(-gamma * elapsed_days)
        """
        now = time.time()
        if self._last_decay_time > 0 and self.time_decay_gamma > 0:
            elapsed_days = (now - self._last_decay_time) / 86400.0
            if elapsed_days > 0:
                decay_factor = math.exp(-self.time_decay_gamma * elapsed_days)
                self.intensity *= decay_factor
        self._last_decay_time = now

    def reset(self) -> None:
        """Reset intensity and clear all biases (after a double-loop commit)."""
        self.intensity = 0.0
        self.biases.clear()
        self._last_decay_time = time.time()

    # ── properties ────────────────────────────────────────────

    @property
    def current_level(self) -> str:
        """Current correction level based on intensity threshold."""
        i = self.intensity
        if i < LEVEL_L0_MAX:
            return "L0_HINT"
        elif i < LEVEL_L1_MAX:
            return "L1_BIAS"
        elif i < LEVEL_L2_MAX:
            return "L2_OVERRIDE"
        elif i < LEVEL_L3_MAX:
            return "L3_CANDIDATE"
        else:
            return "L4_COMMIT"

    @property
    def is_at_twist_point(self) -> bool:
        """True when intensity is in the twist zone (single↔double loop transition)."""
        return TWIST_LOW <= self.intensity < TWIST_HIGH

    @property
    def should_trigger_double_loop(self) -> bool:
        """True when intensity has crossed the commit threshold."""
        return self.intensity >= LEVEL_L3_MAX

    @property
    def bias_count(self) -> int:
        """Number of biases accumulated."""
        return len(self.biases)

    @property
    def unique_locations(self) -> List[str]:
        """Unique error locations across all biases."""
        seen: List[str] = []
        for b in self.biases:
            if b.error_location and b.error_location not in seen:
                seen.append(b.error_location)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gv_id": self.gv_id,
            "error_type": self.error_type,
            "intensity": self.intensity,
            "level": self.current_level,
            "bias_count": self.bias_count,
            "unique_locations": self.unique_locations,
            "is_at_twist_point": self.is_at_twist_point,
            "should_trigger": self.should_trigger_double_loop,
            "biases": [b.to_dict() for b in self.biases],
            "bias_weight_per_instance": self.bias_weight_per_instance,
            "time_decay_gamma": self.time_decay_gamma,
            "confidence_threshold": self.confidence_threshold,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CorrectionSpectrum":
        s = cls(
            gv_id=d.get("gv_id", ""),
            error_type=d.get("error_type", ""),
            bias_weight_per_instance=d.get("bias_weight_per_instance", 0.15),
            time_decay_gamma=d.get("time_decay_gamma", 7.0),
            confidence_threshold=d.get("confidence_threshold", 0.6),
        )
        s.intensity = d.get("intensity", 0.0)
        s.biases = [CorrectionBias.from_dict(b) for b in d.get("biases", [])]
        s._last_decay_time = time.time()
        return s


# ═══════════════════════════════════════════════════════════════
# TwistPoint
# ═══════════════════════════════════════════════════════════════

@dataclass
class TwistPoint:
    """Represents a point on the Mobius strip where single-loop transitions to double-loop.

    Wraps a CorrectionSpectrum at the twist zone and provides:
      - pre_rca: context for root cause analysis
      - override: suggested temporary rule override
      - downstream: hints for downstream effects after a commit
    """

    spectrum: Optional[CorrectionSpectrum] = None

    @property
    def pre_rca(self) -> Dict[str, Any]:
        """Pre-RCA context: primary location, error pattern, bias history."""
        if not self.spectrum or not self.spectrum.biases:
            return {
                "primary_location": "",
                "error_pattern": "",
                "bias_history": [],
            }

        biases = self.spectrum.biases

        # Find primary (most common) error location
        loc_counts: Dict[str, int] = {}
        for b in biases:
            if b.error_location:
                loc_counts[b.error_location] = loc_counts.get(b.error_location, 0) + 1
        primary_location = max(loc_counts, key=loc_counts.get) if loc_counts else ""

        # Error pattern from the most confident bias
        sorted_biases = sorted(biases, key=lambda b: b.confidence, reverse=True)
        error_pattern = sorted_biases[0].correction_hint if sorted_biases else ""

        return {
            "primary_location": primary_location,
            "error_pattern": error_pattern,
            "bias_history": [
                {
                    "location": b.error_location,
                    "hint": b.correction_hint,
                    "confidence": b.confidence,
                }
                for b in biases
            ],
        }

    @property
    def override(self) -> Dict[str, Any]:
        """Suggested temporary rule override based on accumulated biases."""
        if not self.spectrum or not self.spectrum.biases:
            return {"suggested_override": ""}

        biases = self.spectrum.biases
        sorted_biases = sorted(biases, key=lambda b: b.confidence, reverse=True)

        # Build override from the most confident and most frequent hints
        hint_texts: List[str] = []
        seen: set = set()
        for b in sorted_biases:
            if b.correction_hint and b.correction_hint not in seen:
                hint_texts.append(b.correction_hint)
                seen.add(b.correction_hint)

        suggested = "; ".join(hint_texts[:3])  # top 3 unique hints

        return {
            "suggested_override": suggested,
            "source_count": len(biases),
            "average_confidence": sum(b.confidence for b in biases) / len(biases) if biases else 0.0,
        }

    @property
    def downstream(self) -> Dict[str, Any]:
        """Downstream hints after a double-loop commit."""
        return {
            "type": "RULE_CHANGED",
            "message": "Governance variable modified; downstream effects should be monitored.",
        }

    def get_five_whys_chain(self) -> List[str]:
        """Generate a 5-Whys chain from the bias history.

        Each bias contributes a "why" level based on its correction hint.
        """
        whys: List[str] = []
        if self.spectrum:
            for b in self.spectrum.biases[:5]:
                if b.correction_hint:
                    whys.append(f"Why: {b.correction_hint}")
        # Pad to at least 5 if we have fewer biases
        while len(whys) < 5:
            whys.append(f"Why: further investigation needed at level {len(whys)+1}")
        return whys[:5]


# ═══════════════════════════════════════════════════════════════
# MobiusController
# ═══════════════════════════════════════════════════════════════

class MobiusController:
    """Central controller for the Mobius Loop correction spectrum.

    Manages multiple CorrectionSpectrum instances, one per (error_type, gv_id) pair.
    Provides persistence, dashboard, and integration hooks for DoubleLoopExecutor.

    v0.3: Adaptive parameters — decay rate and trigger threshold are now dynamic,
    calibrated from error severity and intervention efficacy history.
    (3D cross-validated: 物理势阱深度, 系统V(C)多样性, 数学贝叶斯校准)

    Args:
        bias_weight: Base intensity increment per bias (default 0.15).
        time_decay_gamma: Default decay rate per day (default 7.0). Can be overridden
            per error_type via severity_gamma_map.
        artifact_dir: Directory for persisting state. If None, state is not auto-saved.
    """

    # Severity-based decay rates (物理: 深势阱 → 慢衰减 → 长记忆)
    # Higher severity = slower decay = longer memory
    DEFAULT_SEVERITY_MAP: Dict[str, Dict[str, float]] = {
        "compliance":    {"gamma": 3.0,  "threshold_boost": 0.05},   # 合规: 极慢衰减, 阈值更高
        "safety":        {"gamma": 2.0,  "threshold_boost": 0.10},   # 安全: 最慢衰减, 阈值最高
        "accuracy":      {"gamma": 7.0,  "threshold_boost": 0.0},    # 准确性: 默认
        "completeness":  {"gamma": 7.0,  "threshold_boost": 0.0},    # 完整性: 默认
        "logical_error": {"gamma": 5.0,  "threshold_boost": 0.0},    # 逻辑错误: 中等
    }

    def __init__(
        self,
        bias_weight: float = 0.15,
        time_decay_gamma: float = 7.0,
        artifact_dir: Optional[Path] = None,
        severity_map: Optional[Dict[str, Dict[str, float]]] = None,
    ):
        self.bias_weight = bias_weight
        self.time_decay_gamma = time_decay_gamma
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self._spectra: Dict[str, CorrectionSpectrum] = {}
        self._severity_map = severity_map or self.DEFAULT_SEVERITY_MAP
        # Efficacy tracking for adaptive thresholds
        self._intervention_history: Dict[str, Dict[str, int]] = {}  # key → {attempts, successes}

        if self.artifact_dir:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)

    # ── spectrum management ──────────────────────────────────

    def _make_key(self, gv_id: str, error_type: str) -> str:
        return f"{error_type}:{gv_id}"

    def _split_key(self, key: str) -> Tuple[str, str]:
        parts = key.split(":", 1)
        return (parts[1], parts[0]) if len(parts) == 2 else ("", "")

    # ── adaptive parameters (v0.3) ─────────────────────────────

    def get_severity_gamma(self, error_type: str) -> float:
        """Get severity-adjusted decay rate for an error type.

        Physics: deeper potential well → slower relaxation → lower gamma.
        High severity errors (compliance, safety) need long memory;
        low severity errors (accuracy) decay faster.
        """
        severity = self._severity_map.get(error_type, {})
        return severity.get("gamma", self.time_decay_gamma)

    def get_severity_threshold_boost(self, error_type: str) -> float:
        """Get severity-based threshold boost.

        More critical error types get higher trigger thresholds —
        we want to be more careful before modifying safety/compliance rules.
        """
        severity = self._severity_map.get(error_type, {})
        return severity.get("threshold_boost", 0.0)

    def get_adaptive_threshold(self, gv_id: str, error_type: str) -> float:
        """Compute confidence-calibrated trigger threshold.

        Mathematical: threshold = base + severity_boost - efficacy_bonus
          - base = 0.85 (default)
          - severity_boost: higher for critical error types
          - efficacy_bonus: lower threshold when historical success rate is high

        Cybernetic: like a PID controller's setpoint — adjusts based on
        how well past interventions have worked.
        """
        base = LEVEL_L3_MAX  # 0.85
        severity_boost = self.get_severity_threshold_boost(error_type)
        efficacy_bonus = self._get_efficacy_bonus(gv_id, error_type)
        return max(0.5, min(0.95, base + severity_boost - efficacy_bonus))

    def _get_efficacy_bonus(self, gv_id: str, error_type: str) -> float:
        """Calculate efficacy bonus: higher success rate → lower threshold.

        Range: 0.0 (no history) to 0.15 (perfect success rate).
        """
        key = self._make_key(gv_id, error_type)
        history = self._intervention_history.get(key, {})
        attempts = history.get("attempts", 0)
        successes = history.get("successes", 0)
        if attempts == 0:
            return 0.0
        success_rate = successes / attempts
        # Bonus scale: max 0.15 reduction at 100% success rate
        return 0.15 * success_rate

    def record_intervention_outcome(self, gv_id: str, error_type: str, success: bool) -> None:
        """Record outcome of a double-loop intervention for efficacy calibration.

        Called after commit or rollback to update the efficacy history.
        """
        key = self._make_key(gv_id, error_type)
        if key not in self._intervention_history:
            self._intervention_history[key] = {"attempts": 0, "successes": 0}
        self._intervention_history[key]["attempts"] += 1
        if success:
            self._intervention_history[key]["successes"] += 1

    def get_diversity_index(self) -> float:
        """Compute controller diversity V(C) — Ashby's requisite variety metric.

        Cybernetic: V(C) must ≥ V(E) for effective control.
        Diversity = unique (error_type × location) combinations across all spectra.

        Returns:
            Shannon entropy of the bias distribution across error locations.
            Higher = more diverse controller response repertoire.
        """
        import math
        location_counts: Dict[str, int] = {}
        total = 0
        for spectrum in self._spectra.values():
            for bias in spectrum.biases:
                key = f"{bias.error_type}@{bias.error_location}"
                location_counts[key] = location_counts.get(key, 0) + 1
                total += 1

        if total == 0:
            return 0.0

        # Shannon entropy of the distribution
        entropy = 0.0
        for count in location_counts.values():
            p = count / total
            entropy -= p * math.log(p)
        # Normalize by log(N) for a [0, 1] index
        n_unique = len(location_counts)
        if n_unique <= 1:
            return 0.0
        return entropy / math.log(n_unique)

    def get_or_create_spectrum(self, gv_id: str, error_type: str) -> CorrectionSpectrum:
        """Get existing spectrum or create a new one for the given pair."""
        key = self._make_key(gv_id, error_type)
        if key not in self._spectra:
            self._spectra[key] = CorrectionSpectrum(
                gv_id=gv_id,
                error_type=error_type,
                bias_weight_per_instance=self.bias_weight,
                time_decay_gamma=self.time_decay_gamma,
            )
        return self._spectra[key]

    def get_spectrum(self, gv_id: str, error_type: str) -> Optional[CorrectionSpectrum]:
        """Get spectrum if it exists, None otherwise."""
        key = self._make_key(gv_id, error_type)
        return self._spectra.get(key)

    def all_spectra(self) -> List[CorrectionSpectrum]:
        """Return all tracked spectra (alias for list access)."""
        return list(self._spectra.values())

    # ── bias recording ───────────────────────────────────────

    def push_bias(self, bias: CorrectionBias) -> None:
        """Record a correction bias. Alias for record_bias."""
        self.record_bias(bias)

    def record_bias(self, bias: CorrectionBias) -> None:
        """Record a correction bias, updating the relevant spectrum.

        Creates a new spectrum if one doesn't exist for this (error_type, gv_id).
        Uses severity-adjusted gamma for new spectra.
        Auto-saves state if artifact_dir is configured.
        """
        spectrum = self.get_or_create_spectrum(bias.gv_id, bias.error_type)
        # Apply severity-adjusted decay rate
        severity_gamma = self.get_severity_gamma(bias.error_type)
        if spectrum.time_decay_gamma != severity_gamma:
            spectrum.time_decay_gamma = severity_gamma
        spectrum.add_bias(bias)
        if self.artifact_dir:
            self.save_state()

    # ── trigger checks ───────────────────────────────────────

    def should_trigger(self, gv_id: str, error_type: str) -> bool:
        """Check if the spectrum for this pair has reached the adaptive commit threshold.

        Uses confidence-calibrated threshold: lower threshold when past interventions
        were successful, higher threshold for safety-critical error types.
        """
        spectrum = self.get_spectrum(gv_id, error_type)
        if spectrum is None:
            return False
        spectrum.apply_decay()
        threshold = self.get_adaptive_threshold(gv_id, error_type)
        return spectrum.intensity >= threshold

    def get_twist_info(self, gv_id: str, error_type: str) -> Dict[str, Any]:
        """Get twist point information for a given (gv_id, error_type) pair.

        Returns a dict with pre_rca, override, and at_twist_point flag.
        """
        spectrum = self.get_spectrum(gv_id, error_type)
        if spectrum is None:
            return {
                "at_twist_point": False,
                "pre_rca": {"primary_location": "", "error_pattern": "", "bias_history": []},
                "override": {"suggested_override": ""},
            }

        spectrum.apply_decay()
        tp = TwistPoint(spectrum=spectrum)

        return {
            "at_twist_point": spectrum.is_at_twist_point,
            "intensity": spectrum.intensity,
            "level": spectrum.current_level,
            "bias_count": spectrum.bias_count,
            "pre_rca": tp.pre_rca,
            "override": tp.override,
            "downstream": tp.downstream,
        }

    def get_intensity(self, gv_id: str, error_type: str) -> float:
        """Get the current intensity for a spectrum, after applying decay."""
        spectrum = self.get_spectrum(gv_id, error_type)
        if spectrum is None:
            return 0.0
        spectrum.apply_decay()
        return spectrum.intensity

    # ── commit & reset ───────────────────────────────────────

    def on_double_loop_committed(self, event: Any) -> List[Dict[str, Any]]:
        """Handle post-commit actions after a double-loop event is committed.

        Resets the relevant spectrum and returns downstream hints.

        Args:
            event: A DoubleLoopEvent (or any object with error_type, gv_id,
                   five_whys_chain, committed attributes).

        Returns:
            List of hint dicts for downstream processing.
        """
        hints: List[Dict[str, Any]] = []

        spectrum = self.get_spectrum(event.gv_id, event.error_type)
        if spectrum is None:
            return hints

        tp = TwistPoint(spectrum=spectrum)

        # Hint 1: Rule changed
        hints.append({
            "type": "RULE_CHANGED",
            "gv_id": event.gv_id,
            "error_type": event.error_type,
            "message": f"Governance variable '{event.gv_id}' was modified via double-loop learning.",
        })

        # Hint 2: Execution check based on five_whys
        if hasattr(event, "five_whys_chain") and event.five_whys_chain:
            hints.append({
                "type": "EXECUTION_CHECK",
                "gv_id": event.gv_id,
                "message": "Monitor downstream tasks for unintended side effects.",
                "five_whys_depth": len(event.five_whys_chain),
            })
        else:
            hints.append({
                "type": "EXECUTION_CHECK",
                "gv_id": event.gv_id,
                "message": "Monitor downstream tasks for unintended side effects.",
            })

        # Reset the spectrum
        self.commit_and_reset(event.gv_id, event.error_type)

        return hints

    def commit_and_reset(self, gv_id: str, error_type: str) -> None:
        """Reset a spectrum after a successful double-loop commit.

        Records the successful intervention for adaptive threshold calibration.
        """
        spectrum = self.get_spectrum(gv_id, error_type)
        if spectrum:
            spectrum.reset()
        # Record successful intervention
        self.record_intervention_outcome(gv_id, error_type, success=True)
        if self.artifact_dir:
            self.save_state()

    # ── globals ──────────────────────────────────────────────

    def apply_all_decay(self) -> None:
        """Apply time decay to all tracked spectra."""
        for spectrum in self._spectra.values():
            spectrum.apply_decay()

    # ── dashboard ────────────────────────────────────────────

    def dashboard(self) -> Dict[str, Any]:
        """Generate a summary dashboard of all spectra."""
        self.apply_all_decay()

        active = [s for s in self._spectra.values() if s.bias_count > 0]
        at_twist = [s for s in active if s.is_at_twist_point]
        trigger_ready = [s for s in active if s.should_trigger_double_loop]

        spectra_list = []
        for s in active:
            spectra_list.append({
                "gv_id": s.gv_id,
                "error_type": s.error_type,
                "intensity": round(s.intensity, 4),
                "level": s.current_level,
                "bias_count": s.bias_count,
                "unique_locations": s.unique_locations,
                "is_at_twist_point": s.is_at_twist_point,
                "should_trigger": s.should_trigger_double_loop,
            })

        return {
            "total_spectra": len(self._spectra),
            "active_spectra": len(active),
            "at_twist_point": len(at_twist),
            "trigger_ready": len(trigger_ready),
            "spectra": spectra_list,
        }

    # ── serialization ────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all spectra to a dict."""
        return {
            "bias_weight": self.bias_weight,
            "time_decay_gamma": self.time_decay_gamma,
            "spectra": {
                key: s.to_dict() for key, s in self._spectra.items()
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MobiusController":
        """Deserialize from a dict."""
        mc = cls(
            bias_weight=d.get("bias_weight", 0.15),
            time_decay_gamma=d.get("time_decay_gamma", 7.0),
        )
        for key, sd in d.get("spectra", {}).items():
            mc._spectra[key] = CorrectionSpectrum.from_dict(sd)
        return mc

    # ── persistence ──────────────────────────────────────────

    @property
    def _state_path(self) -> Optional[Path]:
        if self.artifact_dir:
            return self.artifact_dir / "mobius_state.json"
        return None

    def save_state(self) -> None:
        """Persist all spectra state to disk."""
        path = self._state_path
        if path is None:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def load_state(self) -> bool:
        """Load persisted state from disk. Returns True if successful."""
        path = self._state_path
        if path is None or not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = self.from_dict(data)
            self.bias_weight = loaded.bias_weight
            self.time_decay_gamma = loaded.time_decay_gamma
            self._spectra = loaded._spectra
            return True
        except (json.JSONDecodeError, IOError, KeyError):
            return False

    def save(self) -> None:
        """Alias for save_state — explicit save for test compatibility."""
        self.save_state()

    def load(self) -> bool:
        """Alias for load_state — explicit load for test compatibility."""
        return self.load_state()
