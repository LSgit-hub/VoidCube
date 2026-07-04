# NOTE(O-04): This module is the canonical entry point for runtime task profile
# normalization. All callers (planning_runtime, task_queue, gateway, governor,
# facade, governor_bridge) must derive governance_task_type/task_family/
# execution_kind from here — never replicate the logic locally.
# See baseline §8.

from __future__ import annotations

from typing import Dict, Optional


def _normalized_optional(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    return normalized or None


def normalize_runtime_task_family(
    value: Optional[str],
    *,
    default: Optional[str] = "general_self_evolution",
) -> Optional[str]:
    normalized = _normalized_optional(value)
    if normalized is None:
        return default
    if normalized in {"body_upgrade", "body_improvement"}:
        return "body_upgrade"
    if normalized == "body_switch":
        return "body_switch"
    if normalized in {"memory", "memory_task", "memory_maintenance"}:
        return "memory_maintenance"
    if normalized in {"self_learning", "self_learning_followup"}:
        return "self_learning"
    if normalized == "user":
        return "user"
    return "general_self_evolution"


def normalize_runtime_task_type(
    value: Optional[str],
    *,
    default: Optional[str] = None,
) -> Optional[str]:
    normalized = normalize_runtime_task_family(value, default=None)
    if normalized is None:
        return default
    if normalized in {"body_upgrade", "body_switch", "general_self_evolution"}:
        return "self_evolution"
    return normalized


def derive_runtime_task_profile(
    *,
    task_type: Optional[str] = None,
    governance_task_type: Optional[str] = None,
    task_family: Optional[str] = None,
    execution_kind: Optional[str] = None,
    kind: Optional[str] = None,
    default_task_family: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    normalized_task_type = _normalized_optional(task_type)
    normalized_governance_task_type = _normalized_optional(governance_task_type)
    normalized_task_family = _normalized_optional(task_family)
    normalized_execution_kind = _normalized_optional(execution_kind)
    normalized_kind = _normalized_optional(kind)

    if normalized_execution_kind is None and normalized_kind is not None:
        normalized_execution_kind = normalized_kind
    if normalized_execution_kind is not None:
        normalized_execution_kind = normalize_runtime_task_family(
            normalized_execution_kind,
            default=None,
        )

    if normalized_task_family is not None:
        normalized_task_family = normalize_runtime_task_family(
            normalized_task_family,
            default=default_task_family,
        )
    else:
        family_hint = (
            normalized_execution_kind
            or normalized_governance_task_type
            or normalized_task_type
        )
        if family_hint is not None or default_task_family is not None:
            normalized_task_family = normalize_runtime_task_family(
                family_hint,
                default=default_task_family,
            )

    if normalized_governance_task_type is None:
        governance_hint = normalized_task_family or normalized_task_type
        if governance_hint is not None:
            normalized_governance_task_type = normalize_runtime_task_type(governance_hint)

    if normalized_governance_task_type in {"self_evolution", "memory_maintenance"}:
        if normalized_execution_kind is None and normalized_task_family is not None:
            normalized_execution_kind = normalized_task_family
    else:
        normalized_execution_kind = None

    return {
        "governance_task_type": normalized_governance_task_type,
        "task_family": normalized_task_family,
        "execution_kind": normalized_execution_kind,
    }


def resolve_broad_task_type(
    *,
    task_type: Optional[str] = None,
    governance_task_type: Optional[str] = None,
    task_family: Optional[str] = None,
    execution_kind: Optional[str] = None,
    source: Optional[str] = None,
) -> str:
    explicit = _normalized_optional(task_type)
    if explicit:
        return explicit

    runtime_task_profile = derive_runtime_task_profile(
        governance_task_type=governance_task_type,
        task_family=task_family,
        execution_kind=execution_kind,
        default_task_family="general_self_evolution",
    )
    normalized_source = _normalized_optional(source) or "self_learning"
    normalized_task_family = runtime_task_profile["task_family"] or "general_self_evolution"

    if normalized_task_family == "self_learning":
        return "self_learning_followup" if normalized_source == "self_learning" else "self_learning"
    if normalized_task_family == "memory_maintenance":
        return "memory_maintenance"
    if normalized_task_family == "user":
        return "user"
    return "self_evolution"
