"""Minimal shared application runtime for CLI and future frontends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import queue
from typing import Any, Mapping, Sequence
import uuid

from ..domain.contracts.artifacts import Artifact
from ..domain.contracts.events import (
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
from ..domain.contracts.ports import EventSink
from ..domain.contracts.interaction import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalSink,
    ClarificationDecision,
    ClarificationRequest,
    ClarificationSink,
    resolve_approval,
    resolve_clarification,
)
from ..domain.session.identity import generate_session_id
from .sessions import (
    BranchSessionResult,
    HistoryMutationResult,
    ResumeSessionResult,
    SessionHydration,
    SessionHydrationStatus,
    SessionLifecycleState,
    SessionRepository,
    SessionTitleResult,
    SessionTitleStatus,
    branch_session as _branch_session,
    get_session_title as _get_session_title,
    hydrate_session as _hydrate_session,
    remove_last_user_turn as _remove_last_user_turn,
    resume_session as _resume_session,
    set_session_title as _set_session_title,
    start_new_session as _start_new_session,
)
from ..domain.contracts.tool_events import ToolEvent
from ..domain.contracts.turn_queue import TurnInputRoute
from ..domain.contracts.turn import (
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
    pending_title: str | None = None
    session_hydration: SessionHydration | None = None
    agent_running: bool = False
    pending_input_queue: queue.Queue[Any] = field(default_factory=queue.Queue)

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

    def set_pending_title(self, title: str | None) -> None:
        self.state.pending_title = title

    def clear_pending_title(self) -> None:
        self.state.pending_title = None

    def set_session_hydration(self, hydration: SessionHydration) -> None:
        self.state.session_hydration = hydration

    def clear_session_hydration(self) -> None:
        self.state.session_hydration = None

    def continue_session(self, session_id: str, *, session_start: datetime) -> None:
        """Rebind an active conversation to a persisted continuation session."""
        normalized = str(session_id or "").strip()
        if not normalized or normalized == self.state.session_id:
            return
        self.state.session_id = normalized
        self.state.session_start = session_start
        self.clear_session_hydration()

    def load_session_hydration(
        self,
        *,
        repository: SessionRepository | None,
        session_id: str | None = None,
    ) -> tuple[SessionHydration, bool]:
        """Load and cache one hydration result in the shared session state."""
        hydration = self.state.session_hydration
        loaded_now = hydration is None
        if hydration is None:
            hydration = self.hydrate_session(
                repository=repository,
                session_id=session_id,
            )
            if hydration.session_id != self.state.session_id:
                self.continue_session(
                    hydration.session_id,
                    session_start=self.state.session_start,
                )
            self.set_session_hydration(hydration)
        if hydration.status is SessionHydrationStatus.READY:
            self.replace_history(hydration.conversation_history)
        return hydration, loaded_now

    def reset_input_queues(self) -> None:
        """Start a fresh adapter run while retaining shared session identity."""
        self.state.pending_input_queue = queue.Queue()

    def set_agent_running(self, value: bool) -> None:
        self.state.agent_running = bool(value)

    def apply_session_state(self, state: SessionLifecycleState) -> None:
        """Adopt one shared session transition before adapter-side activation."""
        previous_session_id = self.state.session_id
        if previous_session_id != state.session_id:
            self._emit(
                SessionEvent(
                    kind=SessionEventKind.ENDED,
                    session_id=previous_session_id,
                    reason="session_transition",
                )
            )
        self.state.session_id = state.session_id
        self.state.session_start = state.session_start
        self.replace_history(state.conversation_history)
        self.state.resumed = bool(state.resumed)
        self.state.active_turn_id = None
        self.state.pending_title = state.pending_title
        self.clear_session_hydration()
        self.set_agent_running(False)
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

    def start_new_session(
        self,
        *,
        repository: SessionRepository | None,
        started_at: datetime,
        source: str,
        model: str,
        model_config: Mapping[str, Any],
        create_record: bool,
    ) -> SessionLifecycleState:
        """Create the next session transition through the shared use case."""
        return _start_new_session(
            repository=repository,
            current_session_id=self.state.session_id,
            started_at=started_at,
            source=source,
            model=model,
            model_config=model_config,
            create_record=create_record,
            uuid_factory=self._uuid_factory,
        )

    def resume_session(
        self,
        *,
        repository: SessionRepository,
        target_session_id: str,
        session_start: datetime,
    ) -> ResumeSessionResult:
        """Resolve a stored session against the current shared session."""
        return _resume_session(
            repository=repository,
            current_session_id=self.state.session_id,
            target_session_id=target_session_id,
            session_start=session_start,
        )

    def branch_session(
        self,
        *,
        repository: SessionRepository,
        started_at: datetime,
        requested_title: str,
        source: str,
        model: str,
        model_config: Mapping[str, Any],
    ) -> BranchSessionResult:
        """Create a child session from the canonical shared history."""
        return _branch_session(
            repository=repository,
            current_session_id=self.state.session_id,
            conversation_history=self.state.conversation_history,
            started_at=started_at,
            requested_title=requested_title,
            source=source,
            model=model,
            model_config=model_config,
            uuid_factory=self._uuid_factory,
        )

    def hydrate_session(
        self,
        *,
        repository: SessionRepository | None,
        session_id: str | None = None,
    ) -> SessionHydration:
        """Load session history through the shared session read use case."""
        return _hydrate_session(
            repository=repository,
            session_id=session_id or self.state.session_id,
        )

    def remove_last_user_turn(
        self,
        *,
        repository: SessionRepository | None,
    ) -> HistoryMutationResult:
        """Apply one history mutation and update shared state on success."""
        result = _remove_last_user_turn(
            self.state.conversation_history,
            repository=repository,
            session_id=self.state.session_id if repository is not None else "",
        )
        if result.conversation_history != tuple(self.state.conversation_history):
            self.replace_history(result.conversation_history)
        return result

    def get_session_title(
        self,
        *,
        repository: SessionRepository | None,
    ) -> SessionTitleResult:
        return _get_session_title(
            repository=repository,
            session_id=self.state.session_id,
            pending_title=self.state.pending_title,
        )

    def set_session_title(
        self,
        *,
        repository: SessionRepository | None,
        raw_title: str,
    ) -> SessionTitleResult:
        result = _set_session_title(
            repository=repository,
            session_id=self.state.session_id,
            raw_title=raw_title,
        )
        if result.status is SessionTitleStatus.QUEUED:
            self.set_pending_title(result.title)
        return result

    def enqueue_turn_input(
        self,
        payload: Any,
    ) -> TurnInputRoute:
        """Queue input for Scheduler admission without cancelling active work."""
        self.state.pending_input_queue.put(payload)
        return TurnInputRoute.NEXT_TURN

    def begin_turn(
        self,
        user_message: Any,
        *,
        attachments: Sequence[Mapping[str, Any]] = (),
    ) -> TurnInput:
        if self.state.turn_active:
            raise RuntimeError("an application turn is already active")
        turn_id = self._new_turn_id()
        turn_input = _begin_turn(
            self.state.conversation_history,
            user_message,
            attachments=attachments,
        )
        self.state.conversation_history = list(turn_input.conversation_history)
        self.state.active_turn_id = turn_id
        self.set_agent_running(True)
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
        self.set_agent_running(False)

    def abort_turn(self, error: str, *, interrupted: bool = False) -> None:
        """Close a turn that failed before the normal result pipeline ran."""
        if not self.state.turn_active:
            self.set_agent_running(False)
            return
        self.finish_turn(
            TurnOutcome(
                conversation_history=tuple(self.state.conversation_history),
                response="",
                failed=not interrupted,
                partial=False,
                interrupted=interrupted,
                error=str(error or "Turn aborted"),
            ),
            history_applied=True,
        )

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
