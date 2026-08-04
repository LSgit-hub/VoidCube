"""Minimal shared application runtime for CLI and future frontends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence
import uuid

from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.contracts.events import (
    ApplicationEvent,
    ApprovalRequested,
    ArtifactCreated,
    ClarificationRequested,
    MessageDelta,
    SessionEvent,
    SessionEventKind,
    UsageUpdated,
    TurnEvent,
    TurnEventKind,
)
from VoidCube_app.contracts.ports import EventSink
from VoidCube_app.interaction_contract import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalSink,
    ClarificationDecision,
    ClarificationRequest,
    ClarificationSink,
    resolve_approval,
    resolve_clarification,
)
from VoidCube_app.session_identity import generate_session_id
from VoidCube_app.tool_events import ToolEvent
from VoidCube_app.turn_contract import (
    Message,
    TurnInput,
    TurnOutcome,
    begin_turn as _begin_turn,
)


@dataclass(slots=True)
class ApplicationState:
    """Canonical UI-independent session and turn state."""

    session_id: str
    session_start: datetime
    conversation_history: list[Message] = field(default_factory=list)
    resumed: bool = False
    active_turn_id: str | None = None

    @property
    def turn_active(self) -> bool:
        return self.active_turn_id is not None


class ApplicationRuntime:
    """Own shared session/turn state and publish adapter-neutral events."""

    def __init__(
        self,
        state: ApplicationState,
        *,
        event_sink: EventSink | None = None,
        uuid_factory=uuid.uuid4,
    ) -> None:
        self.state = state
        self._event_sink = event_sink
        self._uuid_factory = uuid_factory

        self._emit(
            SessionEvent(
                kind=(
                    SessionEventKind.RESUMED
                    if state.resumed
                    else SessionEventKind.STARTED
                ),
                session_id=state.session_id,
                resumed=state.resumed,
            )
        )

    @classmethod
    def create(
        cls,
        *,
        session_id: str | None = None,
        session_start: datetime,
        conversation_history: Sequence[Mapping[str, Any]] = (),
        resumed: bool = False,
        event_sink: EventSink | None = None,
        uuid_factory=uuid.uuid4,
    ) -> "ApplicationRuntime":
        identity = str(session_id or "").strip() or generate_session_id(
            session_start,
            uuid_factory=uuid_factory,
        )
        state = ApplicationState(
            session_id=identity,
            session_start=session_start,
            conversation_history=(
                conversation_history
                if isinstance(conversation_history, list)
                else [dict(message) for message in conversation_history]
            ),
            resumed=bool(resumed),
        )
        return cls(
            state,
            event_sink=event_sink,
            uuid_factory=uuid_factory,
        )

    def replace_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        self.state.conversation_history = [dict(message) for message in history]

    def begin_turn(self, user_message: Any) -> TurnInput:
        if self.state.turn_active:
            raise RuntimeError("an application turn is already active")
        turn_id = self._new_turn_id()
        turn_input = _begin_turn(self.state.conversation_history, user_message)
        self.state.conversation_history = list(turn_input.conversation_history)
        self.state.active_turn_id = turn_id
        self._emit(
            TurnEvent(
                kind=TurnEventKind.STARTED,
                session_id=self.state.session_id,
                turn_id=turn_id,
            )
        )
        return turn_input

    def finish_turn(self, outcome: TurnOutcome, *, history_applied: bool = False) -> None:
        turn_id = self.state.active_turn_id or self._new_turn_id()
        if not history_applied:
            self.replace_history(outcome.conversation_history)
        if outcome.interrupted:
            kind = TurnEventKind.INTERRUPTED
        elif outcome.failed or outcome.partial:
            kind = TurnEventKind.FAILED
        else:
            kind = TurnEventKind.COMPLETED
        self._emit(
            TurnEvent(
                kind=kind,
                session_id=self.state.session_id,
                turn_id=turn_id,
                error=outcome.error,
            )
        )
        self.state.active_turn_id = None

    def tool_event_sink(self, event: ToolEvent) -> None:
        self._emit(event)
        for artifact in event.artifacts:
            self.artifact_sink(artifact)

    def message_delta_sink(self, text: str) -> None:
        turn_id = self.state.active_turn_id or ""
        self._emit(
            MessageDelta(
                session_id=self.state.session_id,
                turn_id=turn_id,
                text=str(text or ""),
            )
        )

    def usage_sink(self, usage: Mapping[str, Any]) -> None:
        self._emit(
            UsageUpdated(
                session_id=self.state.session_id,
                turn_id=self.state.active_turn_id or "",
                usage=dict(usage),
            )
        )

    def artifact_sink(self, artifact: Artifact) -> None:
        self._emit(
            ArtifactCreated(
                session_id=self.state.session_id,
                turn_id=self.state.active_turn_id or "",
                artifact=artifact,
            )
        )

    def resolve_approval(
        self,
        request: ApprovalRequest,
        sink: ApprovalSink | None,
    ) -> ApprovalDecision:
        self._emit(
            ApprovalRequested(
                session_id=self.state.session_id,
                turn_id=self.state.active_turn_id or "",
                request=request,
            )
        )
        return resolve_approval(request, sink)

    def resolve_clarification(
        self,
        request: ClarificationRequest,
        sink: ClarificationSink | None,
    ) -> ClarificationDecision:
        self._emit(
            ClarificationRequested(
                session_id=self.state.session_id,
                turn_id=self.state.active_turn_id or "",
                request=request,
            )
        )
        return resolve_clarification(request, sink)

    def approval_sink(self, sink: ApprovalSink | None) -> ApprovalSink:
        return lambda request: self.resolve_approval(request, sink)

    def clarification_sink(self, sink: ClarificationSink | None) -> ClarificationSink:
        return lambda request: self.resolve_clarification(request, sink)

    def end_session(self, reason: str = "") -> None:
        self._emit(
            SessionEvent(
                kind=SessionEventKind.ENDED,
                session_id=self.state.session_id,
                reason=str(reason or ""),
            )
        )

    def _new_turn_id(self) -> str:
        return self._uuid_factory().hex

    def _emit(self, event: ApplicationEvent) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event)
        except Exception:
            # Adapter event failures must not change the turn state machine.
            return


__all__ = ["ApplicationRuntime", "ApplicationState"]
