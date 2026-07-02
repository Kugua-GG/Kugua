"""ContextManager — minimal stub for package imports."""
import json
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, List

class LayerType(Enum):
    L0="L0"; L1="L1"; L2="L2"

@dataclass
class L2Entry:
    role: str = "user"; content: str = ""; timestamp: str = ""

@dataclass
class L0Layer:
    content: str = ""; tools_schema: str = ""; frozen: bool = False

@dataclass
class L1Layer:
    intent_anchor: Dict[str, Any] = field(default_factory=dict)
    task_dag: List[Dict[str, Any]] = field(default_factory=list)
    plan: str = ""; frozen: bool = False

@dataclass
class L2Layer:
    entries: List[L2Entry] = field(default_factory=list)

class ContextManager:
    def __init__(self, config: Any = None, session_id: str = ""):
        self.config = config; self.session_id = session_id
        self.L0 = L0Layer(); self.L1 = L1Layer(); self.L2 = L2Layer()

    def freeze_L0(self, system_prompt: str, tools_schema: str = "") -> None:
        self.L0.content = system_prompt; self.L0.tools_schema = tools_schema; self.L0.frozen = True

    def freeze_L1(self, intent_anchor: Dict, task_dag: List[Dict], plan: str = "") -> None:
        self.L1.intent_anchor = intent_anchor; self.L1.task_dag = task_dag; self.L1.plan = plan; self.L1.frozen = True

    def append(self, role: str, content: str) -> None:
        self.L2.entries.append(L2Entry(role=role, content=content, timestamp=datetime.now(timezone.utc).isoformat()))

    def assemble(self, current_message: str = "") -> str:
        parts = []
        if self.L0.content: parts.append("[L0:immutable]\n" + self.L0.content)
        if self.L1.intent_anchor: parts.append("[L1:semi-stable]\n" + json.dumps(self.L1.intent_anchor, ensure_ascii=False))
        if self.L2.entries:
            log = "\n".join(f"[{e.role}] {e.content}" for e in self.L2.entries)
            parts.append("[L2:mutable-log]\n" + log)
        if current_message: parts.append(current_message)
        return "\n\n---\n\n".join(parts)
