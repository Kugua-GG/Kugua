"""
kugua — EV Log (external verification event log)
v0.3.0

Structured logging of checker scores per KB entry for downstream calibration.
"""
from __future__ import annotations
import json, time
from pathlib import Path
from datetime import datetime, timezone


class EVLogWriter:
    """Write structured external verification events.

    Usage:
        writer = EVLogWriter(Path("./artifacts"))
        writer.write(kb_id="loan_lpr", concept_id="rate_cap", checker_score=85, task_id="t1")
    """

    def __init__(self, artifacts_dir: Path):
        self.path = Path(artifacts_dir) / "ev_log.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kb_id: str, concept_id: str, checker_score: int,
              task_id: str = "", **extra):
        entry = {
            "kb_id": kb_id, "concept_id": concept_id,
            "checker_score": int(checker_score),
            "task_id": task_id, "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class EVLogReader:
    """Read and query EV log entries."""

    def __init__(self, artifacts_dir: Path):
        self.path = Path(artifacts_dir) / "ev_log.jsonl"

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def query(self, kb_id: str = "", concept_id: str = "", limit: int = 100) -> list[dict]:
        records = self.read_all()
        if kb_id:
            records = [r for r in records if r.get("kb_id") == kb_id]
        if concept_id:
            records = [r for r in records if r.get("concept_id") == concept_id]
        return records[-limit:]
