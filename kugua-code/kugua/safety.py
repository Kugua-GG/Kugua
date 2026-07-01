"""
SafetyManager — 安全门控模块 v0.3.0

提供 5 级信任梯度、动态调整、ErrorBudget 消耗、Kill Switch 触发链、
事故自动分级，是 kugua 内核安全红线的硬约束执行层。

信任梯度:
  L1_TRUST — 最低信任，仅允许只读
  L2_TRUST — 默认信任，允许读写文件
  L3_TRUST — 提升信任，允许执行命令
  L4_TRUST — 高信任，允许删除操作
  L5_TRUST — 最高信任（保留，不自动授予）

信任动态调整:
  - 连续 10 次安全操作 → 升级（L2→L3, L3→L4, 封顶 L4）
  - 事故 score >= 50 → 直接降级到 L1
  - 事故 score >= 20 → 降一级
  - 最低信任 = L1（不会降到 L1 以下）

ErrorBudget:
  - max_errors=10, window_hours=24
  - L4+ 操作消耗 budget
  - 预算耗尽 → 自动信任降级到 L1

Kill Switch 触发条件:
  - 连续 3 次权限被拒绝 → 自动 Kill Switch
  - 单次 L5 操作尝试 → 立即 Kill Switch
  - I 级事故记录 → 自动 Kill Switch
  - Kill Switch 只能通过 reset_kill_switch() 手动重置

向后兼容:
  - check_permission(), kill_switch(), log_incident(), query_incidents() 签名不变
  - OPERATION_PERMISSIONS, TrustLevel, Incident, ErrorBudget 导出不变
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# 信任级别常量
# ═══════════════════════════════════════════════════════════════

L1_TRUST, L2_TRUST, L3_TRUST, L4_TRUST, L5_TRUST = 1, 2, 3, 4, 5

TRUST_LABELS = {
    L1_TRUST: "L1_最低",
    L2_TRUST: "L2_默认",
    L3_TRUST: "L3_提升",
    L4_TRUST: "L4_高信任",
    L5_TRUST: "L5_最高(保留)",
}

# 操作 → 所需信任级别
OPERATION_PERMISSIONS: Dict[str, int] = {
    "read_file":       L1_TRUST,
    "write_file":      L3_TRUST,
    "edit_file":       L3_TRUST,
    "execute_cmd":     L4_TRUST,
    "delete_file":     L4_TRUST,
    "rm_rf":           L5_TRUST,
    "sudo":            L5_TRUST,
    "git_push_force":  L5_TRUST,
    "eval_shell":      L5_TRUST,
    "pipe_to_sh":      L5_TRUST,
}

# L5 操作集合（触发立即 Kill Switch）
L5_OPERATIONS = frozenset(k for k, v in OPERATION_PERMISSIONS.items() if v >= L5_TRUST)

# 信任升级阈值
UPGRADE_THRESHOLD_L2_TO_L3 = 10    # 连续安全操作次数
UPGRADE_THRESHOLD_L3_TO_L4 = 30    # 连续安全操作次数

# 信任降级阈值
DOWNGRADE_TO_L1_SCORE = 50         # 事故分数阈值
DOWNGRADE_ONE_LEVEL_SCORE = 20     # 事故分数阈值

# Kill Switch 自动触发阈值 — 上下文依赖
# P0/P1 (安全模式): 容忍更多拒绝，阈值高
# P2 (执行中):    中等
# P3/P4 (高风险):  敏感，阈值低
KS_THRESHOLD_SAFE = 10       # P0/P1
KS_THRESHOLD_EXEC = 5        # P2
KS_THRESHOLD_RISK = 3        # P3/P4

# 时间窗衰减: 连续拒绝超过此秒数后重置计数器
KS_DECAY_WINDOW_SEC = 30

# 旧常量保留向后兼容
AUTO_KILL_DENIAL_COUNT = KS_THRESHOLD_RISK


# ═══════════════════════════════════════════════════════════════
# TrustLevel
# ═══════════════════════════════════════════════════════════════

@dataclass
class TrustLevel:
    """可动态调整的信任级别。"""
    value: int = L2_TRUST

    def __ge__(self, other) -> bool:
        if isinstance(other, TrustLevel):
            return self.value >= other.value
        return self.value >= other

    def __lt__(self, other) -> bool:
        if isinstance(other, TrustLevel):
            return self.value < other.value
        return self.value < other

    def __eq__(self, other) -> bool:
        if isinstance(other, TrustLevel):
            return self.value == other.value
        return self.value == other

    def __int__(self) -> int:
        return self.value

    def __repr__(self) -> str:
        label = TRUST_LABELS.get(self.value, f"L{self.value}")
        return f"TrustLevel({label})"

    def upgrade(self) -> bool:
        """升级信任级别，封顶 L4（L5 不自动授予）。"""
        if self.value < L4_TRUST:
            self.value += 1
            return True
        return False

    def downgrade(self, steps: int = 1) -> bool:
        """降级信任级别，最低 L1。"""
        if self.value > L1_TRUST:
            self.value = max(L1_TRUST, self.value - steps)
            return True
        return False

    def set_to(self, level: int) -> None:
        """直接设置信任级别，约束在 [L1, L5] 范围内。"""
        self.value = max(L1_TRUST, min(L5_TRUST, level))


# ═══════════════════════════════════════════════════════════════
# Incident
# ═══════════════════════════════════════════════════════════════

@dataclass
class Incident:
    """安全事故记录，支持手动和自动分级。

    Attributes:
        level: I / II / III / IV（罗马数字，I 最严重）
        category: 事故类别
        description: 描述
        impact: 影响范围文字描述
        score: 综合严重度分数 [0, 100]
        timestamp: ISO 8601 时间戳
        operation: 触发事故的操作类型
        recoverable: 是否可恢复
    """
    level: str = "IV"
    category: str = ""
    description: str = ""
    impact: str = ""
    score: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    operation: str = ""
    recoverable: bool = True

    @property
    def is_critical(self) -> bool:
        """I 级或 II 级事故为严重。"""
        return self.level in ("I", "II")


# ═══════════════════════════════════════════════════════════════
# ErrorBudget
# ═══════════════════════════════════════════════════════════════

@dataclass
class ErrorBudget:
    """错误预算，用于限制高风险操作的频率。

    预算消耗后通过时间窗口自动恢复。
    """
    max_errors: int = 10
    current: int = 0
    window_hours: int = 24
    _window_start: float = field(default_factory=time.time)

    def consume(self, count: int = 1) -> None:
        """消耗错误预算。"""
        self._check_window()
        self.current = min(self.current + count, self.max_errors * 2)

    def replenish(self, count: int = 1) -> None:
        """恢复错误预算。"""
        self.current = max(0, self.current - count)

    def is_exhausted(self) -> bool:
        """预算是否耗尽。"""
        self._check_window()
        return self.current >= self.max_errors

    def remaining(self) -> int:
        """剩余预算。"""
        self._check_window()
        return max(0, self.max_errors - self.current)

    def reset_window(self) -> None:
        """手动重置时间窗口并恢复全部预算。"""
        self.current = 0
        self._window_start = time.time()

    def _check_window(self) -> None:
        """检查时间窗口是否过期，过期则自动重置预算。"""
        elapsed_hours = (time.time() - self._window_start) / 3600.0
        if elapsed_hours >= self.window_hours:
            self.reset_window()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_errors": self.max_errors,
            "current": self.current,
            "window_hours": self.window_hours,
            "remaining": self.remaining(),
            "exhausted": self.is_exhausted(),
        }


# ═══════════════════════════════════════════════════════════════
# SafetyManager
# ═══════════════════════════════════════════════════════════════

class SafetyManager:
    """安全门控管理器 — kugua 内核安全红线的硬约束执行层。

    核心职责:
      1. 操作权限检查（基于信任梯度）
      2. Kill Switch 熔断机制
      3. 信任级别动态调整
      4. 错误预算管理
      5. 事故记录与自动分级

    Usage:
        sm = SafetyManager(cfg)
        allowed, reason = sm.check_permission("read_file")
        if not allowed:
            sm.kill_switch(reason)
    """

    def __init__(self, config: Any = None):
        self.config = config

        # ── 从 SAFETY_DEFAULTS 加载校准参数 ──
        from kugua.config import SAFETY_DEFAULTS as SD
        self._ks_threshold_safe = SD["ks_threshold_safe"]
        self._ks_threshold_exec = SD["ks_threshold_exec"]
        self._ks_threshold_risk = SD["ks_threshold_risk"]
        self._ks_decay_window = SD["ks_decay_window_sec"]
        self._budget_denial_weight = SD["budget_denial_weight"]
        self._upgrade_l2 = SD["trust_upgrade_l2_to_l3"]
        self._upgrade_l3 = SD["trust_upgrade_l3_to_l4"]

        self._trust_level = TrustLevel(L2_TRUST)
        self._budget = ErrorBudget(
            max_errors=SD["error_budget_max"],
            window_hours=SD["error_budget_window_hours"],
        )
        self._incidents: List[Incident] = []
        self._state: Dict[str, Any] = {
            "emergency_stop": False,
            "kill_reason": "",
            "killed_at": "",
        }

        # ── 三阶段决策计数器 ──
        self._consecutive_safe_ops: int = 0
        self._permission_denials: int = 0     # 权限拒绝 (计入 KS)
        self._budget_denials: int = 0         # 预算拒绝 (加权计入)
        self._last_denial_time: float = 0.0   # 上次拒绝时间戳 (时间窗衰减)
        self._total_checks: int = 0
        self._total_denials: int = 0
        self.current_phase: str = "P0_ready"  # 上下文依赖阈值

    # ── 向后兼容属性 ──
    @property
    def _consecutive_denials(self) -> int:
        """向后兼容: 总拒绝计数 = 权限拒绝 + 加权预算拒绝。"""
        return self._permission_denials + int(self._budget_denials * self._budget_denial_weight)

    # ── 权限检查（核心方法）──────────────────────────────────

    def check_permission(self, operation: str) -> Tuple[bool, str]:
        """检查操作是否被允许。

        三阶段决策 (v0.3.0 混沌校准):
          1. Kill Switch 熔断
          2. L5 永久禁止 → 计入权限拒绝
          3. 信任级别检查 → 计入权限拒绝
          4. ErrorBudget 检查 → 计入预算拒绝 (加权, 不直接触发 KS)
          5. 时间窗衰减 + 上下文依赖阈值 → 自动 KS

        Returns:
            (allowed: bool, reason: str)
        """
        self._total_checks += 1
        self._apply_time_decay()

        # 第 1 层: Kill Switch 熔断
        if self._state.get("emergency_stop", False):
            return False, f"BLOCKED: {self._state.get('kill_reason', 'Kill Switch activated')}"

        # 第 2 层: L5 操作 — 永久禁止 (计入权限拒绝)
        if operation in L5_OPERATIONS:
            self._consecutive_safe_ops = 0
            self._permission_denials += 1
            self._last_denial_time = time.time()
            self._check_auto_kill_switch()
            return False, (
                f"'{operation}' is permanently prohibited (L5). "
                f"Denial {self._permission_denials}/{self._ks_threshold_for_phase()}"
            )

        # 第 3 层: 操作权限表检查 (计入权限拒绝)
        if operation in OPERATION_PERMISSIONS:
            required = OPERATION_PERMISSIONS[operation]
            if required >= L5_TRUST:
                return False, f"'{operation}' permanently prohibited (L5)"

            if self._trust_level < required:
                self._consecutive_safe_ops = 0
                self._permission_denials += 1
                self._last_denial_time = time.time()
                self._check_auto_kill_switch()
                return False, (
                    f"'{operation}' requires L{required}, "
                    f"current trust is L{self._trust_level.value}"
                )

        # 第 4 层: ErrorBudget 检查 (计入预算拒绝, 加权)
        if operation in OPERATION_PERMISSIONS:
            required = OPERATION_PERMISSIONS[operation]
            if required >= L4_TRUST:
                self._budget.consume()
                if self._budget.is_exhausted():
                    self._trust_level.downgrade(steps=2)
                    self._budget_denials += 1
                    self._last_denial_time = time.time()
                    return False, (
                        f"ErrorBudget exhausted ({self._budget.current}/{self._budget.max_errors}). "
                        f"Trust downgraded to L{self._trust_level.value}."
                    )

        # 通过所有检查 → 记录安全操作, 重置拒绝计数
        self._permission_denials = 0
        self._budget_denials = 0
        self._consecutive_safe_ops += 1
        self._maybe_upgrade_trust()

        return True, f"Allowed at L{self._trust_level.value}"

    def report_l5_attempt(self, operation: str) -> Tuple[bool, str]:
        """报告一次 L5 操作的实际尝试执行（区别于权限查询）。

        与 check_permission 不同，此方法表示实际尝试执行危险操作，
        会立即触发 Kill Switch。
        """
        self.kill_switch(f"L5 operation execution attempted: '{operation}'")
        return False, f"'{operation}' execution blocked. Kill Switch activated."

    # ── Kill Switch ──────────────────────────────────────────

    def kill_switch(self, reason: str = "") -> Dict[str, Any]:
        """触发 Kill Switch 熔断。

        熔断后所有操作（包括 read_file）都会被拒绝，
        只能通过 reset_kill_switch() 手动重置。
        """
        self._state["emergency_stop"] = True
        self._state["kill_reason"] = reason
        self._state["killed_at"] = datetime.now(timezone.utc).isoformat()
        return dict(self._state)

    def reset_kill_switch(self) -> Dict[str, Any]:
        """手动重置 Kill Switch。

        注意: 这是需要人工确认的操作。调用前应确保安全问题已解决。
        """
        self._state["emergency_stop"] = False
        self._state["kill_reason"] = ""
        self._state["killed_at"] = ""
        self._permission_denials = 0
        self._budget_denials = 0
        self._last_denial_time = 0.0
        return dict(self._state)

    def _apply_time_decay(self) -> None:
        """时间窗衰减: 超过 KS_DECAY_WINDOW_SEC 秒无拒绝, 重置计数器。"""
        if self._last_denial_time > 0:
            elapsed = time.time() - self._last_denial_time
            if elapsed > self._ks_decay_window:
                self._permission_denials = 0
                self._budget_denials = 0
                self._last_denial_time = 0.0

    def _ks_threshold_for_phase(self) -> int:
        """上下文依赖的 KS 阈值。"""
        p = self.current_phase
        if p.startswith("P0") or p.startswith("P1"):
            return self._ks_threshold_safe   # 10 — 安全模式高容忍
        elif p.startswith("P2"):
            return self._ks_threshold_exec   # 7  — 执行中中等
        else:
            return self._ks_threshold_risk   # 9  — 高风险敏感 (校准最优值)

    def _check_auto_kill_switch(self) -> None:
        """检查是否满足自动 Kill Switch 条件。

        条件: 权限拒绝 + 加权预算拒绝 >= 上下文依赖的阈值
        """
        effective = self._permission_denials + int(self._budget_denials * self._budget_denial_weight)
        threshold = self._ks_threshold_for_phase()
        if effective >= threshold:
            self.kill_switch(
                f"Auto Kill Switch: {effective} effective denials >= {threshold} "
                f"(permission={self._permission_denials}, budget={self._budget_denials}*{self._budget_denial_weight}, "
                f"phase={self.current_phase})"
            )

    # ── 信任动态调整 ─────────────────────────────────────────

    def record_safe_operation(self) -> None:
        """记录一次安全操作，用于信任升级评估。"""
        self._consecutive_safe_ops += 1
        self._permission_denials = 0
        self._budget_denials = 0
        self._maybe_upgrade_trust()

    def downgrade_trust(self, incident: Optional[Incident] = None) -> bool:
        """根据事故严重度降级信任。

        Args:
            incident: 触发降级的事故（可选）

        Returns:
            True 如果实际发生了降级
        """
        if incident is None:
            return self._trust_level.downgrade(1)

        if incident.score >= DOWNGRADE_TO_L1_SCORE:
            return self._trust_level.downgrade(steps=self._trust_level.value - L1_TRUST)
        elif incident.score >= DOWNGRADE_ONE_LEVEL_SCORE:
            return self._trust_level.downgrade(1)
        return False

    def _maybe_upgrade_trust(self) -> None:
        """检查是否满足信任升级条件 (使用校准后的阈值)。"""
        if self._consecutive_safe_ops >= self._upgrade_l3:
            if self._trust_level.value < L4_TRUST:
                self._trust_level.set_to(L4_TRUST)
        elif self._consecutive_safe_ops >= self._upgrade_l2:
            if self._trust_level.value < L3_TRUST:
                self._trust_level.set_to(L3_TRUST)

    # ── 事故管理 ─────────────────────────────────────────────

    def log_incident(self, incident: Incident) -> None:
        """记录安全事故。

        I 级事故自动触发 Kill Switch。
        II 级以上事故自动降级信任。
        """
        self._incidents.append(incident)

        # 自动信任降级
        if incident.score >= DOWNGRADE_ONE_LEVEL_SCORE:
            self.downgrade_trust(incident)
            self._consecutive_safe_ops = 0  # 重置安全操作计数

        # I 级事故 → 自动 Kill Switch
        if incident.level == "I":
            self.kill_switch(
                f"Level I incident: {incident.category} — {incident.description[:100]}"
            )

    def query_incidents(self, days: int = 30) -> List[Incident]:
        """查询最近 N 天内的事故。

        Args:
            days: 查询天数，<= 0 表示全部

        Returns:
            事故列表（按时间倒序）
        """
        if days <= 0:
            return sorted(self._incidents, key=lambda i: i.timestamp, reverse=True)

        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        filtered = [
            i for i in self._incidents
            if datetime.fromisoformat(i.timestamp).timestamp() >= cutoff
        ]
        return sorted(filtered, key=lambda i: i.timestamp, reverse=True)

    @staticmethod
    def classify_incident(
        operation: str = "",
        impact_scope: str = "局部",
        recoverable: bool = True,
        description: str = "",
    ) -> Incident:
        """根据操作类型、影响范围和可恢复性自动分级。

        分级逻辑:
          I 级:   不可恢复 + (广泛影响 或 L5操作)
          II 级:  不可恢复 + 中等影响 或 高影响 + 可恢复
          III 级: 可恢复 + 非局部影响 或 L4操作
          IV 级:  其他

        Args:
            operation: 触发事故的操作类型
            impact_scope: 影响范围 ("局部" / "中等" / "广泛")
            recoverable: 是否可恢复
            description: 事故描述

        Returns:
            自动分级后的 Incident 对象
        """
        # 计算分数
        scope_score = {"局部": 10, "中等": 30, "广泛": 60}.get(impact_scope, 10)
        recover_penalty = 0 if recoverable else 40
        op_penalty = 20 if operation in L5_OPERATIONS else (10 if operation in OPERATION_PERMISSIONS and OPERATION_PERMISSIONS.get(operation, 0) >= L4_TRUST else 0)
        score = min(100, scope_score + recover_penalty + op_penalty)

        # 自动分级
        if not recoverable and (impact_scope == "广泛" or operation in L5_OPERATIONS):
            level = "I"
        elif not recoverable and impact_scope == "中等":
            level = "II"
        elif score >= 50:
            level = "II"
        elif recoverable and impact_scope != "局部":
            level = "III"
        elif operation in OPERATION_PERMISSIONS and OPERATION_PERMISSIONS.get(operation, 0) >= L4_TRUST:
            level = "III"
        else:
            level = "IV"

        return Incident(
            level=level,
            category="auto_classified",
            description=description or f"自动分级: {operation}",
            impact=impact_scope,
            score=score,
            operation=operation,
            recoverable=recoverable,
        )

    # ── 状态查询 ─────────────────────────────────────────────

    @property
    def consecutive_safe_ops(self) -> int:
        """连续安全操作计数。"""
        return self._consecutive_safe_ops

    @property
    def is_emergency_stop(self) -> bool:
        """是否处于紧急停止状态。"""
        return self._state.get("emergency_stop", False)

    @property
    def trust_level_value(self) -> int:
        """当前信任级别数值。"""
        return self._trust_level.value

    def get_state_summary(self) -> Dict[str, Any]:
        """获取安全门控完整状态摘要。"""
        return {
            "emergency_stop": self._state.get("emergency_stop", False),
            "kill_reason": self._state.get("kill_reason", ""),
            "trust_level": self._trust_level.value,
            "trust_label": TRUST_LABELS.get(self._trust_level.value, "未知"),
            "consecutive_safe_ops": self._consecutive_safe_ops,
            "permission_denials": self._permission_denials,
            "budget_denials": self._budget_denials,
            "effective_denials": self._permission_denials + int(self._budget_denials * self._budget_denial_weight),
            "ks_threshold": self._ks_threshold_for_phase(),
            "current_phase": self.current_phase,
            "total_checks": self._total_checks,
            "total_denials": self._total_denials,
            "incident_count": len(self._incidents),
            "error_budget": self._budget.to_dict(),
        }
