from __future__ import annotations

from datetime import datetime
import uuid

import pytest

from VoidCube_app.session_lifecycle import (
    HistoryMutationStatus,
    SessionAlreadyActiveError,
    SessionHydrationStatus,
    SessionNotFoundError,
    SessionTitleStatus,
    branch_session,
    hydrate_session,
    get_session_title,
    remove_last_user_turn,
    set_session_title,
    resume_session,
    start_new_session,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _fixed_uuid() -> uuid.UUID:
    return uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")


class _SessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.messages: dict[str, list[dict[str, object]]] = {}
        self.events: list[tuple[object, ...]] = []
        self.failed_content: set[object] = set()

    def get_session(self, session_id: str):
        self.events.append(("get_session", session_id))
        return self.sessions.get(session_id)

    def get_messages_as_conversation(self, session_id: str):
        self.events.append(("get_messages", session_id))
        return list(self.messages.get(session_id, []))

    def create_session(self, **values):
        self.events.append(("create_session", dict(values)))
        session_id = str(values["session_id"])
        self.sessions[session_id] = dict(values)
        return session_id

    def end_session(self, session_id: str, end_reason: str) -> None:
        self.events.append(("end_session", session_id, end_reason))

    def reopen_session(self, session_id: str) -> None:
        self.events.append(("reopen_session", session_id))

    def get_session_title(self, session_id: str):
        self.events.append(("get_session_title", session_id))
        return self.sessions.get(session_id, {}).get("title")

    @staticmethod
    def sanitize_title(title):
        cleaned = " ".join(str(title or "").split())
        if len(cleaned) > 100:
            raise ValueError("Title too long")
        return cleaned or None

    def get_session_by_title(self, title: str):
        self.events.append(("get_session_by_title", title))
        return next(
            (session for session in self.sessions.values() if session.get("title") == title),
            None,
        )

    def get_next_title_in_lineage(self, base_title: str) -> str:
        self.events.append(("get_next_title", base_title))
        return f"{base_title} #2"

    def set_session_title(self, session_id: str, title: str) -> bool:
        self.events.append(("set_session_title", session_id, title))
        self.sessions[session_id]["title"] = title
        return True

    def append_message(self, **message):
        self.events.append(("append_message", dict(message)))
        if message.get("content") in self.failed_content:
            raise RuntimeError("copy failed")
        self.messages.setdefault(str(message["session_id"]), []).append(dict(message))
        return len(self.messages[str(message["session_id"])])

    def truncate_last_user_turn(self, session_id: str) -> int:
        self.events.append(("truncate_last_user_turn", session_id))
        messages = self.messages.get(session_id, [])
        last_user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].get("role") == "user"
            ),
            None,
        )
        if last_user_index is None:
            return 0
        removed = len(messages) - last_user_index
        self.messages[session_id] = messages[:last_user_index]
        return removed


def test_start_new_session_ends_old_session_and_optionally_creates_record() -> None:
    repository = _SessionRepository()
    started_at = datetime(2026, 7, 29, 20, 0, 0)

    state = start_new_session(
        repository=repository,
        current_session_id="old-session",
        started_at=started_at,
        source="cli",
        model="model-a",
        model_config={"max_iterations": 12},
        create_record=True,
        uuid_factory=_fixed_uuid,
    )

    assert state.session_id == "20260729_200000_012345"
    assert state.session_start == started_at
    assert state.conversation_history == ()
    assert state.resumed is False
    assert repository.events == [
        ("end_session", "old-session", "new_session"),
        (
            "create_session",
            {
                "session_id": state.session_id,
                "source": "cli",
                "model": "model-a",
                "model_config": {"max_iterations": 12},
            },
        ),
    ]

    repository.events.clear()
    start_new_session(
        repository=repository,
        current_session_id=state.session_id,
        started_at=started_at,
        source="cli",
        model="model-a",
        model_config={},
        create_record=False,
        uuid_factory=_fixed_uuid,
    )
    assert repository.events == [("end_session", state.session_id, "new_session")]


def test_resume_session_filters_metadata_and_preserves_transition_order() -> None:
    repository = _SessionRepository()
    repository.sessions["target"] = {"id": "target", "title": "Work"}
    repository.messages["target"] = [
        {"role": "session_meta", "content": "transcript only"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    started_at = datetime(2026, 7, 29, 20, 1, 0)

    result = resume_session(
        repository=repository,
        current_session_id="current",
        target_session_id="target",
        session_start=started_at,
    )

    assert result.metadata == {"id": "target", "title": "Work"}
    assert result.state.session_id == "target"
    assert [message["role"] for message in result.state.conversation_history] == [
        "user",
        "assistant",
    ]
    assert repository.events == [
        ("get_session", "target"),
        ("end_session", "current", "resumed_other"),
        ("get_messages", "target"),
        ("reopen_session", "target"),
    ]


def test_resume_session_rejects_missing_or_already_active_target_before_writes() -> None:
    repository = _SessionRepository()

    with pytest.raises(SessionNotFoundError):
        resume_session(
            repository=repository,
            current_session_id="current",
            target_session_id="missing",
            session_start=datetime(2026, 7, 29),
        )
    assert repository.events == [("get_session", "missing")]

    repository.events.clear()
    repository.sessions["current"] = {"id": "current"}
    with pytest.raises(SessionAlreadyActiveError):
        resume_session(
            repository=repository,
            current_session_id="current",
            target_session_id="current",
            session_start=datetime(2026, 7, 29),
        )
    assert repository.events == [("get_session", "current")]


def test_hydrate_session_reports_missing_empty_and_ready_outcomes() -> None:
    repository = _SessionRepository()

    missing = hydrate_session(repository=repository, session_id="missing")
    assert missing.status is SessionHydrationStatus.MISSING
    assert missing.metadata is None
    assert missing.conversation_history == ()
    assert repository.events == [("get_session", "missing")]

    repository.events.clear()
    repository.sessions["empty"] = {"id": "empty", "title": "Empty"}
    repository.messages["empty"] = [
        {"role": "session_meta", "content": "transcript only"}
    ]
    empty = hydrate_session(repository=repository, session_id="empty")
    assert empty.status is SessionHydrationStatus.EMPTY
    assert empty.conversation_history == ()
    assert repository.events == [
        ("get_session", "empty"),
        ("get_messages", "empty"),
        ("reopen_session", "empty"),
    ]

    repository.events.clear()
    repository.sessions["ready"] = {"id": "ready", "title": "Ready"}
    repository.messages["ready"] = [{"role": "user", "content": "hello"}]
    ready = hydrate_session(repository=repository, session_id="ready")
    assert ready.status is SessionHydrationStatus.READY
    assert ready.metadata == {"id": "ready", "title": "Ready"}
    assert ready.conversation_history == ({"role": "user", "content": "hello"},)
    assert repository.events[-1] == ("reopen_session", "ready")


def test_branch_session_copies_supported_history_and_tolerates_message_failure() -> None:
    repository = _SessionRepository()
    repository.sessions["parent"] = {"id": "parent", "title": "Investigation"}
    repository.failed_content.add("skip me")
    history = [
        {"role": "user", "content": "keep me"},
        {
            "role": "assistant",
            "content": "skip me",
            "name": "fallback-tool-name",
            "reasoning": "checked",
        },
    ]

    result = branch_session(
        repository=repository,
        current_session_id="parent",
        conversation_history=history,
        started_at=datetime(2026, 7, 29, 20, 2, 0),
        requested_title="",
        source="cli",
        model="model-a",
        model_config={"max_iterations": 12},
        uuid_factory=_fixed_uuid,
    )

    assert result.parent_session_id == "parent"
    assert result.title == "Investigation #2"
    assert result.copied_message_count == 1
    assert result.state.session_id == "20260729_200200_012345"
    assert result.state.conversation_history == tuple(history)
    assert result.state.resumed is True
    create_event = next(event for event in repository.events if event[0] == "create_session")
    assert create_event[1]["parent_session_id"] == "parent"
    failed_copy = [event for event in repository.events if event[0] == "append_message"][1]
    assert failed_copy[1]["tool_name"] == "fallback-tool-name"
    assert repository.sessions[result.state.session_id]["title"] == "Investigation #2"


def test_remove_last_user_turn_reports_non_mutating_outcomes() -> None:
    empty = remove_last_user_turn([])
    no_user = remove_last_user_turn([{"role": "assistant", "content": "hello"}])

    assert empty.status is HistoryMutationStatus.EMPTY
    assert empty.conversation_history == ()
    assert no_user.status is HistoryMutationStatus.NO_USER_MESSAGE
    assert no_user.conversation_history == (
        {"role": "assistant", "content": "hello"},
    )


def test_remove_last_user_turn_truncates_memory_and_repository() -> None:
    repository = _SessionRepository()
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer one"},
        {"role": "user", "content": [{"type": "text", "text": "second"}]},
        {"role": "assistant", "content": "answer two"},
        {"role": "tool", "content": "tool result"},
    ]
    repository.messages["active"] = list(history)

    result = remove_last_user_turn(
        history,
        repository=repository,
        session_id="active",
    )

    assert result.status is HistoryMutationStatus.APPLIED
    assert result.conversation_history == tuple(history[:2])
    assert result.removed_messages == tuple(history[2:])
    assert result.user_message == [{"type": "text", "text": "second"}]
    assert result.persisted_removed_count == 3
    assert result.persistence_error == ""
    assert repository.messages["active"] == history[:2]


def test_remove_last_user_turn_keeps_history_unchanged_when_repository_fails() -> None:
    class _BrokenRepository(_SessionRepository):
        def truncate_last_user_turn(self, session_id: str) -> int:
            raise RuntimeError(f"cannot truncate {session_id}")

    result = remove_last_user_turn(
        [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ],
        repository=_BrokenRepository(),
        session_id="active",
    )

    assert result.status is HistoryMutationStatus.PERSISTENCE_FAILED
    assert result.conversation_history == (
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    )
    assert result.persistence_error == "cannot truncate active"


def test_get_session_title_reports_current_pending_unset_and_unavailable() -> None:
    repository = _SessionRepository()
    repository.sessions["current"] = {"id": "current", "title": "Saved"}

    current = get_session_title(
        repository=repository,
        session_id="current",
        pending_title="Pending",
    )
    pending = get_session_title(
        repository=repository,
        session_id="new",
        pending_title="Pending",
    )
    unset = get_session_title(
        repository=repository,
        session_id="new",
        pending_title=None,
    )
    unavailable = get_session_title(
        repository=None,
        session_id="new",
        pending_title=None,
    )

    assert (current.status, current.title) == (SessionTitleStatus.CURRENT, "Saved")
    assert (pending.status, pending.title) == (SessionTitleStatus.PENDING, "Pending")
    assert unset.status is SessionTitleStatus.UNSET
    assert unavailable.status is SessionTitleStatus.UNAVAILABLE


def test_set_session_title_updates_existing_or_queues_new_session() -> None:
    repository = _SessionRepository()
    repository.sessions["current"] = {"id": "current"}

    updated = set_session_title(
        repository=repository,
        session_id="current",
        raw_title="  Existing   title  ",
    )
    queued = set_session_title(
        repository=repository,
        session_id="new",
        raw_title="  Future   title  ",
    )

    assert (updated.status, updated.title) == (SessionTitleStatus.UPDATED, "Existing title")
    assert repository.sessions["current"]["title"] == "Existing title"
    assert (queued.status, queued.title) == (SessionTitleStatus.QUEUED, "Future title")


def test_set_session_title_reports_conflict_invalid_and_unavailable() -> None:
    repository = _SessionRepository()
    repository.sessions["other"] = {"id": "other", "title": "Taken"}

    conflict = set_session_title(
        repository=repository,
        session_id="new",
        raw_title="Taken",
    )
    empty = set_session_title(
        repository=repository,
        session_id="new",
        raw_title="  ",
    )
    too_long = set_session_title(
        repository=repository,
        session_id="new",
        raw_title="x" * 101,
    )
    unavailable = set_session_title(
        repository=None,
        session_id="new",
        raw_title="Title",
    )

    assert conflict.status is SessionTitleStatus.CONFLICT
    assert conflict.conflicting_session_id == "other"
    assert empty.status is SessionTitleStatus.INVALID
    assert empty.error == ""
    assert too_long.status is SessionTitleStatus.INVALID
    assert too_long.error == "Title too long"
    assert unavailable.status is SessionTitleStatus.UNAVAILABLE
