"""
Double-Loop Efficacy Tracker — measures permanent entropy reduction from rule changes.

Tracks: baseline → modified → outcome (verified / reverted) for each
double-loop learning event. Computes cumulative entropy reduction (delta-S).

Pure Python stdlib — no external dependencies.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# EfficacyEvent
# ═══════════════════════════════════════════════════════════════

@dataclass
class EfficacyEvent:
    """A single double-loop modification event being tracked."""

    event_id: str
    error_type: str
    gv_id: str
    status: str = "baseline"       # baseline | modified | verified | reverted
    entropy_delta: float = 0.0     # positive = entropy reduction (good)
    started_at: str = ""
    modified_at: str = ""
    resolved_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "error_type": self.error_type,
            "gv_id": self.gv_id,
            "status": self.status,
            "entropy_delta": self.entropy_delta,
            "started_at": self.started_at,
            "modified_at": self.modified_at,
            "resolved_at": self.resolved_at,
        }


# ═══════════════════════════════════════════════════════════════
# DoubleLoopEfficacyTracker
# ═══════════════════════════════════════════════════════════════

class DoubleLoopEfficacyTracker:
    """Tracks double-loop learning efficacy over time.

    Each event goes through: baseline → modified → verified/reverted.
    Only verified events contribute to permanent entropy reduction.

    Args:
        artifacts_dir: Directory for persisting tracker state.
    """

    def __init__(self, artifacts_dir: Optional[Path] = None):
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else Path(".")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._events: Dict[str, EfficacyEvent] = {}
        self._state_file = self.artifacts_dir / "efficacy_state.json"
        self._load_state()

    # ── persistence ──────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted events from disk."""
        if self._state_file.exists():
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for eid, edict in data.items():
                    self._events[eid] = EfficacyEvent(**edict)
            except (json.JSONDecodeError, IOError, TypeError):
                self._events = {}

    def _save_state(self) -> None:
        """Persist events to disk."""
        try:
            data = {eid: ev.to_dict() for eid, ev in self._events.items()}
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── lifecycle ────────────────────────────────────────────

    def start_baseline(self, error_type: str, gv_id: str) -> str:
        """Record the start of a double-loop modification.

        Args:
            error_type: Error category.
            gv_id: Governance variable ID.

        Returns:
            event_id: Unique identifier for this efficacy tracking event.
        """
        event_id = f"eff_{uuid.uuid4().hex[:12]}"
        event = EfficacyEvent(
            event_id=event_id,
            error_type=error_type,
            gv_id=gv_id,
            status="baseline",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._events[event_id] = event
        self._save_state()
        return event_id

    def mark_modified(self, event_id: str) -> bool:
        """Mark that the rule modification has been applied.

        Returns:
            True if the event was found and updated.
        """
        event = self._events.get(event_id)
        if event is None:
            return False
        event.status = "modified"
        event.modified_at = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return True

    def record_outcome(self, event_id: str, success: bool, entropy_delta: float = 0.0) -> bool:
        """Record the final outcome of a double-loop modification.

        Args:
            event_id: The efficacy event ID.
            success: True if the modification was validated and committed.
            entropy_delta: Estimated entropy reduction (positive = good).

        Returns:
            True if the event was found and updated.
        """
        event = self._events.get(event_id)
        if event is None:
            return False
        event.status = "verified" if success else "reverted"
        event.entropy_delta = entropy_delta if success else 0.0
        event.resolved_at = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return True

    # ── queries ──────────────────────────────────────────────

    @property
    def total_entropy_reduction(self) -> float:
        """Cumulative entropy reduction from all verified events."""
        return sum(
            ev.entropy_delta
            for ev in self._events.values()
            if ev.status == "verified"
        )

    @property
    def pending_count(self) -> int:
        """Number of events still in baseline or modified state."""
        return sum(
            1 for ev in self._events.values()
            if ev.status in ("baseline", "modified")
        )

    @property
    def verified_count(self) -> int:
        """Number of verified (committed) events."""
        return sum(1 for ev in self._events.values() if ev.status == "verified")

    @property
    def reverted_count(self) -> int:
        """Number of reverted events."""
        return sum(1 for ev in self._events.values() if ev.status == "reverted")

    def get_verified_reductions(self) -> List[Dict[str, Any]]:
        """Return list of verified events with their entropy reductions."""
        return [
            ev.to_dict()
            for ev in self._events.values()
            if ev.status == "verified"
        ]

    def get_event(self, event_id: str) -> Optional[EfficacyEvent]:
        """Retrieve a specific event by ID."""
        return self._events.get(event_id)

    def to_dict(self) -> Dict[str, Any]:
        """Summary dict for dashboards and MCP tools."""
        return {
            "verified_events": self.verified_count,
            "total_entropy_reduction": round(self.total_entropy_reduction, 1),
            "pending_events": self.pending_count,
            "reverted_events": self.reverted_count,
            "total_events": len(self._events),
        }
