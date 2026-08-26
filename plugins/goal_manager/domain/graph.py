"""DAG validation and graph traversal helpers."""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Iterable


class GoalConflict(ValueError):
    """A domain conflict that should be exposed as HTTP 409."""

    def __init__(self, detail: str, **payload: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.payload = payload


def find_cycle_path(
    source_id: str,
    target_id: str,
    outgoing: Callable[[str], Iterable[str]],
) -> list[str] | None:
    """Return a cycle path if adding source -> target would create one."""
    if source_id == target_id:
        return [source_id, target_id]
    queue: deque[tuple[str, list[str]]] = deque([(target_id, [target_id])])
    visited = {target_id}
    while queue:
        node_id, path = queue.popleft()
        for child_id in outgoing(node_id):
            if child_id == source_id:
                return [source_id, *path, source_id]
            if child_id not in visited:
                visited.add(child_id)
                queue.append((child_id, [*path, child_id]))
    return None


def bounded_subgraph(
    start_node: str,
    depth: int,
    edges: Iterable[dict[str, Any]],
    edge_types: set[str] | None = None,
) -> tuple[set[str], list[dict[str, Any]]]:
    """Traverse both directions while keeping a small, predictable projection."""
    if depth < 0:
        return set(), []
    normalized = [
        edge for edge in edges
        if not edge_types or edge.get("edge_type") in edge_types
    ]
    nodes = {start_node}
    frontier = {start_node}
    for _ in range(depth):
        next_frontier: set[str] = set()
        for edge in normalized:
            endpoints = {edge["source_id"], edge["target_id"]}
            if endpoints & frontier:
                next_frontier.update(endpoints - nodes)
        nodes.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    return nodes, [
        edge for edge in normalized
        if edge["source_id"] in nodes and edge["target_id"] in nodes
    ]
