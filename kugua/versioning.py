"""
kugua.versioning — Git-like Knowledge Version Control v0.3.1

Content-addressed version DAG for knowledge base entries.
Every DoubleLoop commit creates an immutable KnowledgeCommit with:
  - SHA-256 content hash (Merkle-DAG style)
  - Parent version hash(es)
  - Semantic diff (before/after rule content)
  - 5-Whys RCA chain
  - Audit verification data
  - Rollback target

Enables: time-travel, diff, blame, rollback to any historical version.

Reference: Dolt (2024) — Merkle-DAG versioned database;
           neleus-db (2024) — content-addressed agent state;
           Git internals — DAG-based commit history.

Pure Python stdlib — no external dependencies.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# KnowledgeCommit — the atomic unit of versioned knowledge
# ═══════════════════════════════════════════════════════════════

@dataclass
class KnowledgeCommit:
    """A single versioned change to a governance variable.

    Content-addressed: commit_hash = SHA-256(parent + gv_id + diff + reason).

    Like a Git commit but for knowledge rules — immutable, traceable, rollback-able.
    """

    gv_id: str                          # which rule was modified
    commit_hash: str = ""               # SHA-256 content hash
    parent_hash: str = ""               # previous version hash (empty = genesis)
    merge_parents: List[str] = field(default_factory=list)  # for merge commits
    diff_before: str = ""               # rule content before modification
    diff_after: str = ""                # rule content after modification
    reason: str = ""                    # modification reason (from RCA)
    five_whys: List[str] = field(default_factory=list)  # RCA chain
    audit_result: Dict[str, Any] = field(default_factory=dict)  # verification data
    author: str = "kugua-double-loop"
    timestamp: str = ""
    message: str = ""                   # human-readable summary
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        if not self.commit_hash:
            self.commit_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Content-addressed hash: SHA-256 of key fields.

        Like Git's object hash — same content always produces same hash.
        Includes parent_hash to form a cryptographic chain of trust.
        """
        payload = json.dumps({
            "gv_id": self.gv_id,
            "parent_hash": self.parent_hash,
            "diff_before": self.diff_before,
            "diff_after": self.diff_after,
            "reason": self.reason,
            "five_whys": self.five_whys,
            "timestamp": self.timestamp,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def compute_diff(self) -> Dict[str, Any]:
        """Compute a simple line-level diff between before and after."""
        before_lines = self.diff_before.split("\n") if self.diff_before else []
        after_lines = self.diff_after.split("\n") if self.diff_after else []

        # Simple LCS-like diff (line-level)
        added = [l for l in after_lines if l not in before_lines]
        removed = [l for l in before_lines if l not in after_lines]
        unchanged = [l for l in before_lines if l in after_lines]

        return {
            "added_lines": len(added),
            "removed_lines": len(removed),
            "unchanged_lines": len(unchanged),
            "added": added,
            "removed": removed,
            "change_ratio": len(added + removed) / max(len(before_lines + after_lines), 1),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commit_hash": self.commit_hash,
            "gv_id": self.gv_id,
            "parent_hash": self.parent_hash,
            "merge_parents": self.merge_parents,
            "diff_before": self.diff_before,
            "diff_after": self.diff_after,
            "reason": self.reason,
            "five_whys": self.five_whys,
            "audit_result": self.audit_result,
            "author": self.author,
            "timestamp": self.timestamp,
            "message": self.message,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnowledgeCommit":
        return cls(
            gv_id=d.get("gv_id", ""),
            commit_hash=d.get("commit_hash", ""),
            parent_hash=d.get("parent_hash", ""),
            merge_parents=d.get("merge_parents", []),
            diff_before=d.get("diff_before", ""),
            diff_after=d.get("diff_after", ""),
            reason=d.get("reason", ""),
            five_whys=d.get("five_whys", []),
            audit_result=d.get("audit_result", {}),
            author=d.get("author", "kugua-double-loop"),
            timestamp=d.get("timestamp", ""),
            message=d.get("message", ""),
            tags=d.get("tags", []),
        )


# ═══════════════════════════════════════════════════════════════
# VersionGraph — DAG of KnowledgeCommits
# ═══════════════════════════════════════════════════════════════

class VersionGraph:
    """DAG-based version history for knowledge base entries.

    Each gv_id can have its own version chain. The graph structure allows:
      - Linear history: commit → commit → commit
      - Branching: two commits sharing the same parent
      - Merging: a commit with two parent hashes (merge_parents)

    Like `git log --oneline` but for governance rules.
    """

    def __init__(self, artifacts_dir: Optional[Path] = None):
        self._commits: Dict[str, KnowledgeCommit] = {}  # hash → commit
        self._heads: Dict[str, str] = {}                 # gv_id → latest commit_hash
        self._chains: Dict[str, List[str]] = {}          # gv_id → [hash1, hash2, ...]
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self._state_file = (
            self.artifacts_dir / "version_graph.json"
            if self.artifacts_dir else None
        )
        self._load_state()

    # ── persistence ──────────────────────────────────────────

    def _load_state(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for cd in data.get("commits", []):
                    commit = KnowledgeCommit.from_dict(cd)
                    self._commits[commit.commit_hash] = commit
                self._heads = data.get("heads", {})
                self._chains = data.get("chains", {})
            except (json.JSONDecodeError, IOError):
                pass

    def _save_state(self) -> None:
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "commits": [c.to_dict() for c in self._commits.values()],
                    "heads": self._heads,
                    "chains": self._chains,
                }, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── commit ───────────────────────────────────────────────

    def commit(self, commit: KnowledgeCommit) -> str:
        """Record a new version. Returns the commit hash.

        Automatically sets parent_hash from the current HEAD for this gv_id.
        Like `git commit` — creates a new node in the DAG pointing to parent.
        """
        # Set parent from current HEAD if not explicitly set
        if not commit.parent_hash:
            commit.parent_hash = self._heads.get(commit.gv_id, "")

        # Recompute hash with parent set
        commit.commit_hash = commit._compute_hash()

        # Store
        self._commits[commit.commit_hash] = commit
        self._heads[commit.gv_id] = commit.commit_hash

        # Update chain
        if commit.gv_id not in self._chains:
            self._chains[commit.gv_id] = []
        self._chains[commit.gv_id].append(commit.commit_hash)

        self._save_state()
        return commit.commit_hash

    # ── queries ──────────────────────────────────────────────

    def get_head(self, gv_id: str) -> Optional[KnowledgeCommit]:
        """Get the latest commit for a governance variable (HEAD)."""
        head_hash = self._heads.get(gv_id)
        if head_hash:
            return self._commits.get(head_hash)
        return None

    def get_commit(self, commit_hash: str) -> Optional[KnowledgeCommit]:
        """Get a specific commit by hash."""
        return self._commits.get(commit_hash)

    def log(self, gv_id: str, max_count: int = 20) -> List[KnowledgeCommit]:
        """Show version history for a governance variable.

        Like `git log` — returns commits in reverse chronological order.
        """
        chain = self._chains.get(gv_id, [])
        commits = []
        # Walk backward from the end
        for h in reversed(chain[-max_count:]):
            c = self._commits.get(h)
            if c:
                commits.append(c)
        return commits

    def diff(self, hash_a: str, hash_b: str) -> Dict[str, Any]:
        """Compute semantic diff between two commits.

        Like `git diff hash_a hash_b` but for knowledge content.
        """
        commit_a = self._commits.get(hash_a)
        commit_b = self._commits.get(hash_b)
        if not commit_a or not commit_b:
            return {"error": "commit not found"}

        # Compute the effective content at each commit
        content_a = commit_a.diff_after or commit_a.diff_before
        content_b = commit_b.diff_after or commit_b.diff_before

        # Line-level diff
        lines_a = set(content_a.split("\n"))
        lines_b = set(content_b.split("\n"))

        return {
            "commit_a": hash_a,
            "commit_b": hash_b,
            "added": list(lines_b - lines_a),
            "removed": list(lines_a - lines_b),
            "unchanged": list(lines_a & lines_b),
            "is_noop": lines_a == lines_b,
        }

    # ── rollback ─────────────────────────────────────────────

    def rollback(self, gv_id: str, target_hash: str) -> Optional[KnowledgeCommit]:
        """Roll back a governance variable to a specific historical version.

        Creates a NEW commit that reverts to the target state.
        Like `git revert` — doesn't erase history, adds a new commit.
        """
        target = self._commits.get(target_hash)
        if not target or target.gv_id != gv_id:
            return None

        current = self.get_head(gv_id)
        current_content = current.diff_after if current else ""

        rollback_commit = KnowledgeCommit(
            gv_id=gv_id,
            parent_hash=self._heads.get(gv_id, ""),
            diff_before=current_content,
            diff_after=target.diff_after or target.diff_before,
            reason=f"ROLLBACK to {target_hash[:8]}: {target.message or 'manual rollback'}",
            message=f"Rollback to version {target_hash[:8]}",
            tags=["rollback"],
        )

        self.commit(rollback_commit)
        return rollback_commit

    def get_state_at(self, gv_id: str, commit_hash: str) -> Optional[str]:
        """Reconstruct the rule content at any historical commit.

        Walks the DAG backward from the given commit to find the effective state.
        """
        commit = self._commits.get(commit_hash)
        if not commit or commit.gv_id != gv_id:
            return None
        return commit.diff_after or commit.diff_before

    # ── graph queries ────────────────────────────────────────

    def get_ancestors(self, commit_hash: str, max_depth: int = 50) -> List[str]:
        """Get all ancestor hashes by walking parent pointers.

        Like `git rev-list` — BFS up the DAG.
        """
        ancestors = []
        visited = set()
        queue = [commit_hash]
        depth = 0
        while queue and depth < max_depth:
            h = queue.pop(0)
            if h in visited:
                continue
            visited.add(h)
            c = self._commits.get(h)
            if c:
                ancestors.append(h)
                if c.parent_hash and c.parent_hash not in visited:
                    queue.append(c.parent_hash)
                for mp in c.merge_parents:
                    if mp not in visited:
                        queue.append(mp)
            depth += 1
        return ancestors

    def blame(self, gv_id: str) -> List[Dict[str, Any]]:
        """Show who last modified each piece of a rule.

        Like `git blame` — traces each change to its source commit.
        """
        chain = self._chains.get(gv_id, [])
        blame_lines = []
        for h in chain:
            c = self._commits.get(h)
            if c:
                blame_lines.append({
                    "commit_hash": c.commit_hash[:8],
                    "timestamp": c.timestamp,
                    "reason": c.reason[:100],
                    "change_size": len(c.diff_after) - len(c.diff_before),
                })
        return blame_lines

    @property
    def total_commits(self) -> int:
        return len(self._commits)

    @property
    def tracked_variables(self) -> List[str]:
        return list(self._heads.keys())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_commits": self.total_commits,
            "tracked_variables": self.tracked_variables,
            "heads": dict(self._heads),
            "recent_commits": [
                c.to_dict() for c in sorted(
                    self._commits.values(),
                    key=lambda x: x.timestamp,
                    reverse=True,
                )[:10]
            ],
        }
