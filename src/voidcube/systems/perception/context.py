"""Build bounded, text-only perception context for later API-B calls."""

from __future__ import annotations

from typing import Any, Iterable

from .models import SceneState, TimelineSegment


def build_perception_context(
    *,
    level: str,
    user_query: str,
    scene: SceneState | None = None,
    segments: Iterable[TimelineSegment] = (),
    max_segments: int = 24,
    max_chars: int = 24000,
) -> dict[str, Any]:
    """Create L0/L1/L2 text context with an explicit no-frame boundary."""

    normalized = str(level or "L0").strip().upper()
    if normalized not in {"L0", "L1", "L2"}:
        raise ValueError("perception context level must be L0, L1, or L2")
    payload: dict[str, Any] = {
        "schema_version": "perception-context.v1",
        "context_level": normalized,
        "user_query": str(user_query or "").strip()[:4000],
        "scene_state": scene.as_dict() if scene is not None else {},
        "timeline_segments": [],
        "uncertainties": [],
        "privacy": {
            "raw_frames_included": False,
            "raw_ocr_included": False,
            "control_tools_allowed": False,
        },
    }
    if normalized in {"L1", "L2"}:
        rows = []
        for segment in list(segments)[-max(1, min(100, int(max_segments))):]:
            item = segment.as_dict()
            item.pop("source_record_ids", None)
            rows.append(item)
        payload["timeline_segments"] = rows
    if normalized == "L0":
        payload["timeline_segments"] = []
    payload["uncertainties"] = [
        item
        for segment in payload["timeline_segments"]
        for item in list(segment.get("unknowns") or ()) + list(segment.get("coverage_gaps") or ())
    ][:40]
    # Bound serialized content without ever including image/frame fields.
    while len(str(payload)) > max(1000, int(max_chars)) and payload["timeline_segments"]:
        payload["timeline_segments"].pop(0)
    return payload


__all__ = ["build_perception_context"]
