"""
kugua — FreshObserver (hallucination-immune gate)
v0.2.1

Independent LLM instance with minimal context. Each observation is stateless —
the observer sees every decision point with fresh eyes, avoiding context-overload
hallucination that affects the main model.

Three gates:
  GATE_RCA      — root cause analysis sanity check
  GATE_PROPOSAL — rule modification safety check
  GATE_AUDIT    — meta-review of blind audit votes

Design principle: context asymmetry
  Main model: carries accumulated KB entries, task history, confidence scores
  FreshObserver: only receives the current decision point, no history, no KB
"""
from __future__ import annotations

import json, time
from typing import Optional


GATE_PROMPTS = {
    "GATE_RCA": {
        "name": "Root Cause Sanity Check",
        "role": "You are a rational checker with no prior context. Your only task: judge whether a claimed root cause holds under common sense.",
        "prompt": """Judge whether the following root cause analysis is reasonable based on common sense only.

**Observed error pattern:**
{error_pattern}

**Claimed root cause:**
{root_cause}

**5-Whys chain:**
{five_whys}

Answer:
1. Is this root cause obviously reasonable — would a rational person reach a similar conclusion?
2. Are there obvious logical leaps or causal reversals in the 5-Whys chain?
3. Is there a simpler, more common explanation being overlooked? (Occam's razor)

Return JSON:
{{"verdict": "REASONABLE or SUSPICIOUS or HALLUCINATION",
 "hallucination_indicators": ["signs of hallucination, empty if none"],
 "simpler_explanation": "simpler possible explanation, empty if none",
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning (<=100 chars)"}}""",
    },
    "GATE_PROPOSAL": {
        "name": "Modification Safety Check",
        "role": "You are a safety checker with no prior context. Your only task: judge whether a rule modification is safe and logical.",
        "prompt": """Judge whether the following rule modification is safe based on common sense only.

**Rule before modification:**
{before}

**Rule after modification:**
{after}

**Reason for modification:**
{reason}

Answer:
1. Is the modified rule clearer, more precise, less ambiguous than before?
2. Is the modification excessive — "rewrote the whole module to fix one bug"?
3. Could the modification introduce new, more serious errors?
4. Does the modification preserve consistency with the obviously-correct parts of the original rule?

Return JSON:
{{"verdict": "SAFE or RISKY or DANGEROUS",
 "risk_indicators": ["risk signs, empty if none"],
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning (<=100 chars)"}}""",
    },
    "GATE_AUDIT": {
        "name": "Meta-Review of Blind Audit",
        "role": "You are a meta-reviewer. Your only task: judge whether three review opinions are coherent and reasonable.",
        "prompt": """Review whether the following three blind audit votes are reasonable.

**Audit summary:**
{audit_summary}

Answer:
1. Are the three reviewers roughly consistent? If divergent, is the divergence from different valid angles rather than random?
2. Does any reviewer make clearly unreasonable or self-contradictory claims?
3. Taken together, is the overall conclusion trustworthy?

Return JSON:
{{"verdict": "COHERENT or DIVERGENT or UNRELIABLE",
 "incoherent_voter_index": -1,
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning (<=100 chars)"}}""",
    },
}


class Observation:
    """Single fresh observation result."""

    def __init__(self):
        self.gate: str = ""
        self.verdict: str = ""
        self.confidence: float = 0.0
        self.calibrated_confidence: float = 0.0
        self.reasoning: str = ""
        self.indicators: list[str] = []
        self.passed: bool = False
        self.elapsed_ms: float = 0
        self.model: str = ""
        self.provider: str = ""

    def to_dict(self) -> dict:
        return {
            "gate": self.gate, "verdict": self.verdict,
            "confidence": self.confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "reasoning": self.reasoning, "indicators": self.indicators,
            "passed": self.passed, "elapsed_ms": self.elapsed_ms,
            "model": self.model, "provider": self.provider,
        }


class ObserverGateResult:
    """Complete observer gate result (may contain multiple gates)."""

    def __init__(self):
        self.observations: list[Observation] = []
        self.all_passed: bool = False
        self.blocked_at: str = ""
        self.block_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "all_passed": self.all_passed,
            "blocked_at": self.blocked_at,
            "block_reason": self.block_reason,
            "observations": [o.to_dict() for o in self.observations],
        }


class FreshObserver:
    """Fresh observer — independent LLM instance, stateless per call.

    Features:
      - Independent LLM client (different model/provider avoids common-mode failure)
      - Each observation = single stateless call, no accumulated history
      - temperature=0.0 for maximum determinism
      - Gates at key double-loop decision points

    Usage:
        obs_client = LLMClient(cfg_for_observer)
        observer = FreshObserver(llm_client=obs_client)
        result = observer.gate_rca(
            error_pattern="5 rate validation failures",
            root_cause="GV LPR cap doesn't distinguish fixed/floating rates",
            five_whys=["..."]
        )
        if not result.all_passed:
            print(f"Blocked by observer: {result.block_reason}")
    """

    def __init__(self, llm_client=None, calibration=None):
        self.client = llm_client
        self.calibration = calibration

    def gate_rca(self, error_pattern: str, root_cause: str,
                 five_whys: list[str] = None) -> ObserverGateResult:
        result = ObserverGateResult()
        obs = self._observe("GATE_RCA", {
            "error_pattern": error_pattern,
            "root_cause": root_cause,
            "five_whys": json.dumps(five_whys or [], ensure_ascii=False),
        })
        result.observations.append(obs)
        result.all_passed = obs.passed
        if not obs.passed:
            result.blocked_at = "GATE_RCA"
            result.block_reason = f"RCA judged {obs.verdict}: {obs.reasoning}"
        return result

    def gate_proposal(self, before: str, after: str, reason: str) -> ObserverGateResult:
        result = ObserverGateResult()
        obs = self._observe("GATE_PROPOSAL", {
            "before": before, "after": after, "reason": reason,
        })
        result.observations.append(obs)
        result.all_passed = obs.passed
        if not obs.passed:
            result.blocked_at = "GATE_PROPOSAL"
            result.block_reason = f"Proposal judged {obs.verdict}: {obs.reasoning}"
        return result

    def gate_audit(self, audit_summary: str) -> ObserverGateResult:
        result = ObserverGateResult()
        obs = self._observe("GATE_AUDIT", {"audit_summary": audit_summary})
        result.observations.append(obs)
        result.all_passed = obs.passed
        if not obs.passed:
            result.blocked_at = "GATE_AUDIT"
            result.block_reason = f"Audit judged {obs.verdict}: {obs.reasoning}"
        return result

    def gate_full_cycle(self, error_pattern: str, root_cause: str,
                        five_whys: list[str], before: str, after: str,
                        reason: str, audit_summary: str) -> ObserverGateResult:
        result = ObserverGateResult()
        for gate_fn, gate_name in [
            (lambda: self.gate_rca(error_pattern, root_cause, five_whys), "GATE_RCA"),
            (lambda: self.gate_proposal(before, after, reason), "GATE_PROPOSAL"),
            (lambda: self.gate_audit(audit_summary), "GATE_AUDIT"),
        ]:
            gate_result = gate_fn()
            result.observations.extend(gate_result.observations)
            if not gate_result.all_passed:
                result.all_passed = False
                result.blocked_at = gate_result.blocked_at
                result.block_reason = gate_result.block_reason
                return result
        result.all_passed = True
        return result

    def _observe(self, gate: str, params: dict) -> Observation:
        obs = Observation()
        obs.gate = gate
        template = GATE_PROMPTS[gate]

        if not self.client:
            obs.verdict = "PASSED_BY_DEFAULT"
            obs.confidence = 1.0
            obs.reasoning = "No observer LLM configured, passed by default"
            obs.passed = True
            return obs

        t0 = time.time()
        prompt = template["prompt"].format(**params)
        resp = self.client.chat(
            [{"role": "system", "content": template["role"]},
             {"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=512,
        )

        obs.elapsed_ms = (time.time() - t0) * 1000
        obs.model = resp.get("model", "") if isinstance(resp, dict) else getattr(resp, "model", "")
        obs.provider = "observer"

        try:
            content = resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "content", "")
            data = json.loads(content)
        except (json.JSONDecodeError, AttributeError):
            import re
            content = str(resp) if not isinstance(resp, dict) else resp.get("content", "")
            m = re.search(r'```(?:json)?\s*(.*?)```', content, re.DOTALL)
            data = json.loads(m.group(1)) if m else {}

        obs.verdict = data.get("verdict", "SUSPICIOUS")
        obs.confidence = float(data.get("confidence", 0.5))
        obs.reasoning = data.get("reasoning", "")[:200]

        for field in ["hallucination_indicators", "risk_indicators"]:
            items = data.get(field, [])
            if isinstance(items, list):
                obs.indicators.extend(items)

        safe_verdicts = {"REASONABLE", "SAFE", "COHERENT", "PASSED_BY_DEFAULT"}
        obs.passed = obs.verdict.upper() in safe_verdicts

        if self.calibration:
            obs.calibrated_confidence = self.calibration.get_calibrated_confidence(
                f"observer_{gate.lower()}", obs.confidence
            )
        else:
            obs.calibrated_confidence = obs.confidence

        return obs


def create_observer_from_config(config) -> FreshObserver:
    """Build FreshObserver from KuguaConfig."""
    from kugua.executor import LLMClient
    obs_provider = config.get_observer_provider() if hasattr(config, 'get_observer_provider') else None
    if not obs_provider:
        return FreshObserver(llm_client=None)
    observer_config = type(config)(providers=[obs_provider], artifacts_dir=config.artifacts_dir)
    client = LLMClient(observer_config)
    return FreshObserver(llm_client=client)
