"""
Negentropy — five-dimension system health metric v0.3

Academic grounding:
  1. Process Order      — Bandt & Pompe (2002) Permutation Entropy, PRL 88:174102
  2. Intent Anchoring   — Change-point ratio with exponential time decay
  3. Knowledge Efficacy — Shannon (1948) entropy on error-type distribution
  4. Information Fidelity — Retrieval efficiency ratio
  5. Double-Loop Efficacy — Binary entropy × success rate
  Composite             — Weighted sum with configurable weights

Pure Python stdlib — zero external dependencies.
"""

import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# Default weights
# ═══════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS: Dict[str, float] = {
    "process_order": 0.25,
    "intent_anchoring": 0.25,
    "knowledge_efficacy": 0.20,
    "information_fidelity": 0.15,
    "double_loop_efficacy": 0.15,
}

# ═══════════════════════════════════════════════════════════════
# Pure entropy functions (stateless, testable)
# ═══════════════════════════════════════════════════════════════

def permutation_entropy(
    sequence: List,
    D: int = 3,
    tau: int = 1,
) -> float:
    """Bandt & Pompe (2002) permutation entropy, normalized to [0, 1].

    Maps a sequence to ordinal-pattern probabilities and computes
    Shannon entropy. Returns 0 for perfectly regular sequences,
    1 for maximally disordered ones.

    Args:
        sequence: List of ordered values (numbers or strings mappable to order).
        D: Embedding dimension (3-7 recommended).
        tau: Lag between samples.

    Returns:
        Normalized permutation entropy H_norm in [0, 1].
        Returns 0 if sequence is too short (< D).
    """
    n = len(sequence)
    if n < D:
        return 0.0

    # Map sequence to numeric ranks (handle strings via index mapping)
    if sequence and isinstance(sequence[0], str):
        unique = list(dict.fromkeys(sequence))  # preserve order
        rank_map = {v: i for i, v in enumerate(unique)}
        values = [rank_map.get(v, 0) for v in sequence]
    else:
        values = list(sequence)

    # Count ordinal patterns
    pattern_counts: Dict[Tuple[int, ...], int] = {}
    max_idx = n - (D - 1) * tau
    for i in range(max_idx):
        window = values[i:i + D * tau:tau]
        # Get the permutation that sorts the window
        # pattern = tuple of ranks (0 = smallest, D-1 = largest)
        sorted_pairs = sorted(enumerate(window), key=lambda x: x[1])
        pattern = tuple(rank for rank, _ in sorted_pairs)
        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1

    total = sum(pattern_counts.values())
    if total == 0:
        return 0.0

    # Shannon entropy
    H = 0.0
    for count in pattern_counts.values():
        p = count / total
        if p > 0:
            H -= p * math.log2(p)

    # Normalize by max possible entropy: log2(D!)
    max_H = math.log2(math.factorial(D))
    if max_H == 0:
        return 0.0
    return H / max_H


def shannon_entropy(categories: List[str]) -> float:
    """Shannon (1948) entropy on a categorical distribution, normalized [0, 1].

    Computes H = -Σ p_i log₂(p_i) / log₂(N_unique).
    Low H → distribution is concentrated (few categories dominate).
    High H → distribution is spread evenly across categories.

    Args:
        categories: List of category labels (may contain duplicates).

    Returns:
        Normalized entropy in [0, 1]. Empty list → 0.
    """
    if not categories:
        return 0.0

    counter = Counter(categories)
    total = len(categories)
    n_unique = len(counter)

    if n_unique <= 1:
        return 0.0

    H = 0.0
    for count in counter.values():
        p = count / total
        if p > 0:
            H -= p * math.log2(p)

    max_H = math.log2(n_unique)
    if max_H == 0:
        return 0.0
    return H / max_H


def binary_entropy(p: float) -> float:
    """Binary Shannon entropy H(p) = -p log₂(p) - (1-p) log₂(1-p).

    Args:
        p: Success probability in [0, 1].

    Returns:
        Entropy in [0, 1]. H(0.5) = 1.0 (max uncertainty),
        H(0) = H(1) = 0.0 (certainty).
    """
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)


# ═══════════════════════════════════════════════════════════════
# Dimension computation helpers (stateless, pure)
# ═══════════════════════════════════════════════════════════════

def _compute_process_order(phase_history: list) -> float:
    """Permutation entropy on the phase transition sequence.

    Phase regressions, switches, and stagnation all increase entropy.
    Returns 0-100 score (higher = more ordered).
    """
    if not phase_history or len(phase_history) < 2:
        return 100.0  # not enough data, assume ordered

    # Extract phase names from history entries
    # phase_history entries are dicts: {"from": ..., "to": ...} or {"phase": ..., ...}
    phases = []
    for entry in phase_history[-50:]:  # last 50 transitions
        if isinstance(entry, dict):
            phase = entry.get("to") or entry.get("phase") or ""
            if phase:
                phases.append(phase)

    if len(phases) < 3:
        return 100.0

    # Permutation entropy on the phase sequence
    H_norm = permutation_entropy(phases, D=3, tau=1)

    # Invert: high entropy = disorder = low score
    score = (1.0 - H_norm) * 100.0

    # Stagnation penalty: same phase repeated many times in a row
    max_run = _max_consecutive_run(phases)
    if max_run >= 10:
        score = max(0.0, score - 10.0)

    return round(score, 1)


def _compute_intent_anchoring(
    anchor_changes: int,
    total_subtasks: int,
    last_change_at: Optional[str] = None,
) -> float:
    """Change-point ratio with exponential time decay.

    Recent anchor changes hurt more than old ones.
    Returns 0-100 score.
    """
    total = max(total_subtasks, 1)
    change_rate = anchor_changes / total

    # Base score: fewer changes = higher score
    score = 100.0 * (1.0 - min(change_rate, 1.0))

    # Time decay penalty: recent changes reduce score more
    if last_change_at:
        try:
            change_dt = datetime.fromisoformat(last_change_at)
            elapsed_minutes = (
                datetime.now(timezone.utc) - change_dt
            ).total_seconds() / 60.0

            if elapsed_minutes < 5:
                score *= 0.80   # very recent: heavy penalty
            elif elapsed_minutes < 30:
                score *= 0.90   # moderately recent: light penalty
            # > 30 min: no penalty
        except (ValueError, TypeError):
            pass

    return round(max(0.0, score), 1)


def _compute_knowledge_efficacy(efficacy_events: Optional[dict] = None) -> float:
    """Shannon entropy on error-type distribution from efficacy events.

    Low entropy = same error dominates (system stuck).
    High entropy = errors are diverse (system encountering variety).
    No errors = perfect score.

    Returns 0-100 score.
    """
    if not efficacy_events:
        return 100.0  # no events = no errors = perfect

    # Collect error types from resolved events
    error_types = []
    for ev in efficacy_events.values():
        et = getattr(ev, "error_type", "") or ev.get("error_type", "") if isinstance(ev, dict) else ""
        if et:
            error_types.append(et)

    if not error_types:
        return 100.0

    # Shannon entropy on error distribution
    H_norm = shannon_entropy(error_types)

    # Count dominance of most frequent error
    counter = Counter(error_types)
    dominant_ratio = max(counter.values()) / len(error_types)

    # High entropy = diverse errors = better (not stuck on one problem)
    # But dominant errors still hurt
    score = H_norm * 100.0 * (1.0 - dominant_ratio * 0.5)

    return round(max(0.0, min(100.0, score)), 1)


def _compute_information_fidelity(
    retrieve_calls: int,
    total_subtasks: int,
) -> float:
    """Retrieval efficiency ratio.

    Fewer KB retrievals per subtask = more efficient context use.
    Returns 0-100 score.
    """
    total = max(total_subtasks, 1)
    ratio = retrieve_calls / total

    if ratio <= 2.0:
        score = 100.0
    elif ratio <= 5.0:
        score = 90.0
    elif ratio <= 10.0:
        score = 70.0
    elif ratio <= 20.0:
        score = 40.0
    else:
        score = max(0.0, 100.0 - ratio * 3.0)

    # Empty retrieval penalty: lots of calls but few tasks
    if total_subtasks > 10 and ratio > 15.0:
        score = max(0.0, score - 10.0)

    return round(score, 1)


def _compute_double_loop_efficacy(
    verified: int,
    reverted: int,
) -> float:
    """Binary entropy × success rate.

    High success rate with high certainty = high score.
    Uncertain (50/50) = low score. Certain failure = 0.

    Returns 0-100 score.
    """
    total = verified + reverted
    if total < 3:
        return 50.0  # insufficient data, neutral

    p = verified / total
    H = binary_entropy(p)
    certainty = 1.0 - H

    # certainty × success_bias
    score = certainty * p * 100.0

    return round(max(0.0, min(100.0, score)), 1)


def _max_consecutive_run(items: list) -> int:
    """Return the length of the longest run of identical consecutive items."""
    if not items:
        return 0
    max_run = 1
    current_run = 1
    for i in range(1, len(items)):
        if items[i] == items[i - 1]:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run


# ═══════════════════════════════════════════════════════════════
# Negentropy — main class (backward-compatible API)
# ═══════════════════════════════════════════════════════════════

class Negentropy:
    """Five-dimension negentropy (system order) metric for AI agent health.

    Academic grounding:
      - Process Order:   Bandt & Pompe (2002) permutation entropy
      - Knowledge Eff.:  Shannon (1948) entropy on error distribution
      - Double-Loop Eff: Binary entropy × success rate
      - Scheffer et al. (2009) critical slowing framework (via CSD module)

    Args:
        state: State dict from StatesMachine.load_state().
        efficacy: Optional DoubleLoopEfficacyTracker instance.
        weights: Optional dict overriding DEFAULT_WEIGHTS.
    """

    def __init__(
        self,
        state: Dict[str, Any],
        efficacy: Any = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.state = state
        self.efficacy = efficacy
        self._weights = weights or dict(DEFAULT_WEIGHTS)
        # Pre-extract values for dimension methods
        self._phase_history = state.get("phase_history", []) or []
        self._anchor_changes = state.get("anchor_changes", 0)
        self._total_subtasks = state.get("total_subtasks", 0)
        self._retrieve_calls = state.get("retrieve_calls", 0)
        self._last_anchor_change = state.get("last_anchor_change_at", None)

    # ── Five dimensions ────────────────────────────────────

    def process_order(self) -> float:
        """Phase sequence order via permutation entropy (Bandt & Pompe 2002)."""
        return _compute_process_order(self._phase_history)

    def intent_anchoring(self) -> float:
        """Goal stability via change-frequency ratio with time decay."""
        return _compute_intent_anchoring(
            self._anchor_changes,
            self._total_subtasks,
            self._last_anchor_change,
        )

    def knowledge_efficacy(self) -> float:
        """Error diversity via Shannon entropy on error types."""
        events = getattr(self.efficacy, "_events", None) if self.efficacy else None
        return _compute_knowledge_efficacy(events)

    def information_fidelity(self) -> float:
        """Retrieval efficiency ratio."""
        return _compute_information_fidelity(
            self._retrieve_calls,
            self._total_subtasks,
        )

    def double_loop_efficacy(self) -> float:
        """Double-loop success certainty via binary entropy."""
        if self.efficacy and hasattr(self.efficacy, "verified_count"):
            return _compute_double_loop_efficacy(
                self.efficacy.verified_count,
                self.efficacy.reverted_count,
            )
        return 50.0

    # ── Composite ──────────────────────────────────────────

    def composite(self) -> float:
        """Weighted composite negentropy score (0-100)."""
        w = self._weights
        score = (
            w["process_order"] * self.process_order()
            + w["intent_anchoring"] * self.intent_anchoring()
            + w["knowledge_efficacy"] * self.knowledge_efficacy()
            + w["information_fidelity"] * self.information_fidelity()
            + w["double_loop_efficacy"] * self.double_loop_efficacy()
        )
        return round(score, 1)

    # ── Serialization ──────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Full metrics dict (MCP / dashboard compatible)."""
        return {
            "composite": self.composite(),
            "process_order": self.process_order(),
            "intent_anchoring": self.intent_anchoring(),
            "knowledge_efficacy": self.knowledge_efficacy(),
            "information_fidelity": self.information_fidelity(),
            "double_loop_efficacy": self.double_loop_efficacy(),
            "weights": dict(self._weights),
            "raw": {
                "phase_regressions": self.state.get("phase_regressions", 0),
                "phase_switches": self.state.get("phase_switches", 0),
                "anchor_changes": self._anchor_changes,
                "stagnation_events": self.state.get("stagnation_events", 0),
                "retrieve_calls": self._retrieve_calls,
                "total_subtasks": self._total_subtasks,
                "efficacy": (
                    self.efficacy.to_dict() if self.efficacy and hasattr(self.efficacy, "to_dict") else {}
                ),
            },
        }

    def breakdown(self) -> Dict[str, Dict[str, float]]:
        """Detailed breakdown with per-dimension metadata."""
        d = self.to_dict()
        tiers = {n: _tier_label(s) for n, s in d.items()
                 if n not in ("composite", "weights", "raw") and isinstance(s, (int, float))}
        return {
            "composite": {"score": d["composite"], "tier": _tier_label(d["composite"])},
            "dimensions": {
                name: {"score": d[name], "tier": tiers.get(name, "?"), "weight": self._weights.get(name, 0)}
                for name in self._weights
            },
        }


# ═══════════════════════════════════════════════════════════════
# NegentropyHistory — time series tracking
# ═══════════════════════════════════════════════════════════════

@dataclass
class NegentropySnapshot:
    """A single point in the negentropy time series."""
    timestamp: str = ""
    composite: float = 0.0
    dimensions: Dict[str, float] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "composite": self.composite,
            "dimensions": dict(self.dimensions),
            "weights": dict(self.weights),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NegentropySnapshot":
        return cls(
            timestamp=d.get("timestamp", ""),
            composite=d.get("composite", 0.0),
            dimensions=d.get("dimensions", {}),
            weights=d.get("weights", {}),
        )


class NegentropyHistory:
    """Time-series tracker for negentropy scores.

    Stores snapshots as JSONL for append-only persistence.
    Supports trend detection and degradation alerts.

    Args:
        artifacts_dir: Directory for history file storage.
        max_entries: Maximum snapshots to keep in memory (default 1000).
    """

    def __init__(
        self,
        artifacts_dir: Optional[Path] = None,
        max_entries: int = 1000,
    ):
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else Path(".")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._snapshots: List[NegentropySnapshot] = []
        self._history_file = self.artifacts_dir / "negentropy_history.jsonl"
        self._load()

    # ── Persistence ────────────────────────────────────────

    def _load(self) -> None:
        """Load history from JSONL file."""
        if not self._history_file.exists():
            return
        try:
            with open(self._history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self._snapshots.append(NegentropySnapshot.from_dict(d))
                    except (json.JSONDecodeError, KeyError):
                        pass
        except IOError:
            pass

    def _save_one(self, snapshot: NegentropySnapshot) -> None:
        """Append a single snapshot to the JSONL file."""
        try:
            with open(self._history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")
        except IOError:
            pass

    # ── Recording ──────────────────────────────────────────

    def record(self, snapshot_dict: Dict[str, Any]) -> None:
        """Record a new snapshot from a Negentropy.to_dict() output."""
        dims = {
            k: v for k, v in snapshot_dict.items()
            if k not in ("composite", "weights", "raw")
            and isinstance(v, (int, float))
        }
        s = NegentropySnapshot(
            composite=snapshot_dict.get("composite", 0.0),
            dimensions=dims,
            weights=snapshot_dict.get("weights", {}),
        )
        self._snapshots.append(s)
        if len(self._snapshots) > self.max_entries:
            self._snapshots = self._snapshots[-self.max_entries:]
        self._save_one(s)

    # ── Queries ────────────────────────────────────────────

    def recent(self, n: int = 10) -> List[NegentropySnapshot]:
        """Return the n most recent snapshots."""
        return self._snapshots[-n:]

    def delta(self, n: int = 1) -> float:
        """Change in composite score from n snapshots ago."""
        if len(self._snapshots) < n + 1:
            return 0.0
        return round(
            self._snapshots[-1].composite - self._snapshots[-(n + 1)].composite, 1
        )

    def trend(self, window: int = 10) -> float:
        """Simple linear regression slope on last `window` composite scores.

        Returns:
            Slope (score change per snapshot). Positive = improving.
        """
        if len(self._snapshots) < 3:
            return 0.0
        pts = self._snapshots[-window:]
        n = len(pts)
        if n < 3:
            return 0.0

        # Simple linear regression: y = slope * x + intercept
        x_mean = (n - 1) / 2.0
        y_mean = sum(s.composite for s in pts) / n

        numerator = 0.0
        denominator = 0.0
        for i, s in enumerate(pts):
            dx = i - x_mean
            numerator += dx * (s.composite - y_mean)
            denominator += dx * dx

        if denominator == 0.0:
            return 0.0
        return round(numerator / denominator, 3)

    def is_degrading(self, consecutive: int = 3) -> bool:
        """True if composite has been dropping for `consecutive` snapshots."""
        if len(self._snapshots) < consecutive + 1:
            return False
        recent = self._snapshots[-consecutive - 1:]
        for i in range(1, len(recent)):
            if recent[i].composite >= recent[i - 1].composite:
                return False
        return True

    @property
    def latest(self) -> Optional[NegentropySnapshot]:
        """Most recent snapshot, or None."""
        return self._snapshots[-1] if self._snapshots else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self._snapshots),
            "latest_composite": self.latest.composite if self.latest else None,
            "delta": self.delta(1),
            "trend": self.trend(),
            "degrading": self.is_degrading(),
            "recent": [s.to_dict() for s in self.recent(5)],
        }


# ═══════════════════════════════════════════════════════════════
# Output generators
# ═══════════════════════════════════════════════════════════════

def _tier_label(score: float) -> str:
    """Return tier label for a score."""
    if score >= 80:
        return "优秀"
    elif score >= 60:
        return "尚可"
    elif score >= 40:
        return "需改进"
    else:
        return "严重熵增"


def _bar(score: float, width: int = 20) -> str:
    """ASCII bar chart for a 0-100 score."""
    filled = max(0, min(width, int(score / 100.0 * width)))
    return "█" * filled + "░" * (width - filled)


def generate_integrity_report(ne: Negentropy) -> str:
    """One-line text report for logging / MCP output."""
    d = ne.to_dict()
    return (
        f"COMPOSITE: {d['composite']}% | "
        f"PO: {d['process_order']}% | "
        f"IA: {d['intent_anchoring']}% | "
        f"KE: {d['knowledge_efficacy']}% | "
        f"IF: {d['information_fidelity']}% | "
        f"DLE: {d['double_loop_efficacy']}%"
    )


def generate_dashboard(ne: Negentropy) -> str:
    """HTML dashboard with inline CSS for the five-dimension negentropy meter."""
    d = ne.to_dict()
    composite = d["composite"]
    tier = _tier_label(composite)

    dims = [
        ("流程有序度", d["process_order"], "Bandt & Pompe 2002 排列熵"),
        ("意图锚定度", d["intent_anchoring"], "变更频率 + 时间衰减"),
        ("知识生效度", d["knowledge_efficacy"], "Shannon 1948 错误分布熵"),
        ("信息保真度", d["information_fidelity"], "检索效率比"),
        ("双环效能", d["double_loop_efficacy"], "二元熵 × 成功率"),
    ]

    rows = ""
    for name, score, method in dims:
        t = _tier_label(score)
        rows += f"""
        <tr>
            <td class="dim-name">{name}</td>
            <td class="dim-bar"><div class="bar-bg"><div class="bar-fill" style="width:{score}%"></div></div></td>
            <td class="dim-score">{score}%</td>
            <td class="dim-tier tier-{t}">{t}</td>
            <td class="dim-method">{method}</td>
        </tr>"""

    raw = d.get("raw", {})
    efficacy_raw = raw.get("efficacy", {})

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>苦瓜code · 负熵仪表板</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:system-ui,-apple-system,sans-serif; background:#0d1117; color:#c9d1d9; padding:2rem; max-width:800px; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin-bottom:.25rem; }}
.subtitle {{ color:#8b949e; font-size:.85rem; margin-bottom:2rem; }}
.composite {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:1.5rem; text-align:center; margin-bottom:1.5rem; }}
.composite .score {{ font-size:3rem; font-weight:700; }}
.composite .tier {{ font-size:1rem; margin-top:.5rem; }}
.tier-优秀 {{ color:#3fb950; }}
.tier-尚可 {{ color:#d29922; }}
.tier-需改进 {{ color:#db6d28; }}
.tier-严重熵增 {{ color:#f85149; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; padding:.5rem .75rem; color:#8b949e; font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; border-bottom:1px solid #30363d; }}
td {{ padding:.6rem .75rem; border-bottom:1px solid #21262d; font-size:.9rem; }}
.dim-name {{ font-weight:600; }}
.dim-score {{ text-align:right; font-variant-numeric:tabular-nums; }}
.dim-method {{ color:#8b949e; font-size:.75rem; }}
.bar-bg {{ background:#21262d; border-radius:4px; height:8px; width:120px; overflow:hidden; }}
.bar-fill {{ height:100%; border-radius:4px; background:linear-gradient(90deg,#3fb950,#d29922,#f85149); background-size:120px 100%; background-position:{int(100-composite)}% 0; }}
.raw {{ margin-top:2rem; padding:1rem; background:#161b22; border-radius:6px; font-size:.8rem; color:#8b949e; }}
.raw h3 {{ color:#c9d1d9; margin-bottom:.5rem; font-size:.9rem; }}
.raw-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:.5rem; }}
</style>
</head>
<body>
<h1>🍈 苦瓜code · 负熵仪表板</h1>
<p class="subtitle">kugua core v0.3 · 五维系统健康度量</p>

<div class="composite">
    <div class="score tier-{tier}">{composite}%</div>
    <div class="tier tier-{tier}">{tier}</div>
</div>

<table>
<thead><tr><th>维度</th><th></th><th>得分</th><th>评级</th><th>方法</th></tr></thead>
<tbody>{rows}</tbody>
</table>

<div class="raw">
<h3>原始数据</h3>
<div class="raw-grid">
    <div>阶段回退: {raw.get('phase_regressions',0)}</div>
    <div>阶段切换: {raw.get('phase_switches',0)}</div>
    <div>锚点变更: {raw.get('anchor_changes',0)}</div>
    <div>停滞事件: {raw.get('stagnation_events',0)}</div>
    <div>检索调用: {raw.get('retrieve_calls',0)}</div>
    <div>子任务数: {raw.get('total_subtasks',0)}</div>
    <div>已验证双环: {efficacy_raw.get('verified_events',0)}</div>
    <div>已回滚双环: {efficacy_raw.get('reverted_events',0)}</div>
    <div>总熵减: {efficacy_raw.get('total_entropy_reduction',0)}</div>
</div>
</div>

</body>
</html>"""
