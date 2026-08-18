"""Pure observation-program projections for endogenous planning."""

from __future__ import annotations

from typing import Any, Dict, Mapping


def build_observation_program_entries(
    *,
    uncertainty_ledger: Mapping[str, Any],
    governance_channels: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    """Create observation entries before persistence and lifecycle enrichment."""
    observation_requests = [
        dict(item)
        for item in list(governance_channels.get("observation_requests") or [])
        if isinstance(item, dict)
    ]
    requests_by_target: Dict[str, Dict[str, Any]] = {}
    for request in observation_requests:
        payload = dict(request.get("payload") or {})
        target = str(payload.get("observation_target") or "").strip().lower()
        if target and target not in requests_by_target:
            requests_by_target[target] = request

    entries: list[Dict[str, Any]] = []
    for ledger_entry in list(uncertainty_ledger.get("entries") or []):
        if not isinstance(ledger_entry, dict):
            continue
        target = str(
            ledger_entry.get("observation_target")
            or ledger_entry.get("domain")
            or ""
        ).strip().lower()
        if not target:
            continue
        observation_request = dict(requests_by_target.get(target) or {})
        risk = _clamp_ratio(ledger_entry.get("risk") or 0.0)
        priority = _clamp_ratio(
            risk * 0.72
            + _clamp_ratio(ledger_entry.get("confidence") or 0.0) * 0.18
            + (0.08 if observation_request else 0.0)
        )
        evidence_items = list(ledger_entry.get("evidence") or [])
        recommended_probe = str(ledger_entry.get("recommended_probe") or "").strip()
        entries.append(
            {
                "program_id": f"observe:{target}",
                "target": target,
                "source_domain": ledger_entry.get("domain"),
                "priority": round(priority, 4),
                "risk": round(risk, 4),
                "confidence": round(
                    _clamp_ratio(ledger_entry.get("confidence") or 0.0),
                    4,
                ),
                "recommended_probe": recommended_probe,
                "evidence_goal": (
                    f"Reduce uncertainty around {target} by collecting direct evidence about: "
                    f"{recommended_probe}."
                    if recommended_probe
                    else f"Reduce uncertainty around {target}."
                ),
                "linked_request_signal": observation_request.get("signal_type"),
                "request_message": observation_request.get("message"),
                "supporting_evidence_count": len(evidence_items),
            }
        )
    return entries


def derive_observation_persistence_state(target_stats: Mapping[str, Any]) -> str:
    """Classify one observation target from normalized lifecycle counters."""
    recommended = max(0, int(target_stats.get("recommended") or 0))
    resolved = max(0, int(target_stats.get("resolved") or 0))
    stalled = max(0, int(target_stats.get("stalled") or 0))
    seen = max(0, int(target_stats.get("seen") or 0))
    last_status = str(target_stats.get("last_status") or "").strip().lower()

    if stalled >= 2 or (stalled >= 1 and recommended >= 3):
        return "stalled"
    if resolved >= 2 and resolved >= recommended:
        return "stabilizing"
    if recommended >= 3 or seen >= 3:
        return "persistent"
    if last_status == "resolved":
        return "cooling"
    return "emerging"


def project_observation_program(
    entries_seed: list[Mapping[str, Any]],
    *,
    target_stats: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Add lifecycle state to entries and return the read-model contract."""
    entries: list[Dict[str, Any]] = []
    for raw_entry in entries_seed:
        entry = dict(raw_entry)
        target = str(entry.get("target") or "").strip().lower()
        target_memory = dict(target_stats.get(target) or {})
        persistence_state = derive_observation_persistence_state(target_memory)
        entry.update(
            {
                "persistence_state": persistence_state,
                "last_status": target_memory.get("last_status"),
                "seen_count": max(0, int(target_memory.get("seen") or 0)),
                "recommended_count": max(
                    0, int(target_memory.get("recommended") or 0)
                ),
                "resolved_count": max(0, int(target_memory.get("resolved") or 0)),
                "stalled_count": max(0, int(target_memory.get("stalled") or 0)),
                "recommended_next_step": (
                    "collect_observation"
                    if float(entry.get("risk") or 0.0) >= 0.45
                    or persistence_state in {"persistent", "stalled"}
                    else "monitor"
                ),
            }
        )
        entries.append(entry)

    entries.sort(key=lambda item: item.get("priority") or 0.0, reverse=True)
    summary = (
        f"The endogenous core has prepared {len(entries)} observation target(s); "
        f"highest priority target is {entries[0]['target']}."
        if entries
        else "The endogenous core does not currently require an explicit observation program."
    )
    return {
        "summary": summary,
        "active_count": len(entries),
        "highest_priority_target": entries[0]["target"] if entries else None,
        "entries": entries[:6],
    }


def _clamp_ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "build_observation_program_entries",
    "derive_observation_persistence_state",
    "project_observation_program",
]
