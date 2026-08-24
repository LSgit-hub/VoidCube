from __future__ import annotations

from datetime import datetime

import pytest

from voidcube.application.sessions import (
    BranchSessionResult,
    ResumeSessionResult,
    SessionLifecycleState,
)
from voidcube.interfaces.cli.session_command_adapter import (
    ResumeSummaryLabels,
    ResumeTargetStatus,
    project_branch_summary,
    project_resume_summary,
    resolve_resume_target,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

_STARTED_AT = datetime(2026, 7, 29, 12, 0, 0)
_LABELS = ResumeSummaryLabels(
    resumed_session="Resumed session",
    user_messages="user messages",
    total="total",
    no_messages_starting_fresh="no messages, starting fresh",
)


def _state(
    history: tuple[dict[str, object], ...],
    *,
    session_id: str = "target-session",
) -> SessionLifecycleState:
    return SessionLifecycleState(
        session_id=session_id,
        session_start=_STARTED_AT,
        conversation_history=history,
        resumed=True,
    )


def test_resume_target_maps_one_based_recent_session_index() -> None:
    named_calls: list[str] = []

    target = resolve_resume_target(
        "2",
        recent_sessions=({"id": "first"}, {"id": "second"}),
        resolve_named=lambda value: named_calls.append(value) or None,
    )

    assert target.status is ResumeTargetStatus.RESOLVED
    assert target.session_id == "second"
    assert target.available_count == 2
    assert named_calls == []


@pytest.mark.parametrize("requested", ["0", "-1", "3"])
def test_resume_target_rejects_invalid_numeric_index(requested: str) -> None:
    target = resolve_resume_target(
        requested,
        recent_sessions=({"id": "first"}, {"id": "second"}),
        resolve_named=lambda _value: pytest.fail(
            "numeric indexes must not resolve by name"
        ),
    )

    assert target.status is ResumeTargetStatus.INDEX_OUT_OF_RANGE
    assert target.available_count == 2


def test_resume_target_uses_named_resolution_and_preserves_fallback() -> None:
    resolved = resolve_resume_target(
        "Mixed Title",
        recent_sessions=(),
        resolve_named=lambda value: (
            "resolved-id" if value == "Mixed Title" else None
        ),
    )
    fallback = resolve_resume_target(
        "Unresolved ID",
        recent_sessions=(),
        resolve_named=lambda _value: None,
    )

    assert resolved.session_id == "resolved-id"
    assert fallback.session_id == "Unresolved ID"


@pytest.mark.parametrize(
    ("history", "metadata", "expected"),
    [
        (
            (
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ),
            {"title": "Saved work"},
            (
                '  ↻ Resumed session target-session "Saved work" '
                "(1 user messages, 2 total)"
            ),
        ),
        (
            ({"role": "assistant", "content": "answer"},),
            {},
            "  ↻ Resumed session target-session (0 user messages, 1 total)",
        ),
        (
            (),
            {"title": "Empty"},
            (
                '  ↻ Resumed session target-session "Empty" — '
                "no messages, starting fresh."
            ),
        ),
    ],
)
def test_resume_summary_projects_history_and_optional_title(
    history: tuple[dict[str, object], ...],
    metadata: dict[str, object],
    expected: str,
) -> None:
    result = ResumeSessionResult(state=_state(history), metadata=metadata)

    assert project_resume_summary(result, labels=_LABELS) == expected


@pytest.mark.parametrize(
    ("user_count", "noun"),
    [(0, "messages"), (1, "message"), (2, "messages")],
)
def test_branch_summary_pluralizes_user_message_count(
    user_count: int,
    noun: str,
) -> None:
    history = tuple(
        {"role": "user", "content": str(index)}
        for index in range(user_count)
    )
    result = BranchSessionResult(
        state=_state(history, session_id="branch-id"),
        parent_session_id="parent-id",
        title="Branch title",
        copied_message_count=len(history),
    )

    assert project_branch_summary(result) == (
        f'  ⑂ Branched session "Branch title" ({user_count} user {noun})',
        "  Original session: parent-id",
        "  Branch session:   branch-id",
    )
