"""
Double-Loop Executor — full RCA → Propose → ObserverGate → Audit → Validate → Commit/Rollback cycle.

Double-loop learning is kugua's core self-improvement mechanism:
  Single-loop = correct behavior under same rules (retry)
  Double-loop = modify the rules themselves (when same errors recur)

The DoubleLoopExecutor orchestrates the full 6-phase cycle:
  1. RCA (Root Cause Analysis)  — find why the error keeps happening
  2. Propose                      — draft a rule modification
  3. ObserverGate (FreshObserver) — independent blind review of proposal safety
  4. Blind Audit                  — 3-reviewer consensus on the modification
  5. Validate                     — test the modification against recent cases
  6. Commit / Rollback            — apply or revert based on validation

Integration with MobiusController:
  - Checks mobius.should_trigger() before proceeding
  - Resets spectrum on successful commit via mobius.on_double_loop_committed()

Pure Python stdlib — optional integrations with observer, knowledge, CSD, efficacy.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# Optional imports (graceful degradation)
# ═══════════════════════════════════════════════════════════════

try:
    from kugua.mobius import MobiusController, CorrectionSpectrum, TwistPoint
except ImportError:
    MobiusController = None  # type: ignore
    CorrectionSpectrum = None  # type: ignore
    TwistPoint = None  # type: ignore

try:
    from kugua.critical_slowing import CriticalSlowingDetector, CriticalSlowingSignal
except ImportError:
    CriticalSlowingDetector = None  # type: ignore
    CriticalSlowingSignal = None  # type: ignore

try:
    from kugua.efficacy import DoubleLoopEfficacyTracker
except ImportError:
    DoubleLoopEfficacyTracker = None  # type: ignore


# ═══════════════════════════════════════════════════════════════
# DoubleLoopEvent
# ═══════════════════════════════════════════════════════════════

@dataclass
class DoubleLoopEvent:
    """A single double-loop learning event, tracking the full lifecycle.

    Created when a trigger fires; moves through RCA → Propose → Audit →
    Validate → Commit/Rollback phases.
    """

    error_type: str = ""
    gv_id: str = ""
    event_id: str = ""
    phase: str = "triggered"     # triggered | rca | proposed | audited | validated | committed | rolled_back
    trigger_signal: str = ""     # what triggered this: MOBIUS, CSD, FALLBACK
    root_cause_summary: str = ""
    five_whys_chain: List[str] = field(default_factory=list)
    gv_content_before: str = ""
    gv_content_after: str = ""
    modification_reason: str = ""
    audit_result: Dict[str, Any] = field(default_factory=dict)
    validation_passed: bool = False
    committed: bool = False
    committed_at: str = ""
    created_at: str = ""
    error_rate_before: float = 0.0
    error_rate_after: float = 0.0
    observer_gate_passed: bool = False
    observer_gate_detail: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"dle_{uuid.uuid4().hex[:10]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "error_type": self.error_type,
            "gv_id": self.gv_id,
            "phase": self.phase,
            "trigger_signal": self.trigger_signal,
            "root_cause_summary": self.root_cause_summary,
            "five_whys_chain": self.five_whys_chain,
            "gv_content_before": self.gv_content_before,
            "gv_content_after": self.gv_content_after,
            "modification_reason": self.modification_reason,
            "audit_result": self.audit_result,
            "validation_passed": self.validation_passed,
            "committed": self.committed,
            "committed_at": self.committed_at,
            "created_at": self.created_at,
            "error_rate_before": self.error_rate_before,
            "error_rate_after": self.error_rate_after,
            "observer_gate_passed": self.observer_gate_passed,
            "observer_gate_detail": self.observer_gate_detail,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DoubleLoopEvent":
        return cls(
            error_type=d.get("error_type", ""),
            gv_id=d.get("gv_id", ""),
            event_id=d.get("event_id", ""),
            phase=d.get("phase", "triggered"),
            trigger_signal=d.get("trigger_signal", ""),
            root_cause_summary=d.get("root_cause_summary", ""),
            five_whys_chain=d.get("five_whys_chain", []),
            gv_content_before=d.get("gv_content_before", ""),
            gv_content_after=d.get("gv_content_after", ""),
            modification_reason=d.get("modification_reason", ""),
            audit_result=d.get("audit_result", {}),
            validation_passed=d.get("validation_passed", False),
            committed=d.get("committed", False),
            committed_at=d.get("committed_at", ""),
            created_at=d.get("created_at", ""),
        )


# ═══════════════════════════════════════════════════════════════
# DoubleLoopExecutor
# ═══════════════════════════════════════════════════════════════

class DoubleLoopExecutor:
    """Orchestrates the full double-loop learning cycle.

    Integrates with:
      - MobiusController for continuous trigger evaluation
      - CriticalSlowingDetector for statistical trend signals
      - DoubleLoopEfficacyTracker for outcome measurement
      - LLMClient for AI-powered RCA, proposal, and audit
      - KnowledgeBase for rule lookup and modification
      - FreshObserver for independent safety gating

    Args:
        mobius: Optional MobiusController for spectrum-based triggering.
        mobius_controller: Alias for mobius (backward compat).
        csd: Optional CriticalSlowingDetector for statistical triggers.
        efficacy: Optional DoubleLoopEfficacyTracker for outcome measurement.
        kb: Optional KnowledgeBase for rule management.
        llm_client: Optional LLMClient for AI-powered phases.
        observer: Optional FreshObserver for safety gating.
        artifacts_dir: Directory for persisting event history.
        min_error_count: Minimum error count for FALLBACK trigger (default 3).
    """

    def __init__(
        self,
        mobius: Optional[Any] = None,
        mobius_controller: Optional[Any] = None,
        csd: Optional[Any] = None,
        efficacy: Optional[Any] = None,
        kb: Optional[Any] = None,
        llm_client: Optional[Any] = None,
        observer: Optional[Any] = None,
        artifacts_dir: Optional[Path] = None,
        min_error_count: int = 3,
    ):
        # Accept both 'mobius' and 'mobius_controller' parameter names
        self.mobius = mobius or mobius_controller
        self.csd = csd
        self.efficacy = efficacy
        self.kb = kb
        self.llm_client = llm_client
        self.observer = observer
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else Path(".")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.min_error_count = min_error_count

        # Event storage
        self._events: List[DoubleLoopEvent] = []
        self._error_counts: Dict[str, int] = {}  # key = "error_type:gv_id" -> count
        self._state_file = self.artifacts_dir / "double_loop_state.json"
        self._load_state()

    # ── persistence ──────────────────────────────────────────

    def _load_state(self) -> None:
        """Load persisted event history."""
        if self._state_file.exists():
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._events = [DoubleLoopEvent.from_dict(e) for e in data.get("events", [])]
                self._error_counts = data.get("error_counts", {})
            except (json.JSONDecodeError, IOError):
                self._events = []
                self._error_counts = {}

    def _save_state(self) -> None:
        """Persist event history."""
        try:
            data = {
                "events": [e.to_dict() for e in self._events],
                "error_counts": self._error_counts,
            }
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── event queries ────────────────────────────────────────

    @property
    def recent_events(self) -> List[DoubleLoopEvent]:
        """Return recent events (last 50)."""
        return list(self._events[-50:])

    @property
    def committed_events(self) -> List[DoubleLoopEvent]:
        """Return only committed events."""
        return [e for e in self._events if e.committed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_events": len(self._events),
            "committed_count": len(self.committed_events),
            "rolled_back_count": sum(1 for e in self._events if e.phase == "rolled_back"),
            "events": [e.to_dict() for e in self._events[-20:]],
            "error_counts": dict(self._error_counts),
        }

    # ── trigger evaluation ───────────────────────────────────

    def record_error(self, error_type: str, gv_id: str) -> None:
        """Record an error occurrence for fallback trigger counting."""
        key = f"{error_type}:{gv_id}"
        self._error_counts[key] = self._error_counts.get(key, 0) + 1
        self._save_state()

    def _evaluate_trigger(self, error_type: str, gv_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Evaluate whether double-loop should trigger for this (error_type, gv_id).

        Checks in order:
          1. Mobius spectrum (continuous intensity-based trigger)
          2. Critical slowing detector (statistical trend)
          3. Fallback error count (>= min_error_count) — with direction awareness

        Direction-aware logic (3D cross-validated):
          - tau > 0 AND p < 0.05 → CSD worsening: trigger (物理: 势阱变浅)
          - tau < 0 AND p < 0.05 → CSD improving: SUPPRESS trigger (物理: 自组织修复)
          - tau ≈ 0 OR p >= 0.05 → no statistical signal, fall back to count

        Returns:
            (should_trigger: bool, reason: str, diagnostics: dict)
        """
        diagnostics: Dict[str, Any] = {
            "trigger_source": "",
            "mobius_intensity": 0.0,
            "csd_tau": 0.0,
            "csd_p": 1.0,
            "csd_critical": False,
            "csd_improving": False,
            "error_count": 0,
            "suppressed": False,
        }

        # 1. Mobius check
        if self.mobius is not None:
            try:
                if self.mobius.should_trigger(gv_id, error_type):
                    intensity = self.mobius.get_intensity(gv_id, error_type)
                    diagnostics["trigger_source"] = "MOBIUS"
                    diagnostics["mobius_intensity"] = intensity
                    return True, (
                        f"MOBIUS: intensity={intensity:.3f} >= 0.85 threshold. "
                        f"Spectrum has reached L4_COMMIT."
                    ), diagnostics
            except Exception:
                pass

        # 2. CSD check — with direction awareness
        if self.csd is not None:
            try:
                signal = self.csd.detect(error_type, gv_id)
                diagnostics["csd_tau"] = signal.kendall_tau
                diagnostics["csd_p"] = signal.p_value
                diagnostics["csd_critical"] = signal.critical

                # Direction-aware: significant NEGATIVE tau = improving (governance is working)
                if signal.significant and signal.kendall_tau < 0:
                    diagnostics["csd_improving"] = True
                    diagnostics["trigger_source"] = "CSD_IMPROVING"
                    # Physical interpretation: potential well deepening → self-healing
                    # Cybernetic: negative feedback is effective, no 2nd-order needed
                    # Mathematical: significant monotonic DECREASE in recovery time
                    key = f"{error_type}:{gv_id}"
                    count = self._error_counts.get(key, 0)
                    return False, (
                        f"SUPPRESSED: recovery time significantly IMPROVING "
                        f"(tau={signal.kendall_tau:.3f}, p={signal.p_value:.4f}). "
                        f"Governance is working — no double-loop needed. "
                        f"Errors={count}, but trend is downward."
                    ), diagnostics

                if signal.critical:
                    diagnostics["trigger_source"] = "CSD"
                    return True, (
                        f"CSD: critical slowing detected. "
                        f"tau={signal.kendall_tau:.3f}, p={signal.p_value:.4f}"
                    ), diagnostics
            except Exception:
                pass

        # 3. Fallback: error count
        key = f"{error_type}:{gv_id}"
        count = self._error_counts.get(key, 0)
        diagnostics["error_count"] = count

        if count >= self.min_error_count:
            diagnostics["trigger_source"] = "FALLBACK"
            return True, (
                f"FALLBACK: {count} errors >= {self.min_error_count} threshold"
            ), diagnostics

        diagnostics["trigger_source"] = "NONE"
        return False, (
            f"No trigger: mobius=False, csd=False, errors={count}/{self.min_error_count}"
        ), diagnostics

    # ── main execution cycle ─────────────────────────────────

    def execute(
        self,
        error_type: str,
        gv_id: str,
        context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        llm_client: Optional[Any] = None,
    ) -> DoubleLoopEvent:
        """Execute the full double-loop learning cycle.

        Phases: RCA → Propose → ObserverGate → Audit → Validate → Commit/Rollback

        Args:
            error_type: Error category (accuracy, completeness, compliance, etc.)
            gv_id: Governance variable ID (KB entry key).
            context: Optional additional context for analysis.
            model: LLM model override.
            llm_client: LLM client override (falls back to self.llm_client).

        Returns:
            DoubleLoopEvent with full lifecycle tracking.
        """
        client = llm_client or self.llm_client
        event = DoubleLoopEvent(error_type=error_type, gv_id=gv_id)

        # Phase 0: Evaluate trigger (direction-aware)
        should_trigger, reason, diagnostics = self._evaluate_trigger(error_type, gv_id)
        event.trigger_signal = reason
        # Store diagnostics for audit trail
        event.audit_result["_trigger_diagnostics"] = diagnostics
        if not should_trigger:
            event.phase = "aborted"
            event.root_cause_summary = f"Trigger not met: {reason}"
            # If suppressed due to improving trend, record as positive evidence
            if diagnostics.get("csd_improving"):
                event.root_cause_summary += (
                    f" | Governance for '{gv_id}' is effective (recovery improving). "
                    f"This is a POSITIVE signal — no rule change needed."
                )
            self._events.append(event)
            self._save_state()
            return event

        # Record error for fallback counting
        self.record_error(error_type, gv_id)

        # Phase 1: Safety gate
        if not self._passes_safety_gate(event):
            event.phase = "aborted"
            event.root_cause_summary = "Blocked by safety gate"
            self._events.append(event)
            self._save_state()
            return event

        # Estimate pre-modification error rate
        event.error_rate_before = self._estimate_error_rate(error_type, gv_id)

        # Phase 2: Root Cause Analysis
        event.phase = "rca"
        event = self._root_cause_analysis(event, client, model, context)

        # Phase 3: Propose modification
        event.phase = "proposed"
        event = self._propose_modification(event, client, model)

        # Phase 4: Observer Gate (independent blind review)
        gate_passed, gate_detail = self._observer_gate(event, "GATE_PROPOSAL")
        event.observer_gate_passed = gate_passed
        event.observer_gate_detail = gate_detail
        if not gate_passed:
            event.phase = "aborted"
            event.root_cause_summary += f" | Observer gate blocked: {gate_detail}"
            self._events.append(event)
            self._save_state()
            return event

        # Phase 5: Blind Audit
        event.phase = "audited"
        event = self._blind_audit(event, client, model)

        # Phase 6: Validate
        event.phase = "validated"
        event = self._validate(event, client, model)

        # Phase 7: Commit or Rollback
        if event.validation_passed:
            event = self._commit(event)
        else:
            event = self._rollback(event)

        # Sync graph if knowledge base available
        self._sync_graph(event)

        self._events.append(event)
        self._save_state()
        return event

    # ── safety gate ──────────────────────────────────────────

    def _passes_safety_gate(self, event: DoubleLoopEvent) -> bool:
        """Check if the double-loop event passes basic safety constraints.

        Rules:
          - Cannot modify L3 (axiom) entries
          - Cannot modify entries marked as is_constant
          - gv_id must be non-empty
        """
        if not event.gv_id or not event.gv_id.strip():
            return False

        # Check against knowledge base if available
        if self.kb is not None:
            try:
                entry = self.kb.get(event.gv_id)
                if entry is not None:
                    if getattr(entry, "level", "") == "L3":
                        return False
                    if getattr(entry, "is_constant", False):
                        return False
            except Exception:
                pass

        return True

    # ── root cause analysis ──────────────────────────────────

    def _root_cause_analysis(
        self,
        event: DoubleLoopEvent,
        llm_client: Optional[Any],
        model: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> DoubleLoopEvent:
        """Perform root cause analysis using 5-Whys methodology.

        Uses LLM if available; falls back to heuristic analysis from mobius data.
        """
        # Generate five whys from mobius twist point data if available
        if self.mobius is not None:
            try:
                twist_info = self.mobius.get_twist_info(event.gv_id, event.error_type)
                if twist_info.get("at_twist_point"):
                    pre_rca = twist_info.get("pre_rca", {})
                    event.root_cause_summary = (
                        f"Primary location: {pre_rca.get('primary_location', 'unknown')}. "
                        f"Pattern: {pre_rca.get('error_pattern', 'no pattern identified')}."
                    )
                    # Build 5-whys from bias history
                    bias_history = pre_rca.get("bias_history", [])
                    whys = []
                    for i, bh in enumerate(bias_history[:5]):
                        whys.append(
                            f"Why {i+1}: Error at '{bh.get('location', '?')}' — "
                            f"{bh.get('hint', 'no hint')}"
                        )
                    while len(whys) < 5:
                        whys.append(f"Why {len(whys)+1}: Root cause requires deeper investigation.")
                    event.five_whys_chain = whys
                    return event
            except Exception:
                pass

        # LLM-based RCA
        if llm_client is not None and hasattr(llm_client, "chat"):
            try:
                prompt = self._build_rca_prompt(event, context)
                result = llm_client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a root cause analyst. Use 5-Whys methodology."},
                        {"role": "user", "content": prompt},
                    ],
                )
                if result.get("ok") and result.get("content"):
                    event.root_cause_summary = result["content"][:500]
                    # Extract why chains from response
                    lines = result["content"].split("\n")
                    whys = [l.strip() for l in lines if l.strip().lower().startswith("why")]
                    if whys:
                        event.five_whys_chain = whys[:5]
                    return event
            except Exception:
                pass

        # Heuristic fallback
        event.root_cause_summary = (
            f"Recurring {event.error_type} error affecting '{event.gv_id}'. "
            f"Likely root cause: governance rule insufficient or outdated."
        )
        event.five_whys_chain = [
            f"Why 1: {event.error_type} error occurred repeatedly.",
            f"Why 2: Current rule for '{event.gv_id}' did not prevent it.",
            f"Why 3: The rule may lack specificity for edge cases.",
            f"Why 4: Edge cases were not anticipated when the rule was created.",
            f"Why 5: The rule creation process needs systematic improvement.",
        ]
        return event

    def _build_rca_prompt(
        self, event: DoubleLoopEvent, context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build prompt for LLM-based root cause analysis."""
        ctx_str = json.dumps(context, ensure_ascii=False) if context else "None"
        return (
            f"Perform a 5-Whys root cause analysis for the following repeated error:\n\n"
            f"Error Type: {event.error_type}\n"
            f"Governance Variable: {event.gv_id}\n"
            f"Context: {ctx_str}\n\n"
            f"Output each 'Why' on a separate line, starting with 'Why 1:', 'Why 2:', etc.\n"
            f"Conclude with a root cause summary."
        )

    # ── propose modification ─────────────────────────────────

    def _propose_modification(
        self,
        event: DoubleLoopEvent,
        llm_client: Optional[Any],
        model: Optional[str],
    ) -> DoubleLoopEvent:
        """Propose a rule modification based on RCA findings.

        Looks up current rule content from KB, then drafts a modification.
        """
        # Get current rule content
        if self.kb is not None:
            try:
                entry = self.kb.get(event.gv_id)
                if entry is not None:
                    event.gv_content_before = getattr(entry, "content", "")
            except Exception:
                pass

        # Use mobius twist info for override suggestion
        if self.mobius is not None and not event.gv_content_after:
            try:
                twist_info = self.mobius.get_twist_info(event.gv_id, event.error_type)
                override = twist_info.get("override", {})
                suggested = override.get("suggested_override", "")
                if suggested:
                    event.gv_content_after = suggested
                    event.modification_reason = (
                        f"Based on {twist_info.get('bias_count', 0)} accumulated biases "
                        f"at intensity {twist_info.get('intensity', 0):.3f}. "
                        f"RCA: {event.root_cause_summary[:200]}"
                    )
                    return event
            except Exception:
                pass

        # LLM-based proposal
        if llm_client is not None and hasattr(llm_client, "chat"):
            try:
                prompt = self._build_proposal_prompt(event)
                result = llm_client.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a governance rule designer. Propose precise, actionable modifications."},
                        {"role": "user", "content": prompt},
                    ],
                )
                if result.get("ok") and result.get("content"):
                    event.gv_content_after = result["content"][:1000]
                    event.modification_reason = (
                        f"LLM-generated modification based on RCA: {event.root_cause_summary[:200]}"
                    )
                    return event
            except Exception:
                pass

        # Heuristic fallback
        event.gv_content_after = (
            f"[Proposed modification for {event.gv_id}] "
            f"Add guard for {event.error_type} errors. "
            f"Root cause: {event.root_cause_summary[:200]}"
        )
        event.modification_reason = "Heuristic modification based on error pattern."
        return event

    def _build_proposal_prompt(self, event: DoubleLoopEvent) -> str:
        """Build prompt for LLM-based rule modification proposal."""
        return (
            f"Propose a precise modification to the governance rule '{event.gv_id}' "
            f"to prevent recurring {event.error_type} errors.\n\n"
            f"Current Rule:\n{event.gv_content_before or '(not available)'}\n\n"
            f"Root Cause Analysis:\n{event.root_cause_summary}\n\n"
            f"5-Whys Chain:\n" + "\n".join(event.five_whys_chain) + "\n\n"
            f"Output ONLY the modified rule text. Be specific and actionable."
        )

    # ── observer gate ────────────────────────────────────────

    def _observer_gate(
        self, event: DoubleLoopEvent, gate_type: str = "GATE_PROPOSAL"
    ) -> Tuple[bool, str]:
        """Run the proposal through an independent observer gate.

        Uses FreshObserver if available; uses ObserverWeight heuristic otherwise.
        """
        # Try FreshObserver
        if self.observer is not None:
            try:
                if gate_type == "GATE_PROPOSAL":
                    if hasattr(self.observer, "gate_proposal"):
                        result = self.observer.gate_proposal(
                            before=event.gv_content_before,
                            after=event.gv_content_after,
                            reason=event.modification_reason,
                        )
                        if hasattr(result, "all_passed"):
                            return result.all_passed, getattr(result, "block_reason", "")
                elif gate_type == "GATE_RCA":
                    if hasattr(self.observer, "gate_rca"):
                        result = self.observer.gate_rca(
                            error_pattern=event.error_type,
                            root_cause=event.root_cause_summary,
                            five_whys=event.five_whys_chain,
                        )
                        if hasattr(result, "all_passed"):
                            return result.all_passed, getattr(result, "block_reason", "")
                elif gate_type == "GATE_AUDIT":
                    if hasattr(self.observer, "gate_audit"):
                        result = self.observer.gate_audit(
                            audit_summary=json.dumps(event.audit_result)
                        )
                        if hasattr(result, "all_passed"):
                            return result.all_passed, getattr(result, "block_reason", "")
            except Exception:
                pass

        # Heuristic fallback using ObserverWeight logic
        # Block if: no modification proposed, or before == after
        if not event.gv_content_after or event.gv_content_after == event.gv_content_before:
            return False, "No actual modification proposed (content unchanged or empty)."

        # Block if modification is too short (likely placeholder)
        if len(event.gv_content_after) < 10:
            return False, "Proposed modification too short (< 10 chars)."

        # Block if modification reason is empty
        if not event.modification_reason:
            return False, "No modification reason provided."

        return True, "Observer gate passed (heuristic)."

    # ── blind audit ──────────────────────────────────────────

    def _blind_audit(
        self,
        event: DoubleLoopEvent,
        llm_client: Optional[Any],
        model: Optional[str],
    ) -> DoubleLoopEvent:
        """Perform adversarial blind audit of the proposed modification.

        Uses the AdversarialAuditor (Prosecutor/Defender/Judge triangle) when
        MetaReviewer is available, falling back to the legacy 3-perspective audit.

        Three-dimensional cross-validation:
          - 物理: 三体相互作用产生稳定轨道（两体 opinion dynamics 会塌缩到假共识）
          - 系统: 三角反馈是最简二阶控制系统（对抗确保信息完备性）
          - 数学: ≥2/3 裁决 ≈ 贝叶斯因子决策规则
        """
        audit = {
            "correctness": {"score": 0.5, "comment": ""},
            "safety": {"score": 0.5, "comment": ""},
            "completeness": {"score": 0.5, "comment": ""},
            "overall_score": 0.5,
            "passed": False,
            "reviewer_count": 0,
            "method": "legacy",
            "divergence_tree": {},
        }

        # ── Try adversarial audit via MetaReviewer ──
        try:
            from kugua.meta_reviewer import MetaReviewer, AdversarialAuditor

            if llm_client is not None and hasattr(llm_client, "chat"):
                # Create MetaReviewer and AdversarialAuditor
                mr = MetaReviewer(llm_client=llm_client)
                adversarial = AdversarialAuditor(
                    llm_client=llm_client,
                    meta_reviewer=mr,
                )

                adv_result = adversarial.audit(
                    gv_content_before=event.gv_content_before,
                    gv_content_after=event.gv_content_after,
                    modification_reason=event.modification_reason,
                    root_cause_summary=event.root_cause_summary,
                    five_whys_chain=event.five_whys_chain,
                    error_type=event.error_type,
                )

                # Map adversarial result to audit dict
                audit["method"] = "adversarial"
                audit["overall_score"] = adv_result.composite_score or (
                    sum(v.confidence for v in adv_result.votes) / max(len(adv_result.votes), 1)
                )
                audit["passed"] = adv_result.passed
                audit["reviewer_count"] = len(adv_result.votes)
                audit["approve_count"] = adv_result.approve_count
                audit["reject_count"] = adv_result.reject_count
                audit["needs_revision_count"] = adv_result.needs_revision_count
                audit["summary"] = adv_result.summary

                # Attach divergence tree if available
                if hasattr(adv_result, '_divergence_tree'):
                    audit["divergence_tree"] = adv_result._divergence_tree.to_dict()

                # Map individual votes to correctness/safety/completeness
                if adv_result.votes:
                    # Judge vote → overall
                    judge = adv_result.votes[0]
                    audit["correctness"]["score"] = judge.confidence
                    audit["correctness"]["comment"] = judge.reasoning[:200]
                    # If we have expert witness → safety
                    if len(adv_result.votes) > 1:
                        expert = adv_result.votes[1]
                        audit["safety"]["score"] = expert.confidence
                        audit["safety"]["comment"] = expert.reasoning[:200]
                    # Meta-reviewer votes → completeness
                    mr_votes = [v for v in adv_result.votes if v.template != "adversarial_judge" and v.template != "expert_witness"]
                    if mr_votes:
                        audit["completeness"]["score"] = sum(v.confidence for v in mr_votes) / len(mr_votes)
                        audit["completeness"]["comment"] = "; ".join(v.reasoning[:100] for v in mr_votes)

                event.audit_result = audit
                return event
        except ImportError:
            pass
        except Exception:
            pass

        # ── Fallback: Legacy 3-perspective audit ──
        if llm_client is not None and hasattr(llm_client, "chat"):
            perspectives = [
                ("correctness", "Evaluate whether this modification correctly addresses the root cause."),
                ("safety", "Evaluate whether this modification introduces any safety risks or side effects."),
                ("completeness", "Evaluate whether this modification covers relevant edge cases."),
            ]
            scores = []
            for perspective, instruction in perspectives:
                try:
                    prompt = (
                        f"{instruction}\n\n"
                        f"Error Type: {event.error_type}\n"
                        f"Governance Variable: {event.gv_id}\n"
                        f"Root Cause: {event.root_cause_summary[:300]}\n"
                        f"Before: {event.gv_content_before[:500]}\n"
                        f"After: {event.gv_content_after[:500]}\n"
                        f"Reason: {event.modification_reason[:200]}\n\n"
                        f"Respond with a score (0.0-1.0) and brief comment."
                    )
                    result = llm_client.chat(
                        model=model,
                        messages=[
                            {"role": "system", "content": f"You are a {perspective} auditor."},
                            {"role": "user", "content": prompt},
                        ],
                    )
                    if result.get("ok") and result.get("content"):
                        score = self._extract_score(result["content"])
                        audit[perspective] = {
                            "score": score,
                            "comment": result["content"][:200],
                        }
                        scores.append(score)
                        audit["reviewer_count"] += 1
                except Exception:
                    pass

            if scores:
                audit["overall_score"] = sum(scores) / len(scores)
                audit["passed"] = audit["overall_score"] >= 0.5

        event.audit_result = audit
        return event

    @staticmethod
    def _extract_score(text: str) -> float:
        """Extract a 0.0-1.0 score from audit text."""
        import re
        # Try to find a decimal score
        m = re.search(r"(\d+\.?\d*)\s*/\s*1", text)
        if m:
            return max(0.0, min(1.0, float(m.group(1))))
        m = re.search(r"score[:\s]*(\d+\.?\d*)", text, re.IGNORECASE)
        if m:
            return max(0.0, min(1.0, float(m.group(1))))
        m = re.search(r"(\d+)%\s*(?:score|confidence)", text, re.IGNORECASE)
        if m:
            return max(0.0, min(1.0, float(m.group(1)) / 100.0))
        # Fallback: first decimal found
        m = re.search(r"(\d+\.\d+)", text)
        if m:
            val = float(m.group(1))
            if 0.0 <= val <= 1.0:
                return val
            if 1.0 < val <= 100.0:
                return val / 100.0
        return 0.5

    # ── validate ─────────────────────────────────────────────

    def _validate(
        self,
        event: DoubleLoopEvent,
        llm_client: Optional[Any],
        model: Optional[str],
    ) -> DoubleLoopEvent:
        """Validate the proposed modification against recent cases.

        Checks:
          1. Audit passed?
          2. Observer gate passed?
          3. Modification is different from original?
        """
        audit_passed = event.audit_result.get("passed", False)
        gate_passed = event.observer_gate_passed
        has_change = (
            event.gv_content_after
            and event.gv_content_after != event.gv_content_before
        )
        has_reason = bool(event.modification_reason)

        event.validation_passed = all([audit_passed, gate_passed, has_change, has_reason])
        return event

    # ── commit / rollback ────────────────────────────────────

    def _commit(self, event: DoubleLoopEvent) -> DoubleLoopEvent:
        """Commit the modification to the knowledge base and reset mobius spectrum."""
        event.phase = "committed"
        event.committed = True
        event.committed_at = datetime.now(timezone.utc).isoformat()

        # Update knowledge base if available
        if self.kb is not None:
            try:
                entry = self.kb.get(event.gv_id)
                if entry is not None:
                    entry.content = event.gv_content_after
                    entry.confidence = min(1.0, getattr(entry, "confidence", 0.5) + 0.1)
                    self.kb.mark_success(event.gv_id)
            except Exception:
                pass

        # Record efficacy if tracker available
        if self.efficacy is not None:
            try:
                eff_id = self.efficacy.start_baseline(event.error_type, event.gv_id)
                self.efficacy.mark_modified(eff_id)
                delta = 0.3  # conservative entropy reduction estimate
                self.efficacy.record_outcome(eff_id, success=True, entropy_delta=delta)
            except Exception:
                pass

        # Notify mobius controller to reset spectrum
        if self.mobius is not None:
            try:
                self.mobius.on_double_loop_committed(event)
            except Exception:
                pass

        # Record version for traceability and rollback
        commit_hash = self._record_version(event)
        if commit_hash:
            event.audit_result["commit_hash"] = commit_hash

        return event

    def _rollback(self, event: DoubleLoopEvent) -> DoubleLoopEvent:
        """Roll back the modification attempt."""
        event.phase = "rolled_back"
        event.committed = False
        event.gv_content_after = event.gv_content_before

        # Record failed intervention in mobius for threshold calibration
        if self.mobius is not None:
            try:
                self.mobius.record_intervention_outcome(
                    event.gv_id, event.error_type, success=False
                )
            except Exception:
                pass

        # Record reverted outcome if tracker available
        if self.efficacy is not None:
            try:
                eff_id = self.efficacy.start_baseline(event.error_type, event.gv_id)
                self.efficacy.record_outcome(eff_id, success=False, entropy_delta=0.0)
            except Exception:
                pass

        return event

    # ── graph sync ───────────────────────────────────────────

    def _sync_graph(self, event: DoubleLoopEvent) -> None:
        """Synchronize the modification with the GraphKB if available.

        Adds causal edges between the error type and the modified governance variable.
        """
        if self.kb is None:
            return
        try:
            # Check if kb has graph attribute
            graph = getattr(self.kb, "graph", None)
            if graph is not None and event.committed:
                # Add/update edge between error_type and gv_id
                try:
                    graph.add_edge(
                        source_node=event.error_type,
                        target_node=event.gv_id,
                        relation="MODIFIED_BY",
                        weight=0.8,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    # ── versioning ───────────────────────────────────────────

    def _record_version(self, event: DoubleLoopEvent) -> Optional[str]:
        """Record a versioned commit when a DoubleLoop modification is committed.

        Creates a KnowledgeCommit in the version graph for traceability and rollback.
        Returns the commit hash, or None if versioning is unavailable.
        """
        try:
            from kugua.versioning import KnowledgeCommit, VersionGraph

            # Initialize version graph lazily
            if not hasattr(self, '_version_graph'):
                artifacts = getattr(self, 'artifacts_dir', None) or Path(".")
                self._version_graph = VersionGraph(artifacts_dir=artifacts)

            commit = KnowledgeCommit(
                gv_id=event.gv_id,
                diff_before=event.gv_content_before,
                diff_after=event.gv_content_after,
                reason=event.modification_reason,
                five_whys=event.five_whys_chain,
                audit_result=event.audit_result,
                message=f"DoubleLoop: {event.error_type} fix for {event.gv_id}",
                tags=[event.error_type, event.phase],
            )
            return self._version_graph.commit(commit)
        except ImportError:
            pass
        except Exception:
            pass
        return None

    def get_version_history(self, gv_id: str) -> list:
        """Get version history for a governance variable.

        Returns list of KnowledgeCommit dicts for this gv_id.
        Requires _record_version to have been called at least once.
        """
        if not hasattr(self, '_version_graph'):
            return []
        return [c.to_dict() for c in self._version_graph.log(gv_id)]

    def rollback_gv(self, gv_id: str, target_hash: str) -> Optional[dict]:
        """Roll back a governance variable to a specific historical version.

        Args:
            gv_id: The governance variable to roll back.
            target_hash: The commit hash to revert to.

        Returns:
            The rollback commit dict, or None if rollback failed.
        """
        if not hasattr(self, '_version_graph'):
            return None
        commit = self._version_graph.rollback(gv_id, target_hash)
        if commit and self.kb:
            entry = self.kb.get(gv_id)
            if entry:
                entry.content = commit.diff_after
        return commit.to_dict() if commit else None

    # ── error rate estimation ────────────────────────────────

    def _estimate_error_rate(self, error_type: str, gv_id: str) -> float:
        """Estimate current error rate for the given pair.

        1.0 = every occurrence is an error, 0.0 = no errors.
        """
        key = f"{error_type}:{gv_id}"
        count = self._error_counts.get(key, 0)

        # More errors → higher rate, bounded at 1.0
        if count == 0:
            return 0.0
        if count <= 2:
            return 0.3
        if count <= 5:
            return 0.6
        if count <= 10:
            return 0.8
        return 0.95
