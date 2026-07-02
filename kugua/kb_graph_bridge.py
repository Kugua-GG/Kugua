"""
kugua — KB-Graph Bridge
v0.3.0

Synchronizes KnowledgeBase (vertical, evidence) with GraphKB (horizontal, topology).
When a KB entry is added/updated/removed, the bridge maintains corresponding graph nodes.
"""
from __future__ import annotations


class KBGraphBridge:
    """Bridge between KnowledgeBase and GraphKB.

    Keeps the graph in sync with KB changes:
      - KB add -> GraphKB node
      - KB level change -> edge weight update
      - KB scope tags -> graph edges between related nodes

    Usage:
        bridge = KBGraphBridge(kb, graph_kb)
        bridge.sync_on_add(entry)      # called after KB.add()
        bridge.sync_on_level_change(key, old_level, new_level)
    """

    def __init__(self, kb=None, graph_kb=None):
        self.kb = kb
        self.graph = graph_kb

    def sync_on_add(self, entry) -> bool:
        """Create a graph node for a newly added KB entry."""
        if not self.graph:
            return False
        try:
            from kugua.graph import Node
            key = getattr(entry, 'key', str(entry))
            level = getattr(entry, 'level', 'L1')
            if not self.graph.has_node(key):
                node = Node(key, "KBEntry", {
                    "level": level,
                    "name": getattr(entry, 'key', key),
                })
                self.graph.add_node(node)
            return True
        except Exception:
            return False

    def sync_on_level_change(self, key: str, old_level: str, new_level: str) -> bool:
        """Update edge weights when a KB entry's evidence level changes."""
        if not self.graph or not self.graph.has_node(key):
            return False
        try:
            weight_map = {"L3": 1.0, "L2": 0.7, "L1": 0.3, "L0": 0.1}
            new_weight = weight_map.get(new_level, 0.5)
            for edge in list(self.graph._adj_out.get(key, [])):
                self.graph.update_edge_weight(
                    edge.source_node, edge.target_node, edge.relation, new_weight
                )
            return True
        except Exception:
            return False

    def sync_tags(self, key: str, tags: list[str]) -> bool:
        """Create edges between KB entry and its tag nodes."""
        if not self.graph:
            return False
        try:
            from kugua.graph import Node, Edge
            for tag in tags:
                tag_id = f"tag:{tag}"
                if not self.graph.has_node(tag_id):
                    self.graph.add_node(Node(tag_id, "Tag", {"name": tag}))
                if not self.graph.has_edge(key, tag_id, "TAGGED_AS"):
                    self.graph.add_edge(Edge(
                        source_node=key, target_node=tag_id,
                        relation="TAGGED_AS", weight=0.8,
                    ))
            return True
        except Exception:
            return False
