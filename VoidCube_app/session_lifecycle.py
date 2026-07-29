"""Shared session lifecycle transitions for front-end adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence
import uuid

from VoidCube_app.session_identity import generate_session_id


Message = dict[str, Any]


class SessionRepository(Protocol):
    """Persistence operations required by session lifecycle use cases."""

    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    def get_messages_as_conversation(self, session_id: str) -> list[Message]: ...

    def create_session(
        self,
        session_id: str,
        source: str,
        model: str | None = None,
        model_config: Mapping[str, Any] | None = None,
        parent_session_id: str | None = None,
    ) -> str: ...

    def end_session(self, session_id: str, end_reason: str) -> None: ...

    def reopen_session(self, session_id: str) -> None: ...

    def get_session_title(self, session_id: str) -> str | None: ...

    def get_next_title_in_lineage(self, base_title: str) -> str: ...

    def set_session_title(self, session_id: str, title: str) -> bool: ...

    def append_message(self, **message: Any) -> int: ...

    def truncate_last_user_turn(self, session_id: str) -> int: ...

    def sanitize_title(self, title: str | None) -> str | None: ...

    def get_session_by_title(self, title: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class SessionLifecycleState:
    """UI-independent state produced by a session transition."""

    session_id: str
    session_start: datetime
    conversation_history: tuple[Message, ...]
    resumed: bool
    pending_title: str | None = None


class SessionHydrationStatus(str, Enum):
    MISSING = "missing"
    EMPTY = "empty"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class SessionHydration:
    """Stored metadata and history loaded for an adapter runtime."""

    session_id: str
    status: SessionHydrationStatus
    metadata: Mapping[str, Any] | None = None
    conversation_history: tuple[Message, ...] = ()


class HistoryMutationStatus(str, Enum):
    EMPTY = "empty"
    NO_USER_MESSAGE = "no_user_message"
    PERSISTENCE_FAILED = "persistence_failed"
    APPLIED = "applied"


@dataclass(frozen=True, slots=True)
class HistoryMutationResult:
    """Result of removing the latest user turn from memory and persistence."""

    status: HistoryMutationStatus
    conversation_history: tuple[Message, ...]
    removed_messages: tuple[Message, ...] = ()
    user_message: Any = None
    persisted_removed_count: int = 0
    persistence_error: str = ""

    def hydration(
        self,
        *,
        session_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionHydration:
        return SessionHydration(
            session_id=session_id,
            status=(
                SessionHydrationStatus.READY
                if self.conversation_history
                else SessionHydrationStatus.EMPTY
            ),
            metadata=metadata,
            conversation_history=self.conversation_history,
        )


class SessionTitleStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    CURRENT = "current"
    PENDING = "pending"
    UNSET = "unset"
    UPDATED = "updated"
    QUEUED = "queued"
    CONFLICT = "conflict"
    INVALID = "invalid"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class SessionTitleResult:
    status: SessionTitleStatus
    session_id: str
    title: str | None = None
    conflicting_session_id: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class ResumeSessionResult:
    state: SessionLifecycleState
    metadata: Mapping[str, Any]

    @property
    def hydration(self) -> SessionHydration:
        return SessionHydration(
            session_id=self.state.session_id,
            status=(
                SessionHydrationStatus.READY
                if self.state.conversation_history
                else SessionHydrationStatus.EMPTY
            ),
            metadata=self.metadata,
            conversation_history=self.state.conversation_history,
        )


@dataclass(frozen=True, slots=True)
class BranchSessionResult:
    state: SessionLifecycleState
    parent_session_id: str
    title: str
    copied_message_count: int


class SessionNotFoundError(LookupError):
    """Raised when a requested session does not exist."""


class SessionAlreadyActiveError(ValueError):
    """Raised when a resume target is already active."""


def _end_session_best_effort(
    repository: SessionRepository | None,
    session_id: str,
    reason: str,
) -> None:
    if repository is None or not session_id:
        return
    try:
        repository.end_session(session_id, reason)
    except Exception:
        pass


def _load_conversation_history(
    repository: SessionRepository,
    session_id: str,
) -> tuple[Message, ...]:
    restored = repository.get_messages_as_conversation(session_id) or []
    return tuple(
        message for message in restored if message.get("role") != "session_meta"
    )


def hydrate_session(
    *,
    repository: SessionRepository,
    session_id: str,
) -> SessionHydration:
    """Load and reopen one persisted session without adapter rendering."""
    metadata = repository.get_session(session_id)
    if metadata is None:
        return SessionHydration(
            session_id=session_id,
            status=SessionHydrationStatus.MISSING,
        )

    history = _load_conversation_history(repository, session_id)
    try:
        repository.reopen_session(session_id)
    except Exception:
        pass
    return SessionHydration(
        session_id=session_id,
        status=(
            SessionHydrationStatus.READY
            if history
            else SessionHydrationStatus.EMPTY
        ),
        metadata=metadata,
        conversation_history=history,
    )


def remove_last_user_turn(
    conversation_history: Sequence[Message],
    *,
    repository: SessionRepository | None = None,
    session_id: str = "",
) -> HistoryMutationResult:
    """Remove the last user message and every message that follows it."""
    history = tuple(conversation_history)
    if not history:
        return HistoryMutationResult(
            status=HistoryMutationStatus.EMPTY,
            conversation_history=(),
        )

    last_user_index = next(
        (
            index
            for index in range(len(history) - 1, -1, -1)
            if history[index].get("role") == "user"
        ),
        None,
    )
    if last_user_index is None:
        return HistoryMutationResult(
            status=HistoryMutationStatus.NO_USER_MESSAGE,
            conversation_history=history,
        )

    persisted_removed_count = 0
    if repository is not None and session_id:
        try:
            persisted_removed_count = repository.truncate_last_user_turn(session_id)
        except Exception as exc:
            return HistoryMutationResult(
                status=HistoryMutationStatus.PERSISTENCE_FAILED,
                conversation_history=history,
                persistence_error=str(exc),
            )

    removed = history[last_user_index:]
    return HistoryMutationResult(
        status=HistoryMutationStatus.APPLIED,
        conversation_history=history[:last_user_index],
        removed_messages=removed,
        user_message=history[last_user_index].get("content", ""),
        persisted_removed_count=persisted_removed_count,
    )


def get_session_title(
    *,
    repository: SessionRepository | None,
    session_id: str,
    pending_title: str | None,
) -> SessionTitleResult:
    if repository is None:
        return SessionTitleResult(SessionTitleStatus.UNAVAILABLE, session_id)
    session = repository.get_session(session_id)
    if session and session.get("title"):
        return SessionTitleResult(
            SessionTitleStatus.CURRENT,
            session_id,
            title=str(session["title"]),
        )
    if pending_title:
        return SessionTitleResult(
            SessionTitleStatus.PENDING,
            session_id,
            title=pending_title,
        )
    return SessionTitleResult(SessionTitleStatus.UNSET, session_id)


def set_session_title(
    *,
    repository: SessionRepository | None,
    session_id: str,
    raw_title: str,
) -> SessionTitleResult:
    if repository is None:
        return SessionTitleResult(SessionTitleStatus.UNAVAILABLE, session_id)
    try:
        title = repository.sanitize_title(raw_title)
    except ValueError as exc:
        return SessionTitleResult(
            SessionTitleStatus.INVALID,
            session_id,
            error=str(exc),
        )
    if not title:
        return SessionTitleResult(SessionTitleStatus.INVALID, session_id)

    if repository.get_session(session_id):
        try:
            updated = repository.set_session_title(session_id, title)
        except ValueError as exc:
            return SessionTitleResult(
                SessionTitleStatus.CONFLICT,
                session_id,
                title=title,
                error=str(exc),
            )
        return SessionTitleResult(
            SessionTitleStatus.UPDATED if updated else SessionTitleStatus.NOT_FOUND,
            session_id,
            title=title,
        )

    existing = repository.get_session_by_title(title)
    if existing:
        return SessionTitleResult(
            SessionTitleStatus.CONFLICT,
            session_id,
            title=title,
            conflicting_session_id=str(existing.get("id") or ""),
        )
    return SessionTitleResult(SessionTitleStatus.QUEUED, session_id, title=title)


def start_new_session(
    *,
    repository: SessionRepository | None,
    current_session_id: str,
    started_at: datetime,
    source: str,
    model: str,
    model_config: Mapping[str, Any],
    create_record: bool,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> SessionLifecycleState:
    """End the current session and return a fresh empty session state."""
    _end_session_best_effort(repository, current_session_id, "new_session")
    session_id = generate_session_id(started_at, uuid_factory=uuid_factory)

    if repository is not None and create_record:
        try:
            repository.create_session(
                session_id=session_id,
                source=source,
                model=model,
                model_config=model_config,
            )
        except Exception:
            pass

    return SessionLifecycleState(
        session_id=session_id,
        session_start=started_at,
        conversation_history=(),
        resumed=False,
    )


def resume_session(
    *,
    repository: SessionRepository,
    current_session_id: str,
    target_session_id: str,
    session_start: datetime,
) -> ResumeSessionResult:
    """Load and reopen an existing session selected by an adapter."""
    metadata = repository.get_session(target_session_id)
    if metadata is None:
        raise SessionNotFoundError(target_session_id)
    if target_session_id == current_session_id:
        raise SessionAlreadyActiveError(target_session_id)

    _end_session_best_effort(repository, current_session_id, "resumed_other")
    history = _load_conversation_history(repository, target_session_id)
    try:
        repository.reopen_session(target_session_id)
    except Exception:
        pass

    return ResumeSessionResult(
        state=SessionLifecycleState(
            session_id=target_session_id,
            session_start=session_start,
            conversation_history=history,
            resumed=True,
        ),
        metadata=metadata,
    )


def branch_session(
    *,
    repository: SessionRepository,
    current_session_id: str,
    conversation_history: Sequence[Message],
    started_at: datetime,
    requested_title: str,
    source: str,
    model: str,
    model_config: Mapping[str, Any],
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> BranchSessionResult:
    """Persist a copy of the current transcript as a child session."""
    if requested_title:
        title = requested_title
    else:
        current_title = repository.get_session_title(current_session_id)
        title = repository.get_next_title_in_lineage(current_title or "branch")

    session_id = generate_session_id(started_at, uuid_factory=uuid_factory)
    _end_session_best_effort(repository, current_session_id, "branched")
    repository.create_session(
        session_id=session_id,
        source=source,
        model=model,
        model_config=model_config,
        parent_session_id=current_session_id,
    )

    copied_message_count = 0
    for message in conversation_history:
        try:
            repository.append_message(
                session_id=session_id,
                role=message.get("role", "user"),
                content=message.get("content"),
                tool_name=message.get("tool_name") or message.get("name"),
                tool_calls=message.get("tool_calls"),
                tool_call_id=message.get("tool_call_id"),
                reasoning=message.get("reasoning"),
            )
            copied_message_count += 1
        except Exception:
            pass

    try:
        repository.set_session_title(session_id, title)
    except Exception:
        pass

    return BranchSessionResult(
        state=SessionLifecycleState(
            session_id=session_id,
            session_start=started_at,
            conversation_history=tuple(conversation_history),
            resumed=True,
        ),
        parent_session_id=current_session_id,
        title=title,
        copied_message_count=copied_message_count,
    )
