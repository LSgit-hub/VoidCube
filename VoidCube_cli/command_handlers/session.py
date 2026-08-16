"""Session command handlers backed by shared application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from VoidCube_app.session_lifecycle import (
    BranchSessionResult,
    ResumeSessionResult,
    SessionAlreadyActiveError,
    SessionHydration,
    SessionLifecycleState,
    SessionNotFoundError,
    SessionTitleResult,
    SessionTitleStatus,
)
from VoidCube_cli.command_router import ParsedCliCommand
from VoidCube_cli.session_command_adapter import (
    ResumeSummaryLabels,
    ResumeTargetStatus,
    is_resume_index,
    project_branch_summary,
    project_resume_summary,
    resolve_resume_target,
)


@dataclass(frozen=True, slots=True)
class TitleCommandPorts:
    get_title: Callable[[], SessionTitleResult]
    set_title: Callable[[str], SessionTitleResult]
    emit: Callable[[str], None]
    unavailable_message: str


@dataclass(frozen=True, slots=True)
class ResumeCommandText:
    usage: str
    hint: str
    unavailable: str
    sessions_help: str
    already_active: str
    summary: ResumeSummaryLabels


@dataclass(frozen=True, slots=True)
class ResumeCommandPorts:
    repository_available: Callable[[], bool]
    show_recent_sessions: Callable[[], bool]
    list_recent_sessions: Callable[[], Sequence[Mapping[str, object]]]
    resolve_named: Callable[[str], str | None]
    resume: Callable[[str], ResumeSessionResult]
    apply_state: Callable[[SessionLifecycleState], None]
    set_hydration: Callable[[SessionHydration], None]
    display_history: Callable[[], None]
    emit: Callable[[str], None]
    text: ResumeCommandText


@dataclass(frozen=True, slots=True)
class BranchCommandPorts:
    conversation_history: Callable[[], Sequence[dict[str, object]]]
    repository_available: Callable[[], bool]
    branch: Callable[[str], BranchSessionResult]
    apply_state: Callable[[SessionLifecycleState], None]
    emit: Callable[[str], None]
    no_conversation_message: str
    unavailable_message: str


@dataclass(frozen=True, slots=True)
class NewSessionCommandPorts:
    agent_available: Callable[[], bool]
    notify_boundary: Callable[[str], None]
    reset_trace: Callable[[], None]
    start_session: Callable[[bool], SessionLifecycleState]
    apply_state: Callable[[SessionLifecycleState], None]
    emit: Callable[[str], None]
    started_message: str


@dataclass(frozen=True, slots=True)
class ClearCommandPorts:
    session: NewSessionCommandPorts
    render_display: Callable[[], None]


def handle_title_command(
    request: ParsedCliCommand,
    *,
    ports: TitleCommandPorts,
) -> None:
    if not request.arguments:
        _show_title(ports.get_title(), ports=ports)
        return
    _show_title_update(ports.set_title(request.arguments), ports=ports)


def handle_new_session_command(
    request: ParsedCliCommand,
    *,
    ports: NewSessionCommandPorts,
) -> None:
    del request
    _start_new_session(ports, announce=True)


def handle_clear_command(
    request: ParsedCliCommand,
    *,
    ports: ClearCommandPorts,
) -> None:
    del request
    _start_new_session(ports.session, announce=False)
    ports.render_display()


def handle_resume_command(
    request: ParsedCliCommand,
    *,
    ports: ResumeCommandPorts,
) -> None:
    requested = request.arguments or "1"
    if not ports.repository_available():
        ports.emit(ports.text.unavailable)
        return

    target = resolve_resume_target(
        requested,
        recent_sessions=(
            ports.list_recent_sessions() if is_resume_index(requested) else ()
        ),
        resolve_named=ports.resolve_named,
    )
    if target.status is ResumeTargetStatus.INDEX_OUT_OF_RANGE:
        ports.emit(
            f"  Session index out of range: {requested} "
            f"(there are {target.available_count} recent sessions)"
        )
        ports.emit(ports.text.sessions_help)
        return
    try:
        result = ports.resume(target.session_id)
    except SessionNotFoundError:
        ports.emit(f"  Session not found: {requested}")
        ports.emit(ports.text.sessions_help)
        return
    except SessionAlreadyActiveError:
        ports.emit(ports.text.already_active)
        return
    ports.apply_state(result.state)
    ports.set_hydration(result.hydration)
    ports.emit(project_resume_summary(result, labels=ports.text.summary))
    if result.state.conversation_history:
        ports.display_history()


def handle_branch_command(
    request: ParsedCliCommand,
    *,
    ports: BranchCommandPorts,
) -> None:
    if not ports.conversation_history():
        ports.emit(ports.no_conversation_message)
        return
    if not ports.repository_available():
        ports.emit(ports.unavailable_message)
        return
    try:
        result = ports.branch(request.arguments)
    except Exception as exc:
        ports.emit(f"  Failed to create branch session: {exc}")
        return
    ports.apply_state(result.state)
    for line in project_branch_summary(result):
        ports.emit(line)


def _start_new_session(ports: NewSessionCommandPorts, *, announce: bool) -> None:
    has_agent = ports.agent_available()
    if has_agent:
        ports.notify_boundary("on_session_finalize")
    ports.reset_trace()
    state = ports.start_session(has_agent)
    ports.apply_state(state)
    if has_agent:
        ports.notify_boundary("on_session_reset")
    if announce:
        ports.emit(ports.started_message)


def _show_title(result: SessionTitleResult, *, ports: TitleCommandPorts) -> None:
    if result.status is SessionTitleStatus.UNAVAILABLE:
        ports.emit(ports.unavailable_message)
        return
    ports.emit(f"  Session ID: {result.session_id}")
    if result.status is SessionTitleStatus.CURRENT:
        ports.emit(f"  Title: {result.title}")
    elif result.status is SessionTitleStatus.PENDING:
        ports.emit(f"  Title (pending): {result.title}")
    else:
        ports.emit("  No title set. Usage: /title <your session title>")


def _show_title_update(
    result: SessionTitleResult,
    *,
    ports: TitleCommandPorts,
) -> None:
    if result.status is SessionTitleStatus.UNAVAILABLE:
        ports.emit(ports.unavailable_message)
    elif result.status is SessionTitleStatus.INVALID:
        ports.emit(
            f"  {result.error}"
            if result.error
            else "  Title is empty after cleanup. Please use printable characters."
        )
    elif result.status is SessionTitleStatus.UPDATED:
        ports.emit(f"  Session title set: {result.title}")
    elif result.status is SessionTitleStatus.NOT_FOUND:
        ports.emit("  Session not found in database.")
    elif result.status is SessionTitleStatus.CONFLICT and result.error:
        ports.emit(f"  {result.error}")
    elif result.status is SessionTitleStatus.CONFLICT:
        ports.emit(
            f"  Title '{result.title}' is already in use by session "
            f"{result.conflicting_session_id}"
        )
    elif result.status is SessionTitleStatus.QUEUED:
        ports.emit(
            f"  Session title queued: {result.title} (will be saved on first message)"
        )
