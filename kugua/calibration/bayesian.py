"""
kugua — BayesianCalibrator
v0.3.0

Posterior calibration of confidence scores using external evidence (EV log).
Maps checker scores to likelihoods and performs Bayesian updates.
"""
from __future__ import annotations

import json, math
from pathlib import Path
from typing import Optional


def calc_likelihood(checker_score: float) -> float:
    """Map checker score (0-100) to likelihood P(evidence|hypothesis)."""
    s = float(checker_score)
    if s >= 90:
        return 0.95
    elif s >= 60:
        return 0.6
    else:
        return 0.1


def bayesian_update(prior: float, likelihoods: list[float]) -> float:
    """Bayesian update: prior * product(likelihoods) / normalization."""
    p = float(prior)
    for L in likelihoods:
        p = (p * L) / (p * L + (1 - p) * (1 - L))
    return max(0.01, min(0.99, p))


def group_ev_by_concept(ev_records: list[dict]) -> dict[str, list[dict]]:
    """Group EV log records by concept_id."""
    groups = {}
    for r in ev_records:
        cid = r.get("concept_id", "unknown")
        groups.setdefault(cid, []).append(r)
    return groups


class BayesianCalibrator:
    """Calibrate KB entry confidence using external evidence.

    Reads EV logs, computes likelihoods from checker scores,
    and returns calibration digests.

    Usage:
        cal = BayesianCalibrator(artifacts_dir=Path("./artifacts"))
        digest = cal.calibrate(days=7)
    """

    def __init__(self, config=None, artifacts_dir: Optional[Path] = None):
        self.config = config
        self.artifacts_dir = artifacts_dir or (
            config.artifacts_dir if config and hasattr(config, 'artifacts_dir') else Path("./artifacts")
        )
        self._ev_log_path = self.artifacts_dir / "ev_log.jsonl"

    def calibrate(self, days: int = 7) -> dict:
        """Run calibration over recent EV log entries.

        Returns:
            dict with: updates (list of suggested confidence adjustments),
                      digest (summary), total_entries
        """
        records = self._read_ev_log(days)
        if not records:
            return {"updates": [], "digest": "No EV log data", "total_entries": 0}

        groups = group_ev_by_concept(records)
        updates = []
        for concept_id, entries in groups.items():
            scores = [e.get("checker_score", 50) for e in entries]
            likes = [calc_likelihood(s) for s in scores]
            posterior = bayesian_update(0.5, likes)
            updates.append({
                "concept_id": concept_id,
                "prior": 0.5,
                "posterior": round(posterior, 4),
                "sample_count": len(entries),
                "avg_checker_score": round(sum(scores) / len(scores), 1),
            })

        return {
            "updates": sorted(updates, key=lambda u: -u["sample_count"]),
            "digest": f"Calibrated {len(groups)} concepts from {len(records)} EV records",
            "total_entries": len(records),
        }

    def _read_ev_log(self, days: int) -> list[dict]:
        if not self._ev_log_path.exists():
            return []
        records = []
        cutoff = None
        if days > 0:
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with open(self._ev_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if cutoff:
                        ts = r.get("timestamp", "")
                        if ts:
                            try:
                                dt = datetime.fromisoformat(ts)
                                if dt < cutoff:
                                    continue
                            except Exception:
                                pass
                    records.append(r)
                except json.JSONDecodeError:
                    pass
        return records
