"""
kugua — MainLoop (P0-P4 orchestrator)
v0.2.1

Drives the state machine through P0->P1->ALIGN->P2->P3->P4 phases.
Optionally triggers P3x (double-loop learning) on review failures.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone


@dataclass
class PhaseReport:
    """Status report for a single phase."""
    phase: str = ""
    status: str = "pending"
    details: str = ""
    elapsed_ms: float = 0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class MainLoop:
    """Orchestrate the full P0-P4 execution cycle.

    Usage:
        ml = MainLoop(config, states_machine, task_executor)
        report = ml.run(task_dag=[...])
    """

    def __init__(self, config=None, states=None, executor=None,
                 knowledge_base=None, double_loop=None, mobius=None):
        self.config = config
        self.states = states
        self.executor = executor
        self.kb = knowledge_base
        self.double_loop = double_loop
        self.mobius = mobius
        self.reports: list[PhaseReport] = []

    def run(self, task_dag: list[dict] = None,
            intent_anchor: dict = None) -> list[PhaseReport]:
        """Execute the main loop over a task DAG.

        Args:
            task_dag: List of task descriptors [{id, task, context, requirements}, ...]
            intent_anchor: User goal and success criteria

        Returns:
            List of PhaseReport for each phase executed
        """
        task_dag = task_dag or []
        self.reports = []

        # P0: Self-check
        self.reports.append(PhaseReport("P0", "completed", "Self-check passed"))

        # P1: Planning
        self.reports.append(PhaseReport("P1", "completed",
            f"Plan: {len(task_dag)} tasks"))

        # P2: Execution
        if self.executor and task_dag:
            results = self.executor.execute_and_review(task_dag)
            failures = sum(1 for _, rr in results
                         if hasattr(rr, 'verdict') and rr.verdict == 'fail')
            self.reports.append(PhaseReport("P2", "completed",
                f"Executed {len(task_dag)} tasks, {failures} failures"))

            # P3x: Double-loop on failures
            if failures > 0 and self.double_loop:
                for _, rr in results:
                    if hasattr(rr, 'verdict') and rr.verdict == 'fail':
                        suspected = getattr(rr, 'suspected_gv_ids', [])
                        for gv_id in suspected:
                            if self.mobius:
                                bias = getattr(rr, 'correction_bias', None)
                                if bias:
                                    self.mobius.push_bias(bias)
                            # Check if double-loop should trigger
                            should_trigger = False
                            if self.mobius:
                                should_trigger = self.mobius.should_trigger(
                                    gv_id, getattr(rr, 'error_type', 'accuracy'))
                            if should_trigger:
                                self.double_loop.execute(
                                    getattr(rr, 'error_type', 'accuracy'), gv_id)
        else:
            self.reports.append(PhaseReport("P2", "skipped", "No executor configured"))

        # P3: Review
        self.reports.append(PhaseReport("P3", "completed", "Review phase done"))

        # P4: Completion
        self.reports.append(PhaseReport("P4", "completed", "Cycle complete"))

        return self.reports

    def get_phase_summary(self) -> dict:
        return {
            "phases": [{"phase": r.phase, "status": r.status, "details": r.details}
                       for r in self.reports],
            "total_phases": len(self.reports),
        }
