"""VoidCube tool-registry adapters for Goal Manager."""

from __future__ import annotations

import json
import logging
from typing import Any

from .client import GoalClient, GoalServiceError
from .schemas import SCHEMAS

logger = logging.getLogger(__name__)
TOOL_NAMES = tuple(SCHEMAS)
READ_TOOLS = {
    "goal_project_get", "goal_get_context", "goal_graph_query", "goal_next_actions",
    "goal_protocol_next_action", "goal_plan_review", "goal_lifecycle_get",
}
NON_IDEMPOTENT_WRITE_TOOLS = {
    "goal_project_create", "goal_node_create", "goal_edge_create", "goal_batch_apply",
    "goal_attach_evidence", "goal_rollback", "goal_redo", "goal_intent_contract_set",
    "goal_replan", "goal_record_execution_result", "goal_record_observation",
    "goal_verify_evidence", "goal_apply_evidence_verification", "goal_submit_for_review",
}


def _handle(tool_name: str, args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        result = GoalClient().call_tool(tool_name, args)
        return json.dumps({"success": True, "data": result}, ensure_ascii=False)
    except GoalServiceError as exc:
        payload = exc.payload if isinstance(exc.payload, dict) else {"detail": str(exc.payload)}
        return json.dumps(
            {"success": False, "status_code": exc.status_code, **payload},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.warning("Goal tool %s failed: %s", tool_name, exc)
        return json.dumps(
            {"success": False, "error": "goal_service_client_error", "detail": type(exc).__name__},
            ensure_ascii=False,
        )


def register_tools(registry: Any) -> None:
    for name, schema in SCHEMAS.items():
        registry.register(
            name=name,
            toolset="goal_manager",
            schema=schema,
            handler=lambda args, _name=name, **kwargs: _handle(_name, args, **kwargs),
            effect="read_only" if name in READ_TOOLS else (
                "non_idempotent_write" if name in NON_IDEMPOTENT_WRITE_TOOLS else "idempotent_write"
            ),
        )
