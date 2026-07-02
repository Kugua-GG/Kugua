"""
kugua.causal_graph — Causal Graph with Root Cause Tracing v0.3.1

Extends GraphKB with causal semantics (Pearl's SCM framework):
  - Causal edges: CAUSES, PREVENTS, CONFOUNDS, MEDIATES, MODIFIES
  - Backward root-cause tracing: given a symptom, find the deepest cause
  - Causal path finding with mechanism annotations
  - Confounder detection (nodes with multiple incoming causal edges)
  - Intervention effect estimation (do-calculus lite)

Reference: Pearl (2009) — Causality: Models, Reasoning, and Inference;
           Pearl's three-tier hierarchy: association → intervention → counterfactual;
           Bilgel (2024) — The Role of Pearl's Causal Framework in Empirical Research.

Pure Python stdlib — extends kugua.graph.GraphKB.
"""

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from kugua.graph import GraphKB, Node, Edge
except ImportError:
    GraphKB = None  # type: ignore
    Node = None     # type: ignore
    Edge = None     # type: ignore


# ═══════════════════════════════════════════════════════════════
# Causal relation types
# ═══════════════════════════════════════════════════════════════

CAUSAL_RELATIONS = {
    "CAUSES": {
        "direction": "forward",
        "description": "A directly increases the probability of B",
        "reversible": True,
    },
    "PREVENTS": {
        "direction": "forward",
        "description": "A directly decreases the probability of B",
        "reversible": True,
    },
    "CONFOUNDS": {
        "direction": "bidirectional",
        "description": "A influences both B and C, creating spurious correlation",
        "reversible": False,
    },
    "MEDIATES": {
        "direction": "forward",
        "description": "A's effect on C passes through B",
        "reversible": True,
    },
    "MODIFIES": {
        "direction": "forward",
        "description": "A changes the strength/direction of B's effect on C",
        "reversible": False,
    },
    "CORRELATES_WITH": {
        "direction": "bidirectional",
        "description": "A and B co-occur but causality is unverified",
        "reversible": False,
    },
}


# ═══════════════════════════════════════════════════════════════
# CausalNode
# ═══════════════════════════════════════════════════════════════

@dataclass
class CausalNode:
    """A node in the causal graph with Pearl-style intervention tracking.

    Fields:
        node_id: Unique identifier (e.g., gv_id, error_type, or concept key).
        node_type: Causal role — ROOT_CAUSE, INTERMEDIATE, SYMPTOM, CONFOUNDER, EXOGENOUS.
        description: Human-readable description of what this node represents.
        intervention_status: Whether this node has been intervened on (do-operator).
        base_rate: Baseline probability/rate of this node occurring.
    """

    node_id: str
    node_type: str = "INTERMEDIATE"  # ROOT_CAUSE | INTERMEDIATE | SYMPTOM | CONFOUNDER | EXOGENOUS
    description: str = ""
    intervention_status: str = "OBSERVATIONAL"  # OBSERVATIONAL | INTERVENED | COUNTERFACTUAL
    base_rate: float = 0.0       # P(node) — baseline probability
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "description": self.description,
            "intervention_status": self.intervention_status,
            "base_rate": self.base_rate,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CausalNode":
        return cls(
            node_id=d.get("node_id", ""),
            node_type=d.get("node_type", "INTERMEDIATE"),
            description=d.get("description", ""),
            intervention_status=d.get("intervention_status", "OBSERVATIONAL"),
            base_rate=d.get("base_rate", 0.0),
            properties=d.get("properties", {}),
        )


# ═══════════════════════════════════════════════════════════════
# CausalEdge
# ═══════════════════════════════════════════════════════════════

@dataclass
class CausalEdge:
    """A causal link between two nodes.

    Fields:
        source_id: The cause node.
        target_id: The effect node.
        relation: One of CAUSAL_RELATIONS keys.
        mechanism: Human-readable explanation of HOW the cause produces the effect.
        strength: Estimated causal strength [0, 1].
        confidence: Confidence in the causal claim [0, 1].
        evidence: List of context IDs that support this causal link.
    """

    source_id: str
    target_id: str
    relation: str = "CAUSES"
    mechanism: str = ""
    strength: float = 0.5
    confidence: float = 0.5
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "mechanism": self.mechanism,
            "strength": self.strength,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CausalEdge":
        return cls(
            source_id=d.get("source_id", ""),
            target_id=d.get("target_id", ""),
            relation=d.get("relation", "CAUSES"),
            mechanism=d.get("mechanism", ""),
            strength=d.get("strength", 0.5),
            confidence=d.get("confidence", 0.5),
            evidence=d.get("evidence", []),
        )


# ═══════════════════════════════════════════════════════════════
# CausalGraph
# ═══════════════════════════════════════════════════════════════

class CausalGraph:
    """Causal graph with root cause tracing and Pearl-style intervention reasoning.

    Key operations:
      - add_causal_link(cause, effect, mechanism) — define causal relationships
      - trace_root_cause(symptom_id) — backward trace to deepest cause
      - find_causal_paths(source, target) — all causal paths between two nodes
      - detect_confounders() — find nodes with multiple incoming causal edges
      - compute_intervention_effect(node_id) — estimate P(effect | do(cause))
    """

    def __init__(self, artifacts_dir: Optional[Path] = None):
        self._nodes: Dict[str, CausalNode] = {}
        self._edges: List[CausalEdge] = []
        # Adjacency for fast traversal
        self._incoming: Dict[str, List[CausalEdge]] = defaultdict(list)   # target → edges
        self._outgoing: Dict[str, List[CausalEdge]] = defaultdict(list)   # source → edges
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self._state_file = (
            self.artifacts_dir / "causal_graph.json"
            if self.artifacts_dir else None
        )
        self._load_state()

    # ── persistence ──────────────────────────────────────────

    def _load_state(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for nd in data.get("nodes", []):
                    node = CausalNode.from_dict(nd)
                    self._nodes[node.node_id] = node
                for ed in data.get("edges", []):
                    edge = CausalEdge.from_dict(ed)
                    self._edges.append(edge)
                    self._outgoing[edge.source_id].append(edge)
                    self._incoming[edge.target_id].append(edge)
            except (json.JSONDecodeError, IOError):
                pass

    def _save_state(self) -> None:
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "nodes": [n.to_dict() for n in self._nodes.values()],
                    "edges": [e.to_dict() for e in self._edges],
                }, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # ── node operations ──────────────────────────────────────

    def add_node(
        self,
        node_id: str,
        node_type: str = "INTERMEDIATE",
        description: str = "",
        base_rate: float = 0.0,
    ) -> CausalNode:
        """Add or update a causal node."""
        node = CausalNode(
            node_id=node_id,
            node_type=node_type,
            description=description,
            base_rate=base_rate,
        )
        self._nodes[node_id] = node
        self._save_state()
        return node

    def get_node(self, node_id: str) -> Optional[CausalNode]:
        return self._nodes.get(node_id)

    def set_node_type(self, node_id: str, node_type: str) -> None:
        """Update a node's causal role (ROOT_CAUSE, SYMPTOM, etc.)."""
        if node_id in self._nodes:
            self._nodes[node_id].node_type = node_type
            self._save_state()

    # ── edge operations ──────────────────────────────────────

    def add_causal_link(
        self,
        cause_id: str,
        effect_id: str,
        relation: str = "CAUSES",
        mechanism: str = "",
        strength: float = 0.5,
        confidence: float = 0.5,
        evidence: Optional[List[str]] = None,
    ) -> CausalEdge:
        """Add a causal link between two nodes.

        Args:
            cause_id: The cause node.
            effect_id: The effect node.
            relation: One of CAUSES, PREVENTS, CONFOUNDS, MEDIATES, MODIFIES, CORRELATES_WITH.
            mechanism: How does the cause produce the effect? (Natural language)
            strength: Estimated causal strength [0, 1].
            confidence: Confidence in the causal claim [0, 1].
            evidence: List of context IDs supporting this claim.
        """
        # Auto-create nodes if they don't exist
        if cause_id not in self._nodes:
            self.add_node(cause_id, node_type="INTERMEDIATE")
        if effect_id not in self._nodes:
            self.add_node(effect_id, node_type="INTERMEDIATE")

        edge = CausalEdge(
            source_id=cause_id,
            target_id=effect_id,
            relation=relation,
            mechanism=mechanism,
            strength=strength,
            confidence=confidence,
            evidence=evidence or [],
        )
        self._edges.append(edge)
        self._outgoing[cause_id].append(edge)
        self._incoming[effect_id].append(edge)
        self._save_state()
        return edge

    # ── root cause tracing ───────────────────────────────────

    def trace_root_cause(
        self,
        symptom_id: str,
        max_depth: int = 10,
        min_strength: float = 0.0,
    ) -> Dict[str, Any]:
        """Backward trace from a symptom to find the deepest root cause(s).

        BFS backward along causal edges (effect → cause), accumulating
        causal chains and stopping at nodes with no incoming causal edges
        (which are ROOT_CAUSE candidates).

        Pearl's insight: when you see symptom C, trace backward along
        the causal graph to find the node(s) whose intervention would
        most effectively break the causal chain.

        Returns:
            Dict with root_causes, causal_chains, and trace_depth.
        """
        if symptom_id not in self._nodes:
            return {"root_causes": [], "causal_chains": [], "error": "symptom not found"}

        # BFS backward
        visited = set()
        queue = deque([(symptom_id, [symptom_id], 0)])  # (node, path, depth)
        root_causes = []
        all_chains = []

        while queue:
            current, path, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)

            # Get incoming causal edges (cause → current)
            incoming = [
                e for e in self._incoming.get(current, [])
                if e.strength >= min_strength
                and e.relation in ("CAUSES", "MEDIATES", "MODIFIES")
            ]

            if not incoming:
                # No incoming causal edges → this is a root cause candidate
                root_causes.append({
                    "node_id": current,
                    "depth": depth,
                    "node_type": self._nodes[current].node_type if current in self._nodes else "UNKNOWN",
                    "description": self._nodes[current].description if current in self._nodes else "",
                    "path": list(reversed(path)),
                    "chain_length": len(path),
                })
                all_chains.append({
                    "path": list(reversed(path)),
                    "root_cause": current,
                    "depth": depth,
                })
            else:
                for edge in incoming:
                    new_path = path + [edge.source_id]
                    queue.append((edge.source_id, new_path, depth + 1))

        # Sort root causes by depth (deepest first = most fundamental cause)
        root_causes.sort(key=lambda x: x["depth"], reverse=True)

        return {
            "symptom": symptom_id,
            "root_causes": root_causes,
            "causal_chains": all_chains,
            "total_paths": len(all_chains),
            "max_depth_reached": max((rc["depth"] for rc in root_causes), default=0),
            "recommendation": (
                f"Intervene on '{root_causes[0]['node_id']}' to break causal chain at depth {root_causes[0]['depth']}"
                if root_causes else "No root cause found — check symptom node connectivity"
            ),
        }

    def find_causal_paths(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 8,
    ) -> List[Dict[str, Any]]:
        """Find all causal paths from source to target.

        DFS with cycle detection. Each path includes the mechanism
        at each step, explaining HOW the cause propagates.
        """
        if source_id not in self._nodes or target_id not in self._nodes:
            return []

        paths = []

        def dfs(current: str, path: List[str], mechanisms: List[str], depth: int):
            if depth > max_depth:
                return
            if current == target_id and len(path) > 1:
                paths.append({
                    "path": list(path),
                    "mechanisms": list(mechanisms),
                    "length": len(path) - 1,
                })
                return
            for edge in self._outgoing.get(current, []):
                if edge.target_id not in path:  # cycle detection
                    dfs(
                        edge.target_id,
                        path + [edge.target_id],
                        mechanisms + [edge.mechanism or f"{edge.relation}: {current}→{edge.target_id}"],
                        depth + 1,
                    )

        dfs(source_id, [source_id], [], 0)
        paths.sort(key=lambda p: p["length"])
        return paths

    # ── confounder detection ─────────────────────────────────

    def detect_confounders(self) -> List[Dict[str, Any]]:
        """Find nodes that are potential confounders.

        A confounder is a node with multiple outgoing causal edges
        to different targets — it creates spurious correlation between them.

        Pearl's backdoor criterion: if a confounder C influences both A and B,
        then P(B | A) ≠ P(B | do(A)). We must control for C to estimate
        the true causal effect of A on B.
        """
        confounders = []
        for node_id, outgoing in self._outgoing.items():
            causal_targets = [
                e.target_id for e in outgoing
                if e.relation == "CAUSES"
            ]
            unique_targets = set(causal_targets)
            if len(unique_targets) >= 2:
                confounders.append({
                    "confounder_id": node_id,
                    "targets": list(unique_targets),
                    "target_count": len(unique_targets),
                    "edges": [
                        {
                            "target": e.target_id,
                            "relation": e.relation,
                            "strength": e.strength,
                        }
                        for e in outgoing if e.relation == "CAUSES"
                    ],
                    "recommendation": (
                        f"Control for '{node_id}' when estimating effects between "
                        f"{', '.join(sorted(unique_targets)[:3])}"
                    ),
                })

        confounders.sort(key=lambda c: c["target_count"], reverse=True)
        return confounders

    # ── intervention effect estimation ───────────────────────

    def compute_intervention_effect(
        self,
        cause_id: str,
        effect_id: str,
    ) -> Dict[str, Any]:
        """Estimate the causal effect P(effect | do(cause)) — simplified.

        Uses the backdoor adjustment formula (Pearl):
        P(Y | do(X)) = Σ_z P(Y | X, Z=z) · P(Z=z)

        Simplified version: multiplies edge strengths along the strongest
        causal path and adjusts for confounders.

        This is a heuristic approximation — full do-calculus requires
        structural equation models with probabilistic data.
        """
        # Find all causal paths
        paths = self.find_causal_paths(cause_id, effect_id)
        if not paths:
            return {
                "cause": cause_id,
                "effect": effect_id,
                "estimated_effect": 0.0,
                "confidence": 0.0,
                "method": "no_path_found",
                "recommendation": "No causal path exists — cannot estimate effect.",
            }

        # Compute path strengths
        path_strengths = []
        for path in paths:
            strength = 1.0
            confidence = 1.0
            for i in range(len(path["path"]) - 1):
                src = path["path"][i]
                tgt = path["path"][i + 1]
                # Find the edge
                for edge in self._outgoing.get(src, []):
                    if edge.target_id == tgt:
                        strength *= edge.strength
                        confidence = min(confidence, edge.confidence)
                        break
            path_strengths.append({
                "path": path["path"],
                "chain_strength": strength,
                "chain_confidence": confidence,
                "length": path["length"],
            })

        path_strengths.sort(key=lambda p: p["chain_strength"], reverse=True)
        strongest = path_strengths[0]

        # Adjust for confounders (crude: reduce confidence if confounders exist)
        confounders = self.detect_confounders()
        confounder_penalty = 0.0
        for c in confounders:
            if cause_id in c["targets"] and effect_id in c["targets"]:
                confounder_penalty += 0.2

        adjusted_confidence = max(0.1, strongest["chain_confidence"] - confounder_penalty)

        return {
            "cause": cause_id,
            "effect": effect_id,
            "estimated_effect": round(strongest["chain_strength"], 3),
            "confidence": round(adjusted_confidence, 3),
            "method": "path_strength_product",
            "strongest_path": strongest["path"],
            "confounder_penalty": round(confounder_penalty, 3),
            "alternative_paths": len(path_strengths) - 1,
            "recommendation": (
                f"P({effect_id} | do({cause_id})) ≈ {strongest['chain_strength']:.2f} "
                f"(confidence: {adjusted_confidence:.2f}). "
                + (f"WARNING: {len(confounders)} confounder(s) detected — estimate may be biased."
                   if confounder_penalty > 0 else "No confounders detected.")
            ),
        }

    # ── graph statistics ─────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def get_causal_neighbors(
        self, node_id: str, direction: str = "both"
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get causal neighbors of a node.

        Args:
            direction: "incoming" (causes), "outgoing" (effects), or "both".
        """
        result: Dict[str, List[Dict[str, Any]]] = {"causes": [], "effects": []}

        if direction in ("incoming", "both"):
            for edge in self._incoming.get(node_id, []):
                result["causes"].append({
                    "node_id": edge.source_id,
                    "relation": edge.relation,
                    "mechanism": edge.mechanism,
                    "strength": edge.strength,
                })

        if direction in ("outgoing", "both"):
            for edge in self._outgoing.get(node_id, []):
                result["effects"].append({
                    "node_id": edge.target_id,
                    "relation": edge.relation,
                    "mechanism": edge.mechanism,
                    "strength": edge.strength,
                })

        return result

    def to_dict(self) -> Dict[str, Any]:
        confounders = self.detect_confounders()
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "confounders": confounders,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }
