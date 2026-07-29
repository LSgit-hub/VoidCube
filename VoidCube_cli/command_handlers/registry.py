"""CLI composition root for explicit command-handler ports."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from VoidCube_app.session_lifecycle import (
    branch_session,
    get_session_title,
    resume_session,
    set_session_title,
    start_new_session,
)
from VoidCube_cli.clear_command_adapter import (
    ClearBannerState,
    ClearDisplayPorts,
    render_clear_display,
)
from VoidCube_cli.attachments import (
    _IMAGE_EXTENSIONS,
    _resolve_attachment_path,
    _split_path_input,
    _termux_example_image_path,
)
from VoidCube_cli.command_execution import initialize_command_execution
from VoidCube_cli.command_handlers.attachments import (
    ImageCommandPorts,
    ImageCommandText,
    PasteCommandPorts,
    PasteCommandText,
    handle_image_command,
    handle_paste_command,
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
from VoidCube_cli.command_handlers.info import (
    PluginsCommandPorts,
    ProfileCommandPorts,
    handle_plugins_command,
    handle_profile_command,
)
from VoidCube_cli.command_handlers.operations import (
    StopCommandPorts,
    handle_stop_command,
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
from VoidCube_cli.session_command_adapter import ResumeSummaryLabels


def _identity_translate(value: str, **kwargs: str) -> str:
    return kwargs.get("default", value)


def install_cli_command_execution(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str] = _identity_translate,
    chat_console_factory: Callable[[], Any] | None = None,
    compact_banner_factory: Callable[[], str] | None = None,
) -> None:
    """Register migrated command domains against narrow callable ports."""
    initialize_command_execution(
        host,
        command_handlers={
            "branch": lambda request: handle_branch_command(
                request,
                ports=BranchCommandPorts(
                    conversation_history=lambda: host.conversation_history,
                    repository_available=lambda: host._session_db is not None,
                    branch=lambda name: branch_session(
                        repository=host._session_db,
                        current_session_id=host.session_id,
                        conversation_history=host.conversation_history,
                        started_at=datetime.now(),
                        requested_title=name,
                        source=os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
                        model=host.model,
                        model_config={
                            "max_iterations": host.max_turns,
                            "reasoning_config": host.reasoning_config,
                        },
                    ),
                    apply_state=host._apply_session_lifecycle_state,
                    emit=emit,
                    no_conversation_message=translate(
                        "  No conversation to branch — send a message first."
                    ),
                    unavailable_message=translate(
                        "  Session database not available."
                    ),
                ),
            ),
            "clear": lambda request: handle_clear_command(
                request,
                ports=ClearCommandPorts(
                    session=_new_session_ports(host, translate=translate),
                    render_display=lambda: render_clear_display(
                        _clear_display_ports(
                            host,
                            emit=emit,
                            translate=translate,
                            chat_console_factory=chat_console_factory,
                            compact_banner_factory=compact_banner_factory,
                        )
                    ),
                ),
            ),
            "new": lambda request: handle_new_session_command(
                request,
                ports=_new_session_ports(host, translate=translate),
            ),
            "image": lambda request: handle_image_command(
                request,
                ports=ImageCommandPorts(
                    is_termux=_is_termux,
                    split_path=_split_path_input,
                    resolve_path=_resolve_attachment_path,
                    supported_extensions=_IMAGE_EXTENSIONS,
                    append_attachment=host._attached_images.append,
                    termux_example_path=_termux_example_image_path,
                    emit=emit,
                    text=ImageCommandText(
                        dim_prefix="\033[2m",
                        reset_suffix="\033[0m",
                        tip_prefix=translate("tips.tip_prefix", default="Tip:"),
                    ),
                ),
            ),
            "paste": lambda request: handle_paste_command(
                request,
                ports=PasteCommandPorts(
                    is_termux=_is_termux,
                    has_clipboard_image=_has_clipboard_image,
                    attach_clipboard_image=host._try_attach_clipboard_image,
                    attachment_count=lambda: len(host._attached_images),
                    emit=emit,
                    text=PasteCommandText(
                        termux_unavailable=(
                            "  \033[2mClipboard image paste is not available on "
                            "Termux — use /image <path> or paste a local image "
                            f"path like {_termux_example_image_path()}\033[0m"
                        ),
                        extraction_failed=(
                            "  \033[2m(>_<) Clipboard has an image but "
                            "extraction failed\033[0m"
                        ),
                        no_image=(
                            "  \033[2m(._.) No image found in clipboard\033[0m"
                        ),
                    ),
                ),
            ),
            "plugins": lambda request: handle_plugins_command(
                request,
                ports=PluginsCommandPorts(
                    discover=_discover_plugins,
                    list_plugins=_list_plugin_records,
                    plugins_home=_display_voidcube_home,
                    emit=print,
                ),
            ),
            "profile": lambda request: handle_profile_command(
                request,
                ports=ProfileCommandPorts(
                    home=_voidcube_home,
                    display_home=_display_voidcube_home,
                    profiles_parent=lambda: Path.home() / ".VoidCube" / "profiles",
                    emit=print,
                    default_profile_message=translate("profile_default"),
                ),
            ),
            "queue": lambda request: handle_queue_command(
                request,
                ports=QueueCommandPorts(
                    enqueue=host._pending_input.put,
                    agent_running=lambda: host._agent_running,
                    emit=emit,
                ),
            ),
            "retry": lambda request: handle_retry_command(
                request,
                ports=RetryCommandPorts(
                    remove_last_user_turn=lambda: host._remove_last_user_turn(
                        empty_message="no_messages_to_retry",
                        no_user_message="no_user_message_found_to_retry",
                    ),
                    enqueue=host._pending_input.put,
                    emit=print,
                ),
            ),
            "resume": lambda request: handle_resume_command(
                request,
                ports=ResumeCommandPorts(
                    repository_available=lambda: host._session_db is not None,
                    show_recent_sessions=lambda: host._show_recent_sessions(
                        reason="resume"
                    ),
                    list_recent_sessions=lambda: host._list_recent_sessions(limit=50),
                    resolve_named=_resolve_named_session,
                    resume=lambda target_id: resume_session(
                        repository=host._session_db,
                        current_session_id=host.session_id,
                        target_session_id=target_id,
                        session_start=host.session_start,
                    ),
                    apply_state=host._apply_session_lifecycle_state,
                    set_hydration=lambda value: setattr(
                        host, "_session_hydration", value
                    ),
                    display_history=host._display_resumed_history,
                    emit=emit,
                    text=ResumeCommandText(
                        usage=translate(
                            "  Usage: /resume <session_id_or_title_or_number>"
                        ),
                        hint=translate(
                            "tips.resume_hint",
                            default=(
                                "Tip:   Use /history or `VoidCube sessions list` "
                                "to find sessions."
                            ),
                        ),
                        unavailable=translate(
                            "  Session database not available."
                        ),
                        sessions_help=translate(
                            "  Use /history or `VoidCube sessions list` to see "
                            "available sessions."
                        ),
                        already_active=translate("  Already on that session."),
                        summary=ResumeSummaryLabels(
                            resumed_session=translate(
                                "prompts.resumed_session",
                                default="Resumed session",
                            ),
                            user_messages=translate(
                                "prompts.user_messages",
                                default="user messages",
                            ),
                            total=translate("prompts.total", default="total"),
                            no_messages_starting_fresh=translate(
                                "prompts.no_messages_starting_fresh",
                                default="no messages, starting fresh",
                            ),
                        ),
                    ),
                ),
            ),
            "statusbar": lambda request: handle_statusbar_command(
                request,
                ports=StatusBarCommandPorts(
                    visible=lambda: host._status_bar_visible,
                    set_visible=lambda value: setattr(host, "_status_bar_visible", value),
                    emit=lambda text: host.console.print(text),
                ),
            ),
            "stop": lambda request: handle_stop_command(
                request,
                ports=StopCommandPorts(
                    list_processes=_list_processes,
                    kill_all=_kill_all_processes,
                    emit=print,
                    no_running_message=(
                        f"  {translate('prompts.no_running_background_processes')}"
                    ),
                    stopping_message=lambda count: (
                        f"  {translate('prompts.stopping_background_processes', count=count)}"
                    ),
                    stopped_message=lambda count: (
                        f"  ✅ {translate('prompts.stopped_background_processes', count=count)}"
                    ),
                ),
            ),
            "title": lambda request: handle_title_command(
                request,
                ports=TitleCommandPorts(
                    get_title=lambda: get_session_title(
                        repository=host._session_db,
                        session_id=host.session_id,
                        pending_title=host._pending_title,
                    ),
                    set_title=lambda raw_title: set_session_title(
                        repository=host._session_db,
                        session_id=host.session_id,
                        raw_title=raw_title,
                    ),
                    set_pending_title=lambda value: setattr(host, "_pending_title", value),
                    emit=emit,
                    unavailable_message=translate("  Session database not available."),
                ),
            ),
        },
    )


def _resolve_named_session(value: str) -> str | None:
    from VoidCube_cli.main import _resolve_session_by_name_or_id

    return _resolve_session_by_name_or_id(value)


def _new_session_ports(
    host: Any,
    *,
    translate: Callable[..., str],
) -> NewSessionCommandPorts:
    return NewSessionCommandPorts(
        agent_available=lambda: host.agent is not None,
        notify_boundary=lambda event_type: _notify_session_boundary(host, event_type),
        reset_trace=lambda: setattr(host, "_current_trace_id", ""),
        start_session=lambda create_record: start_new_session(
            repository=host._session_db,
            current_session_id=host.session_id,
            started_at=datetime.now(),
            source=os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
            model=host.model,
            model_config={
                "max_iterations": host.max_turns,
                "reasoning_config": host.reasoning_config,
            },
            create_record=create_record,
        ),
        apply_state=host._apply_session_lifecycle_state,
        emit=print,
        started_message=translate("new_session_started"),
    )


def _notify_session_boundary(host: Any, event_type: str) -> None:
    try:
        from VoidCube_cli.plugins import invoke_hook

        invoke_hook(
            event_type,
            session_id=host.agent.session_id if host.agent else None,
            platform=getattr(host, "platform", None) or "cli",
        )
    except Exception:
        pass


def _clear_display_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
    chat_console_factory: Callable[[], Any] | None,
    compact_banner_factory: Callable[[], str] | None,
) -> ClearDisplayPorts:
    kwargs: dict[str, Any] = {}
    if chat_console_factory is not None:
        kwargs["chat_console_factory"] = chat_console_factory
    if compact_banner_factory is not None:
        kwargs["compact_banner_factory"] = compact_banner_factory
    return ClearDisplayPorts(
        tui_active=lambda: host._app is not None,
        clear_tui_screen=lambda: _clear_tui_screen(host._app.output),
        show_standalone_banner=host.show_banner,
        compact=lambda: host.compact,
        terminal_width=lambda: shutil.get_terminal_size().columns,
        banner_state=lambda: ClearBannerState(
            model=host.model,
            cwd=os.getenv("TERMINAL_CWD", os.getcwd()),
            enabled_toolsets=tuple(host.enabled_toolsets),
            session_id=host.session_id,
            context_length=_context_length(host.agent),
            conversation_history=tuple(host.conversation_history),
        ),
        emit_tui=emit,
        emit_plain=print,
        fresh_start_message=translate(
            "tips.fresh_start",
            default=(
                "✨ (◕‿◕)✨ Fresh start! Screen cleared and conversation reset."
            ),
        ),
        **kwargs,
    )


def _clear_tui_screen(output: Any) -> None:
    output.erase_screen()
    output.cursor_goto(0, 0)
    output.flush()


def _context_length(agent: Any) -> int | None:
    compressor = getattr(agent, "context_compressor", None)
    return getattr(compressor, "context_length", None)


def _list_processes() -> list[dict[str, Any]]:
    from tools.process_registry import process_registry

    return process_registry.list_sessions()


def _kill_all_processes() -> int:
    from tools.process_registry import process_registry

    return process_registry.kill_all()


def _voidcube_home() -> Path:
    from VoidCube_core.constants import get_VoidCube_home

    return get_VoidCube_home()


def _display_voidcube_home() -> str:
    from VoidCube_core.constants import display_VoidCube_home

    return display_VoidCube_home()


def _discover_plugins() -> object:
    from VoidCube_cli.plugins import discover_plugins

    return discover_plugins()


def _list_plugin_records() -> list[dict[str, Any]]:
    from VoidCube_cli.plugins import get_plugin_manager

    return list(get_plugin_manager().list_plugins().values())


def _is_termux() -> bool:
    from VoidCube_core.constants import is_termux

    return is_termux()


def _has_clipboard_image() -> bool:
    from VoidCube_cli.clipboard import has_clipboard_image

    return has_clipboard_image()
