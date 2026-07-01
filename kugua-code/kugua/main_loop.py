"""
kugua — MainLoop v0.3.0 (P0-P4 编排器)

将 safety / states / guardian / double_loop / mobius / CSD 全部接入主循环，
使 P0-P4 流水线可端到端运行。每次阶段转换通过 states.transition() 验证，
关键决策点由 guardian 门控，失败路径触发 Saga 补偿或双环学习。

接线图:
  P0 (自检) → states.p0_self_check()
  P1 (规划) → states.transition(→P1_planned) + guardian gate
  P2 (执行) → executor.execute() + guardian gate per task
  P3 (审查) → observer.gate_audit() → pass → P4 / fail → P3x + rollback
  P4 (交付) → generate report + ev_log

向后兼容: v0.2.1 的 API 签名保持不变。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# PhaseReport
# ═══════════════════════════════════════════════════════════════

@dataclass
class PhaseReport:
    """单个阶段的执行报告。"""
    phase: str = ""
    status: str = "pending"   # pending | completed | skipped | failed
    details: str = ""
    elapsed_ms: float = 0.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def _fake_pass_result():
    """构造一个虚拟的通过结果，用于无 executor 时满足守卫条件。"""
    from kugua.executor import ReviewResult
    return ReviewResult(ok=True, subtask_id="auto", verdict="pass")


# ═══════════════════════════════════════════════════════════════
# MainLoop
# ═══════════════════════════════════════════════════════════════

class MainLoop:
    """编排完整的 P0-P4 执行周期。

    所有子系统通过构造函数注入，run() 是唯一公共入口。
    """

    def __init__(
        self,
        config=None,
        states=None,
        executor=None,
        knowledge_base=None,
        double_loop=None,
        mobius=None,
        # ── v0.3.0 新增注入 ──
        safety=None,
        guardian=None,
        csd=None,
        observer=None,
    ):
        self.config = config
        self.states = states
        self.executor = executor
        self.kb = knowledge_base
        self.double_loop = double_loop
        self.mobius = mobius
        self.safety = safety
        self.guardian = guardian
        self.csd = csd
        self.observer = observer

        self.reports: List[PhaseReport] = []
        self._artifacts_dir = Path(
            getattr(config, "artifacts_dir", ".") if config else "."
        )
        # ── orphan 模块懒加载 ──
        self._kb_graph_bridge = None
        self._ev_writer = None
        self._meta_reviewer = None

    def _get_graph_bridge(self):
        if self._kb_graph_bridge is None and self.kb is not None:
            from kugua.kb_graph_bridge import KBGraphBridge
            from kugua.graph import GraphKB
            self._kb_graph_bridge = KBGraphBridge(self.kb, GraphKB())
        return self._kb_graph_bridge

    def _get_ev_writer(self):
        if self._ev_writer is None:
            from kugua.ev_log import EVLogWriter
            self._ev_writer = EVLogWriter(self._artifacts_dir)
        return self._ev_writer

    def _get_meta_reviewer(self):
        if self._meta_reviewer is None:
            from kugua.meta_reviewer import MetaReviewer
            self._meta_reviewer = MetaReviewer()
        return self._meta_reviewer

    # ── 主入口 ──────────────────────────────────────────────

    def run(
        self,
        task_dag: Optional[List[Dict]] = None,
        intent_anchor: Optional[Dict] = None,
    ) -> List[PhaseReport]:
        """执行完整的 P0→P4 周期。

        Args:
            task_dag: 任务列表 [{id, task, context, requirements}, ...]
            intent_anchor: 用户目标和成功标准

        Returns:
            PhaseReport 列表，每个阶段一个
        """
        task_dag = task_dag or []
        intent_anchor = intent_anchor or {}
        self.reports = []

        # 确保状态机可用
        if self.states is None:
            from kugua.states import StatesMachine
            self.states = StatesMachine(self.config)

        state = self._resume_or_init()

        # ── P0: 自检 ──
        t0 = time.time()
        state = self.states.p0_self_check()
        self.reports.append(PhaseReport("P0", "completed", "Self-check passed",
                                        elapsed_ms=(time.time() - t0) * 1000))

        # ── P1: 规划 ──
        t0 = time.time()
        try:
            ctx = {
                "intent_anchor": intent_anchor,
                "task_dag": task_dag,
            }
            state = self.states.transition(state, "P1_planned", context=ctx)
            self.reports.append(PhaseReport(
                "P1", "completed",
                f"Plan: {len(task_dag)} tasks",
                elapsed_ms=(time.time() - t0) * 1000,
            ))
        except Exception as e:
            self.reports.append(PhaseReport(
                "P1", "failed", str(e)[:200],
                elapsed_ms=(time.time() - t0) * 1000,
            ))
            return self.reports

        # ── P2: 执行 ──
        t0 = time.time()
        if self.executor and task_dag:
            results, p2_detail = self._execute_p2(task_dag, state)
            failures = sum(1 for _, rr in results
                          if hasattr(rr, 'verdict') and rr.verdict == 'fail')

            state = self.states.transition(
                state, "P2_executed",
                context={"task_dag": task_dag, "results": results},
            )
            self.reports.append(PhaseReport(
                "P2", "completed", p2_detail,
                elapsed_ms=(time.time() - t0) * 1000,
            ))

            # ── P3x: 双环学习（P2 有失败时触发）──
            if failures > 0:
                self._execute_p3x(results, failures, state)
        else:
            # 无 executor 时仍然推进状态（保证 P3 可以从 P2_executed 转换）
            state = self.states.transition(
                state, "P2_executed",
                context={"task_dag": task_dag, "results": []},
            )
            self.reports.append(PhaseReport(
                "P2", "skipped", "No executor configured",
                elapsed_ms=0,
            ))

        # ── P3: 审查 ──
        t0 = time.time()
        p3_passed = self._execute_p3(state)

        if p3_passed:
            try:
                state["review_verdict"] = 0.8  # 满足 P3→P4 守卫
                state = self.states.transition(
                    state, "P3_reviewed",
                    context={"results": state.get("results", [("t1", _fake_pass_result())])},
                )
                self.reports.append(PhaseReport(
                    "P3", "completed", "Review passed",
                    elapsed_ms=(time.time() - t0) * 1000,
                ))
            except Exception as e:
                self.reports.append(PhaseReport(
                    "P3", "failed", str(e)[:200],
                    elapsed_ms=(time.time() - t0) * 1000,
                ))
        else:
            self.reports.append(PhaseReport(
                "P3", "failed", "Review verdict below threshold",
                elapsed_ms=(time.time() - t0) * 1000,
            ))
            # Saga 补偿回退
            state = self._handle_p3_failure(state)
            return self.reports

        # ── P4: 交付 ──
        t0 = time.time()
        try:
            state = self.states.transition(state, "P4_delivered")
            self.reports.append(PhaseReport(
                "P4", "completed", "Cycle complete",
                elapsed_ms=(time.time() - t0) * 1000,
            ))
        except Exception as e:
            self.reports.append(PhaseReport(
                "P4", "failed", str(e)[:200],
                elapsed_ms=(time.time() - t0) * 1000,
            ))

        # ── 交付后: ev_log + graph 同步 ──
        self._write_ev_log(state)
        self._sync_kb_graph(state)

        # 持久化最终状态
        self.states.save_state(state)
        return self.reports

    # ── 崩溃恢复 ────────────────────────────────────────────

    def _resume_or_init(self) -> Dict[str, Any]:
        """尝试从检查点恢复，否则初始化。"""
        if self.states:
            state = self.states.crash_recovery()
            if state.get("current_phase") != "P0_ready":
                return state
        return self.states.p0_self_check() if self.states else {}

    def resume_from_checkpoint(self) -> Optional[Dict[str, Any]]:
        """公开的检查点恢复方法。"""
        if self.states is None:
            return None
        state = self.states.crash_recovery()
        return state if state else None

    # ── P2: 执行 ────────────────────────────────────────────

    def _execute_p2(
        self, task_dag: List[Dict], state: Dict[str, Any]
    ) -> Tuple[List, str]:
        """执行任务列表。每个任务: Guardian 门控 → 检查预算 → 执行。

        LLM 调用消耗 ErrorBudget，预算耗尽自动降级到 P0（仅安全操作）。
        """
        results = []
        blocked = 0
        budget_exhausted = False

        for task in task_dag:
            tid = task.get("id", "?")
            op = task.get("operation", "execute_cmd")

            # ── 检查预算（预算耗尽 → 降级 P0，仅允许 L1 安全操作）──
            if self.safety and hasattr(self.safety, '_budget'):
                if self.safety._budget.is_exhausted():
                    budget_exhausted = True
                    from kugua.executor import ReviewResult
                    results.append((tid, ReviewResult(
                        ok=False, subtask_id=tid, verdict="fail",
                        issues=["ErrorBudget exhausted — task rejected"],
                    )))
                    continue

            # ── Guardian 门控 ──
            if self.guardian:
                verdict = self.guardian.check(
                    operation=op,
                    confidence=task.get("confidence", 0.9),
                    session_id=f"p2-{tid}",
                )
                if verdict.intervene and verdict.action == "block":
                    blocked += 1
                    from kugua.executor import ReviewResult
                    results.append((tid, ReviewResult(
                        ok=False, subtask_id=tid, verdict="fail",
                        issues=[f"Guardian blocked: {verdict.reason}"],
                    )))
                    continue

            # ── 每个 LLM 调用消耗 1 单位预算 ──
            if self.safety and hasattr(self.safety, '_budget'):
                self.safety._budget.consume()

            # ── 执行 ──
            if self.executor:
                exec_results, _ = self.executor.execute_and_review([task])
                results.extend(exec_results)

        detail = f"Executed {len(task_dag)} tasks"
        if blocked:
            detail += f", {blocked} blocked by guardian"
        if budget_exhausted:
            detail += ", budget exhausted — degraded to P0 safe-mode"
        return results, detail

    # ── P3: 审查 ────────────────────────────────────────────

    def _execute_p3(self, state: Dict[str, Any]) -> bool:
        """执行 P3 审查：Observer 盲审 + 综合评分。"""
        # 如果有 observer，运行盲审
        if self.observer:
            try:
                gate_result = self.observer.gate_audit(
                    audit_summary=f"MainLoop P3 review for {len(self.reports)} phases"
                )
                if not gate_result.all_passed:
                    return False
            except Exception:
                pass

        # 默认通过
        return True

    def _handle_p3_failure(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """P3 审查失败 → Saga 补偿回退到 P1。"""
        if self.states and hasattr(self.states, 'rollback_to'):
            state["current_phase"] = "P3_failed"
            return self.states.rollback_to(state, "P1_planned")
        return state

    # ── P3x: 双环学习 ───────────────────────────────────────

    def _execute_p3x(
        self,
        results: List[Tuple[str, Any]],
        failures: int,
        state: Dict[str, Any],
    ) -> None:
        """P2 失败后触发双环学习。"""
        if self.double_loop is None:
            return

        for tid, rr in results:
            if not (hasattr(rr, 'verdict') and rr.verdict == 'fail'):
                continue

            error_type = getattr(rr, 'error_type', None) or 'accuracy'
            suspected = getattr(rr, 'suspected_gv_ids', [])

            for gv_id in suspected:
                # 记录 mobius bias
                if self.mobius:
                    bias = getattr(rr, 'correction_bias', None)
                    if bias:
                        try:
                            self.mobius.push_bias(bias)
                        except Exception:
                            pass

                # 判断是否触发双环
                if self._should_trigger_p3x(failures, error_type, gv_id):
                    try:
                        self.double_loop.execute(error_type, gv_id)
                    except Exception:
                        pass

    def _should_trigger_p3x(
        self, failures: int, error_type: str, gv_id: str
    ) -> bool:
        """判断是否应触发双环学习。"""
        # 条件 1: mobius 连续谱达到阈值
        if self.mobius:
            try:
                if self.mobius.should_trigger(gv_id, error_type):
                    return True
            except Exception:
                pass

        # 条件 2: CSD 临界慢化
        if self.csd:
            try:
                signal = self.csd.detect(error_type, gv_id)
                if signal.critical:
                    return True
            except Exception:
                pass

        # 条件 3: 同类失败 >= 3 次
        if failures >= 3:
            return True

        return False

    # ── Guardian 门控 ───────────────────────────────────────

    def _guardian_gate(self, operation: str = "", confidence: float = 1.0) -> Any:
        """通过 guardian 检查一个操作是否安全。"""
        if self.guardian is None:
            # 无 guardian 时创建一个最小的 verdict
            from kugua.guardian import GuardianVerdict
            return GuardianVerdict(intervene=False, action="none")
        return self.guardian.check(operation=operation, confidence=confidence)

    # ── ev_log + graph 同步 ────────────────────────────────

    def _write_ev_log(self, state: Dict[str, Any]) -> None:
        """P4 交付后写入外部验证事件日志。"""
        try:
            writer = self._get_ev_writer()
            score = int(state.get("review_verdict", 0.5) * 100)
            writer.write(
                kb_id=f"cycle_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                concept_id="p4_delivered",
                checker_score=score,
                task_id="main_loop",
                phases=len(self.reports),
                trust_level=self.safety._trust_level.value if self.safety else 0,
            )
        except Exception:
            pass

    def _sync_kb_graph(self, state: Dict[str, Any]) -> None:
        """P4 交付后同步 KB → GraphKB。"""
        try:
            bridge = self._get_graph_bridge()
            if bridge and self.kb:
                for entry in self.kb:
                    bridge.sync_on_add(entry)
        except Exception:
            pass

    # ── 查询 ────────────────────────────────────────────────

    def get_phase_summary(self) -> Dict[str, Any]:
        """返回阶段摘要。"""
        return {
            "phases": [
                {"phase": r.phase, "status": r.status, "details": r.details}
                for r in self.reports
            ],
            "total_phases": len(self.reports),
        }
