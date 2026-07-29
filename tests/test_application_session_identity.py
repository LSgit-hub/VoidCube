from __future__ import annotations

from datetime import datetime
import uuid

from VoidCube_app.session_identity import resolve_session_identity


class _SessionIndex:
    def __init__(self, sessions: list[dict[str, object]]) -> None:
        self.sessions = sessions
        self.calls: list[dict[str, object]] = []

    def list_sessions_rich(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append(dict(kwargs))
        return list(self.sessions)


def _fixed_uuid() -> uuid.UUID:
    return uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")


def test_requested_session_identity_does_not_query_the_index() -> None:
    index = _SessionIndex([])

    identity = resolve_session_identity(
        requested_session_id="resume-me",
        auto_resume_enabled=True,
        session_index=index,
        session_start=datetime(2026, 7, 29, 10, 0, 0),
        interactive_source="cli",
        autonomous_source="cli_supervisor_task_lane",
    )

    assert identity.session_id == "resume-me"
    assert identity.resumed is True
    assert index.calls == []


def test_auto_resume_prefers_open_autonomous_owner_over_interactive_history() -> None:
    index = _SessionIndex(
        [
            {"id": "interactive", "source": "cli", "message_count": 4},
            {
                "id": "autonomous-owner",
                "source": "cli_supervisor_task_lane",
                "message_count": 0,
                "ended_at": None,
            },
        ]
    )

    identity = resolve_session_identity(
        requested_session_id=None,
        auto_resume_enabled=True,
        session_index=index,
        session_start=datetime(2026, 7, 29, 10, 0, 0),
        interactive_source="cli",
        autonomous_source="cli_supervisor_task_lane",
    )

    assert identity.session_id == "autonomous-owner"
    assert identity.resumed is True
    assert index.calls == [{"limit": 20, "exclude_id_prefixes": ["scheduled_"]}]


def test_auto_resume_falls_back_to_interactive_history_or_a_fresh_identifier() -> None:
    interactive = _SessionIndex(
        [{"id": "interactive", "source": "cli", "message_count": 1}]
    )
    fresh = _SessionIndex([])
    started_at = datetime(2026, 7, 29, 10, 0, 0)

    resumed = resolve_session_identity(
        requested_session_id=None,
        auto_resume_enabled=True,
        session_index=interactive,
        session_start=started_at,
        interactive_source="cli",
        autonomous_source="cli_supervisor_task_lane",
    )
    created = resolve_session_identity(
        requested_session_id=None,
        auto_resume_enabled=True,
        session_index=fresh,
        session_start=started_at,
        interactive_source="cli",
        autonomous_source="cli_supervisor_task_lane",
        uuid_factory=_fixed_uuid,
    )

    assert (resumed.session_id, resumed.resumed) == ("interactive", True)
    assert (created.session_id, created.resumed) == ("20260729_100000_012345", False)


def test_auto_resume_failure_returns_a_fresh_identity_and_diagnostic() -> None:
    class _BrokenIndex:
        def list_sessions_rich(self, **_kwargs: object) -> list[dict[str, object]]:
            raise RuntimeError("index unavailable")

    identity = resolve_session_identity(
        requested_session_id=None,
        auto_resume_enabled=True,
        session_index=_BrokenIndex(),
        session_start=datetime(2026, 7, 29, 10, 0, 0),
        interactive_source="cli",
        autonomous_source="cli_supervisor_task_lane",
        uuid_factory=_fixed_uuid,
    )

    assert identity.session_id == "20260729_100000_012345"
    assert identity.resumed is False
    assert identity.resume_lookup_error == "index unavailable"
