"""
kugua.graph — 图结构知识库模块 v0.2.0

提供有向图知识存储和检索:
  GraphKB       — 有向图知识库,支持节点/边管理、路径查找、度分布
  GraphRetriever — 基于关键词匹配的图检索器,格式化上下文输出

纯 stdlib 实现,无外部依赖。
"""

import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 基础数据结构
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """图节点。

    Fields:
        node_id:   唯一标识符
        labels:    标签列表(用于分类/检索)
        properties: 附加属性字典
    """

    node_id: str
    labels: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)

    def __init__(self, node_id_or_id: str = "", *args, **kwargs):
        if args and isinstance(args[0], str) and len(args) >= 2:
            self.node_id = node_id_or_id
            self.labels = [args[0]]
            self.properties = args[1] if len(args) > 1 else {}
        else:
            self.node_id = node_id_or_id
            self.labels = kwargs.get("labels", [])
            self.properties = kwargs.get("properties", {})

    @property
    def id(self) -> str:
        return self.node_id

    @property
    def type(self) -> str:
        return self.labels[0] if self.labels else ""

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Node):
            return self.node_id == other.node_id
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "labels": self.labels,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Node":
        return cls(
            node_id=d["node_id"],
            labels=list(d.get("labels", [])),
            properties=dict(d.get("properties", {})),
        )


@dataclass
class Edge:
    """有向边。

    Fields:
        source_node: 源节点 ID
        target_node: 目标节点 ID
        relation:    关系类型标签
        weight:      边权重 (0-1)
        properties:  附加属性
    """

    source_node: str
    target_node: str
    relation: str = "related_to"
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def __init__(self, *args, **kwargs):
        # 兼容旧 API: Edge(id, source, target, relation, weight)
        # 和新 API: Edge(source_node, target_node, relation, weight)
        n = len(args)
        if n >= 4:
            self.source_node = args[1] if n > 1 else args[0]
            self.target_node = args[2] if n > 2 else ""
            self.relation = args[3] if n > 3 else "related_to"
            self.weight = float(args[4]) if n > 4 else float(kwargs.get("weight", 1.0))
            self.properties = kwargs.get("properties", {})
        else:
            self.source_node = kwargs.get("source_node", args[0] if args else "")
            self.target_node = kwargs.get("target_node", "")
            self.relation = kwargs.get("relation", "related_to")
            self.weight = float(kwargs.get("weight", 1.0))
            self.properties = kwargs.get("properties", {})

    def __hash__(self) -> int:
        return hash(
            (self.source_node, self.target_node, self.relation)
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Edge):
            return (
                self.source_node == other.source_node
                and self.target_node == other.target_node
                and self.relation == other.relation
            )
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_node": self.source_node,
            "target_node": self.target_node,
            "relation": self.relation,
            "weight": self.weight,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Edge":
        return cls(
            source_node=d["source_node"],
            target_node=d["target_node"],
            relation=d.get("relation", "related_to"),
            weight=d.get("weight", 1.0),
            properties=dict(d.get("properties", {})),
        )


# ---------------------------------------------------------------------------
# GraphKB — 有向图知识库
# ---------------------------------------------------------------------------

class GraphKB:
    """有向图知识库,管理节点和边的结构化关系。

    用途:
        - 知识条目的 taxonomy 关系
        - 公理依赖拓扑
        - 联想式知识扩散(BFS 邻域检索)
    """

    def __init__(self, name: str = "default"):
        self.name = name
        # node_id -> Node
        self._nodes: Dict[str, Node] = {}
        # (source, target, relation) -> Edge
        self._edges: Dict[Tuple[str, str, str], Edge] = {}
        # 邻接表: source -> [(target, edge_key), ...]
        self._out_adj: Dict[str, List[Tuple[str, Tuple[str, str, str]]]] = (
            defaultdict(list)
        )
        # 逆邻接表: target -> [(source, edge_key), ...]
        self._in_adj: Dict[str, List[Tuple[str, Tuple[str, str, str]]]] = (
            defaultdict(list)
        )
        # 节点索引: label -> Set[node_id]
        self._label_index: Dict[str, Set[str]] = defaultdict(set)

    # ---- 节点操作 -------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def add_node(
        self,
        node_id = None,
        labels: Optional[List[str]] = None,
        **properties,
    ) -> "Node":
        """添加或更新节点。兼容旧 API: add_node(Node) 和新 API: add_node(id, labels, **props)"""
        # 兼容旧 API: 传入 Node 对象
        if hasattr(node_id, 'node_id'):
            node = node_id
            node_id = node.node_id
            labels = node.labels
            properties = dict(node.properties)
        elif hasattr(node_id, 'id'):
            node = node_id
            node_id = node.id
            labels = [node.type] if hasattr(node, 'type') else []
            properties = dict(node.properties) if hasattr(node, 'properties') else {}

        if node_id is None:
            raise TypeError("add_node requires node_id or Node object")

        """添加或更新节点。"""
        if node_id in self._nodes:
            node = self._nodes[node_id]
            if labels:
                # 从旧标签索引中移除
                for old_label in node.labels:
                    if old_label in self._label_index:
                        self._label_index[old_label].discard(node_id)
                node.labels = list(labels)
                # 更新标签索引
                for label in node.labels:
                    self._label_index[label].add(node_id)
            node.properties.update(properties)
            return node

        node = Node(node_id=node_id, labels=list(labels or []), properties=properties)
        self._nodes[node_id] = node
        for label in node.labels:
            self._label_index[label].add(node_id)
        return node

    def remove_node(self, node_id: str) -> bool:
        """删除节点及其所有关联边。

        Returns:
            True 如果成功删除, False 如果节点不存在。
        """
        if node_id not in self._nodes:
            return False

        # 删除出边
        for _, edge_key in list(self._out_adj.get(node_id, [])):
            self._edges.pop(edge_key, None)
        self._out_adj.pop(node_id, None)

        # 删除入边
        for _, edge_key in list(self._in_adj.get(node_id, [])):
            self._edges.pop(edge_key, None)
        self._in_adj.pop(node_id, None)

        # 从其他节点的邻接表中清理
        for src, edges in list(self._out_adj.items()):
            self._out_adj[src] = [
                (tgt, ek) for tgt, ek in edges if tgt != node_id
            ]
            if not self._out_adj[src]:
                del self._out_adj[src]

        for tgt, edges in list(self._in_adj.items()):
            self._in_adj[tgt] = [
                (src, ek) for src, ek in edges if src != node_id
            ]
            if not self._in_adj[tgt]:
                del self._in_adj[tgt]

        # 从标签索引移除
        node = self._nodes.pop(node_id)
        for label in node.labels:
            self._label_index[label].discard(node_id)

        return True

    # ---- 边操作 ---------------------------------------------------------------

    def add_edge(
        self,
        source_node = None,
        target_node: str = None,
        relation: str = "related_to",
        weight: float = 1.0,
        **properties,
    ) -> "Edge":
        """添加或更新有向边。兼容旧 API: add_edge(Edge) 和新 API: add_edge(src, tgt, rel, wt)"""
        # 兼容旧 API: 传入 Edge 对象
        if hasattr(source_node, 'source_node'):
            edge = source_node
            source_node = edge.source_node
            target_node = edge.target_node
            relation = edge.relation
            weight = edge.weight
        elif target_node is None and hasattr(source_node, 'source'):
            # Old Edge(id, source, target, relation, weight) compatibility
            edge = source_node
            source_node = edge.source if hasattr(edge, 'source') else edge.source_node
            target_node = edge.target if hasattr(edge, 'target') else edge.target_node
            relation = getattr(edge, 'relation', 'related_to')
            weight = getattr(edge, 'weight', 1.0)
        elif target_node is None:
            raise TypeError(f"add_edge requires target_node or Edge object, got: {source_node}")

        """添加或更新有向边。如果源/目标节点不存在,自动创建。"""
        # 确保节点存在
        if source_node not in self._nodes:
            self.add_node(source_node)
        if target_node not in self._nodes:
            self.add_node(target_node)

        edge_key = (source_node, target_node, relation)

        if edge_key in self._edges:
            edge = self._edges[edge_key]
            edge.weight = weight
            edge.properties.update(properties)
            return edge

        edge = Edge(
            source_node=source_node,
            target_node=target_node,
            relation=relation,
            weight=weight,
            properties=properties,
        )
        self._edges[edge_key] = edge
        self._out_adj[source_node].append((target_node, edge_key))
        self._in_adj[target_node].append((source_node, edge_key))
        return edge

    def remove_edge(
        self,
        source_node: str,
        target_node: str,
        relation: str = "related_to",
    ) -> bool:
        """删除边。"""
        edge_key = (source_node, target_node, relation)
        if edge_key not in self._edges:
            return False

        self._edges.pop(edge_key)

        # 从邻接表删除
        self._out_adj[source_node] = [
            (t, ek) for t, ek in self._out_adj[source_node] if ek != edge_key
        ]
        self._in_adj[target_node] = [
            (s, ek) for s, ek in self._in_adj[target_node] if ek != edge_key
        ]

        return True

    # ---- 遍历 -----------------------------------------------------------------

    def get_neighbors(
        self,
        node_id: str,
        direction: str = "out",
        max_depth: int = 1,
        relations: Optional[List[str]] = None,
        return_tuples: bool = False,
    ) -> list:
        """BFS 获取邻域节点。

        Args:
            node_id:    起始节点 ID
            direction:  遍历方向 "out" | "in" | "both"
            max_depth:  最大深度
            relations:  关系类型过滤(None = 所有)

        Returns:
            [(Node, depth, cumulative_weight), ...]
        """
        if node_id not in self._nodes:
            return []

        visited: Set[str] = {node_id}
        result: List[Tuple[Node, int, float]] = []
        queue: deque[Tuple[str, int, float]] = deque()
        queue.append((node_id, 0, 1.0))

        while queue:
            current, depth, cum_weight = queue.popleft()
            if depth > 0:
                result.append((self._nodes[current], depth, cum_weight))
            if depth >= max_depth:
                continue

            neighbors: List[Tuple[str, float]] = []

            if direction in ("out", "both"):
                adj = self._out_adj.get(current, [])
                for tgt, edge_key in adj:
                    edge = self._edges.get(edge_key)
                    if edge is None:
                        continue
                    if relations and edge.relation not in relations:
                        continue
                    neighbors.append((tgt, edge.weight))

            if direction in ("in", "both"):
                adj = self._in_adj.get(current, [])
                for src, edge_key in adj:
                    edge = self._edges.get(edge_key)
                    if edge is None:
                        continue
                    if relations and edge.relation not in relations:
                        continue
                    neighbors.append((src, edge.weight))

            for neighbor_id, w in neighbors:
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, depth + 1, cum_weight * w))

        if return_tuples:
            return result
        return [node for node, _depth, _weight in result]

    def find_subgraph(
        self,
        node_ids: List[str],
        max_depth: int = 1,
    ) -> "GraphKB":
        """提取包含指定节点及其邻域的子图。

        Returns:
            新的 GraphKB 实例。
        """
        subgraph = GraphKB()
        visited: Set[str] = set()

        def expand(nid: str, depth: int):
            if depth > max_depth or nid in visited:
                return
            visited.add(nid)
            node = self._nodes.get(nid)
            if node:
                subgraph._nodes[nid] = Node(
                    node_id=node.node_id,
                    labels=list(node.labels),
                    properties=dict(node.properties),
                )
            # 出边
            for tgt, edge_key in self._out_adj.get(nid, []):
                edge = self._edges.get(edge_key)
                if edge:
                    subgraph._edges[edge_key] = Edge(
                        source_node=edge.source_node,
                        target_node=edge.target_node,
                        relation=edge.relation,
                        weight=edge.weight,
                        properties=dict(edge.properties),
                    )
                    subgraph._out_adj[edge.source_node].append((edge.target_node, edge_key))
                    subgraph._in_adj[edge.target_node].append((edge.source_node, edge_key))
                    expand(tgt, depth + 1)

        for nid in node_ids:
            expand(nid, 0)
        return subgraph

    def find_paths(
        self,
        source_node: str,
        target_node: str,
        max_depth: int = 5,
        relations: Optional[List[str]] = None,
    ) -> List[List[Tuple[str, str, float]]]:
        """BFS 查找从 source 到 target 的所有路径。

        Returns:
            [[(from, to, weight), ...], ...]
        """
        if source_node not in self._nodes or target_node not in self._nodes:
            return []
        if source_node == target_node:
            return [[(source_node, source_node, 1.0)]]

        all_paths: List[List[Tuple[str, str, float]]] = []
        queue: deque[Tuple[str, List[Tuple[str, str, float]]]] = deque()
        queue.append((source_node, []))

        while queue:
            current, path = queue.popleft()
            if len(path) >= max_depth:
                continue

            for tgt, edge_key in self._out_adj.get(current, []):
                edge = self._edges.get(edge_key)
                if edge is None:
                    continue
                if relations and edge.relation not in relations:
                    continue

                # 检查环
                visited_nodes = {p[0] for p in path} | {current}
                if tgt in visited_nodes:
                    continue

                new_step = (current, tgt, edge.weight)
                new_path = path + [new_step]

                if tgt == target_node:
                    all_paths.append(new_path)
                else:
                    queue.append((tgt, new_path))

        return all_paths

    # ---- 出入边查询 -----------------------------------------------------------

    def get_out_edges(self, node_id: str) -> List[Edge]:
        """获取节点的所有出边。"""
        result: List[Edge] = []
        for _, edge_key in self._out_adj.get(node_id, []):
            edge = self._edges.get(edge_key)
            if edge:
                result.append(edge)
        return result

    def get_in_edges(self, node_id: str) -> List[Edge]:
        """获取节点的所有入边。"""
        result: List[Edge] = []
        for _, edge_key in self._in_adj.get(node_id, []):
            edge = self._edges.get(edge_key)
            if edge:
                result.append(edge)
        return result

    # ---- 边权重更新 -----------------------------------------------------------

    def update_edge_weight(
        self,
        source_node: str,
        target_node: str,
        relation: str,
        weight: float,
    ) -> bool:
        """更新边权重。"""
        edge_key = (source_node, target_node, relation)
        if edge_key in self._edges:
            self._edges[edge_key].weight = weight
            return True
        return False

    # ---- 度分布 (MLE alpha 估计) ----------------------------------------------

    def degree_distribution(self) -> Dict[str, Any]:
        """计算总度分布并用 MLE 估计幂律 alpha。

        MLE: alpha = 1 + n / sum(ln(x_i / x_min))
        其中 x_min 取最小度数。
        """
        degrees: Dict[str, int] = {}
        for node_id in self._nodes:
            out_deg = len(self._out_adj.get(node_id, []))
            in_deg = len(self._in_adj.get(node_id, []))
            degrees[node_id] = out_deg + in_deg

        degree_values = list(degrees.values())
        if not degree_values:
            return {
                "nodes": 0,
                "min_degree": 0,
                "max_degree": 0,
                "mean_degree": 0,
                "alpha_mle": None,
                "distribution": {},
            }

        min_deg = min(degree_values)
        max_deg = max(degree_values)
        mean_deg = sum(degree_values) / len(degree_values)

        # MLE alpha 估计 (仅对 degree > 0)
        positive = [d for d in degree_values if d > 0]
        alpha = None
        if len(positive) > 1 and min_deg > 0:
            x_min = min(positive)
            sum_log = sum(math.log(d / x_min) for d in positive)
            if sum_log > 0:
                alpha = 1 + len(positive) / sum_log

        # 度分布直方图
        dist: Dict[int, int] = defaultdict(int)
        for d in degree_values:
            dist[d] += 1

        return {
            "nodes": len(self._nodes),
            "min_degree": min_deg,
            "max_degree": max_deg,
            "mean_degree": round(mean_deg, 3),
            "alpha_mle": round(alpha, 4) if alpha else None,
            "distribution": dict(sorted(dist.items())),
        }

    def degree_distribution_by_type(self) -> Dict[str, Dict[str, Any]]:
        """按标签分组计算度分布。"""
        result: Dict[str, Dict[str, Any]] = {}
        for label in self._label_index:
            node_ids = self._label_index[label]
            degrees: List[int] = []
            for nid in node_ids:
                out_d = len(self._out_adj.get(nid, []))
                in_d = len(self._in_adj.get(nid, []))
                degrees.append(out_d + in_d)
            if degrees:
                positive = [d for d in degrees if d > 0]
                alpha = None
                if len(positive) > 1 and min(positive) > 0:
                    x_min = min(positive)
                    sum_log = sum(math.log(d / x_min) for d in positive)
                    if sum_log > 0:
                        alpha = 1 + len(positive) / sum_log
                result[label] = {
                    "count": len(node_ids),
                    "min_degree": min(degrees),
                    "max_degree": max(degrees),
                    "mean_degree": round(sum(degrees) / len(degrees), 3),
                    "alpha_mle": round(alpha, 4) if alpha else None,
                }
        return result

    # ---- 序列化 ---------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GraphKB":
        graph = cls()
        for nd in d.get("nodes", []):
            node = Node.from_dict(nd)
            graph._nodes[node.node_id] = node
            for label in node.labels:
                graph._label_index[label].add(node.node_id)
        for ed in d.get("edges", []):
            edge = Edge.from_dict(ed)
            ek = (edge.source_node, edge.target_node, edge.relation)
            graph._edges[ek] = edge
            graph._out_adj[edge.source_node].append((edge.target_node, ek))
            graph._in_adj[edge.target_node].append((edge.source_node, ek))
        return graph

    def save(self, path: str):
        """持久化到 JSON 文件。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "GraphKB":
        """从 JSON 文件加载。"""
        if not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    # ---- 摘要 -----------------------------------------------------------------

    def to_summary(self) -> str:
        """生成可读摘要。"""
        node_count = len(self._nodes)
        edge_count = len(self._edges)
        label_counts = {
            label: len(ids) for label, ids in self._label_index.items()
        }
        deg = self.degree_distribution()

        lines = [
            f"GraphKB 摘要",
            f"  节点数: {node_count}",
            f"  边数:   {edge_count}",
            f"  密度:   {edge_count / max(node_count * (node_count - 1), 1):.4f}",
            f"  度分布: min={deg['min_degree']}, max={deg['max_degree']}, "
            f"mean={deg['mean_degree']}",
        ]
        if deg.get("alpha_mle"):
            lines.append(f"  幂律 α (MLE): {deg['alpha_mle']}")
        if label_counts:
            lines.append(f"  标签分布:")
            for label, count in sorted(label_counts.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"    {label}: {count}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes


# ---------------------------------------------------------------------------
# GraphRetriever — 基于关键词匹配的图检索器
# ---------------------------------------------------------------------------

class GraphRetriever:
    """基于关键词匹配的图检索器。

    在 GraphKB 中按关键词检索节点,并以格式化的上下文形式返回。
    """

    def __init__(self, graph: GraphKB):
        self.graph = graph

    # ---- 检索 ----------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        include_neighbors: bool = True,
        neighbor_depth: int = 1,
    ) -> List[Node]:
        """按关键词匹配检索节点。

        匹配规则:
            1. 精确节点 ID 匹配
            2. 标签匹配(包含关系)
            3. 属性值匹配(字符串包含)
            4. 邻居扩展(可选)

        Args:
            query:            查询字符串(空格分隔关键词)
            top_k:            返回节点数
            include_neighbors: 是否包含检索到节点的邻居
            neighbor_depth:    BFS 深度

        Returns:
            匹配的 Node 列表
        """
        keywords = [kw.lower().strip() for kw in query.split() if kw.strip()]
        if not keywords:
            return []

        scored: Dict[str, float] = defaultdict(float)

        for node_id, node in self.graph._nodes.items():
            score = 0.0

            # 1. 精确 ID 匹配
            for kw in keywords:
                if kw == node_id.lower():
                    score += 10.0

            # 2. 标签匹配
            for label in node.labels:
                label_lower = label.lower()
                for kw in keywords:
                    if kw in label_lower or label_lower in kw:
                        score += 3.0

            # 3. 属性值匹配
            for prop_val in node.properties.values():
                if isinstance(prop_val, str):
                    val_lower = prop_val.lower()
                    for kw in keywords:
                        if kw in val_lower:
                            score += 2.0

            if score > 0:
                scored[node_id] = score

        # 排序
        ranked = sorted(scored.items(), key=lambda x: x[1], reverse=True)
        result_ids = [nid for nid, _ in ranked[:top_k]]

        # 邻居扩展
        if include_neighbors and neighbor_depth > 0:
            visited: Set[str] = set(result_ids)
            queue: deque[Tuple[str, int]] = deque(
                (nid, 0) for nid in result_ids
            )
            while queue:
                current, depth = queue.popleft()
                if depth >= neighbor_depth:
                    continue
                for tgt, _ in self.graph._out_adj.get(current, []):
                    if tgt not in visited:
                        visited.add(tgt)
                        result_ids.append(tgt)
                        queue.append((tgt, depth + 1))
                for src, _ in self.graph._in_adj.get(current, []):
                    if src not in visited:
                        visited.add(src)
                        result_ids.append(src)
                        queue.append((src, depth + 1))

        return [self.graph._nodes[nid] for nid in result_ids if nid in self.graph._nodes]

    # ---- 格式化 ---------------------------------------------------------------

    def format_for_context(self, nodes: List[Node], max_tokens: int = 2000) -> str:
        """将检索到的节点格式化为 LLM 上下文。

        Args:
            nodes:      节点列表
            max_tokens: 最大 token 数(粗略估计,按字符数 / 2)

        Returns:
            格式化的上下文字符串,适合注入 LLM System Prompt。
        """
        if not nodes:
            return "(无相关图节点)"

        lines: List[str] = []
        char_count = 0
        char_limit = max_tokens * 2  # 粗略估计: 1 token ≈ 2 chars

        for node in nodes:
            node_text = f"[{node.node_id}]"
            if node.labels:
                node_text += f" tags: {', '.join(node.labels)}"
            if node.properties:
                props_str = ", ".join(
                    f"{k}={v}" for k, v in node.properties.items()
                    if isinstance(v, (str, int, float, bool))
                )
                if props_str:
                    node_text += f" ({props_str})"

            # 出边
            out_edges = self.graph.get_out_edges(node.node_id)
            if out_edges:
                edge_strs = [
                    f"{e.relation}->{e.target_node}" for e in out_edges[:5]
                ]
                node_text += f"\n        -> " + ", ".join(edge_strs)

            char_count += len(node_text) + 1
            if char_count > char_limit:
                lines.append("... (截断,超出 token 限制)")
                break
            lines.append(node_text)

        header = f"# GraphKB 检索结果 (共 {len(nodes)} 个节点)\n"
        return header + "\n".join(lines)
