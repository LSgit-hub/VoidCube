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
    remove_last_user_turn,
)
from VoidCube_cli.command_handlers.display import (
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
from VoidCube_cli.command_handlers.input import (
    QueueCommandPorts,
    RetryCommandPorts,
    handle_queue_command,
    handle_retry_command,
)
from VoidCube_cli.command_handlers.info import (
    UsageCommandPorts,
    UsageDisplaySnapshot,
    handle_usage_command,
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
from VoidCube_cli.command_handlers.operations import (
    ApiCommandPorts,
    DebugCommandPorts,
    DoctorCommandPorts,
    McpReloadRuntimePorts,
    ReloadMcpCommandPorts,
    handle_api_command,
    handle_debug_command,
    handle_doctor_command,
    handle_reload_mcp_command,
    reload_mcp_servers,
)
from VoidCube_cli.command_handlers.personality import (
    PersonalityCommandPorts,
    handle_personality_command,
)
from VoidCube_cli.command_handlers.reasoning import (
    ReasoningCommandPorts,
    handle_reasoning_command,
)
from VoidCube_cli.command_handlers.fast import (
    FastCommandPorts,
    handle_fast_command,
    parse_service_tier_config,
)
from VoidCube_cli.command_handlers.compression import (
    CompressionCommandPorts,
    handle_compression_command,
)
from VoidCube_cli.command_handlers.browser import (
    BrowserCommandPorts,
    handle_browser_command,
)
from VoidCube_cli.command_handlers.autonomous import (
    AutonomousCommandPorts,
    handle_auto_command,
    handle_auto_q_command,
)
from VoidCube_cli.command_handlers.background import (
    BackgroundCommandPorts,
    BackgroundCommandText,
    handle_background_command,
)
from VoidCube_cli.command_handlers.btw import (
    BtwCommandPorts,
    BtwCommandText,
    handle_btw_command,
)
from VoidCube_cli.command_handlers.language import (
    LanguageCommandPorts,
    handle_language_command,
)
from VoidCube_cli.command_handlers.voice import (
    VoiceCommandPorts,
    handle_voice_command,
)
from VoidCube_cli.command_handlers.preset import (
    PresetCommandPorts,
    PresetCommandText,
    handle_preset_command,
)
from VoidCube_cli.command_handlers.plan import PlanCommandPorts, handle_plan_command
from VoidCube_cli.command_handlers.mcp import (
    McpCommandPorts,
    handle_mcp_command,
)
from VoidCube_cli.command_handlers.tools import (
    ToolsCommandPorts,
    ToolsCommandText,
    handle_tools_command,
)
from VoidCube_cli.command_handlers.skills import (
    SkillRecord,
    SkillSearchResult,
    SkillsCommandPorts,
    handle_skills_command,
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


def test_autonomous_handlers_delegate_focus_and_deactivation_without_runtime_host() -> None:
    events: list[tuple[str, str]] = []
    ports = AutonomousCommandPorts(
        activate=lambda focus: events.append(("activate", focus)),
        deactivate=lambda: events.append(("deactivate", "")),
    )

    handle_auto_command(parse_cli_command("/auto inspect pending tasks"), ports=ports)
    handle_auto_q_command(parse_cli_command("/auto-q ignored"), ports=ports)

    assert events == [("activate", "inspect pending tasks"), ("deactivate", "")]


def test_background_handler_validates_prompt_and_starts_shared_operation() -> None:
    output: list[str] = []
    started: list[str] = []
    ports = BackgroundCommandPorts(
        start_background=started.append,
        emit=output.append,
        text=BackgroundCommandText("usage", "example", "description"),
    )

    handle_background_command(parse_cli_command("/background"), ports=ports)
    handle_background_command(
        parse_cli_command("/background Keep Mixed Case"), ports=ports
    )

    assert output == ["usage", "example", "description"]
    assert started == ["Keep Mixed Case"]


def test_btw_handler_validates_question_and_starts_ephemeral_operation() -> None:
    output: list[str] = []
    started: list[str] = []
    ports = BtwCommandPorts(
        start_btw=started.append,
        emit=output.append,
        text=BtwCommandText("usage", "example", "description"),
    )

    handle_btw_command(parse_cli_command("/btw"), ports=ports)
    handle_btw_command(
        parse_cli_command("/btw Keep Mixed Case"), ports=ports
    )

    assert output == ["usage", "example", "description"]
    assert started == ["Keep Mixed Case"]


def test_language_handler_renders_status_and_preserves_locale_operations() -> None:
    output: list[str] = []
    ports = LanguageCommandPorts(
        current_locale=lambda: "en_US",
        available_locales=lambda: ["en_US", "zh_CN"],
        translate=lambda key, **kwargs: key.format(**kwargs) if kwargs else key,
        set_locale=lambda _locale: pytest.fail("status must not set locale"),
        rebuild_command_lookups=lambda: pytest.fail("status must not rebuild"),
        persist_locale=lambda _locale: pytest.fail("status must not persist"),
        emit=output.append,
    )

    handle_language_command(parse_cli_command("/language"), ports=ports)

    assert output[0] == "\n  language_command.current"
    assert output[1] == "  language_command.available"
    assert output[2:4] == [
        "    ● en_US - language_command.lang_en_US",
        "      zh_CN - language_command.lang_zh_CN",
    ]
    assert output[-1] == "\n  language_command.tip_env\n"


def test_language_handler_updates_locale_and_reports_persistence_status() -> None:
    output: list[str] = []
    events: list[tuple[str, str]] = []
    ports = LanguageCommandPorts(
        current_locale=lambda: "en_US",
        available_locales=lambda: [],
        translate=lambda key, **kwargs: key + (str(kwargs) if kwargs else ""),
        set_locale=lambda locale: events.append(("locale", locale)),
        rebuild_command_lookups=lambda: events.append(("lookups", "")),
        persist_locale=lambda locale: events.append(("persist", locale)) or True,
        emit=output.append,
    )

    handle_language_command(parse_cli_command("/language -CN"), ports=ports)
    handle_language_command(parse_cli_command("/language invalid"), ports=ports)

    assert events == [("locale", "zh_CN"), ("lookups", ""), ("persist", "zh_CN")]
    assert output == [
        "  language_command.set_to_saved{'locale': 'zh_CN'}",
        "  language_command.switched_cn",
        "  language_command.invalid_param{'param': 'invalid'}",
        "  language_command.usage",
    ]


def test_voice_handler_dispatches_runtime_operations_and_tts_status_or_speak() -> None:
    events: list[str] = []
    enabled = [False]
    ports = VoiceCommandPorts(
        enable=lambda: events.append("enable"),
        disable=lambda: events.append("disable"),
        tts_status=lambda: events.append("tts_status"),
        tts_speak=lambda text: events.append(f"tts_speak:{text}"),
        show_status=lambda: events.append("status"),
        voice_mode_enabled=lambda: enabled[0],
        emit=events.append,
    )

    for command in ("/voice on", "/voice off", "/voice tTs", "/voice tts hello", "/voice status"):
        handle_voice_command(parse_cli_command(command), ports=ports)
    handle_voice_command(parse_cli_command("/voice"), ports=ports)
    enabled[0] = True
    handle_voice_command(parse_cli_command("/voice"), ports=ports)

    assert events == ["enable", "disable", "tts_status", "tts_speak:hello", "status", "enable", "disable"]


def test_voice_handler_reports_unknown_subcommand() -> None:
    output: list[str] = []
    ports = VoiceCommandPorts(
        enable=lambda: pytest.fail("invalid command must not enable"),
        disable=lambda: pytest.fail("invalid command must not disable"),
        tts_status=lambda: pytest.fail("invalid command must not report TTS status"),
        tts_speak=lambda _text: pytest.fail("invalid command must not speak"),
        show_status=lambda: pytest.fail("invalid command must not show status"),
        voice_mode_enabled=lambda: False,
        emit=output.append,
    )

    handle_voice_command(parse_cli_command("/voice later"), ports=ports)

    assert output == [
        "Unknown voice subcommand: later",
        "Usage: /voice [on|off|tts [text]|status]",
    ]


def test_preset_handler_renders_catalog_and_rejects_unavailable_execution() -> None:
    output: list[str] = []
    ports = PresetCommandPorts(
        list_presets=lambda: [
            {
                "file": "docker-web",
                "name": "Docker Web",
                "description": "web stack",
                "steps_count": 2,
            }
        ],
        load_preset=lambda name: {
            "name": "Docker Web",
            "description": "web stack",
            "steps": [{"action": "pkg_install", "packages": "docker"}],
        } if name == "docker-web" else None,
        apply_preset=lambda _name: {
            "success": False,
            "reason": "execution_not_available",
        },
        emit=output.append,
        text=PresetCommandText(dim="<dim>", accent="<accent>", bold="<bold>", reset="<reset>"),
    )

    handle_preset_command(parse_cli_command("/preset"), ports=ports)
    handle_preset_command(parse_cli_command("/preset show docker-web"), ports=ports)
    handle_preset_command(parse_cli_command("/preset apply docker-web"), ports=ports)

    assert output == [
        "\n  <bold>Available Presets:<reset>",
        "    <accent>docker-web          <reset> Docker Web",
        "                         web stack (2 steps)",
        "\n  <bold>Preset: Docker Web<reset>",
        "  web stack",
        "\n  <bold>Steps:<reset>",
        "    1. pkg_install -> {'action': 'pkg_install', 'packages': 'docker'}",
        (
            "  Preset execution is unavailable: deployment actions require an "
            "approved execution runtime."
        ),
    ]


def test_plan_handler_uses_shared_skill_message_and_workspace_relative_target() -> None:
    output: list[str] = []
    queued: list[str] = []
    calls: list[tuple[str, str, str]] = []
    plan_path = Path(".VoidCube/plans/plan.md")
    ports = PlanCommandPorts(
        build_plan_path=lambda instruction: plan_path,
        build_skill_message=lambda command, instruction, runtime_note: calls.append(
            (command, instruction, runtime_note)
        ) or "plan skill message",
        enqueue=queued.append,
        emit=output.append,
        render_error=lambda message: pytest.fail(message),
    )

    handle_plan_command(parse_cli_command("/plan Keep Mixed Case"), ports=ports)

    assert calls == [
        (
            "/plan",
            "Keep Mixed Case",
            "Save the markdown plan with write_file to this exact relative path "
            f"inside the active workspace/backend cwd: {plan_path}",
        )
    ]
    assert queued == ["plan skill message"]
    assert output == [
        f"  📝 Plan mode queued via skill. Markdown plan target: {plan_path}"
    ]


def test_plan_handler_reports_missing_skill_or_queue_without_enqueuing() -> None:
    errors: list[str] = []
    ports = PlanCommandPorts(
        build_plan_path=lambda _instruction: Path(".VoidCube/plans/plan.md"),
        build_skill_message=lambda _command, _instruction, _note: None,
        enqueue=lambda _message: pytest.fail("must not enqueue"),
        emit=lambda _message: pytest.fail("must not emit success"),
        render_error=errors.append,
    )

    handle_plan_command(parse_cli_command("/plan"), ports=ports)

    assert errors == ["Failed to load the bundled /plan skill"]


def test_tools_handler_projects_catalog_lists_configuration_and_resets_after_change() -> None:
    output: list[str] = []
    events: list[object] = []
    state: dict[str, object] = {"toolsets": ("terminal",)}
    ports = ToolsCommandPorts(
        render_catalog=lambda: events.append("catalog"),
        list_configuration=lambda: events.append("list"),
        change_configuration=lambda action, names: events.append((action, names)),
        load_enabled_toolsets=lambda: ("web", "terminal"),
        set_enabled_toolsets=lambda value: state.__setitem__("toolsets", value),
        reset_session=lambda: events.append("reset"),
        emit=output.append,
        text=ToolsCommandText(
            usage=lambda action: f"usage {action}",
            builtin_example=lambda action: f"builtin {action}",
            mcp_example=lambda action: f"mcp {action}",
            changing=lambda action, names: f"{action}: {', '.join(names)}",
            session_reset="session reset",
        ),
    )

    handle_tools_command(parse_cli_command("/tools"), ports=ports)
    handle_tools_command(parse_cli_command("/tools list"), ports=ports)
    handle_tools_command(parse_cli_command('/tools enable "web tools" mcp:search'), ports=ports)

    assert events == [
        "catalog",
        "list",
        ("enable", ("web tools", "mcp:search")),
        "reset",
    ]
    assert state["toolsets"] == ("web", "terminal")
    assert output == [
        "enable: web tools, mcp:search",
        "session reset",
    ]


def test_tools_handler_rejects_missing_targets_without_mutation() -> None:
    output: list[str] = []
    ports = ToolsCommandPorts(
        render_catalog=lambda: pytest.fail("must not render catalog"),
        list_configuration=lambda: pytest.fail("must not list configuration"),
        change_configuration=lambda _action, _names: pytest.fail("must not change config"),
        load_enabled_toolsets=lambda: pytest.fail("must not reload toolsets"),
        set_enabled_toolsets=lambda _value: pytest.fail("must not mutate runtime"),
        reset_session=lambda: pytest.fail("must not reset session"),
        emit=output.append,
        text=ToolsCommandText(
            usage=lambda action: f"usage {action}",
            builtin_example=lambda action: f"builtin {action}",
            mcp_example=lambda action: f"mcp {action}",
            changing=lambda action, names: f"{action}: {', '.join(names)}",
            session_reset="session reset",
        ),
    )

    handle_tools_command(parse_cli_command("/tools disable"), ports=ports)

    assert output == ["usage disable", "builtin disable", "mcp disable"]


def test_skills_handler_projects_catalog_and_refreshes_after_mutations() -> None:
    output: list[str] = []
    events: list[object] = []
    ports = SkillsCommandPorts(
        builtin_skills=lambda: (("dev", ("lint",)),),
        installed_skills=lambda: (SkillRecord("hub-skill", "official", "builtin"),),
        search=lambda query: (
            SkillSearchResult("match", f"for {query}", "github", "trusted", ("code",)),
        ),
        install=lambda name: events.append(("install", name)) or (True, "", "installed"),
        uninstall=lambda name: events.append(("uninstall", name)) or (True, ""),
        refresh_cache=lambda: events.append("refresh"),
        emit=output.append,
    )

    handle_skills_command(parse_cli_command("/skills list"), ports=ports)
    handle_skills_command(parse_cli_command("/skills search query text"), ports=ports)
    handle_skills_command(parse_cli_command("/skills install sample"), ports=ports)
    handle_skills_command(parse_cli_command("/skills uninstall sample"), ports=ports)

    assert events == [
        ("install", "sample"),
        "refresh",
        ("uninstall", "sample"),
        "refresh",
    ]
    assert "    dev:" in output
    assert "      - [lint]" in output
    assert "    [hub-skill]" in output
    assert "    1. [match]" in output
    assert "    ✅ 技能 'installed' 安装成功" in output
    assert "    ✅ 技能 'sample' 卸载成功" in output


def test_skills_handler_rejects_missing_names_and_operation_failures() -> None:
    output: list[str] = []
    ports = SkillsCommandPorts(
        builtin_skills=lambda: pytest.fail("must not list"),
        installed_skills=lambda: pytest.fail("must not list"),
        search=lambda _query: pytest.fail("must not search"),
        install=lambda _name: (False, "blocked by policy", ""),
        uninstall=lambda _name: (False, "not installed"),
        refresh_cache=lambda: pytest.fail("must not refresh after failure"),
        emit=output.append,
    )

    handle_skills_command(parse_cli_command("/skills install"), ports=ports)
    handle_skills_command(parse_cli_command("/skills uninstall"), ports=ports)
    handle_skills_command(parse_cli_command("/skills install blocked"), ports=ports)
    handle_skills_command(parse_cli_command("/skills uninstall missing"), ports=ports)

    assert output == [
        "\n  ❌ 请指定要安装的技能名称",
        "    用法: /skills install <name>",
        "\n  ❌ 请指定要卸载的技能名称",
        "    用法: /skills uninstall <name>",
        "\n  正在安装技能: blocked",
        "    ❌ 安装失败: blocked by policy",
        "\n  正在卸载技能: missing",
        "    ❌ 卸载失败: not installed",
    ]


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


def test_config_display_handler_projects_runtime_snapshot_without_mutation(
    tmp_path: Path,
) -> None:
    output: list[str] = []
    config_path = tmp_path / "config.yaml"
    config_path.write_text("runtime: {}\n", encoding="utf-8")
    started_at = datetime(2026, 7, 30, 9, 15, 0)

    handle_config_display_command(
        parse_cli_command("/config ignored"),
        ports=ConfigDisplayPorts(
            model=lambda: "active-model",
            base_url=lambda: "https://api.example/v1",
            api_key=lambda: "secret-1234",
            terminal_environment=lambda: "ssh",
            terminal_working_directory=lambda: "/workspace",
            terminal_timeout=lambda: "75",
            ssh_target=lambda: ("alice", "host.example", "2200"),
            max_turns=lambda: 12,
            enabled_toolsets=lambda: ("web", "terminal"),
            verbose=lambda: True,
            session_start=lambda: started_at,
            config_path=lambda: config_path,
            translate=lambda key, **_kwargs: key.upper(),
            emit=output.append,
        ),
    )

    assert output == [
        "",
        "+" + "-" * 50 + "+",
        "|" + " " * 15 + "(^_^) Configuration" + " " * 16 + "|",
        "+" + "-" * 50 + "+",
        "",
        "MODEL",
        "  Model:     active-model",
        "  Base URL:  https://api.example/v1",
        "  API Key:   ********1234",
        "",
        "TERMINAL",
        "  Environment:  ssh",
        "  SSH Target:   alice@host.example:2200",
        "  Working Dir:  /workspace",
        "  Timeout:      75s",
        "",
        "AGENT",
        "  Max Turns:  12",
        "  Toolsets:   web, terminal",
        "  Verbose:    True",
        "",
        "SESSION",
        "  Started:     2026-07-30 09:15:00",
        f"  Config File: {config_path} (loaded)",
        "",
    ]


def test_toolsets_display_handler_marks_enabled_toolsets_and_uses_text_ports() -> None:
    output: list[str] = []
    labels = {
        "prompts.available_toolsets_title": "Toolsets",
        "prompts.toolsets_unit": "tools",
        "prompts.toolsets_current_enabled": "Enabled:",
        "prompts.toolsets_tip_all": "Tip",
        "prompts.toolsets_example": "Example",
    }

    handle_toolsets_display_command(
        parse_cli_command("/toolsets"),
        ports=ToolsetsDisplayPorts(
            toolsets=lambda: (("terminal", 2, "Run commands"), ("web", 3, "Fetch pages")),
            enabled_toolsets=lambda: ("web",),
            translate=lambda key, default=None, **_kwargs: labels.get(key, default or key),
            emit=output.append,
        ),
    )

    assert output == [
        "",
        "+" + "-" * 58 + "+",
        "|" + " " * 25 + "Toolsets" + " " * 25 + "|",
        "+" + "-" * 58 + "+",
        "",
        "      terminal           [ 2 tools] - Run commands",
        "  (*) web                [ 3 tools] - Fetch pages",
        "",
        "  Enabled: web",
        "",
        "  Tip",
        "  Example",
        "",
    ]


def test_tools_catalog_handler_groups_sorts_and_shortens_descriptions() -> None:
    output: list[str] = []

    handle_tools_catalog_command(
        parse_cli_command("/tools ignored"),
        ports=ToolsCatalogPorts(
            tools=lambda: (
                {"function": {"name": "zebra", "description": "Zebra."}},
                {
                    "function": {
                        "name": "apple",
                        "description": "First sentence. Second sentence\nIgnored line",
                    }
                },
                {"function": {"name": "beta", "description": "Keep e.g. value"}},
            ),
            toolset_for_tool=lambda name: {"zebra": "web", "apple": "file"}.get(name),
            translate=lambda key, default=None, **kwargs: (
                f"{kwargs['count']} total" if key == "prompts.total_tools" else default or key
            ),
            emit=output.append,
        ),
    )

    assert output == [
        "",
        "+" + "-" * 78 + "+",
        "|" + " " * 28 + "(^_^)/ Available Tools" + " " * 28 + "|",
        "+" + "-" * 78 + "+",
        "",
        "  [file]",
        "    * apple                - First sentence.",
        "",
        "  [unknown]",
        "    * beta                 - Keep e.g.",
        "",
        "  [web]",
        "    * zebra                - Zebra.",
        "",
        "  3 total",
        "",
    ]


def test_tools_catalog_handler_reports_empty_catalog() -> None:
    output: list[str] = []

    handle_tools_catalog_command(
        parse_cli_command("/tools"),
        ports=ToolsCatalogPorts(
            tools=lambda: (),
            toolset_for_tool=lambda _name: None,
            translate=lambda key, **_kwargs: {"prompts.no_tools_available": "None"}[key],
            emit=output.append,
        ),
    )

    assert output == ["None"]


def test_help_display_handler_filters_commands_and_sorts_skills() -> None:
    events: list[tuple[object, ...]] = []
    handle_help_display_command(
        parse_cli_command("/help ignored"),
        ports=HelpDisplayPorts(
            command_categories=lambda: {
                "Session": {"/fast": "Fast", "/help": "Help"},
                "Info": {"/status": "Status"},
            },
            command_available=lambda command: command != "/fast",
            skill_commands=lambda: {
                "/zebra": {"description": "Zebra skill"},
                "/alpha": {"description": "Alpha skill"},
            },
            text=HelpDisplayText("Header", "Skills", "Chat", "Multiline", "Paste"),
            is_termux=lambda: False,
            termux_example_path=lambda: "unused.png",
            render_header=lambda value: events.append(("header", value)),
            render_category=lambda value: events.append(("category", value)),
            render_command=lambda command, description: events.append(
                ("command", command, description)
            ),
            render_skill_header=lambda header, count: events.append(
                ("skills", header, count)
            ),
            render_skill=lambda command, description: events.append(
                ("skill", command, description)
            ),
            render_tip=lambda text, final: events.append(("tip", text, final)),
        ),
    )

    assert events == [
        ("header", "Header"),
        ("category", "Session"),
        ("command", "/help", "Help"),
        ("category", "Info"),
        ("command", "/status", "Status"),
        ("skills", "Skills", 2),
        ("skill", "/alpha", "Alpha skill"),
        ("skill", "/zebra", "Zebra skill"),
        ("tip", "Chat", False),
        ("tip", "Multiline", False),
        ("tip", "Paste", True),
    ]


def test_help_display_handler_uses_the_termux_attachment_tip() -> None:
    tips: list[tuple[str, bool]] = []
    handle_help_display_command(
        parse_cli_command("/help"),
        ports=HelpDisplayPorts(
            command_categories=lambda: {},
            command_available=lambda _command: True,
            skill_commands=lambda: {},
            text=HelpDisplayText("Header", "Skills", "Chat", "Multiline", "Paste"),
            is_termux=lambda: True,
            termux_example_path=lambda: "~/Pictures/cat.png",
            render_header=lambda _value: None,
            render_category=lambda _value: None,
            render_command=lambda _command, _description: None,
            render_skill_header=lambda _header, _count: None,
            render_skill=lambda _command, _description: None,
            render_tip=lambda text, final: tips.append((text, final)),
        ),
    )

    assert tips == [
        ("Chat", False),
        ("Multiline", False),
        (
            "Attach image: /image ~/Pictures/cat.png or start your prompt with a local image path",
            True,
        ),
    ]


def test_provider_display_handler_projects_active_provider_models_and_usage() -> None:
    output: list[str] = []
    usage: list[str] = []
    ports = ProviderDisplayPorts(
        snapshot=lambda: ProviderDisplaySnapshot(
            active_provider="primary",
            configured_providers=(
                {
                    "slug": "primary",
                    "name": "Primary",
                    "is_current": True,
                    "api_url": "https://api.example/v1",
                    "models": ("active-model", "backup-model"),
                },
            ),
        ),
        current_model=lambda: "active-model",
        translate=lambda value, **kwargs: kwargs.get("default", value),
        emit=output.append,
        emit_usage=usage.append,
    )

    handle_provider_display_command(parse_cli_command("/provider"), ports=ports)
    handle_provider_display_command(
        parse_cli_command("/provider list"),
        ports=ports,
    )

    assert output == [
        "\n  Current: active-model via primary",
        "",
        "  Configured providers:",
        "    [primary] Primary ← active",
        "      endpoint: https://api.example/v1",
        "      active-model ← current",
        "      backup-model",
        "",
        "  Use /model to switch providers or models:",
        "    /model <model-name>               — switch model",
        "    /model --provider <provider-name> — switch provider",
        "    /model <name> --provider <provider-name> — switch provider and model",
    ]
    assert usage[-1] == "  Run /api to configure provider credentials"
    assert "None" not in usage


def test_memory_display_handler_projects_mem_status_without_setup_operations() -> None:
    output: list[str] = []

    handle_memory_display_command(
        parse_cli_command("/memory ignored"),
        ports=MemoryDisplayPorts(
            database_path=lambda: "runtime/memory/memory.db",
            emit=output.append,
        ),
    )

    assert output == [
        "\n  统一记忆系统: Mem（始终启用）",
        "  数据库: runtime/memory/memory.db",
        "  工具: mem_search, mem_timeline, mem_remember",
        "  审计: Memory Service /recall/traces\n",
    ]


def test_personality_handler_applies_structured_prompt_persists_and_resets_agent() -> None:
    output: list[str] = []
    saved: list[str] = []
    prompts: list[str] = []
    resets: list[str] = []
    handle_personality_command(
        parse_cli_command("/personality CALM"),
        ports=PersonalityCommandPorts(
            personalities=lambda: {
                "calm": {
                    "system_prompt": "Stay calm.",
                    "tone": "gentle",
                    "style": "clear",
                }
            },
            set_system_prompt=prompts.append,
            reset_agent=lambda: resets.append("reset"),
            save_system_prompt=lambda value: saved.append(value) or True,
            emit=output.append,
        ),
    )

    expected_prompt = "Stay calm.\nTone: gentle\nStyle: clear"
    assert prompts == [expected_prompt]
    assert saved == [expected_prompt]
    assert resets == ["reset"]
    assert output == [
        "(^_^)b Personality set to 'calm' (saved to config)",
        '  "Stay calm.\nTone: gentle\nStyle: clear"',
    ]


def test_personality_handler_clears_lists_and_reports_unknown_without_mutation() -> None:
    output: list[str] = []
    prompts: list[str] = []
    resets: list[str] = []
    ports = PersonalityCommandPorts(
        personalities=lambda: {"bright": {"description": "Upbeat"}},
        set_system_prompt=prompts.append,
        reset_agent=lambda: resets.append("reset"),
        save_system_prompt=lambda _value: False,
        emit=output.append,
    )

    handle_personality_command(parse_cli_command("/personality neutral"), ports=ports)
    handle_personality_command(parse_cli_command("/personality unknown"), ports=ports)
    handle_personality_command(parse_cli_command("/personality"), ports=ports)

    assert prompts == [""]
    assert resets == ["reset"]
    assert output[:4] == [
        "(^_^) Personality cleared (session only)",
        "  No personality overlay — using base agent behavior.",
        "(._.) Unknown personality: unknown",
        "  Available: none, bright",
    ]
    assert output[-2:] == ["  Usage: /personality <name>", ""]


def test_reasoning_handler_reports_state_and_updates_display_or_effort() -> None:
    output: list[str] = []
    state: dict[str, object] = {"config": None, "show": False}
    refreshes: list[str] = []
    saved_display: list[bool] = []
    saved_effort: list[str] = []
    ports = ReasoningCommandPorts(
        reasoning_config=lambda: state["config"],
        show_reasoning=lambda: bool(state["show"]),
        set_reasoning_config=lambda value: state.__setitem__("config", value),
        set_show_reasoning=lambda value: state.__setitem__("show", value),
        refresh_agent_reasoning_callback=lambda: refreshes.append("refresh"),
        parse_config=lambda value: {"enabled": True, "effort": value}
        if value == "high"
        else None,
        save_display=lambda value: saved_display.append(value) or True,
        save_effort=lambda value: saved_effort.append(value) or False,
        emit=output.append,
        accent="",
        dim="",
        reset="",
    )

    handle_reasoning_command(parse_cli_command("/reasoning"), ports=ports)
    handle_reasoning_command(parse_cli_command("/reasoning show"), ports=ports)
    handle_reasoning_command(parse_cli_command("/reasoning high"), ports=ports)
    handle_reasoning_command(parse_cli_command("/reasoning unknown"), ports=ports)

    assert output == [
        "  Reasoning effort:  medium (default)",
        "  Reasoning display: off",
        "  Usage: /reasoning <none|minimal|low|medium|high|xhigh|show|hide>",
        "  ✓ Reasoning display: ON (saved)",
        "    Model thinking will be shown during and after each response.",
        "  ✓ Reasoning effort set to 'high' (session only)",
        "  (._.) Unknown argument: unknown",
        "  Valid levels: none, minimal, low, medium, high, xhigh",
        "  Display:      show, hide",
    ]
    assert state == {"config": {"enabled": True, "effort": "high"}, "show": True}
    assert refreshes == ["refresh"]
    assert saved_display == [True]
    assert saved_effort == ["high"]


def test_fast_handler_gates_status_and_service_tier_mutations() -> None:
    unavailable_output: list[str] = []
    unavailable = FastCommandPorts(
        available=lambda: False,
        service_tier=lambda: pytest.fail("must not read state when unavailable"),
        set_service_tier=lambda _value: pytest.fail("must not mutate when unavailable"),
        save_service_tier=lambda _value: pytest.fail("must not save when unavailable"),
        emit=unavailable_output.append,
        accent="",
        dim="",
        reset="",
    )
    handle_fast_command(parse_cli_command("/fast on"), ports=unavailable)

    output: list[str] = []
    state: dict[str, object] = {"service_tier": None}
    saved: list[str] = []
    ports = FastCommandPorts(
        available=lambda: True,
        service_tier=lambda: state["service_tier"],
        set_service_tier=lambda value: state.__setitem__("service_tier", value),
        save_service_tier=lambda value: saved.append(value) or False,
        emit=output.append,
        accent="",
        dim="",
        reset="",
    )
    handle_fast_command(parse_cli_command("/fast"), ports=ports)
    handle_fast_command(parse_cli_command("/fast on"), ports=ports)
    handle_fast_command(parse_cli_command("/fast invalid"), ports=ports)

    assert unavailable_output == [
        "  (._.) /fast is only available for models that support priority processing."
    ]
    assert output == [
        "  Priority Processing: normal",
        "  Usage: /fast [normal|fast|status]",
        "  ✓ Priority Processing set to FAST (session only)",
        "  (._.) Unknown argument: invalid",
        "  Usage: /fast [normal|fast|status]",
    ]
    assert state == {"service_tier": "priority"}
    assert saved == ["fast"]
    assert parse_service_tier_config("priority") == "priority"
    assert parse_service_tier_config("normal") is None
    assert parse_service_tier_config("unknown") is None


def test_compression_handler_checks_preconditions_and_syncs_agent_continuation() -> None:
    output: list[str] = []
    short_history = [{"role": "user", "content": "one"}]
    ports = CompressionCommandPorts(
        conversation_history=lambda: short_history,
        agent=lambda: pytest.fail("must not read agent for a short history"),
        compression_enabled=lambda _agent: pytest.fail("must not read config"),
        estimate_tokens=lambda _history: pytest.fail("must not estimate"),
        compress=lambda _history, _tokens, _focus: pytest.fail("must not compress"),
        synchronize_compressed_session=lambda _history, _agent: pytest.fail("must not sync"),
        summarize=lambda *_args: pytest.fail("must not summarize"),
        emit=output.append,
    )
    handle_compression_command(parse_cli_command("/compress"), ports=ports)

    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]
    agent = SimpleNamespace(compression_enabled=True)
    synced: list[tuple[list[dict[str, object]], object]] = []
    compressed = [history[0], {"role": "assistant", "content": "summary"}]
    ports = CompressionCommandPorts(
        conversation_history=lambda: history,
        agent=lambda: agent,
        compression_enabled=lambda value: bool(value.compression_enabled),
        estimate_tokens=lambda value: len(value) * 100,
        compress=lambda value, tokens, focus: (
            compressed
            if value == history and tokens == 400 and focus == "schema design"
            else pytest.fail("unexpected compression inputs")
        ),
        synchronize_compressed_session=lambda value, active_agent: synced.append(
            (value, active_agent)
        ),
        summarize=lambda before, after, before_tokens, after_tokens: {
            "noop": False,
            "headline": f"Compressed: {len(before)} -> {len(after)} messages",
            "token_line": f"Tokens: {before_tokens} -> {after_tokens}",
            "note": "retained focus",
        },
        emit=output.append,
    )
    handle_compression_command(
        parse_cli_command("/compress schema design"),
        ports=ports,
    )

    assert output == [
        "(._.) Not enough conversation to compress (need at least 4 messages).",
        '🗜️  Compressing 4 messages (~400 tokens), focus: "schema design"...',
        "  ✅ Compressed: 4 -> 2 messages",
        "     Tokens: 400 -> 200",
        "     retained focus",
    ]
    assert synced == [(compressed, agent)]


def test_compression_handler_reports_disabled_agent_and_operation_errors() -> None:
    output: list[str] = []
    history = [{"role": "user", "content": str(index)} for index in range(4)]
    ports = CompressionCommandPorts(
        conversation_history=lambda: history,
        agent=lambda: None,
        compression_enabled=lambda _agent: pytest.fail("must not inspect missing agent"),
        estimate_tokens=lambda _history: pytest.fail("must not estimate"),
        compress=lambda _history, _tokens, _focus: pytest.fail("must not compress"),
        synchronize_compressed_session=lambda _history, _agent: pytest.fail("must not sync"),
        summarize=lambda *_args: pytest.fail("must not summarize"),
        emit=output.append,
    )
    handle_compression_command(parse_cli_command("/compress"), ports=ports)

    disabled = SimpleNamespace(compression_enabled=False)
    ports = CompressionCommandPorts(
        conversation_history=lambda: history,
        agent=lambda: disabled,
        compression_enabled=lambda value: bool(value.compression_enabled),
        estimate_tokens=lambda _history: pytest.fail("must not estimate"),
        compress=lambda _history, _tokens, _focus: pytest.fail("must not compress"),
        synchronize_compressed_session=lambda _history, _agent: pytest.fail("must not sync"),
        summarize=lambda *_args: pytest.fail("must not summarize"),
        emit=output.append,
    )
    handle_compression_command(parse_cli_command("/compress"), ports=ports)

    enabled = SimpleNamespace(compression_enabled=True)
    ports = CompressionCommandPorts(
        conversation_history=lambda: history,
        agent=lambda: enabled,
        compression_enabled=lambda value: bool(value.compression_enabled),
        estimate_tokens=lambda _history: 100,
        compress=lambda _history, _tokens, _focus: (_ for _ in ()).throw(RuntimeError("offline")),
        synchronize_compressed_session=lambda _history, _agent: pytest.fail("must not sync after failure"),
        summarize=lambda *_args: pytest.fail("must not summarize after failure"),
        emit=output.append,
    )
    handle_compression_command(parse_cli_command("/compress"), ports=ports)

    assert output == [
        "(._.) No active agent -- send a message first.",
        "(._.) Compression is disabled in config.",
        "🗜️  Compressing 4 messages (~100 tokens)...",
        "  ❌ Compression failed: offline",
    ]


def test_usage_handler_reports_missing_agent_or_api_calls() -> None:
    output: list[str] = []
    unavailable = UsageCommandPorts(
        agent_available=lambda: False,
        api_calls=lambda: pytest.fail("must not read calls without an agent"),
        rate_limit_display=lambda: None,
        snapshot=lambda: pytest.fail("must not build a snapshot without an agent"),
        emit=output.append,
        no_agent_message="no agent",
        no_calls_message="no calls",
    )
    handle_usage_command(parse_cli_command("/usage"), ports=unavailable)

    no_calls = UsageCommandPorts(
        agent_available=lambda: True,
        api_calls=lambda: 0,
        rate_limit_display=lambda: pytest.fail("must not read rate limits without calls"),
        snapshot=lambda: pytest.fail("must not build a snapshot without calls"),
        emit=output.append,
        no_agent_message="no agent",
        no_calls_message="no calls",
    )
    handle_usage_command(parse_cli_command("/usage ignored"), ports=no_calls)

    assert output == ["no agent", "no calls"]


def test_doctor_handler_delegates_the_diagnostic_operation_through_a_port() -> None:
    calls: list[str] = []

    handle_doctor_command(
        parse_cli_command("/doctor ignored"),
        ports=DoctorCommandPorts(run_diagnosis=lambda: calls.append("run")),
    )

    assert calls == ["run"]


def test_api_handler_delegates_the_configuration_wizard_through_a_port() -> None:
    calls: list[str] = []

    handle_api_command(
        parse_cli_command("/api ignored"),
        ports=ApiCommandPorts(run_wizard=lambda: calls.append("run")),
    )

    assert calls == ["run"]


def test_debug_handler_delegates_debug_report_sharing_through_a_port() -> None:
    calls: list[str] = []

    handle_debug_command(
        parse_cli_command("/debug ignored"),
        ports=DebugCommandPorts(run_debug_share=lambda: calls.append("share")),
    )

    assert calls == ["share"]


def test_reload_mcp_handler_delegates_to_the_shared_runtime_operation() -> None:
    calls: list[str] = []

    handle_reload_mcp_command(
        parse_cli_command("/reload-mcp ignored"),
        ports=ReloadMcpCommandPorts(run_reload=lambda: calls.append("reload")),
    )

    assert calls == ["reload"]


def test_mcp_reload_operation_projects_changes_refreshes_and_persists() -> None:
    output: list[str] = []
    snapshots = iter(({"alpha"}, {"alpha", "beta"}))
    events: list[str] = []
    notes: list[str] = []
    ports = McpReloadRuntimePorts(
        server_names=lambda: next(snapshots),
        shutdown_servers=lambda: events.append("shutdown"),
        discover_tools=lambda: [{"name": "one"}, {"name": "two"}],
        command_running=lambda: False,
        refresh_agent_tools=lambda: events.append("refresh") or 3,
        append_reload_note=notes.append,
        persist_reload_note=lambda: events.append("persist"),
        emit=output.append,
    )

    reload_mcp_servers(ports=ports)

    assert events == ["shutdown", "refresh", "persist"]
    assert output == [
        "🔄 Reloading MCP servers...",
        "  ♻️  Reconnected: alpha",
        "  ➕ Added: beta",
        "  🔧 2 tool(s) available from 2 server(s)",
        "  ✅ 智能体已更新，可用工具：3 个",
    ]
    assert notes == [
        "[SYSTEM: MCP servers have been reloaded. Added servers: beta. "
        "Reconnected servers: alpha. 2 MCP tool(s) now available. "
        "The tool list for this conversation has been updated accordingly.]"
    ]

    failures: list[str] = []
    reload_mcp_servers(
        ports=McpReloadRuntimePorts(
            server_names=lambda: set(),
            shutdown_servers=lambda: None,
            discover_tools=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            command_running=lambda: True,
            refresh_agent_tools=lambda: pytest.fail("must not refresh"),
            append_reload_note=lambda _note: pytest.fail("must not append"),
            persist_reload_note=lambda: pytest.fail("must not persist"),
            emit=failures.append,
        )
    )
    assert failures == ["  ❌ MCP 重新加载失败：offline"]


def test_mcp_handler_lists_mutates_and_tests_servers_through_ports() -> None:
    output: list[str] = []
    config: dict[str, object] = {
        "mcp_servers": {
            "stdio": {"command": "node", "type": "stdio"},
            "remote": {"url": "https://mcp.example", "type": "http"},
        }
    }
    saved: list[dict[str, object]] = []
    probed: list[str] = []
    ports = McpCommandPorts(
        load_config=lambda: config,
        save_config=lambda value: saved.append(dict(value)),
        probe_tools=lambda _name, config: probed.append(config["url"])
        or [{"name": f"tool-{index}"} for index in range(6)],
        emit=output.append,
    )

    handle_mcp_command(parse_cli_command("/mcp list"), ports=ports)
    handle_mcp_command(
        parse_cli_command("/mcp add added https://added.example"), ports=ports
    )
    handle_mcp_command(parse_cli_command("/mcp remove missing"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp remove stdio"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp test remote"), ports=ports)

    assert "        命令: node" in output
    assert config["mcp_servers"] == {
        "remote": {"url": "https://mcp.example", "type": "http"},
        "added": {"url": "https://added.example", "type": "http"},
    }
    assert len(saved) == 2
    assert "    ❌ 未找到 MCP 服务器 'missing'" in output
    assert probed == ["https://mcp.example"]
    assert "    可用工具: 6 个" in output
    assert "      - tool-4" in output
    assert "      ... 还有 1 个工具" in output


def test_mcp_handler_reports_usage_missing_server_and_probe_failure() -> None:
    output: list[str] = []
    ports = McpCommandPorts(
        load_config=lambda: {"mcp_servers": {"bad": {"url": "https://bad"}}},
        save_config=lambda _config: pytest.fail("must not save"),
        probe_tools=lambda _name, _config: (_ for _ in ()).throw(RuntimeError("offline")),
        emit=output.append,
    )

    handle_mcp_command(parse_cli_command("/mcp add"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp remove"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp test missing"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp test bad"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp unknown"), ports=ports)

    assert "    用法: /mcp add <name> <url>" in output
    assert "    用法: /mcp remove <name>" in output
    assert "    ❌ 未找到 MCP 服务器 'missing'" in output
    assert "    ❌ 连接失败: offline" in output
    assert any(line.strip() == "MCP 服务器管理命令 (/mcp)" for line in output)


def test_browser_handler_connects_and_notifies_the_agent_after_launch() -> None:
    output: list[str] = []
    cleanup_calls: list[str] = []
    probes = iter([False, False, True])
    configured: list[str] = []
    notes: list[str] = []
    sleeps: list[float] = []
    launches: list[int] = []
    ports = BrowserCommandPorts(
        current_cdp_url=lambda: "",
        set_cdp_url=configured.append,
        clear_cdp_url=lambda: pytest.fail("must not disconnect"),
        cleanup_browsers=lambda: cleanup_calls.append("cleanup"),
        probe_port=lambda _port: next(probes),
        launch_chrome_debug=lambda port: launches.append(port) or True,
        system_name=lambda: "Windows",
        chrome_data_dir=lambda: "chrome-data",
        cloud_provider=lambda: SimpleNamespace(provider_name=lambda: "Browserbase"),
        enqueue_system_note=notes.append,
        sleep=sleeps.append,
        emit=output.append,
    )

    handle_browser_command(parse_cli_command("/browser connect"), ports=ports)

    assert cleanup_calls == ["cleanup"]
    assert launches == [9222]
    assert sleeps == [0.5]
    assert configured == ["http://localhost:9222"]
    assert "   ✓ Chrome 已启动并在端口 9222 监听" in output
    assert output[-3:] == [
        "🌐 浏览器已通过 CDP 连接到当前 Chrome",
        "   端点：http://localhost:9222",
        "",
    ]
    assert len(notes) == 1
    assert "control their real browser" in notes[0]


def test_browser_handler_projects_status_disconnect_and_invalid_usage() -> None:
    output: list[str] = []
    state = {"url": "ws://example.test:9333"}
    cleanup_calls: list[str] = []
    notes: list[str] = []
    ports = BrowserCommandPorts(
        current_cdp_url=lambda: state["url"],
        set_cdp_url=lambda value: state.__setitem__("url", value),
        clear_cdp_url=lambda: state.__setitem__("url", ""),
        cleanup_browsers=lambda: cleanup_calls.append("cleanup"),
        probe_port=lambda port: port == 9333,
        launch_chrome_debug=lambda _port: pytest.fail("must not launch"),
        system_name=lambda: "Windows",
        chrome_data_dir=lambda: "chrome-data",
        cloud_provider=lambda: SimpleNamespace(provider_name=lambda: "Browserbase"),
        enqueue_system_note=notes.append,
        sleep=lambda _seconds: pytest.fail("must not wait"),
        emit=output.append,
    )

    handle_browser_command(parse_cli_command("/browser status"), ports=ports)
    handle_browser_command(parse_cli_command("/browser disconnect"), ports=ports)
    handle_browser_command(parse_cli_command("/browser invalid"), ports=ports)
    handle_browser_command(parse_cli_command("/browser status"), ports=ports)

    assert "   状态：✓ 可访问" in output
    assert state["url"] == ""
    assert cleanup_calls == ["cleanup"]
    assert len(notes) == 1
    assert "disconnected the browser tools" in notes[0]
    assert "用法：/browser connect|disconnect|status" in output
    assert "🌐 浏览器：Browserbase（云端）" in output


def test_browser_handler_reports_manual_and_custom_endpoint_fallbacks() -> None:
    output: list[str] = []
    configured: list[str] = []
    ports = BrowserCommandPorts(
        current_cdp_url=lambda: "",
        set_cdp_url=configured.append,
        clear_cdp_url=lambda: pytest.fail("must not disconnect"),
        cleanup_browsers=lambda: None,
        probe_port=lambda _port: False,
        launch_chrome_debug=lambda _port: False,
        system_name=lambda: "Darwin",
        chrome_data_dir=lambda: "/tmp/chrome-data",
        cloud_provider=lambda: None,
        enqueue_system_note=lambda _note: None,
        sleep=lambda _seconds: pytest.fail("must not wait after failed launch"),
        emit=output.append,
    )

    handle_browser_command(parse_cli_command("/browser connect"), ports=ports)
    handle_browser_command(
        parse_cli_command("/browser connect ws://remote.example:9444/devtools"),
        ports=ports,
    )

    assert any(
        'open -a "Google Chrome" --args --remote-debugging-port=9222' in line
        for line in output
    )
    assert "   ⚠ 端口 9444 无法访问：ws://remote.example:9444/devtools" in output
    assert configured == [
        "http://localhost:9222",
        "ws://remote.example:9444/devtools",
    ]


def test_usage_handler_projects_rate_limits_cost_and_session_snapshot() -> None:
    output: list[str] = []
    snapshot = UsageDisplaySnapshot(
        model="active-model",
        input_tokens=1_200,
        output_tokens=34,
        cache_read_tokens=56,
        cache_write_tokens=78,
        prompt_tokens=1_334,
        completion_tokens=34,
        total_tokens=1_368,
        api_calls=2,
        session_duration="3m",
        cost_status="estimated",
        cost_source="official_docs_snapshot",
        cost_amount_usd=0.0123,
        context_tokens=987,
        context_length=2_000,
        context_percent=49.35,
        message_count=4,
        compressions=1,
    )

    handle_usage_command(
        parse_cli_command("/usage"),
        ports=UsageCommandPorts(
            agent_available=lambda: True,
            api_calls=lambda: 2,
            rate_limit_display=lambda: "Rate limits",
            snapshot=lambda: snapshot,
            emit=output.append,
            no_agent_message="no agent",
            no_calls_message="no calls",
        ),
    )

    assert output == [
        "",
        "Rate limits",
        "",
        "  📊 Session Token Usage",
        "  " + "─" * 40,
        "  Model:                     active-model",
        "  Input tokens:                   1,200",
        "  Cache read tokens:                 56",
        "  Cache write tokens:                78",
        "  Output tokens:                     34",
        "  Prompt tokens (total):          1,334",
        "  Completion tokens:                 34",
        "  Total tokens:                   1,368",
        "  API calls:                          2",
        "  Session duration:                  3m",
        "  Cost status:               estimated",
        "  Cost source:              official_docs_snapshot",
        "  Total cost:              ~$    0.0123",
        "  " + "─" * 40,
        "  Current context:  987 / 2,000 (49%)",
        "  Messages:         4",
        "  Compressions:     1",
    ]
def test_session_status_handler_uses_timestamp_fallbacks_and_idle_projection() -> None:
    output: list[str] = []
    started_at = datetime(2026, 7, 30, 10, 0, 0)

    handle_session_status_command(
        parse_cli_command("/status"),
        ports=SessionStatusDisplayPorts(
            session_metadata=lambda: {
                "title": "  Work  ",
                "started_at": "not-a-timestamp",
                "updated_at": "1722333900",
            },
            session_id=lambda: "session-1",
            session_start=lambda: started_at,
            home_path=lambda: "home",
            model=lambda: None,
            provider=lambda: None,
            total_tokens=lambda: 0,
            agent_running=lambda: False,
            subagent_snapshot=lambda: {"active": False},
            autonomous_sections=lambda: ("Autonomous: idle",),
            emit=output.append,
        ),
    )

    assert output == [
        "\n".join(
            [
                "Voidcube CLI Status",
                "",
                "Session ID: session-1",
                "Path: home",
                "Title: Work",
                "Model: (unknown) (unknown)",
                "Created: 2026-07-30 10:00",
                f"Last Activity: {datetime.fromtimestamp(1722333900).strftime('%Y-%m-%d %H:%M')}",
                "Tokens: 0",
                "Agent Running: No",
                "Subagents: idle",
                "Autonomous: idle",
            ]
        )
    ]


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

    def apply_history_mutation(repository_port):
        result = remove_last_user_turn(
            state["history"],
            repository=repository_port,
            session_id="active",
        )
        state["history"] = list(result.conversation_history)
        return result

    mutation_ports = HistoryMutationPorts(
        conversation_history=lambda: state["history"],
        repository=lambda: repository,
        session_id=lambda: "active",
        remove_last_user_turn=apply_history_mutation,
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
        remove_last_user_turn=lambda repository_port: remove_last_user_turn(
            history,
            repository=repository_port,
            session_id="",
        ),
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
        parse_cli_command("/rollback"),
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
        emit=output.append,
        unavailable_message="  Session database not available.",
    )

    handle_title_command(parse_cli_command("/title Mixed Case"), ports=ports)

    assert output == [expected]


def test_title_handler_projects_queued_title() -> None:
    output: list[str] = []
    result = SessionTitleResult(
        SessionTitleStatus.QUEUED,
        "session-1",
        title="Future title",
    )
    ports = TitleCommandPorts(
        get_title=lambda: result,
        set_title=lambda _value: result,
        emit=output.append,
        unavailable_message="unavailable",
    )

    handle_title_command(parse_cli_command("/title Future title"), ports=ports)

    assert output == [
        "  Session title queued: Future title (will be saved on first message)"
    ]
