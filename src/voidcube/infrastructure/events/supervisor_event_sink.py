"""Adapters that project autonomous domain events into supervisor observers.

The domain event remains the source value.  This adapter is responsible for
turning it into the JSON-shaped activity record expected by the existing
governance and UI projections, while keeping either projection failure from
preventing the other one from receiving the event.
"""

from __future__ import annotations

import logging
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from ...domain.agent.effect_outcomes import EffectOutcome


def _json_safe(value: Any) -> Any:
    """Return a JSON-compatible copy of common event payload values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe(model_dump(mode="json"))
        except TypeError:
            return _json_safe(model_dump())
    return str(value)


def _event_fields(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return {str(key): _json_safe(value) for key, value in event.items()}
    if is_dataclass(event):
        return _json_safe(event)
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        try:
            result = model_dump(mode="json")
        except TypeError:
            result = model_dump()
        return _json_safe(result)
    return {
        key: _json_safe(value)
        for key, value in vars(event).items()
        if not key.startswith("_")
    }


class SupervisorDomainEventSink:
    """Project one domain event to governance and UI activity observers."""

    def __init__(
        self,
        *,
        record_governance: Callable[[dict[str, Any]], Any] | None = None,
        record_activity: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._record_governance = record_governance
        self._record_activity = record_activity
        self._logger = logger or logging.getLogger(__name__)

    def __call__(self, event: Any) -> EffectOutcome:
        values = _event_fields(event)
        event_type = type(event).__name__
        if isinstance(event, Mapping) and event.get("event_type"):
            event_type = str(event["event_type"])
        task_id = str(values.get("task_id") or "")
        metadata = {
            key: value for key, value in values.items() if key != "task_id"
        }
        if task_id:
            # Governor projections read task identity from metadata; retain it
            # there as well as at the activity payload's top level.
            metadata["task_id"] = task_id
        payload = {
            "event_type": event_type,
            "task_id": task_id,
            "summary": f"自主链路事件：{event_type}",
            "metadata": metadata,
        }

        attempted = 0
        failures: list[str] = []
        if self._record_governance is not None:
            attempted += 1
            try:
                result = self._record_governance(payload)
                if isinstance(result, EffectOutcome) and result.status in {"failed", "degraded"}:
                    failures.append(f"governance: {result.error or result.status}")
            except Exception as exc:  # pragma: no cover - covered via outcome
                failures.append(f"governance: {type(exc).__name__}: {exc}")
                self._logger.warning(
                    "Autonomous governance event publication failed", exc_info=True
                )
        if self._record_activity is not None:
            attempted += 1
            try:
                result = self._record_activity(
                    event_type,
                    scene="planning",
                    summary=payload["summary"],
                    metadata=metadata,
                )
                if isinstance(result, EffectOutcome) and result.status in {"failed", "degraded"}:
                    failures.append(f"activity: {result.error or result.status}")
            except Exception as exc:
                failures.append(f"activity: {type(exc).__name__}: {exc}")
                self._logger.warning(
                    "Autonomous UI event publication failed", exc_info=True
                )

        if not attempted:
            return EffectOutcome(status="skipped", details={"reason": "no_sink"})
        if failures and len(failures) == attempted:
            return EffectOutcome(status="failed", error="; ".join(failures))
        if failures:
            return EffectOutcome(
                status="degraded",
                error="; ".join(failures),
                details={"event_type": event_type},
            )
        return EffectOutcome(status="succeeded", details={"event_type": event_type})


__all__ = ["SupervisorDomainEventSink"]
