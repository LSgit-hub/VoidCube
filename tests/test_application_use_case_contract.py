from __future__ import annotations

from datetime import datetime
import uuid

import pytest

from VoidCube_app.application import ApplicationRuntime
from VoidCube_app.contracts.events import SessionEventKind
from VoidCube_app.session_lifecycle import HistoryMutationStatus, SessionTitleStatus
from VoidCube_app.turn_queue import TurnInputRoute


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _fixed_uuid() -> uuid.UUID:
    return uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")


class _Repository:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.messages: dict[str, list[dict[str, object]]] = {}
        self.events: list[tuple[object, ...]] = []

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
        return self.sessions.get(session_id, {}).get("title")

    def get_next_title_in_lineage(self, base_title: str) -> str:
        return f"{base_title} #2"

    def set_session_title(self, session_id: str, title: str) -> bool:
        self.sessions[session_id]["title"] = title
        return True

    def append_message(self, **message):
        self.messages.setdefault(str(message["session_id"]), []).append(dict(message))
        return len(self.messages[str(message["session_id"])])

    def truncate_last_user_turn(self, session_id: str) -> int:
        messages = self.messages.get(session_id, [])
        index = next(
            (
                position
                for position in range(len(messages) - 1, -1, -1)
                if messages[position].get("role") == "user"
            ),
            None,
        )
        if index is None:
            return 0
        removed = len(messages) - index
        self.messages[session_id] = messages[:index]
        return removed

    @staticmethod
    def sanitize_title(title):
        return " ".join(str(title or "").split()) or None

    def get_session_by_title(self, title: str):
        return next(
            (
                session
                for session in self.sessions.values()
                if session.get("title") == title
            ),
            None,
        )


def test_session_adapter_contract_runs_only_through_application_runtime() -> None:
    repository = _Repository()
    repository.sessions["current"] = {"id": "current", "title": "Current"}
    repository.sessions["target"] = {"id": "target", "title": "Target"}
    repository.messages["target"] = [{"role": "user", "content": "restored"}]
    events = []
    started_at = datetime(2026, 8, 5, 12, 0, 0)
    runtime = ApplicationRuntime.create(
        session_id="current",
        session_start=started_at,
        conversation_history=[{"role": "user", "content": "branch source"}],
        event_sink=events.append,
        uuid_factory=_fixed_uuid,
    )

    branch = runtime.branch_session(
        repository=repository,
        started_at=started_at,
        requested_title="Branch",
        source="contract-test",
        model="model-a",
        model_config={"max_iterations": 5},
    )
    runtime.apply_session_state(branch.state)

    assert branch.parent_session_id == "current"
    assert branch.state.conversation_history == (
        {"role": "user", "content": "branch source"},
    )
    assert runtime.state.session_id == "20260805_120000_012345"

    resumed = runtime.resume_session(
        repository=repository,
        target_session_id="target",
        session_start=started_at,
    )
    runtime.apply_session_state(resumed.state)

    assert runtime.state.session_id == "target"
    assert runtime.state.conversation_history == [
        {"role": "user", "content": "restored"}
    ]
    assert [event.kind for event in events][-4:] == [
        SessionEventKind.ENDED,
        SessionEventKind.RESUMED,
        SessionEventKind.ENDED,
        SessionEventKind.RESUMED,
    ]


def test_public_history_title_and_turn_control_use_cases_share_runtime_state() -> None:
    repository = _Repository()
    repository.sessions["active"] = {"id": "active"}
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two"},
    ]
    repository.messages["active"] = list(history)
    runtime = ApplicationRuntime.create(
        session_id="active",
        session_start=datetime(2026, 8, 5),
        conversation_history=history,
        uuid_factory=_fixed_uuid,
    )

    title = runtime.set_session_title(repository=repository, raw_title="  Work  ")
    current = runtime.get_session_title(repository=repository)
    mutation = runtime.remove_last_user_turn(repository=repository)

    assert title.status is SessionTitleStatus.UPDATED
    assert current.title == "Work"
    assert mutation.status is HistoryMutationStatus.APPLIED
    assert runtime.state.conversation_history == history[:2]
    assert runtime.enqueue_turn_input("follow up") is TurnInputRoute.NEXT_TURN
    assert runtime.state.pending_input_queue.get_nowait() == "follow up"


def test_shared_runtime_owns_pending_title_hydration_busy_state_and_queues() -> None:
    repository = _Repository()
    runtime = ApplicationRuntime.create(
        session_id="active",
        session_start=datetime(2026, 8, 5),
        uuid_factory=_fixed_uuid,
    )

    queued = runtime.set_session_title(repository=repository, raw_title="Queued")

    assert queued.status is SessionTitleStatus.QUEUED
    assert runtime.state.pending_title == "Queued"
    runtime.set_agent_running(True)
    route = runtime.enqueue_turn_input("queue me")

    assert runtime.state.agent_running is True
    assert route is TurnInputRoute.NEXT_TURN
    assert runtime.state.pending_input_queue.get_nowait() == "queue me"

    runtime.clear_pending_title()
    runtime.clear_session_hydration()
    assert runtime.state.pending_title is None
    assert runtime.state.session_hydration is None
