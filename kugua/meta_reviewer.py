"""
kugua — MetaReviewer (blind audit protocol)
v0.3.0

Blind audit of GV modification proposals:
  - 3 independent LLM calls using different templates
  - Information isolation: no original confidence, checker scores, or review history
  - >=2/3 APPROVED -> audit passes

4 templates from different metacognitive frameworks:
  logic_consistency / counterexample_hunt / reversibility_check / boundary_test
"""
from __future__ import annotations

import json, hashlib, random
from typing import Optional


BLIND_AUDIT_TEMPLATES = {
    "logic_consistency": {
        "name": "Logic Consistency Review",
        "role": "You are a formal logic reviewer. Your only task: check internal consistency of the argument.",
        "prompt": """Review the logic consistency of the following knowledge entry modification.

**Rule before:**
{before}

**Rule after:**
{after}

**Reason:**
{reason}

**Impact scope:**
{impact}

Answer from a logical consistency perspective:
1. Is the modification reason logically self-consistent? (No circular arguments, equivocation, false attribution)
2. Does the modified rule contain internal contradictions?
3. If this rule is applied to tasks within {impact}, would it be logically coherent?

Return JSON:
{{"verdict": "APPROVED or REJECTED or NEEDS_REVISION",
 "logical_flaws": ["discovered logical flaws"],
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning"}}""",
    },
    "counterexample_hunt": {
        "name": "Counterexample Hunt",
        "role": "You are a counterexample hunter. Your only task: find concrete counterexamples that would break the modified rule.",
        "prompt": """Search for counterexamples that would break the following rule modification.

**Rule before:**
{before}

**Rule after:**
{after}

**Reason:**
{reason}

**Impact scope:**
{impact}

Actively hunt for counterexamples:
1. Within {impact}, are there known cases where the modified rule would produce errors?
2. Does the modification over-generalize a special case?
3. Under which boundary conditions would the modified rule degrade back to the pre-modification error?

Return JSON:
{{"verdict": "APPROVED or REJECTED or NEEDS_REVISION",
 "counterexamples": ["found counterexamples, empty if none"],
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning"}}""",
    },
    "reversibility_check": {
        "name": "Reversibility Check",
        "role": "You are a system safety reviewer. Your only task: assess reversibility and rollback risk.",
        "prompt": """Assess the reversibility of the following rule modification.

**Rule before:**
{before}

**Rule after:**
{after}

**Impact scope:**
{impact}

Answer from a reversibility perspective:
1. If this modification proves wrong, is rollback to the original rule straightforward?
2. Would the modified rule produce irreversible side effects?
3. During the rollback window (3600s after modification), how many tasks would use this rule?

Return JSON:
{{"verdict": "APPROVED or REJECTED or NEEDS_REVISION",
 "reversibility_risk": "LOW or MEDIUM or HIGH",
 "irreversible_side_effects": ["irreversible side effects, empty if none"],
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning"}}""",
    },
    "boundary_test": {
        "name": "Boundary Clarity Test",
        "role": "You are a boundary tester. Your only task: check whether the modified rule has clear, unambiguous scope.",
        "prompt": """Test the boundary clarity of the following rule modification.

**Rule before:**
{before}

**Rule after:**
{after}

**Reason:**
{reason}

**Impact scope:**
{impact}

Answer from a boundary clarity perspective:
1. Does the modified rule have a clearly defined scope? Are there gray areas?
2. Under what conditions should the old rule vs the new rule apply? Is this boundary clearly decidable?
3. Does the modification introduce new ambiguity — where two users could interpret the rule differently?

Return JSON:
{{"verdict": "APPROVED or REJECTED or NEEDS_REVISION",
 "ambiguous_boundaries": ["ambiguous boundaries, empty if none"],
 "confidence": 0.0-1.0,
 "reasoning": "brief reasoning"}}""",
    },
}


class BlindAuditVote:
    """Single meta-reviewer vote."""

    def __init__(self):
        self.template: str = ""
        self.verdict: str = ""
        self.confidence: float = 0.0
        self.reasoning: str = ""
        self.flaws: list[str] = []
        self.reversibility_risk: str = ""
        self.raw_response: str = ""

    def is_approve(self) -> bool:
        return self.verdict == "APPROVED"


class BlindAuditResult:
    """Aggregated blind audit result."""

    def __init__(self):
        self.passed: bool = False
        self.votes: list[BlindAuditVote] = []
        self.approve_count: int = 0
        self.reject_count: int = 0
        self.needs_revision_count: int = 0
        self.templates_used: list[str] = []
        self.template_hash: str = ""
        self.summary: str = ""
        self.composite_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "approve_count": self.approve_count,
            "reject_count": self.reject_count, "needs_revision_count": self.needs_revision_count,
            "templates_used": self.templates_used, "template_hash": self.template_hash,
            "summary": self.summary, "composite_score": self.composite_score,
            "votes": [{"template": v.template, "verdict": v.verdict,
                        "confidence": v.confidence, "reasoning": v.reasoning[:200],
                        "flaws": v.flaws, "reversibility_risk": v.reversibility_risk}
                      for v in self.votes],
        }


class MetaReviewer:
    """Blind audit executor — 3 meta-reviewers + 4 template pool.

    Protocol:
      1. Randomly select 3 templates from the 4-template pool
      2. Each reviewer receives NO: original confidence, checker scores, review history, proposer identity
      3. >=2/3 APPROVED -> audit passes
      4. Template hash recorded to prove diverse templates were used

    Usage:
        mr = MetaReviewer(llm_client)
        result = mr.audit(
            gv_content_before="Guarantee period defaults to 6 months",
            gv_content_after="Guarantee period must be extracted from contract; default 6 months if absent",
            reason="Current rule ignores contractually specified periods",
            impact="All guarantee contract classification and extraction tasks",
        )
        if result.passed:
            print("Audit passed, proceed to VALIDATE phase")
    """

    def __init__(self, llm_client=None, calibration=None, consistency_scorer=None):
        self.client = llm_client
        self.calibration = calibration
        self.consistency_scorer = consistency_scorer
        self._template_pool = list(BLIND_AUDIT_TEMPLATES.keys())

    def audit(self, gv_content_before: str, gv_content_after: str,
              reason: str = "", impact: str = "",
              error_pattern_summary: str = "") -> BlindAuditResult:
        result = BlindAuditResult()
        selected = self._select_templates()
        result.templates_used = selected
        result.template_hash = hashlib.sha256(",".join(selected).encode()).hexdigest()[:12]

        info = {
            "before": gv_content_before,
            "after": gv_content_after,
            "reason": reason,
            "impact": impact or "unspecified scope",
        }

        if not self.client:
            result.passed = True
            result.summary = "No LLM client, audit passed by default"
            return result

        for template_name in selected:
            template = BLIND_AUDIT_TEMPLATES[template_name]
            prompt = template["prompt"].format(**info)

            resp = self.client.chat(
                [{"role": "system", "content": template["role"]},
                 {"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=1024,
            )
            content = resp.get("content", "") if isinstance(resp, dict) else getattr(resp, "content", "")
            vote = self._parse_vote(content, template_name)
            result.votes.append(vote)

        result.approve_count = sum(1 for v in result.votes if v.verdict == "APPROVED")
        result.reject_count = sum(1 for v in result.votes if v.verdict == "REJECTED")
        result.needs_revision_count = sum(1 for v in result.votes if v.verdict == "NEEDS_REVISION")
        result.passed = result.approve_count >= 2

        flaws_all = []
        for v in result.votes:
            flaws_all.extend(v.flaws)
        result.summary = (
            f"Audit {'PASSED' if result.passed else 'FAILED'}: "
            f"{result.approve_count} approve, {result.reject_count} reject, "
            f"{result.needs_revision_count} needs_revision. "
            + (f"Issues: {'; '.join(flaws_all[:3])}" if flaws_all else "No significant issues")
        )
        return result

    def _select_templates(self) -> list[str]:
        pool = list(self._template_pool)
        random.shuffle(pool)
        selected = pool[:3]
        unique = set(selected)
        if len(unique) < 2:
            for t in pool:
                if t not in unique:
                    selected[2] = t
                    break
        return selected

    @staticmethod
    def _parse_vote(raw: str, template_name: str) -> BlindAuditVote:
        vote = BlindAuditVote()
        vote.template = template_name
        vote.raw_response = raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
            data = json.loads(m.group(1).strip()) if m else {}
        vote.verdict = data.get("verdict", "REJECTED").upper()
        vote.confidence = float(data.get("confidence", 0.5))
        for field in ["logical_flaws", "counterexamples", "irreversible_side_effects", "ambiguous_boundaries"]:
            items = data.get(field, [])
            if isinstance(items, list):
                vote.flaws.extend(items)
        reversibility = data.get("reversibility_risk", "")
        if reversibility:
            vote.reversibility_risk = reversibility
        vote.reasoning = data.get("reasoning", "")
        return vote


# ═══════════════════════════════════════════════════════════════
# Adversarial Auditor — Prosecutor/Defender/Judge structure
# (3D cross-validated: 物理三体稳定轨道, 系统三角反馈, 数学≥2/3裁决)
# ═══════════════════════════════════════════════════════════════

class DivergenceTree:
    """Tracks consensus and divergence points across multiple auditors.

    Inspired by AgentAuditor (2025) "Reasoning Tree" — explicitly maps
    where reviewers agree vs. where they diverge, enabling targeted
    verification of Critical Divergence Points (CDPs).

    Physical: like a free-energy landscape — consensus = potential wells,
             divergence = saddle points where decisions are unstable.
    Cybernetic: like a multi-sensor fusion system — track which sensors
               agree and which report anomalous readings.
    Mathematical: maps onto the covariance structure of reviewer opinions.
    """

    def __init__(self):
        self.consensus_points: list[dict] = []    # all agree
        self.divergence_points: list[dict] = []   # split opinions
        self.resolved: bool = False
        self.remaining_risk: str = "UNKNOWN"

    def build_from_votes(self, votes: list) -> None:
        """Analyze votes to find consensus and divergence points.

        A divergence point is a dimension where reviewers disagree
        (e.g., 2 APPROVED + 1 REJECTED on safety concern).
        """
        if not votes:
            return

        dimensions = ["verdict", "confidence", "reversibility_risk"]
        for dim in dimensions:
            values = []
            for v in votes:
                val = getattr(v, dim, None)
                if val:
                    values.append(str(val))

            unique = set(values)
            if len(unique) == 1:
                self.consensus_points.append({
                    "dimension": dim,
                    "agreed_value": list(unique)[0],
                    "strength": "FULL_CONSENSUS",
                })
            elif len(unique) <= len(votes) // 2 + 1:
                self.divergence_points.append({
                    "dimension": dim,
                    "values": list(unique),
                    "severity": "MODERATE",
                })
            else:
                self.divergence_points.append({
                    "dimension": dim,
                    "values": list(unique),
                    "severity": "HIGH",
                })

        # Gather flaw divergence
        all_flaws: dict[str, list[int]] = {}
        for i, v in enumerate(votes):
            for flaw in v.flaws:
                all_flaws.setdefault(flaw, []).append(i)
        for flaw, reviewers in all_flaws.items():
            if len(reviewers) >= 2:
                self.consensus_points.append({
                    "dimension": "flaw",
                    "agreed_value": flaw,
                    "strength": f"{len(reviewers)}/{len(votes)} reviewers",
                })
            else:
                self.divergence_points.append({
                    "dimension": "flaw",
                    "value": flaw,
                    "noted_by": reviewers,
                    "severity": "LOW",
                })

        # Assess remaining risk
        if not self.divergence_points:
            self.remaining_risk = "LOW"
            self.resolved = True
        elif sum(1 for d in self.divergence_points if d.get("severity") == "HIGH") == 0:
            self.remaining_risk = "MEDIUM"
        else:
            self.remaining_risk = "HIGH"

    def to_dict(self) -> dict:
        return {
            "consensus_points": self.consensus_points,
            "divergence_points": self.divergence_points,
            "resolved": self.resolved,
            "remaining_risk": self.remaining_risk,
        }


class StabilityDetector:
    """Detect when multi-agent consensus has converged (adaptive stopping).

    Inspired by MAD with Stability Detection (NeurIPS 2025):
    Uses a Beta-Binomial mixture model to track consensus dynamics.

    When the distribution of reviewer scores reaches stability
    (KS test p > 0.1 between consecutive rounds), early-stop the audit.

    Physical: like waiting for a system to reach equilibrium after perturbation.
    Cybernetic: like detecting steady-state in a feedback loop — no further
                information gain from additional rounds.
    Mathematical: KS test on score distributions — stable when p > 0.1.
    """

    def __init__(self, stability_threshold: float = 0.1):
        self.stability_threshold = stability_threshold
        self._score_history: list[list[float]] = []

    def record_scores(self, scores: list[float]) -> None:
        """Record a round of reviewer scores."""
        self._score_history.append(sorted(scores))

    def is_stable(self) -> bool:
        """Check if scores have stabilized across rounds."""
        if len(self._score_history) < 2:
            return False
        # KS test between last two score distributions
        ks_stat = self._two_sample_ks(
            self._score_history[-2],
            self._score_history[-1],
        )
        return ks_stat > self.stability_threshold

    @staticmethod
    def _two_sample_ks(a: list[float], b: list[float]) -> float:
        """Simplified two-sample Kolmogorov-Smirnov statistic.

        Returns p-value approximation. p > 0.1 means distributions
        are NOT significantly different → stable.
        """
        n1, n2 = len(a), len(b)
        if n1 == 0 or n2 == 0:
            return 0.0

        # Compute empirical CDFs
        all_vals = sorted(set(a + b))
        max_diff = 0.0
        for val in all_vals:
            cdf1 = sum(1 for x in a if x <= val) / n1
            cdf2 = sum(1 for x in b if x <= val) / n2
            max_diff = max(max_diff, abs(cdf1 - cdf2))

        # Approximate p-value (simplified — for stability detection,
        # direction matters more than exact value)
        # Using the asymptotic formula: p ≈ 2 * exp(-2 * (D * sqrt(n_eff))^2)
        n_eff = (n1 * n2) / (n1 + n2)
        z = max_diff * (n_eff ** 0.5)
        # Approximation: exp(-2*z^2) for large z
        import math
        p_val = 2.0 * math.exp(-2.0 * z * z) if z > 0 else 1.0
        return min(1.0, p_val)


class AdversarialAuditor:
    """Triangle adversarial audit structure: Prosecutor → Defender → Judge.

    Three-dimensional cross-validation:
      - 物理: 三体相互作用产生稳定轨道 — 两体无法收敛到真值
        (two-body opinion dynamics collapse to false consensus)
      - 系统: 三角反馈 ≡ 最简二阶控制系统 — 对抗确保信息完备性
        (adversarial structure prevents blind spots)
      - 数学: ≥2/3 裁决 ≡ 多数决降低 Type I/II 错误率
        (majority voting approximates Bayes factor decision rule)

    Protocol:
      Round 1: [Prosecutor] attacks the modification
               [Defender] defends the modification
      Round 2: [Judge] reviews arguments from both sides → final verdict
      Round 3: (optional) If Judge uncertain → [Expert Witness] provides domain knowledge

    Args:
        llm_client: LLM client for generating arguments and verdicts.
        meta_reviewer: Existing MetaReviewer for blind audit templates.
    """

    # System prompts for the three roles
    PROSECUTOR_PROMPT = """You are a strict PROSECUTOR auditing a proposed governance rule change.

Your job: find every possible flaw, risk, and failure mode in this modification.
Be adversarial — assume the modification is wrong until proven right.

Consider:
1. What could go wrong if this rule is applied?
2. Are there hidden assumptions that don't hold?
3. What edge cases does this rule break?
4. What secondary effects (downstream damage) could this cause?

Output a structured critique. Be specific and concrete — cite examples."""

    DEFENDER_PROMPT = """You are a DEFENDER arguing for a proposed governance rule change.

Your job: present the strongest case for why this modification is necessary and safe.
Address the prosecutor's concerns directly — don't dodge them.

Consider:
1. Why is the current rule inadequate? (cite the error pattern)
2. How does the new rule prevent the specific errors observed?
3. What safeguards does the modification include?
4. Why are the risks acceptable relative to the status quo?

Output a structured defense. Be specific — reference the 5-Whys analysis."""

    JUDGE_PROMPT = """You are a neutral JUDGE evaluating arguments about a governance rule change.

You have read:
- The PROSECUTOR's critique (all the reasons this could fail)
- The DEFENDER's argument (all the reasons this is necessary)

Your job: weigh both sides and render a final verdict.

Consider:
1. Which arguments are grounded in concrete evidence vs. speculation?
2. Does the modification address the actual root cause?
3. Are there risks that neither side identified?
4. What conditions would make this modification succeed vs. fail?

Output JSON:
{{"verdict": "APPROVED or REJECTED or CONDITIONAL_APPROVAL",
 "confidence": 0.0-1.0,
 "key_factors": ["factors that influenced the decision"],
 "conditions": ["conditions for CONDITIONAL_APPROVAL, empty otherwise"],
 "reasoning": "detailed reasoning"}}"""

    def __init__(self, llm_client=None, meta_reviewer: Optional[MetaReviewer] = None):
        self.llm_client = llm_client
        self.meta_reviewer = meta_reviewer

    def audit(
        self,
        gv_content_before: str,
        gv_content_after: str,
        modification_reason: str,
        root_cause_summary: str = "",
        five_whys_chain: list[str] | None = None,
        error_type: str = "",
        max_rounds: int = 3,
    ) -> BlindAuditResult:
        """Run the full adversarial audit.

        Returns a BlindAuditResult compatible with existing code,
        enriched with divergence tree and stability data.
        """
        result = BlindAuditResult()
        tree = DivergenceTree()
        stability = StabilityDetector()

        if not self.llm_client:
            result.passed = True
            result.summary = "No LLM client, adversarial audit passed by default"
            return result

        # ── Round 1: Prosecutor vs Defender ──
        prosecutor_critique = self._run_prosecutor(
            gv_content_before, gv_content_after, modification_reason,
            root_cause_summary, error_type,
        )
        defender_argument = self._run_defender(
            gv_content_before, gv_content_after, modification_reason,
            root_cause_summary, prosecutor_critique, five_whys_chain,
        )

        # ── Round 2: Judge deliberates ──
        judge_verdict = self._run_judge(
            gv_content_before, gv_content_after, modification_reason,
            prosecutor_critique, defender_argument,
        )

        # Parse judge verdict
        judge_vote = self._parse_judge_verdict(judge_verdict)
        result.votes.append(judge_vote)
        stability.record_scores([judge_vote.confidence])

        # ── Round 3 (optional): Expert Witness if judge uncertain ──
        if judge_vote.confidence < 0.6 and max_rounds >= 3:
            expert_opinion = self._run_expert_witness(
                gv_content_before, gv_content_after,
                prosecutor_critique, defender_argument, judge_verdict,
            )
            expert_vote = self._parse_judge_verdict(expert_opinion)
            expert_vote.template = "expert_witness"
            result.votes.append(expert_vote)
            stability.record_scores([judge_vote.confidence, expert_vote.confidence])

        # ── Also run existing MetaReviewer blind audit for triangulation ──
        if self.meta_reviewer and self.meta_reviewer.client:
            try:
                mr_result = self.meta_reviewer.audit(
                    gv_content_before=gv_content_before,
                    gv_content_after=gv_content_after,
                    reason=modification_reason,
                    impact=error_type,
                )
                # Merge meta-reviewer votes into adversarial result
                for v in mr_result.votes:
                    result.votes.append(v)
                result.templates_used = mr_result.templates_used
                result.template_hash = mr_result.template_hash
            except Exception:
                pass

        # ── Build divergence tree ──
        tree.build_from_votes(result.votes)

        # ── Compute final result ──
        result.approve_count = sum(1 for v in result.votes if v.is_approve())
        result.reject_count = sum(1 for v in result.votes if v.verdict == "REJECTED")
        result.needs_revision_count = sum(1 for v in result.votes if v.verdict == "NEEDS_REVISION")
        result.passed = result.approve_count >= max(2, len(result.votes) // 2 + 1)

        # Attach divergence tree data
        result.summary = (
            f"Adversarial audit {'PASSED' if result.passed else 'FAILED'}: "
            f"{result.approve_count} approve, {result.reject_count} reject, "
            f"{result.needs_revision_count} needs_revision. "
            f"Divergence risk: {tree.remaining_risk}. "
            f"Consensus on: {[c['dimension'] for c in tree.consensus_points]}. "
            f"Divergence on: {[d['dimension'] for d in tree.divergence_points]}."
        )

        # Store enriched data in result for downstream use
        result._divergence_tree = tree
        result._stability = stability
        result._prosecutor_critique = prosecutor_critique
        result._defender_argument = defender_argument

        return result

    def _run_prosecutor(self, before, after, reason, rca, error_type) -> str:
        prompt = (
            f"## Modification to Audit\n\n"
            f"**Error Type:** {error_type}\n"
            f"**Root Cause:** {rca[:500]}\n\n"
            f"**Rule Before:**\n{before or '(empty — new rule)'}\n\n"
            f"**Rule After:**\n{after[:1000]}\n\n"
            f"**Modification Reason:** {reason[:500]}\n\n"
            f"Build the strongest possible case AGAINST this modification. "
            f"Find every vulnerability, edge case, and potential failure mode."
        )
        return self._call_llm(self.PROSECUTOR_PROMPT, prompt)

    def _run_defender(self, before, after, reason, rca, prosecutor_critique, five_whys) -> str:
        whys_text = "\n".join(five_whys or [])
        prompt = (
            f"## The Modification\n\n"
            f"**Rule Before:**\n{before or '(empty — new rule)'}\n\n"
            f"**Rule After:**\n{after[:1000]}\n\n"
            f"**Reason:** {reason[:500]}\n\n"
            f"**Root Cause Analysis:**\n{rca[:500]}\n\n"
            f"**5-Whys:**\n{whys_text}\n\n"
            f"## Prosecutor's Critique:\n{prosecutor_critique[:1000]}\n\n"
            f"Defend this modification against the prosecutor's critique. "
            f"Address each concern directly. If a concern is valid, acknowledge it "
            f"and explain why the modification is still the right choice."
        )
        return self._call_llm(self.DEFENDER_PROMPT, prompt)

    def _run_judge(self, before, after, reason, prosecutor_critique, defender_argument) -> str:
        prompt = (
            f"## The Modification\n\n"
            f"**Rule Before:**\n{before or '(empty — new rule)'}\n\n"
            f"**Rule After:**\n{after[:800]}\n\n"
            f"**Reason:** {reason[:400]}\n\n"
            f"## PROSECUTOR's Argument (against):\n{prosecutor_critique[:1000]}\n\n"
            f"## DEFENDER's Argument (for):\n{defender_argument[:1000]}\n\n"
            f"Weigh both sides and render a final verdict. Output JSON."
        )
        return self._call_llm(self.JUDGE_PROMPT, prompt)

    def _run_expert_witness(self, before, after, prosecutor, defender, judge) -> str:
        prompt = (
            f"## Context\n\n"
            f"**Rule Before:**\n{before or '(empty)'}\n\n"
            f"**Rule After:**\n{after[:800]}\n\n"
            f"## The Debate:\n"
            f"PROSECUTOR: {prosecutor[:600]}\n\n"
            f"DEFENDER: {defender[:600]}\n\n"
            f"JUDGE (uncertain, confidence < 0.6): {judge[:400]}\n\n"
            f"As an EXPERT WITNESS with deep domain knowledge, provide an independent "
            f"assessment. What did BOTH sides miss? Are there domain-specific considerations "
            f"that change the analysis? Output JSON with verdict and confidence."
        )
        return self._call_llm(
            "You are an EXPERT WITNESS in governance and domain-specific knowledge. "
            "Provide independent, evidence-based assessment.",
            prompt,
        )

    def _call_llm(self, system: str, user: str) -> str:
        """Call LLM and return content string."""
        try:
            resp = self.llm_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                max_tokens=1024,
            )
            if isinstance(resp, dict):
                return resp.get("content", "")
            return getattr(resp, "content", "")
        except Exception:
            return "{}"

    @staticmethod
    def _parse_judge_verdict(raw: str) -> BlindAuditVote:
        """Parse judge/expert JSON verdict into BlindAuditVote."""
        vote = BlindAuditVote()
        vote.template = "adversarial_judge"
        vote.raw_response = raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
            data = json.loads(m.group(1).strip()) if m else {}

        verdict_str = data.get("verdict", "REJECTED").upper()
        if "APPROV" in verdict_str:
            vote.verdict = "APPROVED"
        elif "CONDITIONAL" in verdict_str:
            vote.verdict = "APPROVED"
            vote.flaws = data.get("conditions", [])
        else:
            vote.verdict = "REJECTED"

        vote.confidence = float(data.get("confidence", 0.5))
        vote.reasoning = data.get("reasoning", "")
        key_factors = data.get("key_factors", [])
        if isinstance(key_factors, list):
            vote.flaws.extend(key_factors)
        return vote
