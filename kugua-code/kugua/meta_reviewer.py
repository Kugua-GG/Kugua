"""
kugua — MetaReviewer (blind audit protocol)
v0.2.1

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
