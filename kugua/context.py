"""
ContextManager — layered context with token budget and sliding-window compression v0.2.2

Academic grounding:
  - MemGPT (Packer et al., UC Berkeley 2023): virtual context management,
    memory pressure warnings, FIFO eviction with recursive summarization
  - "Lost in the Middle" (Liu et al., Stanford 2023): U-shaped attention —
    important info at start/end of context, compressed blocks in the middle
  - Generative Agents (Park et al., Stanford 2023): importance scoring for
    memory retention and pruning

Design:
  L0 (frozen, immutable)  — system prompt + tools schema, locked for session
  L1 (semi-stable, plan)  — intent anchor + task DAG + plan, replan-aware
  L2 (mutable, rolling)   — conversation history, importance-scored, auto-compressed

Pure Python stdlib — zero external dependencies.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class LayerType(Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════

@dataclass
class L2Entry:
    """A single entry in the L2 mutable conversation history.

    Fields:
        role:      "user" | "assistant" | "system" | "worker" | "checker"
        content:   Message text
        timestamp: ISO-format timestamp
        importance: 1-10 score (default 5). Higher = less likely to be pruned.
                   Inspired by Generative Agents (Park et al., 2023).
    """

    role: str = "user"
    content: str = ""
    timestamp: str = ""
    importance: int = 5

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        # Clamp importance
        self.importance = max(1, min(10, self.importance))


@dataclass
class L0Layer:
    """Immutable system-level context (system prompt + tools)."""

    content: str = ""
    tools_schema: str = ""
    frozen: bool = False


@dataclass
class L1Layer:
    """Semi-stable plan-level context (intent + task DAG + plan)."""

    intent_anchor: Dict[str, Any] = field(default_factory=dict)
    task_dag: List[Dict[str, Any]] = field(default_factory=list)
    plan: str = ""
    frozen: bool = False


@dataclass
class L2Layer:
    """Mutable conversation history with rolling-window compression."""

    entries: List[L2Entry] = field(default_factory=list)
    # Compressed history blocks (max 3), each is a summary string
    compressed_blocks: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# Token estimation helper (ported from ContextCompressor, stdlib only)
# ═══════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """Estimate token count for mixed Chinese/English text.

    ~1.3 Chinese chars per token, ~3.5 English chars per token.
    With 30% safety margin.
    """
    import re

    chinese = len(re.findall(r"[一-鿿]", text))
    other = len(text) - chinese
    return int((chinese / 1.3 + other / 3.5) * 1.3)


def _summarize_text(text: str, max_chars: int = 200) -> str:
    """Truncate text to max_chars with sentence-boundary awareness.

    Keeps the beginning and end of long text (U-shaped, "Lost in the Middle").
    """
    if len(text) <= max_chars:
        return text

    head_size = int(max_chars * 0.6)
    tail_size = max_chars - head_size - 3  # 3 for "..."

    head = text[:head_size]
    # Find last sentence boundary in head
    for sep in ["。", ".", "；", ";", "\n"]:
        last = head.rfind(sep)
        if last > head_size * 0.5:
            head = head[: last + 1]
            break

    tail = text[-tail_size:]
    # Find first sentence boundary in tail
    for sep in ["。", ".", "；", ";", "\n"]:
        first = tail.find(sep)
        if 0 < first < tail_size * 0.5:
            tail = tail[first + 1 :]
            break

    return f"{head}...{tail}"


# ═══════════════════════════════════════════════════════════════
# ContextManager
# ═══════════════════════════════════════════════════════════════

class ContextManager:
    """Layered context manager with token budgets and auto-compression.

    MemGPT-inspired three-tier context:
      L0 — Immutable system prompt + tools schema (token budget: unlimited)
      L1 — Semi-stable intent + task DAG + plan (token budget: ~2000)
      L2 — Mutable rolling conversation history (token budget: ~4000)

    Args:
        config:    Optional config object (for session_id, etc.)
        session_id: Unique session identifier.
        l1_budget: Max tokens for L1 layer (default 2000).
        l2_budget: Max tokens for L2 layer (default 4000).
        l2_max_entries: Max uncompressed L2 entries before compression triggers.
        l2_max_blocks: Max compressed history blocks to retain.
        entry_max_chars: Max characters per L2 entry before truncation.
    """

    def __init__(
        self,
        config: Any = None,
        session_id: str = "",
        l1_budget: int = 2000,
        l2_budget: int = 4000,
        l2_max_entries: int = 30,
        l2_max_blocks: int = 3,
        entry_max_chars: int = 500,
    ):
        self.config = config
        self.session_id = session_id
        self.l1_budget = l1_budget
        self.l2_budget = l2_budget
        self.l2_max_entries = l2_max_entries
        self.l2_max_blocks = l2_max_blocks
        self.entry_max_chars = entry_max_chars

        self.L0 = L0Layer()
        self.L1 = L1Layer()
        self.L2 = L2Layer()

        # Track warnings for MainLoop integration
        self._pressure_warning: bool = False
        self._compression_count: int = 0

    # ═════════════════════════════════════════════════════════
    # Freeze / Unfreeze lifecycle
    # ═════════════════════════════════════════════════════════

    def freeze_L0(self, system_prompt: str, tools_schema: str = "") -> None:
        """Lock L0 for the entire session (called once at P0)."""
        self.L0.content = system_prompt
        self.L0.tools_schema = tools_schema
        self.L0.frozen = True

    def freeze_L1(
        self,
        intent_anchor: Dict[str, Any],
        task_dag: List[Dict[str, Any]],
        plan: str = "",
    ) -> None:
        """Lock L1 for the current execution cycle (called at P1)."""
        self.L1.intent_anchor = intent_anchor
        self.L1.task_dag = task_dag
        self.L1.plan = plan
        self.L1.frozen = True

    def unfreeze_L1(self) -> None:
        """Unlock L1 for replanning (called when P3 fails → back to P1)."""
        self.L1.frozen = False

    def is_L1_frozen(self) -> bool:
        return self.L1.frozen

    # ═════════════════════════════════════════════════════════
    # L2 mutation
    # ═════════════════════════════════════════════════════════

    def append(
        self,
        role: str,
        content: str,
        importance: int = 5,
    ) -> None:
        """Append an entry to L2 history.

        Args:
            role:       "user" | "assistant" | "system" | "worker" | "checker"
            content:    Message text.
            importance: 1-10 retention score (default 5). Set higher for
                        critical findings, review failures, or double-loop events.
        """
        # Truncate long entries (LLMLingua-inspired: keep head + tail)
        if len(content) > self.entry_max_chars:
            content = _summarize_text(content, self.entry_max_chars)

        entry = L2Entry(role=role, content=content, importance=importance)
        self.L2.entries.append(entry)

        # Trigger compression if over limit (MemGPT-inspired memory pressure)
        if len(self.L2.entries) > self.l2_max_entries:
            self.compress_if_needed()

    def append_worker(self, content: str, importance: int = 5) -> None:
        """Shorthand for append('worker', content)."""
        self.append("worker", content, importance)

    def append_checker(self, content: str, importance: int = 8) -> None:
        """Shorthand for append('checker', content). Checker findings default to high importance."""
        self.append("checker", content, importance)

    def clear_L2(self) -> None:
        """Clear all L2 entries and compressed blocks (e.g., new session)."""
        self.L2.entries.clear()
        self.L2.compressed_blocks.clear()
        self._compression_count = 0
        self._pressure_warning = False

    # ═════════════════════════════════════════════════════════
    # Compression (MemGPT-inspired)
    # ═════════════════════════════════════════════════════════

    def compress_if_needed(self) -> int:
        """Check token budgets and compress L2 if needed.

        MemGPT-inspired: when context pressure is high, evict old entries
        and summarize them into compressed blocks.

        Strategy:
          1. If L2 entries exceed l2_max_entries → compress oldest 50%.
          2. Oldest entries are summarized into a compressed block.
          3. Max l2_max_blocks compressed blocks retained.
          4. Within remaining entries: prune low-importance ones first.

        Returns:
            Number of entries removed/compressed.
        """
        removed = 0
        entries = self.L2.entries

        # Step 1: Compress oldest entries if over limit
        overflow = len(entries) - self.l2_max_entries
        if overflow > 0:
            # Take oldest overflow entries, summarize them
            to_compress = entries[:overflow]
            entries[:] = entries[overflow:]
            removed += overflow

            # Generate summary of compressed entries
            summary = self._summarize_entries(to_compress)
            self.L2.compressed_blocks.append(summary)

            # Cap compressed blocks
            if len(self.L2.compressed_blocks) > self.l2_max_blocks:
                # Merge oldest two blocks
                merged = self._merge_blocks(
                    self.L2.compressed_blocks[0],
                    self.L2.compressed_blocks[1],
                )
                self.L2.compressed_blocks = [merged] + self.L2.compressed_blocks[2:]

            self._compression_count += 1

        # Step 2: If still over budget after compression, prune low-importance
        while self._l2_token_estimate() > self.l2_budget and len(entries) > 3:
            # Find lowest importance entry
            min_idx = min(
                range(len(entries)),
                key=lambda i: entries[i].importance,
            )
            entries.pop(min_idx)
            removed += 1

        # Step 3: Set pressure warning (MemGPT-inspired: 70% threshold)
        usage_ratio = self._l2_token_estimate() / max(self.l2_budget, 1)
        self._pressure_warning = usage_ratio > 0.7

        return removed

    @property
    def pressure_warning(self) -> bool:
        """True when L2 is over 70% of its token budget (MemGPT-inspired)."""
        return self._pressure_warning

    @property
    def compression_count(self) -> int:
        """Number of times compression has run this session."""
        return self._compression_count

    # ═════════════════════════════════════════════════════════
    # Assemble (U-shaped order — "Lost in the Middle"-inspired)
    # ═════════════════════════════════════════════════════════

    def assemble(self, current_message: str = "") -> str:
        """Assemble the full context for LLM injection.

        Order optimized for the U-shaped attention curve:
          1. L0 (system prompt)           ← beginning (primacy)
          2. L1 (intent + plan)           ← beginning (primacy)
          3. L2 compressed blocks          ← middle (low attention zone)
          4. L2 recent entries (last 10)  ← end (recency)
          5. current_message              ← end (recency)

        Total estimate is included as a [CTX: N tokens] header.
        """
        parts = []

        # 1. L0: immutable system prompt (always first)
        if self.L0.content:
            parts.append(f"[L0:immutable]\n{self.L0.content}")

        # 2. L1: semi-stable plan (near beginning — primacy)
        if self.L1.intent_anchor or self.L1.plan:
            l1_text = ""
            if self.L1.intent_anchor:
                l1_text += json.dumps(
                    self.L1.intent_anchor, ensure_ascii=False, indent=2
                )
            if self.L1.plan:
                l1_text += f"\n\nPlan: {self.L1.plan}"
            if self.L1.task_dag:
                l1_text += f"\n\nTasks: {len(self.L1.task_dag)} items"
            # Truncate L1 if over budget
            if _estimate_tokens(l1_text) > self.l1_budget:
                l1_text = _summarize_text(l1_text, self.l1_budget * 3)
            parts.append(f"[L1:semi-stable]\n{l1_text}")

        # 3. L2 compressed blocks (middle — low attention zone)
        if self.L2.compressed_blocks:
            compressed_text = "\n".join(
                f"[compressed #{i+1}] {block}"
                for i, block in enumerate(self.L2.compressed_blocks)
            )
            parts.append(f"[L2:compressed]\n{compressed_text}")

        # 4. L2 recent entries (near end — recency)
        recent = self.L2.entries[-10:] if self.L2.entries else []
        if recent:
            log = "\n".join(
                f"[{e.role}] {e.content}" for e in recent
            )
            parts.append(f"[L2:recent]\n{log}")

        # 5. Current message (last — recency)
        if current_message:
            parts.append(current_message)

        # Token estimate header
        full = "\n\n---\n\n".join(parts)
        estimated = _estimate_tokens(full)
        header = f"[CTX: ~{estimated} tokens"
        if self._pressure_warning:
            header += " | L2 PRESSURE WARNING"
        header += "]\n\n"

        return header + full

    # ═════════════════════════════════════════════════════════
    # Budget queries
    # ═════════════════════════════════════════════════════════

    def total_tokens(self) -> int:
        """Estimate total tokens across all layers."""
        return _estimate_tokens(self.assemble(""))

    def l2_tokens(self) -> int:
        """Estimate L2 token usage."""
        return self._l2_token_estimate()

    def _l2_token_estimate(self) -> int:
        """Estimate tokens for L2 layer only."""
        total = 0
        for e in self.L2.entries:
            total += _estimate_tokens(e.content)
        for block in self.L2.compressed_blocks:
            total += _estimate_tokens(block)
        return total

    # ═════════════════════════════════════════════════════════
    # Internal helpers
    # ═════════════════════════════════════════════════════════

    @staticmethod
    def _summarize_entries(entries: List[L2Entry]) -> str:
        """Generate a concise summary of compressed L2 entries.

        Extracts key events: errors, reviews, double-loop actions, phase changes.
        """
        if not entries:
            return ""

        n = len(entries)
        roles = set(e.role for e in entries)
        events = []

        for e in entries:
            c = e.content
            if "fail" in c.lower() or "error" in c.lower():
                events.append("review-fail")
            elif "pass" in c.lower():
                events.append("review-pass")
            elif "double" in c.lower() or "loop" in c.lower():
                events.append("double-loop")
            elif "phase" in c.lower() or "P0" in c or "P1" in c:
                events.append("phase-change")

        unique_events = list(dict.fromkeys(events))  # preserve order, deduplicate
        event_str = ", ".join(unique_events[:5]) if unique_events else "general"

        return (
            f"[{n} entries compressed] "
            f"Roles: {', '.join(sorted(roles))}. "
            f"Events: {event_str}."
        )

    @staticmethod
    def _merge_blocks(block_a: str, block_b: str) -> str:
        """Merge two compressed blocks into one (when block limit reached)."""
        return f"[merged] {block_a} | {block_b}"
