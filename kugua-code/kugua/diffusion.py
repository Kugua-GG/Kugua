"""
kugua — Graph Laplacian Confidence Diffusion
v0.2.1

Propagates confidence scores through the GraphKB via random-walk Laplacian.
Used for: spreading verification evidence along causal/similarity edges.

References:
  - Zhou et al. "Learning with Local and Global Consistency" (NIPS 2004)
"""
from __future__ import annotations
import math
from typing import Optional

LEVEL_TO_CONF = {"L3": 0.9, "L2": 0.7, "L1": 0.4, "L0": 0.1}


def build_f_vector(graph, labeled_nodes: dict[str, float]) -> dict[str, float]:
    """Build initial label vector f from labeled nodes.

    Args:
        graph: GraphKB instance
        labeled_nodes: {node_id: confidence_score} for known nodes

    Returns:
        {node_id: score} for all nodes (unlabeled = 0)
    """
    f = {}
    for nid in graph._nodes:
        f[nid] = labeled_nodes.get(nid, 0.0)
    return f


def compute_L_rw(graph) -> dict[str, dict[str, float]]:
    """Compute random-walk normalized Laplacian.

    L_rw = I - D^{-1}A
    Returns sparse dict-of-dicts representation.
    """
    L = {}
    for nid in graph._nodes:
        out_edges = graph.get_out_edges(nid)
        degree = len(out_edges) or 1
        row = {}
        for edge in out_edges:
            row[edge.target_node] = -edge.weight / degree
        row[nid] = 1.0  # I - D^{-1}A diagonal
        L[nid] = row
    return L


def run_diffusion(graph, labeled_nodes: dict[str, float],
                  alpha: float = 0.99, max_iter: int = 100,
                  tol: float = 1e-6) -> dict[str, float]:
    """Run label spreading diffusion.

    Args:
        graph: GraphKB instance
        labeled_nodes: {node_id: initial_confidence} seed labels
        alpha: clamping factor (higher = more trust in initial labels)
        max_iter: maximum iterations
        tol: convergence tolerance

    Returns:
        {node_id: diffused_confidence} for all nodes
    """
    f = build_f_vector(graph, labeled_nodes)
    y = dict(f)  # clamped labels

    for _ in range(max_iter):
        f_prev = dict(f)
        max_delta = 0.0

        for nid in graph._nodes:
            out_edges = graph.get_out_edges(nid)
            in_edges = graph.get_in_edges(nid)
            # Average of neighbors
            neighbor_sum = 0.0
            neighbor_count = 0
            for edge in out_edges:
                neighbor_sum += edge.weight * f.get(edge.target_node, 0.0)
                neighbor_count += 1
            for edge in in_edges:
                neighbor_sum += edge.weight * f.get(edge.source_node, 0.0)
                neighbor_count += 1

            if neighbor_count > 0:
                neighbor_avg = neighbor_sum / neighbor_count
            else:
                neighbor_avg = 0.0

            f[nid] = alpha * neighbor_avg + (1 - alpha) * y.get(nid, 0.0)
            max_delta = max(max_delta, abs(f[nid] - f_prev.get(nid, 0.0)))

        if max_delta < tol:
            break

    return f


def calculate_verification_saved(graph, diffused: dict[str, float],
                                  threshold: float = 0.7) -> int:
    """Count how many nodes reached verification confidence via diffusion."""
    return sum(1 for conf in diffused.values() if conf >= threshold)


def audit_trail(diffused: dict[str, float]) -> list[dict]:
    """Generate audit trail of diffusion results."""
    return sorted(
        [{"node_id": nid, "confidence": round(conf, 4)}
         for nid, conf in diffused.items() if conf > 0.01],
        key=lambda x: -x["confidence"],
    )
