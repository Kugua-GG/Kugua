"""
MainLoop — P0→P4 execution orchestrator v0.2.2

Academic grounding:
  - ReAct (Yao et al., 2022): Thought→Action→Observation interleaving,
    mapped to P1 Plan → P2 Execute → P3 Review micro-loop
  - Reflexion (Shinn et al., NeurIPS 2023): verbal self-reflection with
    episodic memory, mapped to P3x double-loop RCA → KB update
  - Generative Agents (Park et al., 2023): recursive plan decomposition
    and dynamic re-planning, mapped to P3→P1 replan_threshold

Drives the state machine through actual P0→P4 phases:
  P0 Self-Check   — load state, verify KB, check LLM connectivity
  P1 Plan         — freeze intent, validate task DAG, transition to frozen
  P2 Execute      — micro-loop: execute→review per task (ReAct-inspired)
  P3 Review       — aggregate results, check double-loop triggers
  P3x Double-Loop — RCA→Propose→Gate→Audit→Validate→Commit/Rollback
  P4 Deliver      — compute negentropy, save state, generate final report

Pure Python stdlib — zero external dependencies.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# PhaseReport
# ═══════════════════════════════════════════════════════════════

@dataclass
class PhaseReport:
    """Status report for a single phase with optional error and structured data.

    Fields:
        phase:       Phase name (P0, P1, P2, P3, P3x, P4).
        status:      "completed" | "skipped" | "failed" | "pending".
        details:     Human-readable summary.
        elapsed_ms:  Wall-clock time for this phase.
        timestamp:   ISO-format UTC timestamp.
        error:       Error message if status == "failed".
        data:        Structured data produced by this phase
                     (e.g., P2: task_count, failures; P3x: double_loop_events).
    """

    phase: str = ""
    status: str = "pending"
    details: str = ""
    elapsed_ms: float = 0.0
    timestamp: str = ""
    error: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# MainLoop
# ═══════════════════════════════════════════════════════════════

class MainLoop:
    """Orchestrate the full P0→P4 execution cycle with state machine enforcement.

    Args:
        config:        KuguaConfig instance.
        states:        StatesMachine instance (P0-P4 transition enforcement).
        executor:      TaskExecutor instance (Worker + Checker).
        knowledge_base: KnowledgeBase instance.
        double_loop:   DoubleLoopExecutor instance (P3x).
        mobius:        MobiusController instance (continuous correction spectrum).
        context:       ContextManager instance (L0/L1/L2).
        safety:        SafetyManager instance (kill switch checks).
        stagnation:    StagnationDetector instance (loop detection).
        replan_threshold: P3 fail rate above which P3→P1 replan is triggered (0.3 = 30%).
    """

    def __init__(
        self,
        config: Any = None,
        states: Any = None,
        executor: Any = None,
        knowledge_base: Any = None,
        double_loop: Any = None,
        mobius: Any = None,
        context: Any = None,
        safety: Any = None,
        stagnation: Any = None,
        replan_threshold: float = 0.3,
    ):
        self.config = config
        self.states = states
        self.executor = executor
        self.kb = knowledge_base
        self.double_loop = double_loop
        self.mobius = mobius
        self.context = context
        self.safety = safety
        self.stagnation = stagnation
        self.replan_threshold = replan_threshold

        self.reports: List[PhaseReport] = []
        self._state: Dict[str, Any] = {}

    # ═════════════════════════════════════════════════════════
    # Main entry point
    # ═════════════════════════════════════════════════════════

    def run(
        self,
        task_dag: List[Dict[str, Any]] = None,
        intent_anchor: Dict[str, Any] = None,
    ) -> List[PhaseReport]:
        """Execute the full P0→P4 cycle.

        Args:
            task_dag:       List of task descriptors [{id, task, context?, requirements?}, ...]
            intent_anchor:  User goal and success criteria dict.

        Returns:
            List of PhaseReport, one per phase executed.
        """
        task_dag = task_dag or []
        self.reports = []
        import time as _time

        # Load or initialize state
        if self.states:
            self._state = self.states.load_state()

        # ── P0: Self-Check ──────────────────────────────
        t0 = _time.time()
        self.reports.append(self._phase_p0())
        self.reports[-1].elapsed_ms = (_time.time() - t0) * 1000

        # ── P0→P1: Plan ──────────────────────────────────
        t0 = _time.time()
        self.reports.append(self._phase_p1(task_dag, intent_anchor))
        self.reports[-1].elapsed_ms = (_time.time() - t0) * 1000

        # Check if P1 succeeded before proceeding
        if self.reports[-1].status == "failed":
            return self.reports

        # ── P1→P2: Execute ───────────────────────────────
        t0 = _time.time()
        p2_report = self._phase_p2(task_dag)
        p2_report.elapsed_ms = (_time.time() - t0) * 1000
        self.reports.append(p2_report)

        # Collect execution results for P3
        exec_results = p2_report.data.get("results", [])

        # ── P2→P3: Review ────────────────────────────────
        t0 = _time.time()
        p3_report = self._phase_p3(task_dag, exec_results)
        p3_report.elapsed_ms = (_time.time() - t0) * 1000
        self.reports.append(p3_report)

        # Check for replan (Generative Agents-inspired)
        fail_rate = p3_report.data.get("fail_rate", 0.0)
        if fail_rate > self.replan_threshold and self.context:
            # Replan: unfreeze L1, go back to P1
            self.context.unfreeze_L1()
            replan_report = PhaseReport(
                phase="P3→P1",
                status="completed",
                details=(
                    f"Replan triggered: fail rate {fail_rate:.1%} > "
                    f"threshold {self.replan_threshold:.1%}"
                ),
                data={"fail_rate": fail_rate, "threshold": self.replan_threshold},
            )
            self.reports.append(replan_report)
            # Re-execute P1 with same tasks
            t0 = _time.time()
            self.reports.append(self._phase_p1(task_dag, intent_anchor))
            self.reports[-1].elapsed_ms = (_time.time() - t0) * 1000

        # ── P3→P4: Deliver ───────────────────────────────
        t0 = _time.time()
        self.reports.append(self._phase_p4())
        self.reports[-1].elapsed_ms = (_time.time() - t0) * 1000

        return self.reports

    # ═════════════════════════════════════════════════════
    # P0: Self-Check
    # ═════════════════════════════════════════════════════

    def _phase_p0(self) -> PhaseReport:
        """P0: Verify kernel health.

        Checks:
          1. State machine operational
          2. Knowledge base accessible
          3. Safety gate not in emergency stop
          4. LLM connectivity (if configured)
        """
        checks = []

        # 1. State machine
        if self.states:
            try:
                self._state = self.states.p0_self_check()
                checks.append("states:OK")
            except Exception as e:
                return PhaseReport("P0", "failed", f"States machine init failed: {e}", error=str(e))
        else:
            checks.append("states:skipped")

        # 2. KB
        if self.kb:
            try:
                stats = self.kb.effective_stats()
                checks.append(f"kb:{stats['active']} active")
            except Exception as e:
                checks.append(f"kb:error({e})")
        else:
            checks.append("kb:skipped")

        # 3. Safety
        if self.safety:
            if self.safety.emergency_stop_active:
                return PhaseReport(
                    "P0", "failed",
                    f"Emergency stop active: {self.safety._state.get('kill_reason', 'unknown')}",
                )
            checks.append("safety:OK")
        else:
            checks.append("safety:skipped")

        # 4. LLM (optional)
        if self.executor and self.executor.client:
            if self.executor.client.has_providers:
                checks.append("llm:configured")
            else:
                checks.append("llm:no_providers")
        else:
            checks.append("llm:skipped")

        return PhaseReport(
            "P0", "completed",
            f"Self-check: {', '.join(checks)}",
            data={"checks": checks},
        )

    # ═════════════════════════════════════════════════════
    # P1: Plan
    # ═════════════════════════════════════════════════════

    def _phase_p1(
        self,
        task_dag: List[Dict[str, Any]],
        intent_anchor: Dict[str, Any] = None,
    ) -> PhaseReport:
        """P1: Freeze the plan.

        1. Validate task DAG structure
        2. Freeze intent anchor + task DAG in ContextManager (L1)
        3. Transition state machine P0→P1→P1_frozen
        """
        intent_anchor = intent_anchor or {}

        # Validate task DAG
        valid_tasks = 0
        for td in (task_dag or []):
            if td.get("task"):  # at minimum, a task description
                valid_tasks += 1

        if valid_tasks == 0 and task_dag:
            return PhaseReport(
                "P1", "failed",
                "All tasks in DAG are missing 'task' field",
                data={"total": len(task_dag), "valid": 0},
            )

        # Freeze L1 in context
        if self.context:
            self.context.freeze_L1(
                intent_anchor=intent_anchor,
                task_dag=task_dag or [],
                plan=intent_anchor.get("plan", ""),
            )

        # State machine transitions
        try:
            if self.states:
                self._state = self.states.transition(self._state, "P1_planned")
                self._state = self.states.transition(self._state, "P1_frozen")
        except Exception as e:
            return PhaseReport("P1", "failed", f"State transition failed: {e}", error=str(e))

        return PhaseReport(
            "P1", "completed",
            f"Plan frozen: {valid_tasks}/{len(task_dag or [])} valid tasks",
            data={
                "total_tasks": len(task_dag or []),
                "valid_tasks": valid_tasks,
                "intent_keys": list(intent_anchor.keys()),
            },
        )

    # ═════════════════════════════════════════════════════
    # P2: Execute (ReAct-inspired micro-loop)
    # ═════════════════════════════════════════════════════

    def _phase_p2(
        self,
        task_dag: List[Dict[str, Any]],
    ) -> PhaseReport:
        """P2: Execute tasks with ReAct-inspired micro-loop.

        For each task:
          1. execute (Worker) → if ok, review (Checker)
          2. If Checker fails → retry once (micro-loop)
          3. Record result in context (L2 append)
          4. Check kill switch before each task
        """
        if not task_dag:
            try:
                if self.states:
                    self._state = self.states.transition(self._state, "P2_executed")
            except Exception:
                pass
            return PhaseReport("P2", "completed", "No tasks to execute")

        if not self.executor:
            return PhaseReport("P2", "skipped", "No executor configured")

        results = []
        failures = 0
        retries = 0

        for task_desc in task_dag:
            # Kill switch check before each task
            if self.safety and self.safety.emergency_stop_active:
                return PhaseReport(
                    "P2", "failed",
                    "Emergency stop activated during execution",
                    data={"tasks_completed": len(results), "failures": failures},
                )

            tid = task_desc.get("id", "")
            task_text = task_desc.get("task", "")
            ctx = task_desc.get("context", "")
            req = task_desc.get("requirements", "准确性、完整性、合规性")

            # Execute (ReAct: Action step)
            exec_result = self.executor.execute(
                subtask_id=tid, task=task_text, context=ctx,
            )

            # Stagnation check
            if self.stagnation and exec_result.ok:
                stag = self.stagnation.check(tid, exec_result.output, "execute")
                if stag:
                    exec_result.ok = False
                    exec_result.error = f"Stagnation: {stag.retry_count} retries"

            # Review (ReAct: Observation step)
            if exec_result.ok and self.executor:
                review = self.executor.review(
                    subtask_id=tid,
                    worker_output=exec_result.output,
                    requirements=req,
                )
                results.append((exec_result, review))

                # Append to L2 context
                if self.context:
                    self.context.append_worker(
                        f"[{tid}] {exec_result.output[:200]}",
                        importance=8 if review.verdict == "fail" else 5,
                    )

                # Micro-loop retry on failure (ReAct-inspired)
                if review.verdict in ("fail", "pending"):
                    failures += 1
                    retry_result = self.executor.execute(
                        subtask_id=f"{tid}_retry",
                        task=task_text,
                        context=(
                            f"Previous attempt failed with issues: {review.issues}. "
                            f"Correction hint: {getattr(review, 'correction_bias', None)}. "
                            f"Context: {ctx}"
                        ),
                    )
                    if retry_result.ok:
                        retry_review = self.executor.review(
                            subtask_id=f"{tid}_retry",
                            worker_output=retry_result.output,
                            requirements=req,
                        )
                        results.append((retry_result, retry_review))
                        retries += 1
                        if retry_review.verdict == "fail":
                            failures += 1
                    else:
                        failures += 1
            else:
                # Execution itself failed
                failures += 1
                results.append((exec_result, None))
                if self.context:
                    self.context.append_checker(
                        f"[{tid}] EXEC FAILED: {exec_result.error}",
                        importance=9,
                    )

        # Record suspected GV IDs from failures
        suspected: List[str] = []
        for exec_r, review_r in results:
            if review_r and hasattr(review_r, "suspected_gv_ids"):
                suspected.extend(review_r.suspected_gv_ids)

        # State transition
        target_phase = "P2_partial" if failures > 0 else "P2_executed"
        try:
            if self.states:
                self._state = self.states.transition(self._state, target_phase)
        except Exception:
            pass

        return PhaseReport(
            "P2", "completed",
            f"Executed {len(task_dag)} tasks: {failures} failures, {retries} retries",
            data={
                "task_count": len(task_dag),
                "failures": failures,
                "retries": retries,
                "results": results,
                "suspected_gv_ids": list(set(suspected)),
            },
        )

    # ═════════════════════════════════════════════════════
    # P3: Review + P3x Double-Loop
    # ═════════════════════════════════════════════════════

    def _phase_p3(
        self,
        task_dag: List[Dict[str, Any]],
        results: List[Tuple[Any, Any]],
    ) -> PhaseReport:
        """P3: Aggregate review results and trigger double-loop if needed.

        Reflexion-inspired: failures → RCA → KB modification.
        """
        if not results:
            try:
                if self.states:
                    self._state = self.states.transition(self._state, "P3_reviewed")
            except Exception:
                pass
            return PhaseReport("P3", "completed", "No results to review")

        failures = 0
        total = len(results)
        double_loop_events = 0

        # Count failures and collect error types
        error_counts: Dict[str, int] = {}
        for exec_r, review_r in results:
            if review_r is None:
                failures += 1
            elif hasattr(review_r, "verdict") and review_r.verdict in ("fail", "pending"):
                failures += 1
                # Collect suspected GV IDs for double-loop
                for gv_id in getattr(review_r, "suspected_gv_ids", []):
                    error_type = getattr(review_r, "error_type", "accuracy")
                    if isinstance(error_type, str):
                        key = f"{error_type}:{gv_id}"
                        error_counts[key] = error_counts.get(key, 0) + 1

        fail_rate = failures / max(total, 1)

        # P3x: Double-loop on repeated errors
        total_suspected_gvs = set()
        for exec_r, review_r in results:
            if review_r and hasattr(review_r, "suspected_gv_ids"):
                for gv_id in review_r.suspected_gv_ids:
                    total_suspected_gvs.add(gv_id)

        if self.double_loop and total_suspected_gvs:
            for exec_r, review_r in results:
                if review_r and hasattr(review_r, "verdict") and review_r.verdict in ("fail", "pending"):
                    for gv_id in getattr(review_r, "suspected_gv_ids", []):
                        error_type = getattr(review_r, "error_type", "accuracy")

                        # Push bias to Mobius if available
                        if self.mobius and hasattr(review_r, "correction_bias"):
                            bias = review_r.correction_bias
                            if bias:
                                try:
                                    self.mobius.push_bias(bias)
                                except Exception:
                                    pass

                        # Check if double-loop should trigger
                        should_trigger = False
                        if self.mobius:
                            try:
                                should_trigger = self.mobius.should_trigger(
                                    gv_id, error_type
                                )
                            except Exception:
                                pass

                        if should_trigger:
                            try:
                                event = self.double_loop.execute(
                                    error_type, gv_id
                                )
                                double_loop_events += 1
                                if self.context:
                                    self.context.append(
                                        "system",
                                        f"DoubleLoop: {error_type}/{gv_id} → "
                                        f"{event.phase}",
                                        importance=10,
                                    )
                            except Exception as e:
                                if self.context:
                                    self.context.append(
                                        "system",
                                        f"DoubleLoop failed: {e}",
                                        importance=9,
                                    )

        # State transition
        target_phase = "P3_failed" if fail_rate > self.replan_threshold else "P3_reviewed"
        try:
            if self.states:
                self._state = self.states.transition(self._state, target_phase)
        except Exception:
            pass

        return PhaseReport(
            "P3", "completed",
            f"Reviewed {total} tasks: {failures} failed ({fail_rate:.1%}), "
            f"{double_loop_events} double-loop events",
            data={
                "total": total,
                "failures": failures,
                "fail_rate": round(fail_rate, 3),
                "double_loop_events": double_loop_events,
                "error_counts": error_counts,
                "replan_threshold": self.replan_threshold,
            },
        )

    # ═════════════════════════════════════════════════════
    # P4: Deliver
    # ═════════════════════════════════════════════════════

    def _phase_p4(self) -> PhaseReport:
        """P4: Finalize the cycle.

        1. Compute negentropy (if Negentropy available)
        2. Transition to P4_delivered
        3. Save state
        4. Record history snapshot
        """
        negentropy_score = None

        # Compute negentropy if we have the right data
        if self.states:
            try:
                from kugua.negentropy import Negentropy
                ne = Negentropy(self._state)
                negentropy_score = ne.composite()
            except Exception:
                pass

        # State transition
        try:
            if self.states:
                self._state = self.states.transition(self._state, "P4_delivered")
                self.states.save_state(self._state)
        except Exception:
            pass

        # L2 pressure status for report
        l2_status = "normal"
        if self.context:
            l2_status = "pressure" if self.context.pressure_warning else "normal"

        return PhaseReport(
            "P4", "completed",
            f"Cycle complete. Negentropy: {negentropy_score}%",
            data={
                "negentropy": negentropy_score,
                "l2_status": l2_status,
                "compressions": self.context.compression_count if self.context else 0,
                "total_phases": len(self.reports) + 1,
            },
        )

    # ═════════════════════════════════════════════════════
    # Summary
    # ═════════════════════════════════════════════════════

    def get_phase_summary(self) -> Dict[str, Any]:
        """Return a summary of all phases executed."""
        return {
            "phases": [
                {
                    "phase": r.phase,
                    "status": r.status,
                    "details": r.details,
                    "elapsed_ms": r.elapsed_ms,
                    "error": r.error,
                }
                for r in self.reports
            ],
            "total_phases": len(self.reports),
            "failures": sum(1 for r in self.reports if r.status == "failed"),
            "double_loop_events": sum(
                r.data.get("double_loop_events", 0) for r in self.reports
            ),
        }
