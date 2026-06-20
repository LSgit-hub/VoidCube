from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = "memai.llm.v1"


TASK_RESPONSE_SCHEMAS = {
    "extractor.events": '{"events": [{"title": str, "summary": str, "event_kind": str, "impact_scope": str, "topics": [str], "entities": [str], "source_turns": [str], "time_hint": str, "importance": number, "confidence": number, "main_or_side": str, "novelty": number}]}',
    "scholar.scene": '{"title": str, "summary": str, "scene_goal": str, "open_questions": [str]}',
    "scholar.arc": '{"title": str, "summary": str, "arc_goal": str, "drivers": [str], "obstacles": [str], "classification_reason": [str], "main_or_side": str, "status": str, "arc_state": str}',
    "scholar.revision": '{"title": str, "summary": str, "importance": number, "confidence": number}',
}


@dataclass(slots=True)
class LLMProtocolMetadata:
    version: str
    task: str
    response_schema: str
    response_format: str = "json_object"

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "task": self.task,
            "response_schema": self.response_schema,
            "response_format": self.response_format,
        }


def resolve_response_schema(task: str, fallback: str | None = None) -> str:
    return TASK_RESPONSE_SCHEMAS.get(task, fallback or "{}")


def build_protocol_payload(
    *,
    task: str | None,
    user_payload: dict[str, Any],
    response_schema: str | None = None,
) -> dict[str, Any]:
    if task is None:
        return dict(user_payload)
    metadata = LLMProtocolMetadata(
        version=PROTOCOL_VERSION,
        task=task,
        response_schema=resolve_response_schema(task, response_schema),
    )
    payload = dict(user_payload)
    payload["protocol"] = metadata.to_dict()
    return payload


def unwrap_protocol_response(
    payload: dict[str, Any],
    *,
    task: str | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Protocol response must be a JSON object")

    for key in ("result", "output", "response"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            if _task_matches(payload, task):
                return candidate

    if "protocol" in payload and isinstance(payload["protocol"], dict):
        stripped = {key: value for key, value in payload.items() if key != "protocol"}
        if stripped:
            return stripped

    if "protocol_version" in payload or "task" in payload:
        stripped = {
            key: value
            for key, value in payload.items()
            if key not in {"protocol_version", "task", "response_schema"}
        }
        if stripped:
            return stripped

    return payload


def _task_matches(payload: dict[str, Any], task: str | None) -> bool:
    if task is None:
        return True
    protocol = payload.get("protocol")
    if isinstance(protocol, dict):
        response_task = protocol.get("task")
        return response_task in {None, task}
    response_task = payload.get("task")
    return response_task in {None, task}
