"""
StatesMachine — 状态机模块 v0.3.0

kugua 内核的确定性状态转换引擎。实现 δ(S,E)→S' 三层转换模型：
  pre-condition 验证 → 执行转换 → post-condition 验证（失败即回退）

核心机制:
  1. delta(S,E)→S' 三层转换  — 借鉴 nano-vm 的确定性 FSM
  2. PHASE_GUARDS 声明式守卫表 — 借鉴 statewright 的 guard 模式
  3. PhaseStagnationGuard      — 借鉴 Zhang et al. 的 Null-Transition
  4. PHASE_COMPENSATIONS Saga  — 借鉴 Microsoft Agent Governance Toolkit
  5. 检查点 Suspend/Resume      — 借鉴 nano-vm 的崩溃恢复

与其他模块的边界:
  - executor.StagnationDetector → 检测任务级停滞（输出 hash 重复）
  - states.PhaseStagnationGuard  → 检测阶段级停滞（无法推进到下一阶段）
  - 两者互补，命名明确区分

  - double_loop._rollback() → 数据层回退（KB 规则修改的撤销）
  - states.rollback_to()    → 流程层回退（状态机阶段的补偿事务）
  - 两者串联：P3 失败 → Saga 回退 P1 → 如有 KB 修改则 double_loop 撤销

P0-P4 状态转换图:
  P0_init → P0_ready → P1_planned → P1_frozen → P2_executed
                                                      ↓
  P4_delivered ← P3_reviewed ←────────────────────────┘
       ↑              ↓
       └────────── P3_failed → P1_planned (Saga 补偿回退)

向后兼容: 所有 v0.2.1 API 签名保持不变。
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

VALID_PHASES = [
    "P0_init", "P0_ready",
    "P1_planned", "P1_frozen",
    "P2_executed", "P2_partial",
    "P3_reviewed", "P3_failed",
    "P4_delivered",
]
PHASE_ORDER = {p: i for i, p in enumerate(VALID_PHASES)}

# 在以下阶段转换后自动保存检查点
CHECKPOINT_PHASES = frozenset({"P1_frozen", "P2_executed", "P3_reviewed"})

# PhaseStagnationGuard: 连续无合法转换的最大次数
MAX_STAGNATION = 3


# ═══════════════════════════════════════════════════════════════
# Guard — 声明式守卫条件
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Guard:
    """一个声明式守卫条件，用于前置/后置验证。

    支持的操作符:
      exists      — 字段在 context 中存在且非 None
      non_empty   — 字段为 list/dict 且非空
      eq          — 字段 == value
      gte         — 字段 >= value (数值)
      all_assigned — task_dag 中所有任务都已分配 worker
      all_reviewed — 所有任务的 ReviewResult 都已完成
    """
    field: str
    op: str
    value: Any = None

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """在给定上下文中评估此守卫条件。"""
        if self.op == "exists":
            return self.field in context and context[self.field] is not None

        if self.op == "non_empty":
            val = context.get(self.field)
            if val is None:
                return False
            if isinstance(val, (list, dict, str)):
                return len(val) > 0
            return True  # 非 None 数值视为非空

        if self.op == "eq":
            return context.get(self.field) == self.value

        if self.op == "gte":
            val = context.get(self.field)
            if val is None:
                return False
            try:
                return float(val) >= float(self.value)
            except (ValueError, TypeError):
                return False

        if self.op == "all_assigned":
            task_dag = context.get("task_dag", [])
            if not task_dag:
                return False
            return all(
                t.get("assigned_worker") is not None
                for t in task_dag
                if isinstance(t, dict)
            )

        if self.op == "all_reviewed":
            results = context.get("results", [])
            if not results:
                return False
            return all(
                hasattr(r, 'verdict') for _, r in results
            )

        return False

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


# ═══════════════════════════════════════════════════════════════
# 声明式守卫表: 每个转换的前置/后置条件
# ═══════════════════════════════════════════════════════════════

PHASE_GUARDS: Dict[Tuple[str, str], Dict[str, List[Guard]]] = {
    # P0 → P1: 必须有意图书点和任务列表
    ("P0_ready", "P1_planned"): {
        "pre": [
            Guard("intent_anchor", "exists"),
            Guard("task_dag", "non_empty"),
        ],
        "post": [
            Guard("current_phase", "eq", "P1_planned"),
        ],
    },

    # P1 → P1_frozen: 计划必须冻结
    ("P1_planned", "P1_frozen"): {
        "pre": [
            Guard("plan_status", "eq", "frozen"),
            Guard("task_dag", "all_assigned"),
        ],
        "post": [
            Guard("current_phase", "eq", "P1_frozen"),
        ],
    },

    # P1_planned → P2: 直接执行（向后兼容旧测试路径）
    ("P1_planned", "P2_executed"): {
        "pre": [
            Guard("task_dag", "non_empty"),
        ],
        "post": [
            Guard("current_phase", "eq", "P2_executed"),
        ],
    },

    # P1_frozen → P2: 所有任务已分配 + 安全检查通过
    ("P1_frozen", "P2_executed"): {
        "pre": [
            Guard("task_dag", "all_assigned"),
        ],
        "post": [
            Guard("current_phase", "eq", "P2_executed"),
        ],
    },

    # P2 → P3: 所有任务已执行
    ("P2_executed", "P3_reviewed"): {
        "pre": [
            Guard("results", "all_reviewed"),
        ],
        "post": [
            Guard("current_phase", "eq", "P3_reviewed"),
        ],
    },

    # P2_partial → P2_executed: 剩余任务执行完成
    ("P2_partial", "P2_executed"): {
        "pre": [
            Guard("results", "all_reviewed"),
        ],
        "post": [
            Guard("current_phase", "eq", "P2_executed"),
        ],
    },

    # P3 → P4: 审查通过（≥70% 通过率）
    ("P3_reviewed", "P4_delivered"): {
        "pre": [
            Guard("review_verdict", "gte", 0.7),
        ],
        "post": [
            Guard("current_phase", "eq", "P4_delivered"),
        ],
    },
}

# 不允许的跳跃转换（黑名单比白名单更简洁）
FORBIDDEN_JUMPS: Dict[str, int] = {
    # 不允许跳跃超过 2 个阶段，除非是明确允许的回退
    # 此值定义从每个阶段允许的最大正向前进步数
}


# ═══════════════════════════════════════════════════════════════
# Saga 补偿表: 每个阶段的补偿动作
# ═══════════════════════════════════════════════════════════════

PHASE_COMPENSATIONS: Dict[str, str] = {
    "P2_executed": "_compensate_p2",
    "P1_frozen":   "_compensate_p1",
    "P1_planned":  "_compensate_p1_planned",
}


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class PhaseTransitionError(Exception):
    """状态转换被守卫条件拦截时抛出。"""
    pass


# ═══════════════════════════════════════════════════════════════
# AlignResult
# ═══════════════════════════════════════════════════════════════

@dataclass
class AlignResult:
    """意图对齐检测结果。"""
    aligned: bool = True
    drift_score: float = 0.0
    detail: str = ""


# ═══════════════════════════════════════════════════════════════
# StatesMachine
# ═══════════════════════════════════════════════════════════════

class StatesMachine:
    """确定性状态机 — kugua 内核的 P0-P4 阶段转换引擎。

    δ(S, E) → S' : 当前状态 + 验证后事件 = 下一状态。
    模型可以请求转换，状态机验证并执行。状态机是唯一真理源。

    Usage:
        sm = StatesMachine(cfg)
        state = sm.p0_self_check()
        state = sm.transition(state, "P1_planned", context={
            "intent_anchor": {...},
            "task_dag": [...],
        })
    """

    def __init__(self, config: Any = None):
        self.config = config
        self._artifacts_dir = Path(getattr(config, "artifacts_dir", "."))
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_dir = self._artifacts_dir / "checkpoints"
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # 守卫表（使用 MappingProxyType 防止运行时篡改）
        self.PHASE_GUARDS = MappingProxyType(PHASE_GUARDS)
        self.PHASE_COMPENSATIONS = MappingProxyType(PHASE_COMPENSATIONS)

    # ═════════════════════════════════════════════════════════
    # 核心: δ(S,E)→S' 三层转换
    # ═════════════════════════════════════════════════════════

    def transition(
        self,
        state: Dict[str, Any],
        target: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """执行状态转换（三层验证: pre → execute → post）。

        Args:
            state: 当前状态字典
            target: 目标阶段
            context: 可选的上下文数据（用于前置条件评估）

        Returns:
            更新后的状态字典

        Raises:
            PhaseTransitionError: 前置条件不满足或后置条件失败
        """
        context = context or {}

        # ── 第 1 层: 前置条件验证 ──
        pre_ok, pre_reason = self._validate_precondition(state, target, context)
        if not pre_ok:
            state["stagnation_events"] = state.get("stagnation_events", 0) + 1
            self._check_stagnation(state)
            raise PhaseTransitionError(
                f"Pre-condition failed for {state.get('current_phase', '?')} → {target}: {pre_reason}"
            )

        # ── 第 2 层: 执行转换 ──
        old_phase = state.get("current_phase", "")
        old_state_backup = copy.deepcopy(state)

        cur_idx = PHASE_ORDER.get(old_phase, 0)
        tgt_idx = PHASE_ORDER.get(target, 0)

        if tgt_idx < cur_idx:
            state["phase_regressions"] = state.get("phase_regressions", 0) + 1

        state["phase_switches"] = state.get("phase_switches", 0) + 1
        state["current_phase"] = target

        history = state.get("phase_history", [])
        history.append({
            "phase": target,
            "from": old_phase,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        state["phase_history"] = history

        # ── 第 3 层: 后置条件验证 ──
        post_ok, post_reason = self._validate_postcondition(state, target, context)
        if not post_ok:
            # 回退到转换前状态
            state.update(old_state_backup)
            raise PhaseTransitionError(
                f"Post-condition failed for {old_phase} → {target}: {post_reason}"
            )

        # 重置停滞计数（成功转换）
        state["stagnation_events"] = 0

        # 在关键阶段保存检查点
        if target in CHECKPOINT_PHASES:
            self._save_checkpoint(state)

        return state

    # ═════════════════════════════════════════════════════════
    # 守卫评估
    # ═════════════════════════════════════════════════════════

    def _validate_precondition(
        self, state: Dict[str, Any], target: str, context: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """验证前置条件。"""

        # 验证目标阶段合法性
        if target not in VALID_PHASES:
            return False, f"'{target}' is not a valid phase"

        current = state.get("current_phase", "P0_init")
        cur_idx = PHASE_ORDER.get(current, 0)
        tgt_idx = PHASE_ORDER.get(target, 0)

        # 不允许跳跃超过 2 个阶段（防止 P0→P4）
        if tgt_idx > cur_idx + 2:
            return False, (
                f"Jump from {current} (idx={cur_idx}) to {target} (idx={tgt_idx}) "
                f"exceeds max 2-step forward limit"
            )

        # 从守卫表查找守卫
        guard_key = (current, target)
        guards = self.PHASE_GUARDS.get(guard_key, {}).get("pre", [])

        # 合并 state + context 作为评估上下文
        eval_ctx = {**state, **context}

        for guard in guards:
            if not guard.evaluate(eval_ctx):
                return False, (
                    f"Guard '{guard.field} {guard.op} {guard.value}' failed "
                    f"for {current} → {target}"
                )

        return True, "OK"

    def _validate_postcondition(
        self, state: Dict[str, Any], target: str, context: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """验证后置条件。"""
        current = state.get("current_phase", "")

        # 最基本的后置条件：current_phase 必须是 target
        if current != target:
            return False, f"Expected current_phase='{target}', got '{current}'"

        # 从守卫表查找后置守卫
        # 获取转换前的阶段名来查找
        eval_ctx = {**state, **context}
        # 遍历守卫表找到匹配的后置条件
        for guard_key, guard_set in self.PHASE_GUARDS.items():
            from_p, to_p = guard_key
            if to_p == target:
                for guard in guard_set.get("post", []):
                    if not guard.evaluate(eval_ctx):
                        return False, (
                            f"Post-guard '{guard.field} {guard.op} {guard.value}' "
                            f"failed for {from_p} → {target}"
                        )

        return True, "OK"

    def _eval_guard(self, guard_spec: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """评估单个守卫规格（公开方法，方便测试）。"""
        g = Guard(
            field=guard_spec.get("field", ""),
            op=guard_spec.get("op", "exists"),
            value=guard_spec.get("value"),
        )
        return g.evaluate(context)

    def _evaluate_transitions(
        self, state: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[str]:
        """评估当前状态下所有可能的转换，返回第一个合法的目标阶段。

        Returns:
            目标阶段名，或 None（无合法转换 = Null-Transition）
        """
        current = state.get("current_phase", "P0_init")

        for (from_p, to_p), guard_set in self.PHASE_GUARDS.items():
            if from_p != current:
                continue
            eval_ctx = {**state, **context}
            pre_guards = guard_set.get("pre", [])
            if all(g.evaluate(eval_ctx) for g in pre_guards):
                return to_p

        return None  # Null-Transition

    # ═════════════════════════════════════════════════════════
    # PhaseStagnationGuard — 阶段停滞检测
    # （与 executor.StagnationDetector 互补：那个检测任务级输出重复，
    #   这个检测阶段级无法推进）
    # ═════════════════════════════════════════════════════════

    def _check_stagnation(self, state: Dict[str, Any]) -> None:
        """检查阶段停滞是否达到阈值。"""
        stag_count = state.get("stagnation_events", 0)
        if stag_count >= MAX_STAGNATION:
            self._handle_stagnation(state)

    def _handle_stagnation(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """处理阶段停滞：标记需要重新规划。

        当连续 N 次无法找到合法转换时，系统不会无限循环，
        而是发出 replan_needed 信号供上层（main_loop）处理。
        """
        state["replan_needed"] = True
        state["stagnation_at_phase"] = state.get("current_phase", "unknown")
        return state

    # ═════════════════════════════════════════════════════════
    # Saga 补偿回退
    # ═════════════════════════════════════════════════════════

    def rollback_to(
        self, state: Dict[str, Any], target_phase: str
    ) -> Dict[str, Any]:
        """逆序执行补偿事务，回退到目标阶段。

        从当前阶段开始，逆序遍历中间的所有阶段，
        对每个阶段调用其补偿方法。

        Args:
            state: 当前状态
            target_phase: 目标回退阶段

        Returns:
            更新后的状态
        """
        current = state.get("current_phase", "")
        if current == target_phase:
            return state

        cur_idx = PHASE_ORDER.get(current, 0)
        tgt_idx = PHASE_ORDER.get(target_phase, 0)

        # 只支持回退（目标索引 < 当前索引）
        if tgt_idx >= cur_idx:
            return state

        # 收集需要补偿的阶段（逆序）
        phases_to_undo = [
            p for p in VALID_PHASES
            if tgt_idx < PHASE_ORDER.get(p, 0) <= cur_idx
        ]
        phases_to_undo.sort(key=lambda p: PHASE_ORDER.get(p, 0), reverse=True)

        # 逆序执行补偿
        for phase in phases_to_undo:
            compensator_name = self.PHASE_COMPENSATIONS.get(phase)
            if compensator_name:
                compensator = getattr(self, compensator_name, None)
                if compensator:
                    state = compensator(state)

        state["current_phase"] = target_phase
        state["phase_regressions"] = state.get("phase_regressions", 0) + 1

        history = state.get("phase_history", [])
        history.append({
            "phase": target_phase,
            "from": current,
            "rollback": True,
            "compensated": phases_to_undo,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        state["phase_history"] = history

        return state

    def _compensate_p2(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """补偿 P2: 清理执行痕迹，保留日志供审计。"""
        # 标记结果为已回退
        results = state.get("results", [])
        for _, rr in results:
            if hasattr(rr, 'verdict') and rr.verdict == 'fail':
                rr.verdict = 'rolled_back'
        state["p2_compensated"] = True
        return state

    def _compensate_p1(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """补偿 P1: 解冻计划，标记需要重规划。"""
        state["plan_status"] = "unfrozen"
        state["replan_needed"] = True
        return state

    def _compensate_p1_planned(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """补偿 P1_planned: 清除计划细节但保留意图。"""
        state["plan"] = None
        state["plan_status"] = "needs_replan"
        return state

    # ═════════════════════════════════════════════════════════
    # 检查点 — Suspend/Resume
    # ═════════════════════════════════════════════════════════

    def _save_checkpoint(self, state: Dict[str, Any]) -> None:
        """在关键阶段保存检查点。"""
        phase = state.get("current_phase", "")
        if phase not in CHECKPOINT_PHASES:
            return

        checkpoint = {
            "state": copy.deepcopy(state),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
        }
        ckpt_path = self._checkpoint_dir / f"ckpt_{phase}.json"
        try:
            with open(ckpt_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def _load_checkpoint(self, phase: str) -> Optional[Dict[str, Any]]:
        """加载指定阶段的检查点。"""
        ckpt_path = self._checkpoint_dir / f"ckpt_{phase}.json"
        if not ckpt_path.exists():
            return None
        try:
            with open(ckpt_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _find_last_checkpoint(self) -> Optional[Dict[str, Any]]:
        """找到最近的有效检查点。"""
        for phase in reversed(VALID_PHASES):
            if phase in CHECKPOINT_PHASES:
                ckpt = self._load_checkpoint(phase)
                if ckpt and self._is_valid_state(ckpt.get("state", {})):
                    return ckpt
        return None

    def _is_valid_state(self, state: Dict[str, Any]) -> bool:
        """验证状态是否合法。"""
        if not isinstance(state, dict):
            return False
        phase = state.get("current_phase", "")
        return phase in VALID_PHASES

    # ═════════════════════════════════════════════════════════
    # 持久化（向后兼容 API）
    # ═════════════════════════════════════════════════════════

    def p0_self_check(self) -> Dict[str, Any]:
        """初始化状态机，返回初始状态。"""
        state = {
            "current_phase": "P0_ready",
            "phase_history": [],
            "phase_regressions": 0,
            "phase_switches": 0,
            "stagnation_events": 0,
            "anchor_changes": 0,
            "retrieve_calls": 0,
            "total_subtasks": 0,
            "replan_needed": False,
            "stagnation_at_phase": "",
            "p2_compensated": False,
        }
        self.save_state(state)
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        """持久化当前状态到 agent_state.json。"""
        p = self._artifacts_dir / "agent_state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def load_state(self) -> Dict[str, Any]:
        """从 agent_state.json 加载状态。"""
        p = self._artifacts_dir / "agent_state.json"
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return self.p0_self_check()

    def crash_recovery(self) -> Dict[str, Any]:
        """崩溃恢复：agent_state.json 优先，检查点为后备。

        恢复优先级:
          1. agent_state.json（如果存在且合法）→ 直接使用
          2. 最近的有效检查点
          3. P0 全新初始化
        """
        p = self._artifacts_dir / "agent_state.json"
        if p.exists():
            state = self.load_state()
            if self._is_valid_state(state):
                return state

        # 后备: 检查点
        last_ckpt = self._find_last_checkpoint()
        if last_ckpt:
            return last_ckpt.get("state", self.p0_self_check())

        return self.p0_self_check()
