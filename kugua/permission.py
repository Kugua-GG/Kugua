"""
kugua — PermissionGate (independent permission control)
v0.3.0

Risk-level based operation gating with trust gradient.
"""
from __future__ import annotations
from typing import Callable, Optional

RISK_LEVEL = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class PermissionGate:
    """Independent permission gate for task execution.

    Usage:
        gate = PermissionGate(trust_level=2)
        allowed, reason = gate.check("execute_task", risk_level=2, detail="subtask=foo")
    """

    def __init__(self, trust_level_or_safety = None, blocked_actions: list[str] = None):
        # 兼容旧 API: PermissionGate(SafetyManager) 和新 API: PermissionGate(trust_level=2)
        if hasattr(trust_level_or_safety, 'trust_level'):
            self.trust_level = trust_level_or_safety.trust_level.value if hasattr(
                trust_level_or_safety.trust_level, 'value') else 2
        else:
            self.trust_level = trust_level_or_safety if isinstance(trust_level_or_safety, int) else 2
        if self.trust_level is None:
            self.trust_level = 2
        self.blocked = set(blocked_actions or [])
        self.blocked.update({"rm_rf", "sudo", "chmod_777", "git_push_force"})

    def check(self, action: str, risk_level: int = 1, detail: str = "") -> tuple[bool, str]:
        if action in self.blocked:
            return False, f"Action '{action}' is permanently blocked"
        if risk_level > self.trust_level + 1:
            return False, f"Risk level {risk_level} exceeds trust level {self.trust_level}"
        return True, f"Allowed at trust level {self.trust_level}"
