"""
KuguaKernel — unified Agent runtime entry point.

One-line init + run. Wires all 22 modules with dependency injection.
No service locator, no global state — the kernel owns all instances.

Usage:
    kernel = KuguaKernel()
    kernel.init_minimal()          # no LLM needed (MCP/CLI tools)
    # OR
    kernel = KuguaKernel.from_env()  # full LLM integration
    report = kernel.run([{"id": "t1", "task": "Summarize X"}])

Design:
    4 initialization stages, each independently callable:
      init_core()    — states, safety, context, KB, graph
      init_llm()     — LLM client, executor, observer
      init_learning() — CSD, efficacy, mobius, double-loop, meta-reviewer
      init_runtime() — negentropy, main loop
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional

from kugua.config import KuguaConfig
from kugua.states import StatesMachine
from kugua.state import KuguaState
from kugua.safety import SafetyManager
from kugua.permission import PermissionGate
from kugua.context import ContextManager
from kugua.knowledge import KnowledgeBase
from kugua.graph import GraphKB
from kugua.executor import LLMClient, TaskExecutor, StagnationDetector
from kugua.critical_slowing import CriticalSlowingDetector
from kugua.efficacy import DoubleLoopEfficacyTracker
from kugua.mobius import MobiusController
from kugua.double_loop import DoubleLoopExecutor
from kugua.observer import FreshObserver, create_observer_from_config
from kugua.meta_reviewer import MetaReviewer
from kugua.negentropy import Negentropy, NegentropyHistory
from kugua.main_loop import MainLoop, PhaseReport


class KuguaKernel:
    """Unified Agent cognitive kernel runtime.

    Dependency injection — the kernel creates and wires every subsystem.
    Callers access modules through kernel attributes (kernel.kb, kernel.csd, etc.).

    Two main initialization paths:
      - init_minimal():  No LLM needed. For read-only MCP/CLI tools.
      - from_env():      Full LLM integration. Requires API keys in environment.
    """

    def __init__(self, config: Optional[KuguaConfig] = None):
        self.cfg = config or KuguaConfig()
        self.state = KuguaState()

        # ── Always-initialized subsystems ──
        self.states_machine: Optional[StatesMachine] = None
        self.safety: Optional[SafetyManager] = None
        self.permission: Optional[PermissionGate] = None
        self.context: Optional[ContextManager] = None

        # ── Optional subsystems (initialized on demand) ──
        self.llm_client: Optional[LLMClient] = None
        self.executor: Optional[TaskExecutor] = None
        self.kb: Optional[KnowledgeBase] = None
        self.graph: Optional[GraphKB] = None
        self.csd: Optional[CriticalSlowingDetector] = None
        self.efficacy: Optional[DoubleLoopEfficacyTracker] = None
        self.mobius: Optional[MobiusController] = None
        self.double_loop: Optional[DoubleLoopExecutor] = None
        self.observer: Optional[FreshObserver] = None
        self.meta_reviewer: Optional[MetaReviewer] = None
        self.ne: Optional[Negentropy] = None
        self.ne_history: Optional[NegentropyHistory] = None
        self.main_loop: Optional[MainLoop] = None

    # ═══════════════════════════════════════════════════════════
    # Factory
    # ═══════════════════════════════════════════════════════════

    @classmethod
    def from_env(cls) -> "KuguaKernel":
        """Full initialization from environment variables.

        Reads API keys from env vars (DEEPSEEK_API_KEY, MIMO_API_KEY, etc.).
        All 4 stages are initialized in sequence.
        """
        cfg = KuguaConfig.from_env()
        kernel = cls(cfg)
        kernel.init_core()
        kernel.init_llm()
        kernel.init_learning()
        kernel.init_runtime()
        return kernel

    # ═══════════════════════════════════════════════════════════
    # Stage 1: Core (no LLM needed)
    # ═══════════════════════════════════════════════════════════

    def init_minimal(self):
        """Minimal init — no LLM, no API keys. For MCP/CLI read-only tools."""
        self.init_core()

    def init_core(self):
        """States, safety, permission, context, knowledge base, graph, negentropy."""
        self.states_machine = StatesMachine(self.cfg)
        self.safety = SafetyManager(self.cfg)
        self.permission = PermissionGate(self.safety)
        self.context = ContextManager(self.cfg)

        self.kb = KnowledgeBase(self.cfg)
        self.state.kb = self.kb
        self.graph = self.kb.graph
        self.state.graph = self.graph

        # Negentropy (state-only, no LLM needed)
        state_dict = self.states_machine.load_state()
        self.ne = Negentropy(state_dict, efficacy=None)
        self.state.negen = self.ne
        self.ne_history = NegentropyHistory(
            artifacts_dir=self.cfg.artifacts_dir,
        )
        self.ne_history.record(self.ne.to_dict())

    # ═══════════════════════════════════════════════════════════
    # Stage 2: LLM (requires API keys)
    # ═══════════════════════════════════════════════════════════

    def init_llm(self):
        """LLM client, task executor, observer."""
        self.llm_client = LLMClient(self.cfg)
        stagnation = StagnationDetector()
        self.executor = TaskExecutor(
            client=self.llm_client,
            config=self.cfg,
            permission_gate=self._permission_check if self.permission else None,
            stagnation_detector=stagnation,
        )
        self.observer = create_observer_from_config(self.cfg)

    def _permission_check(self, action: str, context: Dict[str, Any]):
        """Adapter: PermissionGate.check() → TaskExecutor permission_gate signature."""
        if self.permission is None:
            return True, "no gate"
        return self.permission.check(action)

    # ═══════════════════════════════════════════════════════════
    # Stage 3: Learning (CSD, efficacy, mobius, double-loop)
    # ═══════════════════════════════════════════════════════════

    def init_learning(self):
        """CSD, efficacy, mobius, double-loop, meta-reviewer."""
        artifacts = self.cfg.artifacts_dir

        self.csd = CriticalSlowingDetector(artifacts_dir=artifacts)
        self.state.csd = self.csd

        self.efficacy = DoubleLoopEfficacyTracker(artifacts_dir=artifacts)
        self.state.efficacy = self.efficacy

        self.mobius = MobiusController(artifact_dir=artifacts)
        self.state.mobius = self.mobius

        self.double_loop = DoubleLoopExecutor(
            mobius=self.mobius,
            csd=self.csd,
            efficacy=self.efficacy,
            kb=self.kb,
            llm_client=self.llm_client,
            observer=self.observer,
            artifacts_dir=artifacts,
        )
        self.state.double_loop = self.double_loop

        if self.llm_client:
            self.meta_reviewer = MetaReviewer(llm_client=self.llm_client)

    # ═══════════════════════════════════════════════════════════
    # Stage 4: Runtime (negentropy, main loop)
    # ═══════════════════════════════════════════════════════════

    def init_runtime(self):
        """Main loop. Negentropy already created in init_core — update with efficacy."""
        # Update negentropy with efficacy data now that learning is initialized
        if self.ne and self.efficacy:
            state_dict = (
                self.states_machine.load_state() if self.states_machine else {}
            )
            self.ne = Negentropy(state_dict, efficacy=self.efficacy)
            self.state.negen = self.ne
            if self.ne_history:
                self.ne_history.record(self.ne.to_dict())

        self.main_loop = MainLoop(
            config=self.cfg,
            states=self.states_machine,
            executor=self.executor,
            knowledge_base=self.kb,
            double_loop=self.double_loop,
            mobius=self.mobius,
        )

    # ═══════════════════════════════════════════════════════════
    # Runtime
    # ═══════════════════════════════════════════════════════════

    def run(
        self,
        task_dag: List[Dict[str, Any]] = None,
        intent_anchor: Dict[str, Any] = None,
    ) -> List[PhaseReport]:
        """Execute the full P0→P4 cycle.

        Args:
            task_dag: List of task descriptors [{id, task, context?, requirements?}, ...]
            intent_anchor: User goal and success criteria dict.

        Returns:
            List of PhaseReport, one per phase executed.
        """
        if not self.main_loop:
            self.init_runtime()
        return self.main_loop.run(task_dag=task_dag, intent_anchor=intent_anchor)

    # ═══════════════════════════════════════════════════════════
    # Dashboard
    # ═══════════════════════════════════════════════════════════

    def dashboard(self) -> Dict[str, Any]:
        """Return comprehensive kernel status across all subsystems."""
        d: Dict[str, Any] = {
            "version": "0.2.1",
            "config": {
                "has_providers": self.cfg.has_providers if self.cfg else False,
            },
        }

        if self.kb:
            d["kb"] = self.kb.effective_stats()

        if self.graph:
            d["graph"] = {
                "nodes": self.graph.node_count,
                "edges": self.graph.edge_count,
            }

        if self.mobius:
            d["mobius"] = self.mobius.dashboard()

        if self.csd:
            d["csd"] = self.csd.to_dict()

        if self.efficacy:
            d["efficacy"] = self.efficacy.to_dict()

        if self.ne:
            d["negentropy"] = self.ne.to_dict()

        if self.ne_history:
            d["negentropy_history"] = self.ne_history.to_dict()

        if self.main_loop:
            d["main_loop"] = self.main_loop.get_phase_summary()

        return d

    def is_ready(self) -> bool:
        """Check if kernel has the minimum subsystems for task execution."""
        return (
            self.states_machine is not None
            and self.safety is not None
            and self.kb is not None
        )
