"""Clarification tool routed through the shared interaction contract."""

from __future__ import annotations

import json
from typing import Any, Sequence

from ...domain.contracts.interaction import (
    ClarificationRequest,
    ClarificationSink,
    ClarificationStatus,
    resolve_clarification,
)
from .registry import registry


CLARIFY_SCHEMA = {
    "name": "clarify",
    "description": (
        "Ask for clarification or more details when the user's request is "
        "genuinely ambiguous or incomplete."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The clarification question to ask the user",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional choices to present to the user",
            },
        },
        "required": ["question"],
    },
}


def clarify_tool(
    question: str,
    options: Sequence[str] | None = None,
    *,
    sink: ClarificationSink | None = None,
) -> str:
    """Request clarification and return a structured tool result."""
    request = ClarificationRequest.create(question, options)
    decision = resolve_clarification(request, sink)
    payload: dict[str, Any] = {
        "success": decision.status is not ClarificationStatus.UNAVAILABLE,
        "status": decision.status.value,
        "question": request.question,
        "options": list(request.options),
        "answer": decision.response_for_agent(),
    }
    if decision.reason:
        payload["reason"] = decision.reason
    return json.dumps(payload, ensure_ascii=False)


def _handle_clarify(args, **_kwargs):
    return clarify_tool(
        question=args.get("question", ""),
        options=args.get("options"),
    )


registry.register(
    name="clarify",
    toolset="assistant",
    schema=CLARIFY_SCHEMA,
    handler=_handle_clarify,
    effect="read_only",
)
