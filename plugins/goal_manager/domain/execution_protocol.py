"""Adaptive execution protocol helpers for Goal Manager."""

from __future__ import annotations

from typing import Any


ACTIVE_STATUSES = {"planned", "in_progress", "blocked", "waiting_review"}
ACTION_NODE_TYPES = {"task", "bug", "test", "feature"}
BLOCKING_SEVERITIES = {"blocking"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, list):
        raise ValueError("intent contract list fields must be lists of text")
    result = []
    for item in value:
        text = _text(item)
        if text:
            result.append(text)
    return result


def normalize_intent_contract(data: dict[str, Any]) -> dict[str, Any]:
    """Return the minimal intent contract shape used by M3 protocol checks."""
    if not isinstance(data, dict):
        raise ValueError("intent contract must be an object")
    outcome = _text(data.get("outcome"))
    if not outcome:
        raise ValueError("intent contract outcome is required")
    return {
        "outcome": outcome,
        "success_criteria": _text_list(data.get("success_criteria")),
        "scope": _text_list(data.get("scope")),
        "constraints": _text_list(data.get("constraints")),
        "assumptions": _text_list(data.get("assumptions")),
        "open_questions": _text_list(data.get("open_questions")),
    }


def review_plan(
    project: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    intent_contract: dict[str, Any] | None,
    ready_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Review the current graph for protocol-level execution readiness."""
    node_by_id = {node["id"]: node for node in nodes}
    root_id = project.get("root_node_id")
    root = node_by_id.get(root_id)
    active_nodes = [node for node in nodes if node.get("status") in ACTIVE_STATUSES]
    completed_nodes = [node for node in nodes if node.get("status") == "completed"]
    blocked_nodes = [node for node in nodes if node.get("status") == "blocked"]
    findings: list[dict[str, Any]] = []

    if intent_contract is None:
        findings.append({
            "code": "missing_intent_contract",
            "severity": "blocking",
            "message": "Set an intent contract before using protocol-level execution guidance.",
        })
    elif not intent_contract.get("success_criteria"):
        findings.append({
            "code": "empty_success_criteria",
            "severity": "warning",
            "message": "The intent contract has no success criteria, so final acceptance is underspecified.",
        })

    if root is None:
        findings.append({
            "code": "missing_root_node",
            "severity": "blocking",
            "message": "The project root node is missing from the active graph.",
        })
    else:
        reachable = _reachable_decomposition_nodes(root["id"], edges)
        disconnected = [
            node for node in active_nodes
            if node["id"] != root["id"] and node["id"] not in reachable
        ]
        for node in disconnected:
            findings.append({
                "code": "active_node_disconnected_from_root",
                "severity": "blocking",
                "message": "Active work should be connected to the project root by decomposition edges.",
                "node_id": node["id"],
                "title": node["title"],
            })

        if root.get("status") != "completed" and not _active_leaf_action_nodes(active_nodes, edges):
            findings.append({
                "code": "no_active_leaf_action",
                "severity": "blocking",
                "message": "The plan has no active task-like leaf for the next execution layer.",
            })

    for node in active_nodes:
        if node.get("node_type") in ACTION_NODE_TYPES and not node.get("acceptance_criteria"):
            findings.append({
                "code": "missing_acceptance_criteria",
                "severity": "warning",
                "message": "Task-like nodes should declare acceptance criteria before completion is claimed.",
                "node_id": node["id"],
                "title": node["title"],
            })

    if blocked_nodes:
        findings.append({
            "code": "blocked_nodes_present",
            "severity": "info",
            "message": "Some nodes are explicitly blocked.",
            "count": len(blocked_nodes),
        })

    summary = {
        "nodes": len(nodes),
        "active_nodes": len(active_nodes),
        "ready_nodes": len(ready_nodes),
        "blocked_nodes": len(blocked_nodes),
        "completed_nodes": len(completed_nodes),
    }
    return {
        "valid": not any(item["severity"] in BLOCKING_SEVERITIES for item in findings),
        "findings": findings,
        "summary": summary,
    }


def select_protocol_action(
    project: dict[str, Any],
    nodes: list[dict[str, Any]],
    intent_contract: dict[str, Any] | None,
    review: dict[str, Any],
    ready_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select the next protocol action from explicit graph and contract state."""
    root = next((node for node in nodes if node["id"] == project.get("root_node_id")), None)
    if root and root.get("status") == "completed":
        return {
            "action_type": "complete",
            "reason": "The root goal is already completed.",
            "nodes": [],
        }

    if intent_contract is None:
        return {
            "action_type": "clarify",
            "reason": "No intent contract is set for this project.",
            "nodes": [],
            "questions": ["What outcome should this goal manager drive toward?"],
        }

    open_questions = list(intent_contract.get("open_questions") or [])
    if open_questions:
        return {
            "action_type": "clarify",
            "reason": "The intent contract still has open questions.",
            "nodes": [],
            "questions": open_questions,
        }

    blocking = [item for item in review.get("findings", []) if item.get("severity") == "blocking"]
    if blocking:
        return {
            "action_type": "replan",
            "reason": "The current plan has blocking structure issues.",
            "nodes": [],
            "findings": blocking,
        }

    if ready_nodes:
        investigation_nodes = _investigation_nodes(ready_nodes)
        if investigation_nodes:
            return {
                "action_type": "investigate",
                "reason": "A ready node has acceptance criteria explicitly marked for investigation.",
                "nodes": investigation_nodes,
            }
        return {
            "action_type": "execute",
            "reason": "At least one task-like node is ready to run.",
            "nodes": ready_nodes,
        }

    if review.get("summary", {}).get("active_nodes", 0) > 0:
        return {
            "action_type": "blocked",
            "reason": "Active work exists, but no task-like node is currently executable.",
            "nodes": [],
        }

    return {
        "action_type": "replan",
        "reason": "No active work exists; create or expand the next execution layer.",
        "nodes": [],
    }


def _reachable_decomposition_nodes(root_id: str, edges: list[dict[str, Any]]) -> set[str]:
    children_by_parent: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("edge_type") == "decomposes_to":
            children_by_parent.setdefault(edge["source_id"], []).append(edge["target_id"])
    reachable: set[str] = set()
    stack = list(children_by_parent.get(root_id, []))
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(children_by_parent.get(node_id, []))
    return reachable


def _active_leaf_action_nodes(active_nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_ids = {node["id"] for node in active_nodes}
    parents_with_active_children = {
        edge["source_id"] for edge in edges
        if edge.get("edge_type") == "decomposes_to" and edge.get("target_id") in active_ids
    }
    return [
        node for node in active_nodes
        if node.get("node_type") in ACTION_NODE_TYPES and node["id"] not in parents_with_active_children
    ]


def _investigation_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for node in nodes:
        for criterion in node.get("acceptance_criteria") or []:
            if not isinstance(criterion, dict):
                continue
            marker = criterion.get("requires_investigation") or criterion.get("needs_investigation")
            if marker is True or str(criterion.get("kind") or "").strip() == "investigation":
                result.append(node)
                break
    return result
