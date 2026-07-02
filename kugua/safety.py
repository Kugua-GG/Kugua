"""SafetyManager — trust gradient, permission gating, audit trail, kill switch.

v0.2.2: Added AuditTrail, time-filtered incident queries, improved permission model.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ── Trust levels ──────────────────────────────────────────
L1_TRUST, L2_TRUST, L3_TRUST, L4_TRUST, L5_TRUST = 1, 2, 3, 4, 5

@dataclass
class TrustLevel:
    value: int = L2_TRUST
    def __ge__(self, o): return self.value >= (o.value if isinstance(o, TrustLevel) else o)
    def __lt__(self, o): return self.value < (o.value if isinstance(o, TrustLevel) else o)

# ── Operation permissions ─────────────────────────────────
OPERATION_PERMISSIONS: Dict[str, int] = {
    "read_file": L1_TRUST,
    "write_file": L3_TRUST,
    "edit_file": L3_TRUST,
    "execute_cmd": L4_TRUST,
    "delete_file": L4_TRUST,
    "rm_rf": L5_TRUST,          # permanently prohibited
    "sudo": L5_TRUST,
    "git_push_force": L5_TRUST,
    "eval_shell": L5_TRUST,
    "pipe_to_sh": L5_TRUST,
}

# ── Incident ──────────────────────────────────────────────

@dataclass
class Incident:
    level: str = "IV"
    category: str = ""
    description: str = ""
    impact: str = ""
    score: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

# ── Audit Trail ───────────────────────────────────────────

@dataclass
class AuditEntry:
    """Single entry in the audit trail."""
    timestamp: str = ""
    operation: str = ""
    allowed: bool = False
    reason: str = ""
    trust_level: int = L2_TRUST

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "allowed": self.allowed,
            "reason": self.reason,
            "trust_level": self.trust_level,
        }


class AuditTrail:
    """Append-only audit log for permission checks.

    Records every permission check with timestamp, result, and reason.
    Bounded to prevent unbounded memory growth.
    """

    def __init__(self, max_entries: int = 1000):
        self._entries: List[AuditEntry] = []
        self.max_entries = max_entries

    def record(
        self, operation: str, allowed: bool, reason: str, trust_level: int,
    ) -> None:
        """Record a permission check."""
        entry = AuditEntry(
            operation=operation, allowed=allowed,
            reason=reason, trust_level=trust_level,
        )
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def recent_denials(self, n: int = 20) -> List[AuditEntry]:
        """Return the n most recent denied operations."""
        denied = [e for e in self._entries if not e.allowed]
        return denied[-n:]

    def recent(self, n: int = 50) -> List[AuditEntry]:
        """Return the n most recent entries (any result)."""
        return self._entries[-n:]

    def to_dict(self) -> Dict[str, Any]:
        total = len(self._entries)
        denials = sum(1 for e in self._entries if not e.allowed)
        return {
            "total_checks": total,
            "total_denials": denials,
            "denial_rate": round(denials / max(total, 1), 3),
            "recent_denials": [e.to_dict() for e in self.recent_denials(5)],
        }


# ═══════════════════════════════════════════════════════════
# SafetyManager
# ═══════════════════════════════════════════════════════════

class SafetyManager:
    """Safety enforcement with trust gradient, audit trail, and kill switch.

    Tracks every permission check, enforces L5 permanent prohibitions,
    and supports emergency shutdown via kill_switch().
    """

    def __init__(self, config: Any = None):
        self.config = config
        self._trust_level = TrustLevel(L2_TRUST)
        self._incidents: List[Incident] = []
        self._state: Dict[str, Any] = {"emergency_stop": False}
        self._audit_trail = AuditTrail()

    # ── Permission ─────────────────────────────────────────

    def check_permission(self, operation: str) -> Tuple[bool, str]:
        """Check if an operation is allowed at the current trust level.

        Records the result in the audit trail regardless of outcome.
        """
        allowed = False
        reason = ""

        if self._state.get("emergency_stop", False):
            reason = "Emergency stop active — all operations blocked"
        elif operation in OPERATION_PERMISSIONS:
            req = OPERATION_PERMISSIONS[operation]
            if req >= L5_TRUST:
                reason = f"'{operation}' permanently prohibited (L5)"
            elif self._trust_level < req:
                reason = (
                    f"'{operation}' requires L{req}, "
                    f"current trust is L{self._trust_level.value}"
                )
            else:
                allowed = True
                reason = f"Allowed at L{self._trust_level.value}"
        else:
            allowed = True
            reason = f"'{operation}' not restricted"

        # Record audit
        self._audit_trail.record(
            operation=operation, allowed=allowed,
            reason=reason, trust_level=self._trust_level.value,
        )
        return allowed, reason

    # ── Kill switch ────────────────────────────────────────

    def kill_switch(self, reason: str = "") -> Dict[str, Any]:
        """Activate emergency stop — blocks all subsequent operations."""
        self._state["emergency_stop"] = True
        self._state["kill_reason"] = reason
        self._state["kill_timestamp"] = datetime.now(timezone.utc).isoformat()
        return dict(self._state)

    @property
    def emergency_stop_active(self) -> bool:
        return bool(self._state.get("emergency_stop", False))

    # ── Incidents ──────────────────────────────────────────

    def log_incident(self, incident: Incident) -> None:
        """Log a safety incident with severity and category."""
        self._incidents.append(incident)

    def query_incidents(self, days: int = 30) -> List[Incident]:
        """Return incidents from the last N days.

        Args:
            days: Look-back window. 0 = all incidents.
        """
        if days <= 0:
            return list(self._incidents)

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result: List[Incident] = []
        for inc in self._incidents:
            try:
                ts = datetime.fromisoformat(inc.timestamp)
                if ts >= cutoff:
                    result.append(inc)
            except (ValueError, TypeError):
                # Unparseable timestamp → include
                result.append(inc)
        return result

    # ── Audit ──────────────────────────────────────────────

    @property
    def audit(self) -> AuditTrail:
        """Access the audit trail for external queries."""
        return self._audit_trail

    def get_audit_summary(self) -> Dict[str, Any]:
        """Return a summary of audit trail activity."""
        return self._audit_trail.to_dict()
