"""UI-independent events emitted by the agent tool runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol


class ToolEventKind(str, Enum):
    STARTED = "tool.started"
    COMPLETED = "tool.completed"
    REASONING = "reasoning.available"
    SUBAGENT_PROGRESS = "subagent.progress"


@dataclass(frozen=True, slots=True)
class ToolEvent:
    kind: ToolEventKind
    call_id: str = ""
    name: str = ""
    arguments: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    preview: str = ""
    result: str = ""
    duration: float = 0.0
    is_error: bool = False
    text: str = ""

    @classmethod
    def started(
        cls,
        *,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
        preview: str = "",
    ) -> "ToolEvent":
        return cls(
            kind=ToolEventKind.STARTED,
            call_id=call_id,
            name=name,
            arguments=MappingProxyType(dict(arguments)),
            preview=preview,
        )

    @classmethod
    def completed(
        cls,
        *,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
        result: str,
        duration: float,
        is_error: bool,
    ) -> "ToolEvent":
        return cls(
            kind=ToolEventKind.COMPLETED,
            call_id=call_id,
            name=name,
            arguments=MappingProxyType(dict(arguments)),
            result=result,
            duration=max(0.0, float(duration)),
            is_error=bool(is_error),
        )

    @classmethod
    def reasoning(cls, text: str) -> "ToolEvent":
        return cls(kind=ToolEventKind.REASONING, text=str(text or ""))

    @classmethod
    def subagent_progress(cls, text: str) -> "ToolEvent":
        return cls(kind=ToolEventKind.SUBAGENT_PROGRESS, text=str(text or ""))


class ToolEventSink(Protocol):
    def __call__(self, event: ToolEvent) -> None: ...
