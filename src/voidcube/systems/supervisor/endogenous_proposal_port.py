"""Application projections for one endogenous LM generation snapshot."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

from .endogenous_proposals import is_lm_task_generation_enabled


@dataclass(frozen=True, slots=True)
class LmGenerationApplicationState:
    reasoning_state: Dict[str, Any]
    candidate_repass_proposals: Optional[List[Dict[str, Any]]]


def project_lm_generation_application_state(
    *,
    runtime_config: Any,
    state_loader: Optional[Callable[[], Mapping[str, Any]]],
) -> LmGenerationApplicationState:
    if not is_lm_task_generation_enabled(runtime_config) or state_loader is None:
        return LmGenerationApplicationState({}, None)
    try:
        state = dict(state_loader() or {})
        context = dict(state.get("context") or {})
    except Exception:
        return LmGenerationApplicationState({}, None)

    status = str(context.get("status") or "").strip()
    reasoning_state = (
        context
        if status.lower() == "completed"
        else {}
    )
    if not status:
        return LmGenerationApplicationState(reasoning_state, None)
    try:
        proposals = [
            deepcopy(dict(item))
            for item in list(state.get("proposals") or [])
            if isinstance(item, dict)
        ]
    except Exception:
        proposals = []
    return LmGenerationApplicationState(reasoning_state, proposals)
