"""UI-independent events emitted by the agent tool runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.contracts.execution import ExecutionState

class ToolEventKind(str, Enum):
    STARTED = "tool.started"
    SUCCEEDED = "tool.succeeded"
    FAILED = "tool.failed"
    CANCELLED = "tool.cancelled"
    TIMED_OUT = "tool.timed_out"
    UNKNOWN = "tool.unknown"
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
    state: ExecutionState | None = None
    text: str = ""
    artifacts: tuple[Artifact, ...] = ()

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
    def terminal(
        cls,
        *,
        call_id: str,
        name: str,
        arguments: Mapping[str, Any],
        result: str,
        duration: float,
        state: ExecutionState,
        artifacts: tuple[Artifact, ...] = (),
    ) -> "ToolEvent":
        kinds = {
            ExecutionState.SUCCEEDED: ToolEventKind.SUCCEEDED,
            ExecutionState.FAILED: ToolEventKind.FAILED,
            ExecutionState.CANCELLED: ToolEventKind.CANCELLED,
            ExecutionState.TIMED_OUT: ToolEventKind.TIMED_OUT,
            ExecutionState.UNKNOWN: ToolEventKind.UNKNOWN,
        }
        return cls(
            kind=kinds[state],
            call_id=call_id,
            name=name,
            arguments=MappingProxyType(dict(arguments)),
            result=result,
            duration=max(0.0, float(duration)),
            state=state,
            artifacts=tuple(artifacts),
        )

    @classmethod
    def reasoning(cls, text: str) -> "ToolEvent":
        return cls(kind=ToolEventKind.REASONING, text=str(text or ""))

    @classmethod
    def subagent_progress(cls, text: str) -> "ToolEvent":
        return cls(kind=ToolEventKind.SUBAGENT_PROGRESS, text=str(text or ""))


class ToolEventSink(Protocol):
    def __call__(self, event: ToolEvent) -> None: ...


TERMINAL_TOOL_EVENT_KINDS = frozenset(
    {
        ToolEventKind.SUCCEEDED,
        ToolEventKind.FAILED,
        ToolEventKind.CANCELLED,
        ToolEventKind.TIMED_OUT,
        ToolEventKind.UNKNOWN,
    }
)
