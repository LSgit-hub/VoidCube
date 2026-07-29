from __future__ import annotations

from datetime import datetime

import pytest

from VoidCube_app.session_lifecycle import (
    BranchSessionResult,
    ResumeSessionResult,
    SessionAlreadyActiveError,
    SessionLifecycleState,
    SessionNotFoundError,
    SessionTitleResult,
    SessionTitleStatus,
)
from VoidCube_cli.command_handlers.display import (
    StatusBarCommandPorts,
    handle_statusbar_command,
)
from VoidCube_cli.command_handlers.input import (
    QueueCommandPorts,
    RetryCommandPorts,
    handle_queue_command,
    handle_retry_command,
)
from VoidCube_cli.command_handlers.session import (
    BranchCommandPorts,
    ClearCommandPorts,
    NewSessionCommandPorts,
    ResumeCommandPorts,
    ResumeCommandText,
    TitleCommandPorts,
    handle_branch_command,
    handle_clear_command,
    handle_new_session_command,
    handle_resume_command,
    handle_title_command,
)
from VoidCube_cli.command_router import parse_cli_command
from VoidCube_cli.session_command_adapter import ResumeSummaryLabels


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

_STARTED_AT = datetime(2026, 7, 29, 12, 0, 0)
_RESUME_TEXT = ResumeCommandText(
    usage="usage",
    hint="hint",
    unavailable="unavailable",
    sessions_help="sessions help",
    already_active="already active",
    summary=ResumeSummaryLabels(
        resumed_session="Resumed session",
        user_messages="user messages",
        total="total",
        no_messages_starting_fresh="no messages, starting fresh",
    ),
)


def _session_state(
    history: tuple[dict[str, object], ...] = (),
    *,
    session_id: str = "target-id",
) -> SessionLifecycleState:
    return SessionLifecycleState(
        session_id=session_id,
        session_start=_STARTED_AT,
        conversation_history=history,
        resumed=True,
    )


def _resume_ports(
    *,
    output: list[str],
    repository_available=lambda: True,
    show_recent_sessions=lambda: False,
    list_recent_sessions=lambda: (),
    resolve_named=lambda _value: None,
    resume=lambda target: ResumeSessionResult(_session_state(session_id=target), {}),
    apply_state=lambda _state: None,
    set_hydration=lambda _hydration: None,
    display_history=lambda: None,
) -> ResumeCommandPorts:
    return ResumeCommandPorts(
        repository_available=repository_available,
        show_recent_sessions=show_recent_sessions,
        list_recent_sessions=list_recent_sessions,
        resolve_named=resolve_named,
        resume=resume,
        apply_state=apply_state,
        set_hydration=set_hydration,
        display_history=display_history,
        emit=output.append,
        text=_RESUME_TEXT,
    )


def _raise(error: Exception):
    raise error


def test_queue_handler_uses_explicit_ports_and_preserves_argument_case() -> None:
    queued: list[str] = []
    output: list[str] = []

    handle_queue_command(
        parse_cli_command("/queue Keep MixedCase"),
        ports=QueueCommandPorts(queued.append, lambda: True, output.append),
    )

    assert queued == ["Keep MixedCase"]
    assert output == ["  Queued for the next turn: Keep MixedCase"]


def test_queue_handler_rejects_empty_payload_without_enqueueing() -> None:
    queued: list[str] = []
    output: list[str] = []

    handle_queue_command(
        parse_cli_command("/queue"),
        ports=QueueCommandPorts(queued.append, lambda: False, output.append),
    )

    assert queued == []
    assert output == ["  Usage: /queue <prompt>"]


def test_statusbar_handler_toggles_only_through_explicit_ports() -> None:
    state = {"visible": True}
    output: list[str] = []

    handle_statusbar_command(
        parse_cli_command("/statusbar"),
        ports=StatusBarCommandPorts(
            visible=lambda: state["visible"],
            set_visible=lambda value: state.__setitem__("visible", value),
            emit=output.append,
        ),
    )

    assert state["visible"] is False
    assert output == ["  Status bar hidden"]


def test_retry_handler_requeues_original_payload_through_ports() -> None:
    payload = ("inspect", ["screen.png"])
    queued: list[object] = []
    output: list[str] = []
    result = type("_Result", (), {"user_message": payload})()

    handle_retry_command(
        parse_cli_command("/retry"),
        ports=RetryCommandPorts(lambda: result, queued.append, output.append),
    )

    assert queued == [payload]
    assert output == [f'(^_^)b Retrying: "{payload}"']


def test_retry_handler_stops_when_history_mutation_did_not_apply() -> None:
    queued: list[object] = []
    output: list[str] = []

    handle_retry_command(
        parse_cli_command("/retry"),
        ports=RetryCommandPorts(lambda: None, queued.append, output.append),
    )

    assert queued == []
    assert output == []


@pytest.mark.parametrize(
    ("has_recent_sessions", "expected"),
    [(True, ["usage"]), (False, ["usage", "hint"])],
)
def test_resume_handler_without_target_projects_recent_session_help(
    has_recent_sessions: bool,
    expected: list[str],
) -> None:
    output: list[str] = []

    handle_resume_command(
        parse_cli_command("/resume"),
        ports=_resume_ports(
            output=output,
            show_recent_sessions=lambda: has_recent_sessions,
        ),
    )

    assert output == expected


def test_resume_handler_stops_when_repository_is_unavailable() -> None:
    output: list[str] = []
    calls: list[str] = []

    handle_resume_command(
        parse_cli_command("/resume target"),
        ports=_resume_ports(
            output=output,
            repository_available=lambda: False,
            resolve_named=lambda value: calls.append(value) or None,
        ),
    )

    assert output == ["unavailable"]
    assert calls == []


def test_resume_handler_only_loads_recent_sessions_for_numeric_targets() -> None:
    recent_calls: list[str] = []
    resolved: list[str] = []
    resumed: list[str] = []
    output: list[str] = []

    handle_resume_command(
        parse_cli_command("/resume Mixed Title"),
        ports=_resume_ports(
            output=output,
            list_recent_sessions=lambda: recent_calls.append("recent") or (),
            resolve_named=lambda value: resolved.append(value) or "named-id",
            resume=lambda target: resumed.append(target)
            or ResumeSessionResult(_session_state(session_id=target), {}),
        ),
    )

    assert recent_calls == []
    assert resolved == ["Mixed Title"]
    assert resumed == ["named-id"]


@pytest.mark.parametrize("requested", ["0", "-1", "3"])
def test_resume_handler_rejects_out_of_range_index(requested: str) -> None:
    output: list[str] = []

    handle_resume_command(
        parse_cli_command(f"/resume {requested}"),
        ports=_resume_ports(
            output=output,
            list_recent_sessions=lambda: ({"id": "one"}, {"id": "two"}),
            resume=lambda _target: pytest.fail("invalid index must not resume"),
        ),
    )

    assert output == [
        f"  Session index out of range: {requested} (there are 2 recent sessions)",
        "sessions help",
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SessionNotFoundError("missing"), ["  Session not found: Missing", "sessions help"]),
        (SessionAlreadyActiveError("active"), ["already active"]),
    ],
)
def test_resume_handler_projects_transition_errors(
    error: Exception,
    expected: list[str],
) -> None:
    output: list[str] = []

    handle_resume_command(
        parse_cli_command("/resume Missing"),
        ports=_resume_ports(
            output=output,
            resume=lambda _target: _raise(error),
        ),
    )

    assert output == expected


def test_resume_handler_applies_state_and_hydration_before_history_output() -> None:
    events: list[object] = []
    history = (
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    )
    result = ResumeSessionResult(
        _session_state(history),
        {"title": "Saved"},
    )

    handle_resume_command(
        parse_cli_command("/resume target-id"),
        ports=_resume_ports(
            output=events,
            resume=lambda _target: result,
            apply_state=lambda state: events.append(("apply", state)),
            set_hydration=lambda hydration: events.append(("hydrate", hydration)),
            display_history=lambda: events.append("display"),
        ),
    )

    assert events == [
        ("apply", result.state),
        ("hydrate", result.hydration),
        '  ↻ Resumed session target-id "Saved" (1 user messages, 2 total)',
        "display",
    ]


def test_resume_handler_does_not_display_empty_history() -> None:
    output: list[str] = []

    handle_resume_command(
        parse_cli_command("/resume target-id"),
        ports=_resume_ports(
            output=output,
            display_history=lambda: pytest.fail("empty history must not render"),
        ),
    )

    assert output == [
        "  ↻ Resumed session target-id — no messages, starting fresh."
    ]


def test_branch_handler_rejects_empty_history_and_unavailable_repository() -> None:
    empty_output: list[str] = []
    unavailable_output: list[str] = []

    handle_branch_command(
        parse_cli_command("/branch"),
        ports=BranchCommandPorts(
            conversation_history=lambda: (),
            repository_available=lambda: pytest.fail(
                "empty history must short-circuit repository access"
            ),
            branch=lambda _name: pytest.fail("empty history must not branch"),
            apply_state=lambda _state: None,
            emit=empty_output.append,
            no_conversation_message="no conversation",
            unavailable_message="unavailable",
        ),
    )
    handle_branch_command(
        parse_cli_command("/branch"),
        ports=BranchCommandPorts(
            conversation_history=lambda: ({"role": "user"},),
            repository_available=lambda: False,
            branch=lambda _name: pytest.fail("unavailable repository must not branch"),
            apply_state=lambda _state: None,
            emit=unavailable_output.append,
            no_conversation_message="no conversation",
            unavailable_message="unavailable",
        ),
    )

    assert empty_output == ["no conversation"]
    assert unavailable_output == ["unavailable"]


def test_branch_handler_projects_transition_failure() -> None:
    output: list[str] = []

    handle_branch_command(
        parse_cli_command("/branch Work"),
        ports=BranchCommandPorts(
            conversation_history=lambda: ({"role": "user"},),
            repository_available=lambda: True,
            branch=lambda _name: _raise(RuntimeError("write failed")),
            apply_state=lambda _state: pytest.fail("failed branch must not apply state"),
            emit=output.append,
            no_conversation_message="no conversation",
            unavailable_message="unavailable",
        ),
    )

    assert output == ["  Failed to create branch session: write failed"]


def test_branch_handler_preserves_title_case_and_applies_state_before_summary() -> None:
    events: list[object] = []
    requested: list[str] = []
    state = _session_state(
        ({"role": "user", "content": "question"},),
        session_id="branch-id",
    )
    result = BranchSessionResult(
        state=state,
        parent_session_id="parent-id",
        title="Mixed Case Branch",
        copied_message_count=1,
    )

    handle_branch_command(
        parse_cli_command("/branch Mixed Case Branch"),
        ports=BranchCommandPorts(
            conversation_history=lambda: state.conversation_history,
            repository_available=lambda: True,
            branch=lambda name: requested.append(name) or result,
            apply_state=lambda value: events.append(("apply", value)),
            emit=events.append,
            no_conversation_message="no conversation",
            unavailable_message="unavailable",
        ),
    )

    assert requested == ["Mixed Case Branch"]
    assert events == [
        ("apply", state),
        '  ⑂ Branched session "Mixed Case Branch" (1 user message)',
        "  Original session: parent-id",
        "  Branch session:   branch-id",
    ]


def test_new_session_handler_preserves_boundary_transition_order() -> None:
    events: list[object] = []
    state = _session_state(session_id="new-id")
    ports = NewSessionCommandPorts(
        agent_available=lambda: True,
        notify_boundary=lambda event: events.append(("hook", event)),
        reset_trace=lambda: events.append("trace"),
        start_session=lambda create_record: events.append(
            ("start", create_record)
        ) or state,
        apply_state=lambda value: events.append(("apply", value)),
        emit=lambda text: events.append(("emit", text)),
        started_message="new session started",
    )

    handle_new_session_command(parse_cli_command("/new"), ports=ports)

    assert events == [
        ("hook", "on_session_finalize"),
        "trace",
        ("start", True),
        ("apply", state),
        ("hook", "on_session_reset"),
        ("emit", "new session started"),
    ]


def test_new_session_handler_skips_hooks_without_agent() -> None:
    events: list[object] = []
    state = _session_state(session_id="new-id")
    ports = NewSessionCommandPorts(
        agent_available=lambda: False,
        notify_boundary=lambda event: pytest.fail(f"unexpected hook: {event}"),
        reset_trace=lambda: events.append("trace"),
        start_session=lambda create_record: events.append(
            ("start", create_record)
        ) or state,
        apply_state=lambda value: events.append(("apply", value)),
        emit=lambda text: events.append(("emit", text)),
        started_message="new session started",
    )

    handle_new_session_command(parse_cli_command("/new"), ports=ports)

    assert events == [
        "trace",
        ("start", False),
        ("apply", state),
        ("emit", "new session started"),
    ]


def test_clear_handler_runs_same_transition_without_announcement_before_display() -> None:
    events: list[object] = []
    state = _session_state(session_id="new-id")
    session_ports = NewSessionCommandPorts(
        agent_available=lambda: True,
        notify_boundary=lambda event: events.append(("hook", event)),
        reset_trace=lambda: events.append("trace"),
        start_session=lambda create_record: events.append(
            ("start", create_record)
        ) or state,
        apply_state=lambda value: events.append(("apply", value)),
        emit=lambda text: events.append(("emit", text)),
        started_message="must stay silent",
    )

    handle_clear_command(
        parse_cli_command("/clear"),
        ports=ClearCommandPorts(
            session=session_ports,
            render_display=lambda: events.append("display"),
        ),
    )

    assert events == [
        ("hook", "on_session_finalize"),
        "trace",
        ("start", True),
        ("apply", state),
        ("hook", "on_session_reset"),
        "display",
    ]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            SessionTitleResult(SessionTitleStatus.CURRENT, "session-1", title="Saved"),
            ["  Session ID: session-1", "  Title: Saved"],
        ),
        (
            SessionTitleResult(SessionTitleStatus.PENDING, "session-1", title="Later"),
            ["  Session ID: session-1", "  Title (pending): Later"],
        ),
        (
            SessionTitleResult(SessionTitleStatus.UNSET, "session-1"),
            [
                "  Session ID: session-1",
                "  No title set. Usage: /title <your session title>",
            ],
        ),
        (
            SessionTitleResult(SessionTitleStatus.UNAVAILABLE, "session-1"),
            ["  Session database not available."],
        ),
    ],
)
def test_title_handler_projects_query_statuses(result, expected) -> None:
    output: list[str] = []
    ports = TitleCommandPorts(
        get_title=lambda: result,
        set_title=lambda _value: result,
        set_pending_title=lambda _value: None,
        emit=output.append,
        unavailable_message="  Session database not available.",
    )

    handle_title_command(parse_cli_command("/title"), ports=ports)

    assert output == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            SessionTitleResult(SessionTitleStatus.UPDATED, "session-1", title="Saved"),
            "  Session title set: Saved",
        ),
        (
            SessionTitleResult(SessionTitleStatus.NOT_FOUND, "session-1", title="Saved"),
            "  Session not found in database.",
        ),
        (
            SessionTitleResult(SessionTitleStatus.INVALID, "session-1", error="Title too long"),
            "  Title too long",
        ),
        (
            SessionTitleResult(SessionTitleStatus.INVALID, "session-1"),
            "  Title is empty after cleanup. Please use printable characters.",
        ),
        (
            SessionTitleResult(SessionTitleStatus.CONFLICT, "session-1", title="Taken", conflicting_session_id="other"),
            "  Title 'Taken' is already in use by session other",
        ),
        (
            SessionTitleResult(SessionTitleStatus.CONFLICT, "session-1", title="Taken", error="duplicate"),
            "  duplicate",
        ),
        (
            SessionTitleResult(SessionTitleStatus.UNAVAILABLE, "session-1"),
            "  Session database not available.",
        ),
    ],
)
def test_title_handler_projects_update_statuses(result, expected) -> None:
    output: list[str] = []
    ports = TitleCommandPorts(
        get_title=lambda: result,
        set_title=lambda _value: result,
        set_pending_title=lambda _value: None,
        emit=output.append,
        unavailable_message="  Session database not available.",
    )

    handle_title_command(parse_cli_command("/title Mixed Case"), ports=ports)

    assert output == [expected]


def test_title_handler_queues_pending_title_through_port() -> None:
    pending: list[str | None] = []
    output: list[str] = []
    result = SessionTitleResult(
        SessionTitleStatus.QUEUED,
        "session-1",
        title="Future title",
    )
    ports = TitleCommandPorts(
        get_title=lambda: result,
        set_title=lambda _value: result,
        set_pending_title=pending.append,
        emit=output.append,
        unavailable_message="unavailable",
    )

    handle_title_command(parse_cli_command("/title Future title"), ports=ports)

    assert pending == ["Future title"]
    assert output == [
        "  Session title queued: Future title (will be saved on first message)"
    ]
