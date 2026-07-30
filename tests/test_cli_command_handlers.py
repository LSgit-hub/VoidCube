from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from VoidCube_app.session_lifecycle import (
    BranchSessionResult,
    ResumeSessionResult,
    SessionAlreadyActiveError,
    SessionHydration,
    SessionHydrationStatus,
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
from VoidCube_cli.command_handlers.history import (
    HistoryCommandPorts,
    HistoryMutationPorts,
    SaveConversationPorts,
    UndoCommandPorts,
    handle_history_command,
    handle_save_conversation_command,
    handle_undo_command,
    remove_last_user_turn_from_history,
    write_conversation_export,
)
from VoidCube_cli.command_handlers.rollback import (
    RollbackCommandPorts,
    RollbackCommandText,
    handle_rollback_command,
    resolve_checkpoint_reference,
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
_ROLLBACK_TEXT = RollbackCommandText(
    no_active_agent="no active agent",
    checkpoints_not_enabled="checkpoints disabled",
    checkpoints_enable_command="enable command",
    checkpoints_enable_config="enable config",
    usage_diff="usage diff",
    no_checkpoints=lambda path: f"no checkpoints: {path}",
    no_changes="no changes",
    more_lines=lambda count: f"more lines: {count}",
    restored=lambda checkpoint, reason: f"restored {checkpoint}: {reason}",
    restored_file=lambda file_path, checkpoint, reason: (
        f"restored {file_path} at {checkpoint}: {reason}"
    ),
    snapshot_saved="snapshot saved",
    chat_undone="chat undone",
    invalid_number=lambda maximum: f"invalid number (max {maximum})",
)


def _rollback_ports(
    *,
    output: list[str],
    manager: object | None,
    list_checkpoints=lambda _manager, _directory: (),
    format_checkpoints=lambda _checkpoints, directory: f"list: {directory}",
    diff=lambda _manager, _directory, _target: {"success": True},
    restore=lambda _manager, _directory, _target, _file: {"success": True},
    has_conversation_history=lambda: False,
    undo_chat_history=lambda: None,
) -> RollbackCommandPorts:
    return RollbackCommandPorts(
        checkpoint_manager=lambda: manager,
        manager_enabled=lambda value: bool(value.enabled),
        working_directory=lambda: "workspace",
        list_checkpoints=list_checkpoints,
        format_checkpoints=format_checkpoints,
        diff=diff,
        restore=restore,
        has_conversation_history=has_conversation_history,
        undo_chat_history=undo_chat_history,
        emit=output.append,
        text=_ROLLBACK_TEXT,
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


def test_history_handler_projects_empty_history_to_recent_sessions_then_message() -> None:
    output: list[str] = []

    handle_history_command(
        parse_cli_command("/history ignored"),
        ports=HistoryCommandPorts(
            conversation_history=lambda: (),
            show_recent_sessions=lambda: False,
            emit=output.append,
            no_history_message="no history",
            tools_label="tools",
        ),
    )

    assert output == ["no history"]


def test_history_handler_hides_tool_messages_and_preserves_user_assistant_order() -> None:
    output: list[str] = []

    handle_history_command(
        parse_cli_command("/history"),
        ports=HistoryCommandPorts(
            conversation_history=lambda: (
                {"role": "system", "content": "ignored"},
                {"role": "tool", "content": "hidden"},
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            ),
            show_recent_sessions=lambda: pytest.fail("non-empty history must not list sessions"),
            emit=output.append,
            no_history_message="no history",
            tools_label="tools",
        ),
    )

    assert output == [
        "",
        "+" + "-" * 50 + "+",
        "|" + " " * 12 + "(^_^) Conversation History" + " " * 11 + "|",
        "+" + "-" * 50 + "+",
        "tools",
        "    (1 tool message hidden)",
        "\n  [You #1]",
        "    question",
        "\n  [Voidcube #2]",
        "    (requested 1 tool call)",
        "",
    ]


def test_save_handler_ignores_arguments_and_overwrites_default_timestamped_path(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    timestamp = datetime(2026, 7, 30, 10, 0, 1)
    destination = tmp_path / "VoidCube_conversation_20260730_100001.json"
    destination.write_text("old export", encoding="utf-8")

    handle_save_conversation_command(
        parse_cli_command("/save another-name.json"),
        ports=SaveConversationPorts(
            conversation_history=lambda: ({"role": "user", "content": "hello"},),
            model=lambda: "active-model",
            session_start=lambda: timestamp,
            now=lambda: timestamp,
            working_directory=lambda: tmp_path,
            write_json=write_conversation_export,
            emit=output.append,
            no_conversation_message="no conversation",
        ),
    )

    assert output == [
        "(^_^)v Conversation saved to: VoidCube_conversation_20260730_100001.json"
    ]
    assert destination.read_text(encoding="utf-8") == (
        '{\n  "model": "active-model",\n'
        '  "session_start": "2026-07-30T10:00:01",\n'
        '  "messages": [\n'
        '    {\n'
        '      "role": "user",\n'
        '      "content": "hello"\n'
        '    }\n'
        '  ]\n'
        '}'
    )


def test_save_handler_reports_empty_history_and_write_failure() -> None:
    empty_output: list[str] = []
    failure_output: list[str] = []
    now = datetime(2026, 7, 30, 10, 0, 1)

    empty_ports = SaveConversationPorts(
        conversation_history=lambda: (),
        model=lambda: pytest.fail("empty export must not read model"),
        session_start=lambda: now,
        now=lambda: now,
        working_directory=lambda: Path("."),
        write_json=lambda _path, _payload: pytest.fail("empty export must not write"),
        emit=empty_output.append,
        no_conversation_message="no conversation",
    )
    handle_save_conversation_command(parse_cli_command("/save"), ports=empty_ports)

    handle_save_conversation_command(
        parse_cli_command("/save"),
        ports=SaveConversationPorts(
            conversation_history=lambda: ({"role": "user", "content": "hello"},),
            model=lambda: "active-model",
            session_start=lambda: now,
            now=lambda: now,
            working_directory=lambda: Path("."),
            write_json=lambda _path, _payload: (_ for _ in ()).throw(OSError("disk full")),
            emit=failure_output.append,
            no_conversation_message="no conversation",
        ),
    )

    assert empty_output == ["no conversation"]
    assert failure_output == ["(x_x) Failed to save: disk full"]


def test_undo_handler_rolls_back_last_user_turn_and_synchronizes_adapter_state() -> None:
    output: list[str] = []
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "tool", "content": "hidden"},
        {"role": "assistant", "content": "two"},
    ]
    repository = type("Repository", (), {"truncate_last_user_turn": lambda _self, _id: 3})()
    hydrated = SessionHydration(
        session_id="active",
        status=SessionHydrationStatus.READY,
        metadata={"id": "active", "title": "Work"},
        conversation_history=tuple(history),
    )
    synced: list[list[dict[str, object]]] = []
    state = {"history": history, "hydration": hydrated}
    mutation_ports = HistoryMutationPorts(
        conversation_history=lambda: state["history"],
        repository=lambda: repository,
        session_id=lambda: "active",
        set_conversation_history=lambda value: state.__setitem__("history", value),
        synchronize_agent_history=lambda value: synced.append(value),
        hydration=lambda: state["hydration"],
        set_hydration=lambda value: state.__setitem__("hydration", value),
        emit=output.append,
    )

    handle_undo_command(
        parse_cli_command("/undo ignored"),
        ports=UndoCommandPorts(
            remove_last_user_turn=lambda: remove_last_user_turn_from_history(
                ports=mutation_ports,
                empty_message="empty",
                no_user_message="no user",
            ),
            emit=output.append,
        ),
    )

    assert state["history"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
    ]
    assert synced == [state["history"]]
    assert state["hydration"].metadata == {"id": "active", "title": "Work"}
    assert state["hydration"].conversation_history == tuple(state["history"])
    assert output == [
        '(^_^)b Undid 3 message(s). Removed: "second"',
        "  2 message(s) remaining in history.",
    ]


def test_undo_handler_keeps_history_when_no_user_turn_exists() -> None:
    output: list[str] = []
    history = [{"role": "assistant", "content": "orphan"}]
    mutation_ports = HistoryMutationPorts(
        conversation_history=lambda: history,
        repository=lambda: pytest.fail("no-user history must not write"),
        session_id=lambda: "active",
        set_conversation_history=lambda _value: pytest.fail("no-user history must not mutate"),
        synchronize_agent_history=lambda _value: pytest.fail("no-user history must not sync"),
        hydration=lambda: None,
        set_hydration=lambda _value: pytest.fail("no-user history must not hydrate"),
        emit=output.append,
    )

    handle_undo_command(
        parse_cli_command("/undo"),
        ports=UndoCommandPorts(
            remove_last_user_turn=lambda: remove_last_user_turn_from_history(
                ports=mutation_ports,
                empty_message="empty",
                no_user_message="no user",
            ),
            emit=output.append,
        ),
    )

    assert history == [{"role": "assistant", "content": "orphan"}]
    assert output == ["no user"]


def test_rollback_handler_stops_before_checkpoint_operations_without_agent_or_flag() -> None:
    no_agent_output: list[str] = []
    disabled_output: list[str] = []

    handle_rollback_command(
        parse_cli_command("/rollback"),
        ports=_rollback_ports(output=no_agent_output, manager=None),
    )
    handle_rollback_command(
        parse_cli_command("/rollback"),
        ports=_rollback_ports(
            output=disabled_output,
            manager=SimpleNamespace(enabled=False),
            list_checkpoints=lambda _manager, _directory: pytest.fail(
                "disabled checkpoints must not list"
            ),
        ),
    )

    assert no_agent_output == ["no active agent"]
    assert disabled_output == [
        "checkpoints disabled",
        "enable command",
        "enable config",
    ]


def test_rollback_handler_lists_checkpoints_with_default_arguments() -> None:
    output: list[str] = []
    manager = SimpleNamespace(enabled=True)
    checkpoints = ({"hash": "first"},)
    observed: list[tuple[object, str]] = []

    handle_rollback_command(
        parse_cli_command("/rollback ignored"),
        ports=_rollback_ports(
            output=output,
            manager=manager,
            list_checkpoints=lambda value, directory: observed.append(
                (value, directory)
            ) or checkpoints,
            format_checkpoints=lambda values, directory: (
                f"formatted {len(values)} for {directory}"
            ),
        ),
    )

    assert observed == [(manager, "workspace")]
    assert output == ["formatted 1 for workspace"]


def test_rollback_handler_handles_diff_usage_empty_invalid_and_truncation() -> None:
    usage_output: list[str] = []
    empty_output: list[str] = []
    invalid_output: list[str] = []
    diff_output: list[str] = []
    manager = SimpleNamespace(enabled=True)
    checkpoints = ({"hash": "one"}, {"hash": "two"})

    handle_rollback_command(
        parse_cli_command("/rollback diff"),
        ports=_rollback_ports(output=usage_output, manager=manager),
    )
    handle_rollback_command(
        parse_cli_command("/rollback diff 1"),
        ports=_rollback_ports(
            output=empty_output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: (),
        ),
    )
    handle_rollback_command(
        parse_cli_command("/rollback diff 3"),
        ports=_rollback_ports(
            output=invalid_output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: checkpoints,
            diff=lambda *_args: pytest.fail("invalid reference must not diff"),
        ),
    )
    handle_rollback_command(
        parse_cli_command("/rollback diff 2"),
        ports=_rollback_ports(
            output=diff_output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: checkpoints,
            diff=lambda _manager, directory, target: (
                {"success": True, "stat": "stat", "diff": "\n".join(str(i) for i in range(82))}
                if (directory, target) == ("workspace", "two")
                else pytest.fail("diff received incorrect target")
            ),
        ),
    )

    assert usage_output == ["usage diff"]
    assert empty_output == ["no checkpoints: workspace"]
    assert invalid_output == ["invalid number (max 2)"]
    assert diff_output == [
        "\nstat",
        "\n".join(str(i) for i in range(80)),
        "\nmore lines: 2",
    ]


def test_rollback_handler_projects_diff_failure_and_no_changes() -> None:
    failure_output: list[str] = []
    no_change_output: list[str] = []
    manager = SimpleNamespace(enabled=True)
    checkpoints = ({"hash": "one"},)

    handle_rollback_command(
        parse_cli_command("/rollback diff one"),
        ports=_rollback_ports(
            output=failure_output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: checkpoints,
            diff=lambda *_args: {"success": False, "error": "diff failed"},
        ),
    )
    handle_rollback_command(
        parse_cli_command("/rollback diff one"),
        ports=_rollback_ports(
            output=no_change_output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: checkpoints,
        ),
    )

    assert failure_output == ["  ❌ diff failed"]
    assert no_change_output == ["no changes"]


def test_rollback_handler_restores_file_then_reuses_undo_route() -> None:
    output: list[str] = []
    manager = SimpleNamespace(enabled=True)
    calls: list[tuple[object, str, str, str | None]] = []
    undo_calls: list[str] = []

    handle_rollback_command(
        parse_cli_command("/rollback 2 src/main.py"),
        ports=_rollback_ports(
            output=output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: (
                {"hash": "one"}, {"hash": "two"}
            ),
            restore=lambda value, directory, target, file_path: calls.append(
                (value, directory, target, file_path)
            ) or {"success": True, "restored_to": "two", "reason": "before edit"},
            has_conversation_history=lambda: True,
            undo_chat_history=lambda: undo_calls.append("undo"),
        ),
    )

    assert calls == [(manager, "workspace", "two", "src/main.py")]
    assert undo_calls == ["undo"]
    assert output == [
        "restored src/main.py at two: before edit",
        "snapshot saved",
        "chat undone",
    ]


def test_rollback_handler_does_not_sync_chat_when_restore_fails() -> None:
    output: list[str] = []
    undo_calls: list[str] = []
    manager = SimpleNamespace(enabled=True)

    handle_rollback_command(
        parse_cli_command("/rollback one"),
        ports=_rollback_ports(
            output=output,
            manager=manager,
            list_checkpoints=lambda _manager, _directory: ({"hash": "one"},),
            restore=lambda *_args: {"success": False, "error": "restore failed"},
            has_conversation_history=lambda: True,
            undo_chat_history=lambda: undo_calls.append("undo"),
        ),
    )

    assert output == ["  ❌ restore failed"]
    assert undo_calls == []


def test_checkpoint_reference_resolution_keeps_hashes_and_requires_valid_indices() -> None:
    checkpoints = ({"hash": "one"}, {"hash": "two"})

    assert resolve_checkpoint_reference("2", checkpoints) == "two"
    assert resolve_checkpoint_reference("0", checkpoints) is None
    assert resolve_checkpoint_reference("deadbeef", checkpoints) == "deadbeef"


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
