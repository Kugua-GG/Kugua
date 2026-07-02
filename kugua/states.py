"""StatesMachine — P0-P4 state machine with transition validation and timeout detection.

v0.2.2: Added valid transition matrix, phase guards, timeout tracking.
"""

import json
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

# ── Phase definitions ─────────────────────────────────────────
VALID_PHASES = [
    "P0_init", "P0_ready",
    "P1_planned", "P1_frozen",
    "P2_executed", "P2_partial",
    "P3_reviewed", "P3_failed",
    "P4_delivered",
]
PHASE_ORDER = {p: i for i, p in enumerate(VALID_PHASES)}

# Valid transitions: current → {allowed next phases}
VALID_TRANSITIONS: Dict[str, Set[str]] = {
    "P0_init":      {"P0_ready"},
    "P0_ready":     {"P1_planned"},
    "P1_planned":   {"P1_frozen", "P0_ready"},
    "P1_frozen":    {"P2_executed"},
    "P2_executed":  {"P2_partial", "P3_reviewed"},
    "P2_partial":   {"P2_executed", "P3_failed"},
    "P3_reviewed":  {"P4_delivered", "P2_executed"},
    "P3_failed":    {"P1_planned", "P4_delivered"},
    "P4_delivered": set(),
}

# Default phase timeout (seconds) — warns if a phase runs too long
DEFAULT_PHASE_TIMEOUTS: Dict[str, int] = {
    "P0_ready":  300,   # 5 min
    "P1_planned": 600,  # 10 min
    "P1_frozen":  300,  # 5 min
    "P2_executed": 1800, # 30 min
    "P2_partial": 900,   # 15 min
    "P3_reviewed": 600,  # 10 min
    "P3_failed":  300,   # 5 min
    "P4_delivered": 0,   # terminal
}


class PhaseTransitionError(Exception):
    """Raised when an invalid phase transition is attempted."""
    pass


@dataclass
class AlignResult:
    aligned: bool = True
    drift_score: float = 0.0
    detail: str = ""


# ═══════════════════════════════════════════════════════════
# StatesMachine
# ═══════════════════════════════════════════════════════════

class StatesMachine:
    """P0-P4 state machine with transition guards and timeout detection.

    Enforces valid phase transitions via VALID_TRANSITIONS matrix.
    Tracks phase duration for timeout warnings.
    Persists state to JSON for crash recovery.
    """

    def __init__(self, config: Any = None):
        self.config = config
        self._artifacts_dir = Path(getattr(config, "artifacts_dir", "."))
        self._current_phase: str = "P0_init"  # in-memory cache

    # ── Initialization ─────────────────────────────────────

    def p0_self_check(self) -> Dict:
        """Initialize a fresh state dict at P0_ready."""
        state = {
            "current_phase": "P0_ready",
            "phase_history": [],
            "phase_regressions": 0,
            "phase_switches": 0,
            "stagnation_events": 0,
            "anchor_changes": 0,
            "retrieve_calls": 0,
            "total_subtasks": 0,
            "phase_started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._current_phase = "P0_ready"
        self.save_state(state)
        return state

    # ── Transition ─────────────────────────────────────────

    def transition(self, state: Dict, target: str) -> Dict:
        """Transition to a target phase, with validation.

        Args:
            state: Current state dict (mutated in place).
            target: Target phase name (e.g. "P1_planned").

        Returns:
            The mutated state dict.

        Raises:
            PhaseTransitionError: if the transition is not allowed.
        """
        current = state.get("current_phase", "P0_init")

        # Validate transition
        allowed = VALID_TRANSITIONS.get(current, set())
        if target not in allowed and current != target:
            raise PhaseTransitionError(
                f"Invalid transition: {current} → {target}. "
                f"Allowed from {current}: {allowed or '(none — terminal)'}"
            )

        # Same phase = no-op
        if target == current:
            return state

        # Regression detection
        cur_idx = PHASE_ORDER.get(current, 0)
        tgt_idx = PHASE_ORDER.get(target, 0)
        if tgt_idx < cur_idx:
            state["phase_regressions"] = state.get("phase_regressions", 0) + 1

        # Record history
        history = state.get("phase_history", [])
        history.append({
            "from": current,
            "to": target,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        state["phase_history"] = history[-50:]  # keep last 50 transitions

        # Update state
        state["phase_switches"] = state.get("phase_switches", 0) + 1
        state["current_phase"] = target
        state["phase_started_at"] = datetime.now(timezone.utc).isoformat()
        self._current_phase = target  # sync in-memory cache

        return state

    # ── Queries ────────────────────────────────────────────

    def get_current_phase(self) -> str:
        """Return the current phase (prefers in-memory cache, falls back to disk)."""
        if self._current_phase != "P0_init":
            return self._current_phase
        return self.load_state().get("current_phase", "P0_init")

    def is_terminal(self) -> bool:
        """Check if the current phase is terminal (P4_delivered)."""
        return self.get_current_phase() == "P4_delivered"

    def can_transition_to(self, target: str) -> bool:
        """Check if a transition to `target` is currently valid."""
        current = self.get_current_phase()
        return target in VALID_TRANSITIONS.get(current, set())

    def is_stale(self, timeout_seconds: Optional[int] = None) -> bool:
        """Check if the current phase has exceeded its timeout.

        Args:
            timeout_seconds: Override the default phase timeout.
                             If None, uses DEFAULT_PHASE_TIMEOUTS.

        Returns:
            True if the phase has been active longer than its timeout.
        """
        state = self.load_state()
        started = state.get("phase_started_at", "")
        if not started:
            return False

        current = state.get("current_phase", "")
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_PHASE_TIMEOUTS.get(current, 0)

        if timeout_seconds <= 0:  # terminal or no timeout
            return False

        try:
            started_dt = datetime.fromisoformat(started)
            elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()
            return elapsed > timeout_seconds
        except (ValueError, TypeError):
            return False

    # ── Persistence ────────────────────────────────────────

    def save_state(self, state: Dict) -> None:
        """Persist state to JSON file."""
        p = self._artifacts_dir / "agent_state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def load_state(self) -> Dict:
        """Load state from JSON, or create fresh P0 state."""
        p = self._artifacts_dir / "agent_state.json"
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return self.p0_self_check()

    def crash_recovery(self) -> Dict:
        """Recover state after a crash — returns the last known good state.

        If the saved state has a stale phase (timeout exceeded), resets to
        a safe recovery point (P0_ready).
        """
        state = self.load_state()
        if self.is_stale():
            # Stale phase → reset to safe state
            state["current_phase"] = "P0_ready"
            state["phase_regressions"] = state.get("phase_regressions", 0) + 1
            state["phase_history"] = (state.get("phase_history", []) or [])[-50:]
            history = state.get("phase_history", [])
            history.append({
                "from": "(stale)",
                "to": "P0_ready",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "crash_recovery_timeout",
            })
            state["phase_history"] = history[-50:]
            self._current_phase = "P0_ready"
            self.save_state(state)
        else:
            self._current_phase = state.get("current_phase", "P0_init")
        return state
