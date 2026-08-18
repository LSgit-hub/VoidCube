"""Body registry status loading for the Supervisor UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List

from .ui_body_projection import project_body_slot_cards


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUIBodyStatusContext:
    """Body registry callbacks required for one UI status snapshot."""

    inspect_layout: Callable[[], JsonDict]
    load_slot_meta: Callable[[str], Any]


def load_body_status(
    *,
    context: SupervisorUIBodyStatusContext,
    chain_history_projection: List[JsonDict],
) -> JsonDict:
    """Load body-owned registry data before applying the pure slot projection."""

    integrity = context.inspect_layout()
    registry = dict(integrity.get("registry") or {})
    status: JsonDict = {
        "active_slot": registry.get("active_slot"),
        "retired_slot": registry.get("retired_slot"),
        "shell_slot": registry.get("shell_slot"),
        "last_switch_result": dict(registry.get("last_switch_result") or {}),
        "integrity": integrity,
        "slot_cards": [],
    }
    if not registry:
        return status

    slot_metas: Dict[str, JsonDict] = {}
    top_level_entries_by_slot: Dict[str, List[str]] = {}
    for slot_id in list(registry.get("slot_ids") or []):
        try:
            meta = context.load_slot_meta(slot_id).model_dump(mode="json")
        except Exception:
            continue
        slot_metas[slot_id] = meta
        worktree_path = str(meta.get("worktree_path") or "").strip()
        if not worktree_path:
            continue
        try:
            top_level_entries_by_slot[slot_id] = sorted(
                child.name for child in Path(worktree_path).iterdir()
            )[:24]
        except Exception:
            continue

    status["slot_cards"] = project_body_slot_cards(
        registry=registry,
        slot_metas=slot_metas,
        chain_history_projection=chain_history_projection,
        integrity_report=integrity,
        top_level_entries_by_slot=top_level_entries_by_slot,
    )
    return status
