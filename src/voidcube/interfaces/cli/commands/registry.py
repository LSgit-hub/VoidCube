"""CLI composition root for explicit command-handler ports."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..clear_command_adapter import (
    ClearBannerState,
    ClearDisplayPorts,
    render_clear_display,
)
from ..attachments import (
    _IMAGE_EXTENSIONS,
    _resolve_attachment_path,
    _split_path_input,
    _termux_example_image_path,
)
from .execution import initialize_command_execution
from .router import parse_cli_command
from .handlers.attachments import (
    ImageCommandPorts,
    ImageCommandText,
    PasteCommandPorts,
    PasteCommandText,
    handle_image_command,
    handle_paste_command,
)
from .handlers.autonomous import (
    AutonomousCommandPorts,
    handle_auto_command,
    handle_auto_q_command,
)
from ..autonomous.events import AutonomousPanelEventPorts
from .handlers.background import (
    BackgroundCommandPorts,
    BackgroundCommandText,
    handle_background_command,
)
from .handlers.btw import (
    BtwCommandPorts,
    BtwCommandText,
    handle_btw_command,
)
from .handlers.browser import (
    BrowserCommandPorts,
    handle_browser_command,
)
from .handlers.display import (
    ConfigDisplayPorts,
    HelpDisplayPorts,
    HelpDisplayText,
    MemoryDisplayPorts,
    ProviderDisplayPorts,
    ProviderDisplaySnapshot,
    SessionStatusDisplayPorts,
    StatusBarCommandPorts,
    ToolsCatalogPorts,
    ToolsetsDisplayPorts,
    handle_config_display_command,
    handle_help_display_command,
    handle_memory_display_command,
    handle_provider_display_command,
    handle_session_status_command,
    handle_statusbar_command,
    handle_tools_catalog_command,
    handle_toolsets_display_command,
)
from .handlers.fast import (
    FastCommandPorts,
    handle_fast_command,
)
from .handlers.goal import (
    GoalCommandPorts,
    handle_goal_command,
)
from .handlers.compression import (
    CompressionCommandPorts,
    handle_compression_command,
)
from .handlers.chat_blocks import (
    ChatBlockCommandPorts,
    handle_export_command,
    handle_find_command,
)
from .handlers.input import (
    QueueCommandPorts,
    RetryCommandPorts,
    handle_queue_command,
    handle_retry_command,
)
from .handlers.info import (
    PluginsCommandPorts,
    ProfileCommandPorts,
    UsageCommandPorts,
    UsageDisplaySnapshot,
    handle_plugins_command,
    handle_profile_command,
    handle_usage_command,
)
from .handlers.language import (
    LanguageCommandPorts,
    handle_language_command,
)
from .handlers.model import (
    ModelCommandPorts,
    handle_model_command,
)
from .handlers.mcp import (
    McpCommandPorts,
    handle_mcp_command,
)
from .handlers.personality import (
    PersonalityCommandPorts,
    handle_personality_command,
)
from .handlers.preset import (
    PresetCommandPorts,
    PresetCommandText,
    handle_preset_command,
)
from .handlers.plan import (
    PlanCommandPorts,
    handle_plan_command,
)
from .handlers.reasoning import (
    ReasoningCommandPorts,
    handle_reasoning_command,
    parse_reasoning_config,
)
from .handlers.history import (
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
from .handlers.operations import (
    ApiCommandPorts,
    CancelCommandPorts,
    DebugCommandPorts,
    DoctorCommandPorts,
    McpReloadRuntimePorts,
    ReloadMcpCommandPorts,
    StopCommandPorts,
    handle_api_command,
    handle_cancel_command,
    handle_debug_command,
    handle_doctor_command,
    handle_reload_mcp_command,
    handle_stop_command,
    reload_mcp_servers,
)
from .handlers.rollback import (
    RollbackCommandPorts,
    RollbackCommandText,
    handle_rollback_command,
)
from .handlers.session import (
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
from .handlers.skills import (
    SkillRecord,
    SkillSearchResult,
    SkillsCommandPorts,
    handle_skills_command,
)
from .handlers.tools import (
    ToolsCommandPorts,
    ToolsCommandText,
    handle_tools_command,
)
from .handlers.voice import (
    VoiceCommandPorts,
    handle_voice_command,
)
from ....application.scheduling.background_task_runtime import BackgroundTaskSnapshot
from .handlers.tasks import (
    TaskMoveResult,
    TasksCommandPorts,
    handle_tasks_command,
)
from ..session_command_adapter import ResumeSummaryLabels


def _identity_translate(value: str, **kwargs: str) -> str:
    return kwargs.get("default", value)


def install_cli_command_execution(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str] = _identity_translate,
    chat_console_factory: Callable[[], Any] | None = None,
    compact_banner_factory: Callable[[], str] | None = None,
    skill_commands: Callable[[], Mapping[str, Mapping[str, str]]] | None = None,
    autonomous_command_ports: AutonomousCommandPorts | None = None,
) -> None:
    """Register migrated command domains against narrow callable ports."""
    initialize_command_execution(
        host,
        command_handlers={
            "auto": lambda request: handle_auto_command(
                request,
                ports=_require_autonomous_command_ports(autonomous_command_ports),
            ),
            "auto-q": lambda request: handle_auto_q_command(
                request,
                ports=_require_autonomous_command_ports(autonomous_command_ports),
            ),
            "background": lambda request: handle_background_command(
                request,
                ports=BackgroundCommandPorts(
                    start_background=host._start_background_agent_task,
                    emit=emit,
                    text=BackgroundCommandText(
                        usage="  Usage: /background <prompt>",
                        example="  Example: /background Summarize the top HN stories today",
                        description=(
                            "  The task runs in a separate session and results display here when done."
                        ),
                    ),
                ),
            ),
            "btw": lambda request: handle_btw_command(
                request,
                ports=BtwCommandPorts(
                    start_btw=host._start_btw_side_question,
                    emit=emit,
                    text=BtwCommandText(
                        usage="  Usage: /btw <question>",
                        example=(
                            "  Example: /btw what module owns session title sanitization?"
                        ),
                        description=(
                            "  Answers using session context. No tools, not persisted."
                        ),
                    ),
                ),
            ),
            "api": lambda request: handle_api_command(
                request,
                ports=_api_command_ports(host),
            ),
            "branch": lambda request: handle_branch_command(
                request,
                ports=BranchCommandPorts(
                    conversation_history=lambda: host.conversation_history,
                    repository_available=lambda: host._session_db is not None,
                    branch=lambda name: host._ensure_application_runtime().branch_session(
                        repository=host._session_db,
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
            "browser": lambda request: handle_browser_command(
                request,
                ports=_browser_command_ports(host, emit=emit),
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
            "config": lambda request: handle_config_display_command(
                request,
                ports=_config_display_ports(host, emit=emit, translate=translate),
            ),
            "compress": lambda request: handle_compression_command(
                request,
                ports=_compression_command_ports(host, emit=emit),
            ),
            "debug": lambda request: handle_debug_command(
                request,
                ports=_debug_command_ports(),
            ),
            "doctor": lambda request: handle_doctor_command(
                request,
                ports=_doctor_command_ports(),
            ),
            "fast": lambda request: handle_fast_command(
                request,
                ports=_fast_command_ports(host, emit=emit),
            ),
            "help": lambda request: handle_help_display_command(
                request,
                ports=_help_display_ports(
                    host,
                    emit=emit,
                    translate=translate,
                    chat_console_factory=chat_console_factory,
                    skill_commands=skill_commands,
                ),
            ),
            "goal": lambda request: handle_goal_command(
                request,
                ports=_goal_command_ports(host, emit=emit, translate=translate),
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
            "language": lambda request: handle_language_command(
                request,
                ports=_language_command_ports(emit=emit),
            ),
            "model": lambda request: handle_model_command(
                request,
                ports=_model_command_ports(host, emit=emit),
            ),
            "memory": lambda request: handle_memory_display_command(
                request,
                ports=_memory_display_ports(),
            ),
            "mcp": lambda request: handle_mcp_command(
                request,
                ports=_mcp_command_ports(),
            ),
            "history": lambda request: handle_history_command(
                request,
                ports=HistoryCommandPorts(
                    conversation_history=lambda: host.conversation_history,
                    show_recent_sessions=lambda: host._show_recent_sessions(
                        reason="history"
                    ),
                    emit=emit,
                    no_history_message=translate("no_conversation_history_yet"),
                    tools_label=translate("tools"),
                ),
            ),
            "find": lambda request: handle_find_command(
                request,
                ports=_chat_block_command_ports(host, emit=emit),
            ),
            "export": lambda request: handle_export_command(
                request,
                ports=_chat_block_command_ports(host, emit=emit),
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
            "personality": lambda request: handle_personality_command(
                request,
                ports=_personality_command_ports(host),
            ),
            "preset": lambda request: handle_preset_command(
                request,
                ports=_preset_command_ports(emit=emit),
            ),
            "plan": lambda request: handle_plan_command(
                request,
                ports=_plan_command_ports(host, emit=emit),
            ),
            "reasoning": lambda request: handle_reasoning_command(
                request,
                ports=_reasoning_command_ports(host, emit=emit),
            ),
            "provider": lambda request: handle_provider_display_command(
                request,
                ports=_provider_display_ports(host, emit=emit, translate=translate),
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
                    remove_last_user_turn=lambda: remove_last_user_turn_from_history(
                        ports=_history_mutation_ports(host, emit=emit),
                        empty_message=translate("no_messages_to_retry"),
                        no_user_message=translate("no_user_message_found_to_retry"),
                    ),
                    enqueue=host._pending_input.put,
                    emit=print,
                ),
            ),
            "reload-mcp": lambda request: handle_reload_mcp_command(
                request,
                ports=ReloadMcpCommandPorts(
                    run_reload=lambda: reload_mcp_for_host(host, emit=emit),
                ),
            ),
            "save": lambda request: handle_save_conversation_command(
                request,
                ports=SaveConversationPorts(
                    conversation_history=lambda: host.conversation_history,
                    model=lambda: host.model,
                    session_start=lambda: host.session_start,
                    now=datetime.now,
                    working_directory=Path.cwd,
                    write_json=write_conversation_export,
                    emit=emit,
                    no_conversation_message=translate("no_conversation_to_save"),
                ),
            ),
            "skills": lambda request: handle_skills_command(
                request,
                ports=_skills_command_ports(emit=emit),
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
                    resume=lambda target_id: host._ensure_application_runtime().resume_session(
                        repository=host._session_db,
                        target_session_id=target_id,
                        session_start=host.session_start,
                    ),
                    apply_state=host._apply_session_lifecycle_state,
                    set_hydration=host._ensure_application_runtime().set_session_hydration,
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
            "rollback": lambda request: handle_rollback_command(
                request,
                ports=_rollback_command_ports(host, emit=emit, translate=translate),
            ),
            "statusbar": lambda request: handle_statusbar_command(
                request,
                ports=StatusBarCommandPorts(
                    visible=lambda: host._status_bar_visible,
                    set_visible=lambda value: setattr(host, "_status_bar_visible", value),
                    emit=lambda text: host.console.print(text),
                ),
            ),
            "status": lambda request: handle_session_status_command(
                request,
                ports=_session_status_display_ports(host),
            ),
            "tasks": lambda request: handle_tasks_command(
                request,
                ports=_tasks_command_ports(host, emit=emit),
            ),
            "cancel": lambda request: handle_cancel_command(
                request,
                ports=CancelCommandPorts(
                    agent_running=lambda: bool(host._agent_running and host.agent),
                    interrupt_agent=lambda: host.agent.interrupt(None),
                    emit=emit,
                    cancel_scheduler=lambda: bool(
                        getattr(host, "_turn_scheduler_runtime", None)
                        and host._turn_scheduler_runtime.cancel_user()
                    ),
                ),
            ),
            "toolsets": lambda request: handle_toolsets_display_command(
                request,
                ports=_toolsets_display_ports(host, emit=emit, translate=translate),
            ),
            "tools": lambda request: handle_tools_command(
                request,
                ports=_tools_command_ports(host, emit=emit, translate=translate),
            ),
            "usage": lambda request: handle_usage_command(
                request,
                ports=_usage_command_ports(host, emit=print),
            ),
            "voice": lambda request: handle_voice_command(
                request,
                ports=VoiceCommandPorts(
                    enable=host._enable_voice_mode,
                    disable=host._disable_voice_mode,
                    tts_status=host._show_voice_tts_status,
                    tts_speak=host._speak_voice_tts,
                    show_status=host._show_voice_status,
                    voice_mode_enabled=lambda: host._voice_state().mode,
                    emit=emit,
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
                    get_title=lambda: host._ensure_application_runtime().get_session_title(
                        repository=host._session_db,
                    ),
                    set_title=lambda raw_title: host._ensure_application_runtime().set_session_title(
                        repository=host._session_db,
                        raw_title=raw_title,
                    ),
                    emit=emit,
                    unavailable_message=translate("  Session database not available."),
                ),
            ),
            "undo": lambda request: handle_undo_command(
                request,
                ports=UndoCommandPorts(
                    remove_last_user_turn=lambda: remove_last_user_turn_from_history(
                        ports=_history_mutation_ports(host, emit=emit),
                        empty_message=translate("no_messages_to_undo"),
                        no_user_message=translate("no_user_message_found_to_undo"),
                    ),
                    emit=emit,
                ),
            ),
        },
    )


def _require_autonomous_command_ports(
    ports: AutonomousCommandPorts | None,
) -> AutonomousCommandPorts:
    if ports is None:
        raise RuntimeError("Autonomous command ports were not configured.")
    return ports


def _language_command_ports(*, emit: Callable[[str], None]) -> LanguageCommandPorts:
    from ....infrastructure.config.configuration import read_raw_config, save_config
    from .catalog import rebuild_lookups
    from ..i18n import get_available_locales, get_i18n, set_locale, t

    def persist_locale(locale: str) -> bool:
        try:
            config = read_raw_config() or {}
            display = config.get("display")
            config["display"] = dict(display) if isinstance(display, dict) else {}
            config["display"]["language"] = locale
            save_config(config)
        except Exception:
            return False
        return True

    def rebuild_lookups_safely() -> None:
        try:
            rebuild_lookups()
        except Exception:
            pass

    return LanguageCommandPorts(
        current_locale=lambda: get_i18n().get_current_locale(),
        available_locales=get_available_locales,
        translate=t,
        set_locale=set_locale,
        rebuild_command_lookups=rebuild_lookups_safely,
        persist_locale=persist_locale,
        emit=emit,
    )


def _preset_command_ports(*, emit: Callable[[str], None]) -> PresetCommandPorts:
    from ....extensions.tools.preset_engine import apply_preset, list_presets, load_preset

    return PresetCommandPorts(
        list_presets=list_presets,
        load_preset=load_preset,
        apply_preset=apply_preset,
        emit=emit,
        text=PresetCommandText(
            dim="\033[2m",
            accent="\033[38;5;39m",
            bold="\033[1m",
            reset="\033[0m",
        ),
    )


def autonomous_command_ports_for_host(
    host: Any,
    *,
    event_ports: AutonomousPanelEventPorts,
    emit: Callable[[str], None],
    refresh_gateway_cli_presence: Callable[..., None],
    interrupt_current_task: Callable[..., bool],
    push_cli_agent_scene: Callable[..., bool],
    thread_factory: Callable[..., Any],
) -> AutonomousCommandPorts:
    """Compose autonomous-gate operations against explicit CLI runtime callbacks."""
    from ..autonomous.gate import (
        handle_auto_command as activate_gate,
        handle_auto_q_command as deactivate_gate,
    )

    def activate(focus: str) -> None:
        command = "/auto" if not focus else f"/auto {focus}"
        activate_gate(
            host,
            command,
            event_ports=event_ports,
            cprint=emit,
            refresh_gateway_cli_presence_callback=refresh_gateway_cli_presence,
            thread_factory=thread_factory,
        )

    return AutonomousCommandPorts(
        activate=activate,
        deactivate=lambda: deactivate_gate(
            host,
            event_ports=event_ports,
            cprint=emit,
            interrupt_current_task_callback=interrupt_current_task,
            push_cli_agent_scene_callback=push_cli_agent_scene,
            thread_factory=thread_factory,
        ),
    )


def exit_autonomous_gate_fast_for_host(
    host: Any,
    *,
    event_ports: AutonomousPanelEventPorts,
    emit: Callable[[str], None],
    interrupt_current_task: Callable[..., bool],
    push_cli_agent_scene: Callable[..., bool],
) -> bool:
    """Run the immediate autonomous-gate exit with the same runtime bindings."""
    from ..autonomous.gate import exit_autonomous_gate_fast

    return exit_autonomous_gate_fast(
        host,
        event_ports=event_ports,
        cprint=emit,
        interrupt_current_task_callback=interrupt_current_task,
        push_cli_agent_scene_callback=push_cli_agent_scene,
    )


def _resolve_named_session(value: str) -> str | None:
    from ..entrypoints.session import _resolve_session_by_name_or_id

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
        start_session=lambda create_record: host._ensure_application_runtime().start_new_session(
            repository=host._session_db,
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


def _tools_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> ToolsCommandPorts:
    from argparse import Namespace

    from ....infrastructure.config.configuration import load_config
    from ..tools_config import tools_disable_enable_command
    try:
        from voidcube.extensions.tools.configuration import get_platform_tools
    except (ModuleNotFoundError, ImportError):
        from voidcube.extensions.tools.configuration import get_platform_tools

    return ToolsCommandPorts(
        render_catalog=lambda: render_tools_for_host(
            host, emit=emit, translate=translate
        ),
        list_configuration=lambda: tools_disable_enable_command(
            Namespace(tools_action="list", platform="cli")
        ),
        change_configuration=lambda action, names: tools_disable_enable_command(
            Namespace(tools_action=action, names=list(names), platform="cli")
        ),
        load_enabled_toolsets=lambda: get_platform_tools(load_config(), "cli"),
        set_enabled_toolsets=lambda value: setattr(host, "enabled_toolsets", value),
        reset_session=lambda: handle_new_session_command(
            parse_cli_command("/new"),
            ports=_new_session_ports(host, translate=translate),
        ),
        emit=emit,
        text=ToolsCommandText(
            usage=lambda action: translate(
                "prompts.tools_usage", subcommand=action
            ),
            builtin_example=lambda action: translate(
                "prompts.tools_builtin_example", subcommand=action
            ),
            mcp_example=lambda action: translate(
                "prompts.tools_mcp_example", subcommand=action
            ),
            changing=_format_tool_config_change,
            session_reset=_format_tool_config_session_reset(),
        ),
    )


def _tasks_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> TasksCommandPorts:
    from time import time

    from ..cli_ui import ChatConsole, _rich_text_from_ansi

    def managers() -> list[Any]:
        agent = getattr(host, "agent", None)
        if agent is None:
            return []
        result: list[Any] = []
        manager_map = getattr(agent, "_subagent_display_managers", None)
        if isinstance(manager_map, dict):
            for manager in manager_map.values():
                if manager is not None and manager not in result:
                    result.append(manager)
        history = getattr(agent, "_subagent_display_history", None)
        if isinstance(history, dict):
            for manager in reversed(tuple(history.values())):
                if manager is not None and manager not in result:
                    result.append(manager)
        manager = getattr(agent, "_subagent_display_manager", None)
        if manager is not None and manager not in result:
            result.append(manager)
        return result

    def render_subagent_tasks() -> str:
        panels = [str(manager.render_tasks_command()) for manager in managers()]
        if len(panels) <= 1:
            return panels[0] if panels else ""
        merged = [panels[0]]
        for panel in panels[1:]:
            lines = panel.splitlines()
            if lines and "子代理" in lines[0]:
                lines = lines[1:]
            if lines:
                merged.append("\n".join(lines))
        return "\n".join(merged)

    def render_subagent_task(task_ref: str) -> str | None:
        for manager in managers():
            detail = manager.render_task_detail(task_ref)
            if detail is not None:
                return str(detail)
        return None

    def render_subagent_task_log(task_ref: str) -> str | None:
        for manager in managers():
            log = manager.render_task_log(task_ref)
            if log is not None:
                return str(log)
        return None

    def background_tasks() -> tuple[BackgroundTaskSnapshot, ...]:
        return tuple(host._list_background_tasks())

    def move(task_ref: str, *, background: bool) -> TaskMoveResult:
        for manager in managers():
            task = manager.resolve_task_ref(task_ref)
            if task is None:
                continue
            try:
                moved = (
                    manager.send_to_background(task.task_id)
                    if background
                    else manager.bring_to_foreground(task.task_id)
                )
            except Exception as exc:
                return TaskMoveResult(found=True, moved=False, error=str(exc))
            return TaskMoveResult(found=True, moved=bool(moved))
        return TaskMoveResult(found=False, moved=False)

    def render_output(value: str) -> None:
        ChatConsole().print(_rich_text_from_ansi(value))

    return TasksCommandPorts(
        has_display_managers=lambda: bool(managers()),
        render_subagent_tasks=render_subagent_tasks,
        render_subagent_task=render_subagent_task,
        render_subagent_task_log=render_subagent_task_log,
        background_tasks=background_tasks,
        now=time,
        move_to_background=lambda task_ref: move(task_ref, background=True),
        bring_to_foreground=lambda task_ref: move(task_ref, background=False),
        render_output=render_output,
        emit=emit,
        invalidate=lambda: host._invalidate(min_interval=0) if getattr(host, "_app", None) else None,
    )


def _plan_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> PlanCommandPorts:
    from voidcube.extensions.skills.commands import build_plan_path, build_skill_invocation_message
    from ..cli_ui import ChatConsole

    pending_input = getattr(host, "_pending_input", None)
    return PlanCommandPorts(
        build_plan_path=build_plan_path,
        build_skill_message=lambda command, instruction, runtime_note: (
            build_skill_invocation_message(
                command,
                instruction,
                task_id=str(getattr(host, "session_id", "") or ""),
                runtime_note=runtime_note,
            )
        ),
        enqueue=pending_input.put if pending_input is not None else None,
        emit=emit,
        render_error=lambda message: ChatConsole().print(f"[bold red]{message}[/]"),
    )


def _goal_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> GoalCommandPorts:
    from ..session_goal_runtime import (
        clear_goal,
        create_goal,
        get_goal,
        update_goal,
    )

    pending_input = getattr(host, "_pending_input", None)
    return GoalCommandPorts(
        get_goal=lambda: get_goal(host),
        create_goal=lambda objective: create_goal(host, objective),
        update_goal=lambda status, reason: update_goal(host, status, reason),
        clear_goal=lambda: clear_goal(host),
        start_goal=pending_input.put if pending_input is not None else None,
        reset_agent=lambda: setattr(host, "agent", None),
        emit=emit,
        translate=translate,
    )


def _skills_command_ports(*, emit: Callable[[str], None]) -> SkillsCommandPorts:
    from voidcube.runtime.agent.prompt_builder import clear_skills_system_prompt_cache
    try:
        from voidcube.extensions.skills import get_all_skills_dirs
        from voidcube.extensions.skills.hub import (
            HubLockFile,
            create_source_router,
            install_skill_from_sources,
            unified_search,
            uninstall_skill,
        )
    except (ModuleNotFoundError, ImportError):
        from voidcube.extensions.skills import get_all_skills_dirs
        from voidcube.extensions.skills.hub import (
            HubLockFile,
            create_source_router,
            install_skill_from_sources,
            unified_search,
            uninstall_skill,
        )

    def builtin_skills() -> tuple[tuple[str, tuple[str, ...]], ...]:
        import os

        categories: dict[str, set[str]] = {}
        excluded = {".git", ".github", ".hub", "__pycache__"}
        for base_dir in get_all_skills_dirs():
            if not base_dir.is_dir():
                continue
            for root, directories, files in os.walk(base_dir):
                directories[:] = [item for item in directories if item not in excluded]
                if "SKILL.md" not in files:
                    continue
                relative = Path(root).relative_to(base_dir).parts
                if not relative:
                    continue
                category = relative[0] if len(relative) > 1 else "其他"
                categories.setdefault(category, set()).add(relative[-1])
        return tuple(
            (category, tuple(sorted(names)))
            for category, names in sorted(categories.items())
        )

    def installed_skills() -> tuple[SkillRecord, ...]:
        return tuple(
            SkillRecord(
                name=str(skill.get("name") or "unknown"),
                source=str(skill.get("source") or "unknown"),
                trust_level=str(skill.get("trust_level") or "unknown"),
            )
            for skill in HubLockFile().list_installed()
        )

    def search(query: str) -> tuple[SkillSearchResult, ...]:
        return tuple(
            SkillSearchResult(
                name=result.name,
                description=result.description,
                source=result.source,
                trust_level=result.trust_level,
                tags=tuple(result.tags),
            )
            for result in unified_search(query, create_source_router(), limit=10)
        )

    def refresh_cache() -> None:
        clear_skills_system_prompt_cache()
        from .. import application as cli_app

        cli_app._skill_commands_cache = None
        cli_app._skill_cmd_imports = None

    return SkillsCommandPorts(
        builtin_skills=builtin_skills,
        installed_skills=installed_skills,
        search=search,
        install=lambda name: install_skill_from_sources(
            name, sources=create_source_router()
        ),
        uninstall=uninstall_skill,
        refresh_cache=refresh_cache,
        emit=emit,
    )


def _format_tool_config_change(action: str, names: Sequence[str]) -> str:
    from ..cli_ui import _ACCENT, _RST

    verb = "Disabling" if action == "disable" else "Enabling"
    return f"{_ACCENT}{verb} {', '.join(names)}...{_RST}"


def _format_tool_config_session_reset() -> str:
    from ..cli_ui import _DIM, _RST

    return f"{_DIM}Session reset. New tool configuration is active.{_RST}"


def _chat_block_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> ChatBlockCommandPorts:
    return ChatBlockCommandPorts(
        blocks=lambda: host._chat_blocks().blocks(),
        session_id=lambda: str(host.session_id or ""),
        now=datetime.now,
        working_directory=Path.cwd,
        emit=emit,
    )


def _history_mutation_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> HistoryMutationPorts:
    def synchronize_agent_history(history: list[dict[str, Any]]) -> None:
        if host.agent:
            host.agent.replace_persisted_session_history(history)

    return HistoryMutationPorts(
        conversation_history=lambda: host.conversation_history,
        repository=lambda: host._session_db,
        session_id=lambda: host.session_id,
        remove_last_user_turn=lambda repository: (
            host._ensure_application_runtime().remove_last_user_turn(
                repository=repository,
            )
        ),
        synchronize_agent_history=synchronize_agent_history,
        hydration=lambda: host._ensure_application_runtime().state.session_hydration,
        set_hydration=host._ensure_application_runtime().set_session_hydration,
        emit=emit,
    )


def _compression_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> CompressionCommandPorts:
    from ....domain.agent.manual_compression_feedback import summarize_manual_compression
    from ....infrastructure.providers.model_metadata import estimate_messages_tokens_rough

    def compress(
        history: Sequence[Mapping[str, Any]],
        approx_tokens: int,
        focus_topic: str | None,
    ) -> list[dict[str, Any]]:
        agent = host.agent
        compressed, _ = agent._compress_context(
            list(history),
            agent._cached_system_prompt or "",
            approx_tokens=approx_tokens,
            focus_topic=focus_topic,
        )
        return compressed

    def synchronize_compressed_session(
        history: list[dict[str, Any]],
        agent: Any,
    ) -> None:
        agent.persist_compressed_session_history(history)
        host.conversation_history = history
        host.session_id = agent.session_id
        host._ensure_application_runtime().clear_session_hydration()

    return CompressionCommandPorts(
        conversation_history=lambda: host.conversation_history,
        agent=lambda: getattr(host, "agent", None),
        compression_enabled=lambda agent: bool(agent.compression_enabled),
        estimate_tokens=estimate_messages_tokens_rough,
        compress=compress,
        synchronize_compressed_session=synchronize_compressed_session,
        summarize=summarize_manual_compression,
        emit=emit,
    )


def render_toolsets_for_host(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> None:
    """Render toolsets for non-slash CLI entry points such as --list-toolsets."""
    handle_toolsets_display_command(
        parse_cli_command("/toolsets"),
        ports=_toolsets_display_ports(host, emit=emit, translate=translate),
    )


def render_tools_for_host(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> None:
    """Render the read-only tool catalog for slash and flag entry points."""
    handle_tools_catalog_command(
        parse_cli_command("/tools"),
        ports=_tools_catalog_ports(host, emit=emit, translate=translate),
    )


def _config_display_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> ConfigDisplayPorts:
    from ....infrastructure.config.runtime_paths import get_config_path

    return ConfigDisplayPorts(
        model=lambda: host.model,
        base_url=lambda: host.base_url,
        api_key=lambda: host.api_key,
        terminal_environment=lambda: os.getenv("TERMINAL_ENV", "local"),
        terminal_working_directory=lambda: os.getenv("TERMINAL_CWD", os.getcwd()),
        terminal_timeout=lambda: os.getenv("TERMINAL_TIMEOUT", "60"),
        ssh_target=lambda: (
            os.getenv("TERMINAL_SSH_USER", "not set"),
            os.getenv("TERMINAL_SSH_HOST", "not set"),
            os.getenv("TERMINAL_SSH_PORT", "22"),
        ),
        max_turns=lambda: host.max_turns,
        enabled_toolsets=lambda: host.enabled_toolsets,
        verbose=lambda: host.verbose,
        session_start=lambda: host.session_start,
        config_path=get_config_path,
        translate=translate,
        emit=emit,
    )


def _toolsets_display_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> ToolsetsDisplayPorts:
    return ToolsetsDisplayPorts(
        toolsets=_localized_toolsets,
        enabled_toolsets=lambda: host.enabled_toolsets,
        translate=translate,
        emit=emit,
    )


def _tools_catalog_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> ToolsCatalogPorts:
    from ....extensions.tools.model_tools import get_tool_definitions, get_toolset_for_tool

    return ToolsCatalogPorts(
        tools=lambda: get_tool_definitions(
            enabled_toolsets=host.enabled_toolsets,
            quiet_mode=True,
        ),
        toolset_for_tool=get_toolset_for_tool,
        translate=translate,
        emit=emit,
    )


def _help_display_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
    chat_console_factory: Callable[[], Any] | None,
    skill_commands: Callable[[], Mapping[str, Mapping[str, str]]] | None,
) -> HelpDisplayPorts:
    from voidcube.extensions.skills.commands import get_skill_commands
    from rich.markup import escape
    from ..cli_ui import _BOLD, _DIM, _RST, ChatConsole, _accent_hex
    from .catalog import COMMANDS_BY_CATEGORY
    from ....infrastructure.runtime.environment import is_termux
    from ..attachments import _termux_example_image_path

    console_factory = chat_console_factory or ChatConsole
    skill_command_catalog = skill_commands or get_skill_commands

    def label(key: str, default: str) -> str:
        try:
            return translate(key, default=default) or default
        except Exception:
            return default

    def render_header(header: str) -> None:
        inner_width = 55
        normalized = (header or "").strip() or "(^_^)? VoidCube AI Assistant"
        if len(normalized) > inner_width:
            normalized = normalized[:inner_width]
        emit(f"\n{_BOLD}+{'-' * inner_width}+{_RST}")
        emit(f"{_BOLD}|{normalized:^{inner_width}}|{_RST}")
        emit(f"{_BOLD}+{'-' * inner_width}+{_RST}")

    def render_category(category: str) -> None:
        emit(f"\n  {_BOLD}── {category} ──{_RST}")

    def render_command(command: str, description: str) -> None:
        console_factory().print(
            f"    [bold {_accent_hex()}]{command:<15}[/] [dim]-[/] {escape(description)}"
        )

    def render_skill_header(header: str, count: int) -> None:
        emit(f"\n  {header} {_RST}({count} installed):")

    def render_skill(command: str, description: str) -> None:
        console_factory().print(
            f"    [bold {_accent_hex()}]{command:<22}[/] [dim]-[/] {escape(description)}"
        )

    def render_tip(text: str, final: bool) -> None:
        emit(f"  {_DIM}{text}{_RST}" + ("\n" if final else ""))

    return HelpDisplayPorts(
        command_categories=lambda: COMMANDS_BY_CATEGORY,
        command_available=host._command_available,
        skill_commands=skill_command_catalog,
        text=HelpDisplayText(
            header=label("help.available_commands", "(^_^)? VoidCube AI Assistant"),
            skill_commands_header=label("help.skill_commands", "🔧 可用技能"),
            tip_chat=label("help.tip_chat", "提示: 直接输入消息与 AI 对话"),
            tip_multiline=label("help.tip_multiline", "多行输入: Alt+Enter 换行"),
            tip_paste=label("help.tip_paste", "粘贴图片: 使用 /paste"),
        ),
        is_termux=is_termux,
        termux_example_path=_termux_example_image_path,
        render_header=render_header,
        render_category=render_category,
        render_command=render_command,
        render_skill_header=render_skill_header,
        render_skill=render_skill,
        render_tip=render_tip,
    )


def _usage_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> UsageCommandPorts:
    from ....infrastructure.providers.rate_limit import format_rate_limit_display
    from ....infrastructure.providers.usage_pricing import (
        CanonicalUsage,
        estimate_usage_cost,
        format_duration_compact,
    )

    def agent() -> Any:
        return host.agent

    def rate_limit_display() -> str | None:
        state = agent().get_rate_limit_state()
        return format_rate_limit_display(state) if state and state.has_data else None

    def snapshot() -> UsageDisplaySnapshot:
        active_agent = agent()
        input_tokens = getattr(active_agent, "session_input_tokens", 0) or 0
        output_tokens = getattr(active_agent, "session_output_tokens", 0) or 0
        cache_read_tokens = getattr(active_agent, "session_cache_read_tokens", 0) or 0
        cache_write_tokens = getattr(active_agent, "session_cache_write_tokens", 0) or 0
        compressor = active_agent.context_compressor
        context_tokens = compressor.last_prompt_tokens
        context_length = compressor.context_length
        cost = estimate_usage_cost(
            active_agent.model,
            CanonicalUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            ),
            provider=getattr(active_agent, "provider", None),
            base_url=getattr(active_agent, "base_url", None),
        )
        return UsageDisplaySnapshot(
            model=active_agent.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            prompt_tokens=active_agent.session_prompt_tokens,
            completion_tokens=active_agent.session_completion_tokens,
            total_tokens=active_agent.session_total_tokens,
            api_calls=active_agent.session_api_calls,
            session_duration=format_duration_compact(
                (datetime.now() - host.session_start).total_seconds()
            ),
            cost_status=cost.status,
            cost_source=cost.source,
            cost_amount_usd=(float(cost.amount_usd) if cost.amount_usd is not None else None),
            context_tokens=context_tokens,
            context_length=context_length,
            context_percent=(
                min(100, context_tokens / context_length * 100)
                if context_length
                else 0
            ),
            message_count=len(host.conversation_history),
            compressions=compressor.compression_count,
        )

    return UsageCommandPorts(
        agent_available=lambda: agent() is not None,
        api_calls=lambda: agent().session_api_calls,
        rate_limit_display=rate_limit_display,
        snapshot=snapshot,
        emit=emit,
        no_agent_message="(._.) No active agent -- send a message first.",
        no_calls_message="(._.) No API calls made yet in this session.",
    )


def _doctor_command_ports() -> DoctorCommandPorts:
    from ..config_validator import print_diagnosis

    return DoctorCommandPorts(run_diagnosis=print_diagnosis)


def _debug_command_ports() -> DebugCommandPorts:
    from types import SimpleNamespace

    from ..debug import run_debug

    return DebugCommandPorts(
        run_debug_share=lambda: run_debug(
            SimpleNamespace(debug_command="share", lines=200, expire=7, local=False)
        )
    )


def _mcp_command_ports() -> McpCommandPorts:
    from ....infrastructure.config.configuration import load_config, save_config
    from ..mcp_config import probe_mcp_server

    return McpCommandPorts(
        load_config=load_config,
        save_config=save_config,
        probe_tools=lambda name, config: [
            {"name": tool_name}
            for tool_name, _description in probe_mcp_server(name, dict(config))
        ],
        emit=print,
    )


def reload_mcp_for_host(
    host: Any,
    *,
    emit: Callable[[str], None] = print,
) -> None:
    """Run the shared MCP reload operation against a CLI host's runtime ports."""
    reload_mcp_servers(ports=_mcp_reload_runtime_ports(host, emit=emit))


def _mcp_reload_runtime_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> McpReloadRuntimePorts:
    from ....extensions.tools.mcp.mcp_tool import (
        _lock,
        _servers,
        discover_mcp_tools,
        shutdown_mcp_servers,
    )
    from ....extensions.tools.model_tools import get_tool_definitions

    def server_names() -> set[str]:
        with _lock:
            return set(_servers)

    def refresh_agent_tools() -> int:
        agent = getattr(host, "agent", None)
        if agent is None:
            return 0
        tools = get_tool_definitions(
            enabled_toolsets=(
                agent.enabled_toolsets
                if hasattr(agent, "enabled_toolsets")
                else None
            ),
            quiet_mode=True,
        )
        agent.tools = tools
        agent.valid_tool_names = (
            {tool["function"]["name"] for tool in tools} if tools else set()
        )
        return len(tools)

    def append_reload_note(note: str) -> None:
        host.conversation_history.append({"role": "user", "content": note})

    def persist_reload_note() -> None:
        agent = getattr(host, "agent", None)
        if agent is None:
            return
        try:
            agent._session_persistence.persist(host.conversation_history)
        except Exception:
            pass

    return McpReloadRuntimePorts(
        server_names=server_names,
        shutdown_servers=shutdown_mcp_servers,
        discover_tools=discover_mcp_tools,
        command_running=lambda: bool(getattr(host, "_command_running", False)),
        refresh_agent_tools=refresh_agent_tools,
        append_reload_note=append_reload_note,
        persist_reload_note=persist_reload_note,
        emit=emit,
    )


def _browser_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> BrowserCommandPorts:
    import platform
    import socket
    import time

    def cleanup_browsers() -> None:
        try:
            from voidcube.extensions.tools.browser.browser_tool import cleanup_all_browsers

            cleanup_all_browsers()
        except Exception:
            pass

    def probe_port(port: int) -> bool:
        try:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            connection.settimeout(1)
            connection.connect(("127.0.0.1", port))
            connection.close()
            return True
        except (OSError, socket.timeout):
            return False

    def cloud_provider() -> Any | None:
        try:
            from voidcube.extensions.tools.browser.browser_tool import _get_cloud_provider

            return _get_cloud_provider()
        except Exception:
            return None

    def enqueue_system_note(note: str) -> None:
        pending_input = getattr(host, "_pending_input", None)
        if pending_input is not None:
            pending_input.put(note)

    return BrowserCommandPorts(
        current_cdp_url=lambda: os.environ.get("BROWSER_CDP_URL", ""),
        set_cdp_url=lambda value: os.environ.__setitem__("BROWSER_CDP_URL", value),
        clear_cdp_url=lambda: os.environ.pop("BROWSER_CDP_URL", None),
        cleanup_browsers=cleanup_browsers,
        probe_port=probe_port,
        launch_chrome_debug=lambda port: host._try_launch_chrome_debug(
            port, platform.system()
        ),
        system_name=platform.system,
        chrome_data_dir=lambda: str(_voidcube_home() / "chrome-debug"),
        cloud_provider=cloud_provider,
        enqueue_system_note=enqueue_system_note,
        sleep=time.sleep,
        emit=emit,
    )


def _api_command_ports(host: Any) -> ApiCommandPorts:
    try:
        from voidcube.interfaces.cli.configuration import ApiConfigRuntime, run_api_config_wizard
    except (ModuleNotFoundError, ImportError):
        from voidcube.interfaces.cli.configuration import ApiConfigRuntime, run_api_config_wizard

    def runtime_setter(attribute: str) -> Callable[[str], None] | None:
        if not hasattr(host, attribute):
            return None
        return lambda value: setattr(host, attribute, value)

    runtime = ApiConfigRuntime(
        set_model=runtime_setter("model"),
        set_provider=runtime_setter("provider"),
        set_requested_provider=runtime_setter("requested_provider"),
    )
    return ApiCommandPorts(run_wizard=lambda: run_api_config_wizard(runtime))


def _model_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> ModelCommandPorts:
    from ....infrastructure.config.configuration import load_config
    from ..model_switch import (
        list_configured_providers,
        parse_model_flags,
        switch_model,
    )
    from ..providers import get_label

    def user_providers() -> Mapping[str, Any] | None:
        try:
            config = load_config()
        except Exception:
            return None
        providers = config.get("providers")
        return providers if isinstance(providers, Mapping) else None

    return ModelCommandPorts(
        parse_flags=parse_model_flags,
        user_providers=user_providers,
        model=lambda: str(getattr(host, "model", "") or ""),
        provider=lambda: str(getattr(host, "provider", "") or ""),
        base_url=lambda: str(getattr(host, "base_url", "") or ""),
        api_key=lambda: str(getattr(host, "api_key", "") or ""),
        provider_label=get_label,
        list_configured_providers=list_configured_providers,
        switch_model=switch_model,
        open_picker=host._open_model_picker,
        apply_result=host._apply_model_switch_result,
        emit=emit,
    )


def _provider_display_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> ProviderDisplayPorts:
    from ....infrastructure.config.configuration import get_active_provider_key, load_config
    from ..model_switch import list_configured_providers

    def snapshot() -> ProviderDisplaySnapshot:
        config = load_config()
        active_provider = get_active_provider_key(config)
        return ProviderDisplaySnapshot(
            active_provider=active_provider,
            configured_providers=list_configured_providers(
                current_provider=active_provider,
                user_providers=config.get("providers"),
                max_models=8,
            ),
        )

    return ProviderDisplayPorts(
        snapshot=snapshot,
        current_model=lambda: str(getattr(host, "model", "") or ""),
        translate=translate,
        emit=print,
        emit_usage=emit,
    )


def _memory_display_ports() -> MemoryDisplayPorts:
    from ....infrastructure.runtime.layout import get_runtime_layout

    return MemoryDisplayPorts(
        database_path=lambda: str(get_runtime_layout().memory_db),
        emit=print,
    )


def _personality_command_ports(host: Any) -> PersonalityCommandPorts:
    from ....infrastructure.config.configuration import save_config_value

    return PersonalityCommandPorts(
        personalities=lambda: getattr(host, "personalities", {}),
        set_system_prompt=lambda value: setattr(host, "system_prompt", value),
        reset_agent=lambda: setattr(host, "agent", None),
        save_system_prompt=lambda value: save_config_value(
            "agent.system_prompt",
            value,
        ),
        emit=print,
    )


def _reasoning_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> ReasoningCommandPorts:
    from ....infrastructure.config.configuration import save_config_value
    from ..cli_ui import _ACCENT, _DIM, _RST

    def refresh_agent_reasoning_callback() -> None:
        agent = getattr(host, "agent", None)
        if agent is not None:
            agent.reasoning_callback = host._current_reasoning_callback()

    def set_reasoning_config(value: dict | None) -> None:
        setattr(host, "reasoning_config", value)
        setattr(host, "agent", None)

    return ReasoningCommandPorts(
        reasoning_config=lambda: getattr(host, "reasoning_config", None),
        show_reasoning=lambda: bool(getattr(host, "show_reasoning", False)),
        set_reasoning_config=set_reasoning_config,
        set_show_reasoning=lambda value: setattr(host, "show_reasoning", value),
        refresh_agent_reasoning_callback=refresh_agent_reasoning_callback,
        parse_config=parse_reasoning_config,
        save_display=lambda value: save_config_value("display.show_reasoning", value),
        save_effort=lambda value: save_config_value("agent.reasoning_effort", value),
        emit=emit,
        accent=_ACCENT,
        dim=_DIM,
        reset=_RST,
    )


def _fast_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
) -> FastCommandPorts:
    from ....infrastructure.config.configuration import save_config_value
    from ..cli_ui import _ACCENT, _DIM, _RST

    def set_service_tier(value: str | None) -> None:
        setattr(host, "service_tier", value)
        setattr(host, "agent", None)

    return FastCommandPorts(
        available=host._fast_command_available,
        service_tier=lambda: getattr(host, "service_tier", None),
        set_service_tier=set_service_tier,
        save_service_tier=lambda value: save_config_value("agent.service_tier", value),
        emit=emit,
        accent=_ACCENT,
        dim=_DIM,
        reset=_RST,
    )


def _session_status_display_ports(host: Any) -> SessionStatusDisplayPorts:
    def session_metadata() -> dict[str, Any]:
        repository = getattr(host, "_session_db", None)
        if repository is None:
            return {}
        try:
            return repository.get_session(host.session_id) or {}
        except Exception:
            return {}

    def emit(text: str) -> None:
        host.console.print(text, highlight=False, markup=False)

    return SessionStatusDisplayPorts(
        session_metadata=session_metadata,
        session_id=lambda: host.session_id,
        session_start=lambda: host.session_start,
        home_path=_display_voidcube_home,
        model=lambda: getattr(host, "model", None),
        provider=lambda: getattr(host, "provider", None),
        total_tokens=lambda: getattr(getattr(host, "agent", None), "session_total_tokens", 0)
        or 0,
        agent_running=lambda: bool(getattr(host, "_agent_running", False)),
        subagent_snapshot=host._get_subagent_observability_snapshot,
        autonomous_sections=lambda: _autonomous_observation_summary_sections(host),
        emit=emit,
        goal_snapshot=lambda: _session_goal_snapshot(host),
    )


def _autonomous_observation_summary_sections(host: Any) -> Sequence[str]:
    from ..autonomous.status_host import autonomous_observation_summary_sections

    return autonomous_observation_summary_sections(host)


def _session_goal_snapshot(host: Any) -> Mapping[str, Any]:
    from ..session_goal_runtime import get_goal

    return get_goal(host) or {}


def _localized_toolsets() -> tuple[tuple[str, int, str], ...]:
    from ..i18n import get_i18n
    from ....extensions.tools.toolsets import get_all_toolsets, get_toolset_info

    i18n = get_i18n()
    locale_data = i18n._translations.get(i18n.get_current_locale(), {})
    translations = locale_data.get("translations", {}).get("toolsets", {})
    entries = []
    for name in sorted(get_all_toolsets()):
        info = get_toolset_info(name)
        if info:
            entries.append(
                (
                    name,
                    len(info.get("tools", [])),
                    str(translations.get(name, info.get("description", ""))),
                )
            )
    return tuple(entries)


def _rollback_command_ports(
    host: Any,
    *,
    emit: Callable[[str], None],
    translate: Callable[..., str],
) -> RollbackCommandPorts:
    return RollbackCommandPorts(
        checkpoint_manager=lambda: (
            host.agent._checkpoint_mgr if getattr(host, "agent", None) else None
        ),
        manager_enabled=lambda manager: bool(manager.enabled),
        working_directory=lambda: os.getenv("TERMINAL_CWD", os.getcwd()),
        list_checkpoints=lambda manager, directory: manager.list_checkpoints(directory),
        format_checkpoints=_format_checkpoint_list,
        diff=lambda manager, directory, target_hash: manager.diff(directory, target_hash),
        restore=lambda manager, directory, target_hash, file_path: manager.restore(
            directory, target_hash, file_path=file_path
        ),
        has_conversation_history=lambda: bool(host.conversation_history),
        undo_chat_history=lambda: host._builtin_command_executor.execute(
            parse_cli_command("/undo")
        ),
        emit=emit,
        text=RollbackCommandText(
            no_active_agent=f"  {translate('prompts.no_active_agent_session')}",
            checkpoints_not_enabled=f"  {translate('prompts.checkpoints_not_enabled')}",
            checkpoints_enable_command=(
                "  " + translate("prompts.checkpoints_enable_command")
            ),
            checkpoints_enable_config=(
                "  " + translate("prompts.checkpoints_enable_config")
            ),
            usage_diff=f"  {translate('prompts.rollback_usage_diff')}",
            no_checkpoints=lambda path: "  " + translate(
                "prompts.rollback_no_checkpoints", path=path
            ),
            no_changes=f"  {translate('prompts.rollback_no_changes')}",
            more_lines=lambda count: "  " + translate(
                "prompts.rollback_more_lines", count=count
            ),
            restored=lambda checkpoint, reason: "  ✅ " + translate(
                "prompts.rollback_restored", checkpoint=checkpoint, reason=reason
            ),
            restored_file=lambda file_path, checkpoint, reason: "  ✅ " + translate(
                "prompts.rollback_restored_file",
                file_path=file_path,
                checkpoint=checkpoint,
                reason=reason,
            ),
            snapshot_saved=f"  {translate('prompts.rollback_snapshot_saved')}",
            chat_undone=f"  {translate('prompts.rollback_chat_undone')}",
            invalid_number=lambda maximum: "  " + translate(
                "prompts.rollback_invalid_number", max=maximum
            ),
        ),
    )


def _format_checkpoint_list(checkpoints: Sequence[dict[str, Any]], directory: str) -> str:
    from ....infrastructure.persistence.checkpoint_manager import format_checkpoint_list

    return format_checkpoint_list(checkpoints, directory)


def _notify_session_boundary(host: Any, event_type: str) -> None:
    try:
        from ....extensions.plugins.cli_adapter import invoke_hook

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
    from voidcube.infrastructure.execution.process_registry import process_registry

    return process_registry.list_sessions()


def _kill_all_processes() -> int:
    from voidcube.infrastructure.execution.process_registry import process_registry

    return process_registry.kill_all()


def _voidcube_home() -> Path:
    from ....infrastructure.config.runtime_paths import get_VoidCube_home

    return get_VoidCube_home()


def _display_voidcube_home() -> str:
    from ....infrastructure.config.runtime_paths import display_VoidCube_home

    return display_VoidCube_home()


def _discover_plugins() -> object:
    from ....extensions.plugins.cli_adapter import discover_plugins

    return discover_plugins()


def _list_plugin_records() -> list[dict[str, Any]]:
    from ....extensions.plugins.cli_adapter import get_plugin_manager

    return list(get_plugin_manager().list_plugins().values())


def _is_termux() -> bool:
    from ....infrastructure.runtime.environment import is_termux

    return is_termux()


def _has_clipboard_image() -> bool:
    from ..clipboard import has_clipboard_image

    return has_clipboard_image()
