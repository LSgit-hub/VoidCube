from __future__ import annotations

import queue
import logging
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import voidcube.interfaces.cli.application as cli_module
from voidcube.interfaces.cli.application import VoidcubeCLI
import voidcube.interfaces.cli.commands.registry as command_handler_registry
from voidcube.interfaces.cli.autonomous.events import AutonomousPanelEventPorts
from voidcube.interfaces.cli.commands.execution import (
    BUILTIN_COMMAND_SPECS,
    CommandBusyLifecycle,
    initialize_command_execution,
)
from voidcube.interfaces.cli.commands.router import parse_cli_command
from voidcube.interfaces.cli.commands.registry import install_cli_command_execution
from voidcube.interfaces.cli.commands.handlers.display import (
    ConfigDisplayPorts,
    HelpDisplayPorts,
    HelpDisplayText,
    MemoryDisplayPorts,
    ProviderDisplayPorts,
    ProviderDisplaySnapshot,
    SessionStatusDisplayPorts,
)
from voidcube.interfaces.cli.commands.handlers.info import UsageCommandPorts, UsageDisplaySnapshot
from voidcube.interfaces.cli.commands.handlers.operations import (
    ApiCommandPorts,
    DebugCommandPorts,
    DoctorCommandPorts,
    McpReloadRuntimePorts,
    ReloadMcpCommandPorts,
)
from voidcube.interfaces.cli.commands.handlers.personality import PersonalityCommandPorts
from voidcube.interfaces.cli.commands.handlers.compression import CompressionCommandPorts
from voidcube.interfaces.cli.commands.handlers.browser import BrowserCommandPorts
from voidcube.interfaces.cli.commands.handlers.mcp import McpCommandPorts, handle_mcp_command
from voidcube.interfaces.cli.commands.handlers.tools import ToolsCommandPorts, ToolsCommandText
from voidcube.interfaces.cli.commands.handlers.skills import SkillsCommandPorts
from voidcube.interfaces.cli.voice_runtime_state import CliVoiceRuntimeState
from voidcube.interfaces.cli.commands.handlers.tasks import TasksCommandPorts
from voidcube.interfaces.cli.commands.handlers.autonomous import AutonomousCommandPorts
from voidcube.interfaces.cli.commands.handlers.plan import PlanCommandPorts
from voidcube.interfaces.cli.commands.handlers.language import LanguageCommandPorts
from voidcube.interfaces.cli.commands.handlers.voice import VoiceCommandPorts
from voidcube.interfaces.cli.commands.handlers.preset import PresetCommandPorts, PresetCommandText
from voidcube.interfaces.cli.commands.catalog import COMMAND_REGISTRY
from voidcube.application.sessions import (
    BranchSessionResult,
    ResumeSessionResult,
    SessionHydration,
    SessionHydrationStatus,
    SessionLifecycleState,
)
from voidcube.application.application_runtime import ApplicationRuntime
from voidcube.domain.contracts.interaction import ApprovalStatus


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


EXPECTED_BUILTINS = {
    "api",
    "attach",
    "auto",
    "auto-q",
    "background",
    "branch",
    "browser",
    "btw",
    "cancel",
    "clear",
    "compress",
    "config",
    "debug",
    "doctor",
    "export",
    "fast",
    "find",
    "goal",
    "help",
    "history",
    "image",
    "language",
    "mcp",
    "memory",
    "model",
    "new",
    "paste",
    "personality",
    "plan",
    "plugins",
    "preset",
    "profile",
    "provider",
    "queue",
    "quit",
    "reasoning",
    "reload-mcp",
    "resume",
    "retry",
    "rollback",
    "save",
    "skills",
    "status",
    "statusbar",
    "stop",
    "tasks",
    "title",
    "tools",
    "toolsets",
    "undo",
    "usage",
    "verbose",
    "voice",
    "yolo",
}


def test_builtin_table_is_complete_and_contains_no_removed_commands() -> None:
    assert set(BUILTIN_COMMAND_SPECS) == EXPECTED_BUILTINS
    assert "cron" not in BUILTIN_COMMAND_SPECS
    assert "insights" not in BUILTIN_COMMAND_SPECS
    assert "connect" not in BUILTIN_COMMAND_SPECS
    assert not (Path("tools") / "connection_profiles.py").exists()
    for spec in BUILTIN_COMMAND_SPECS.values():
        if spec.exits:
            continue
        if spec.handler_key:
            assert not spec.handler_name
        else:
            assert hasattr(VoidcubeCLI, spec.handler_name), spec.handler_name


def test_retired_cron_integration_has_no_active_runtime_or_config_surface() -> None:
    active_surfaces = (
        "src/voidcube/infrastructure/config/configuration.py",
        "src/voidcube/interfaces/cli/application.py",
        "src/voidcube/interfaces/cli/main.py",
        "src/voidcube/interfaces/cli/locales/en_US.json",
        "src/voidcube/interfaces/cli/locales/zh_CN.json",
    )

    for path in active_surfaces:
        source = Path(path).read_text(encoding="utf-8").casefold()
        assert "cron" not in source, path


def test_every_discoverable_cli_builtin_has_an_execution_spec() -> None:
    discoverable = {
        command.name for command in COMMAND_REGISTRY if not command.gateway_only
    }

    assert discoverable <= set(BUILTIN_COMMAND_SPECS)


def test_every_cli_builtin_is_present_in_the_command_catalog() -> None:
    catalog_names = {
        command.name for command in COMMAND_REGISTRY if not command.gateway_only
    }

    assert set(BUILTIN_COMMAND_SPECS) <= catalog_names


def test_executor_distinguishes_exit_builtin_and_dynamic_command() -> None:
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    initialize_command_execution(host)

    exit_result = host._builtin_command_executor.execute(parse_cli_command("/quit"))
    dynamic_result = host._builtin_command_executor.execute(
        parse_cli_command("/plugin-command")
    )

    assert exit_result.handled is True
    assert exit_result.continue_running is False
    assert dynamic_result.handled is False
    assert dynamic_result.continue_running is True


def test_executor_uses_registered_handler_for_tools_commands() -> None:
    calls: list[tuple[str, str | None]] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    initialize_command_execution(
        host,
        command_handlers={
            "help": lambda _request: calls.append(("help", None)),
            "tools": lambda request: calls.append(("tools", request.arguments)),
        },
    )

    host._builtin_command_executor.execute(
        parse_cli_command("/tools Enable MixedCase")
    )
    host._builtin_command_executor.execute(parse_cli_command("/help"))

    assert calls == [("tools", "Enable MixedCase"), ("help", None)]


def test_cli_process_routes_help_through_display_handler(monkeypatch) -> None:
    events: list[tuple[object, ...]] = []
    observed_skill_catalogs: list[object] = []
    skill_catalog = lambda: {}
    monkeypatch.setattr(
        command_handler_registry,
        "_help_display_ports",
        lambda _host, **kwargs: observed_skill_catalogs.append(
            kwargs["skill_commands"]
        )
        or HelpDisplayPorts(
            command_categories=lambda: {"Info": {"/help": "Show help"}},
            command_available=lambda _command: True,
            skill_commands=lambda: {},
            text=HelpDisplayText("Help", "Skills", "Chat", "Multiline", "Paste"),
            is_termux=lambda: False,
            termux_example_path=lambda: "image.png",
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
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(
        app,
        emit=lambda _text: None,
        skill_commands=skill_catalog,
    )

    assert app.process_command("/help") is True
    assert events == [
        ("header", "Help"),
        ("category", "Info"),
        ("command", "/help", "Show help"),
        ("tip", "Chat", False),
        ("tip", "Multiline", False),
        ("tip", "Paste", True),
    ]
    assert observed_skill_catalogs == [skill_catalog]


def test_cli_process_routes_tools_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    events: list[object] = []
    state: dict[str, object] = {"toolsets": ("terminal",)}
    monkeypatch.setattr(
        command_handler_registry,
        "_tools_command_ports",
        lambda _host, *, emit, translate: ToolsCommandPorts(
            render_catalog=lambda: events.append("catalog"),
            list_configuration=lambda: events.append("list"),
            change_configuration=lambda action, names: events.append((action, names)),
            load_enabled_toolsets=lambda: ("web",),
            set_enabled_toolsets=lambda value: state.__setitem__("toolsets", value),
            reset_session=lambda: events.append("reset"),
            emit=emit,
            text=ToolsCommandText(
                usage=lambda action: f"usage {action}",
                builtin_example=lambda action: f"builtin {action}",
                mcp_example=lambda action: f"mcp {action}",
                changing=lambda action, names: f"{action}: {', '.join(names)}",
                session_reset="session reset",
            ),
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/tools enable web") is True
    assert events == [("enable", ("web",)), "reset"]
    assert state["toolsets"] == ("web",)
    assert output == ["enable: web", "session reset"]


def test_cli_process_routes_skills_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    events: list[object] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_skills_command_ports",
        lambda *, emit: SkillsCommandPorts(
            builtin_skills=lambda: (),
            installed_skills=lambda: (),
            search=lambda query: events.append(f"search:{query}") or (),
            install=lambda _name: pytest.fail("must not install"),
            uninstall=lambda _name: pytest.fail("must not uninstall"),
            refresh_cache=lambda: pytest.fail("must not refresh"),
            emit=emit,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/skills search Mixed Case") is True
    assert events == ["search:Mixed Case"]
    assert output == ["\n  搜索技能: 'Mixed Case'", "    未找到匹配的技能"]


def test_cli_process_routes_tasks_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    events: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_tasks_command_ports",
        lambda _host, *, emit: TasksCommandPorts(
            has_display_managers=lambda: True,
            render_subagent_tasks=lambda: events.append("render") or "Subagent Panel",
            render_subagent_task=lambda _task_ref: pytest.fail("must not render detail"),
            render_subagent_task_log=lambda _task_ref: pytest.fail("must not render log"),
            background_tasks=lambda: pytest.fail("must not read CLI background tasks"),
            now=lambda: pytest.fail("must not read time"),
            move_to_background=lambda _task_ref: pytest.fail("must not move task"),
            bring_to_foreground=lambda _task_ref: pytest.fail("must not move task"),
            render_output=lambda text: events.append(text),
            emit=emit,
            invalidate=lambda: pytest.fail("must not invalidate"),
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/tasks list") is True
    assert events == ["render", "Subagent Panel"]
    assert output == []


def test_cli_process_routes_auto_commands_through_explicit_ports() -> None:
    events: list[tuple[str, str]] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(
        app,
        emit=lambda _text: None,
        autonomous_command_ports=AutonomousCommandPorts(
            activate=lambda focus: events.append(("activate", focus)),
            deactivate=lambda: events.append(("deactivate", "")),
        ),
    )

    assert app.process_command("/auto Focus Mixed Case") is True
    assert app.process_command("/auto-q") is True
    assert events == [("activate", "Focus Mixed Case"), ("deactivate", "")]


def test_cli_process_routes_cancel_to_the_active_user_agent() -> None:
    events: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._agent_running = True
    app.agent = SimpleNamespace(interrupt=lambda message: events.append(message))
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda message: events.append(message))

    assert app.process_command("/cancel") is True
    assert events == [
        None,
        "  已请求取消当前用户任务。",
    ]


@pytest.mark.parametrize("command", ["/new", "/resume latest", "/undo", "/model"])
def test_cli_rejects_session_and_model_mutations_during_active_turn(
    monkeypatch, command
) -> None:
    output = []
    mutations = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._turn_scheduler_runtime = SimpleNamespace(
        scheduler=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(active=SimpleNamespace(request_id="active"))
        )
    )
    app._builtin_command_executor = SimpleNamespace(
        execute=lambda _request: mutations.append(command)
    )
    monkeypatch.setattr(cli_module, "_cprint", output.append)

    assert app.process_command(command) is True
    assert mutations == []
    assert output == ["  Command unavailable while a turn is active."]


def test_cli_process_routes_plan_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    events: list[tuple[str, str]] = []
    plan_path = Path(".VoidCube/plans/plan.md")
    monkeypatch.setattr(
        command_handler_registry,
        "_plan_command_ports",
        lambda _host, *, emit: PlanCommandPorts(
            build_plan_path=lambda _instruction: plan_path,
            build_skill_message=lambda command, instruction, _note: events.append(
                (command, instruction)
            ) or "plan message",
            enqueue=lambda message: events.append(("enqueue", message)),
            emit=emit,
            render_error=lambda message: pytest.fail(message),
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/plan Preserve Case") is True
    assert events == [("/plan", "Preserve Case"), ("enqueue", "plan message")]
    assert output == [
        f"  📝 Plan mode queued via skill. Markdown plan target: {plan_path}"
    ]


def test_cli_process_routes_background_through_explicit_ports() -> None:
    output: list[str] = []
    started: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._start_background_agent_task = started.append
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/background Preserve Case") is True
    assert started == ["Preserve Case"]
    assert output == []


def test_cli_process_routes_btw_through_explicit_ports() -> None:
    output: list[str] = []
    started: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._start_btw_side_question = started.append
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/btw Preserve Case") is True
    assert started == ["Preserve Case"]
    assert output == []


def test_cli_process_routes_language_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_language_command_ports",
        lambda *, emit: LanguageCommandPorts(
            current_locale=lambda: "en_US",
            available_locales=lambda: [],
            translate=lambda key, **kwargs: key + (str(kwargs) if kwargs else ""),
            set_locale=lambda locale: events.append(("locale", locale)),
            rebuild_command_lookups=lambda: events.append(("lookups", "")),
            persist_locale=lambda locale: events.append(("persist", locale)) or False,
            emit=emit,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/language -EN") is True
    assert events == [("locale", "en_US"), ("lookups", ""), ("persist", "en_US")]
    assert output == [
        "  language_command.set_to{'locale': 'en_US'}",
        "  language_command.switched_en",
    ]


def test_cli_process_routes_voice_through_explicit_ports() -> None:
    output: list[str] = []
    events: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._voice_runtime_state = CliVoiceRuntimeState()
    app._enable_voice_mode = lambda: events.append("enable")
    app._disable_voice_mode = lambda: events.append("disable")
    app._show_voice_tts_status = lambda: events.append("tts_status")
    app._speak_voice_tts = lambda text: events.append(f"tts_speak:{text}")
    app._show_voice_status = lambda: events.append("status")
    app._voice_target = lambda: "terminal"
    app._set_voice_target = lambda target: events.append(f"target:{target}")
    app._voice_start_recording = lambda: events.append("session")
    app._voice_stop_and_transcribe = lambda: events.append("interrupt")
    app._start_supervisor_continuous_voice = lambda: events.append("continuous:on")
    app._stop_supervisor_continuous_voice = lambda: events.append("continuous:off")
    app._show_voice_help = lambda: events.append("help")
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/voice status") is True
    assert events == ["status"]
    assert output == []


def test_cli_process_routes_preset_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_preset_command_ports",
        lambda *, emit: PresetCommandPorts(
            list_presets=lambda: [],
            load_preset=lambda _name: None,
            apply_preset=lambda _name: {"success": False, "reason": "preset_not_found"},
            emit=emit,
            text=PresetCommandText(dim="", accent="", bold="", reset=""),
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/preset apply missing") is True
    assert output == ["  Preset not found: missing"]


def test_autonomous_registry_ports_reuse_existing_gate_operations(monkeypatch) -> None:
    events: list[tuple[object, ...]] = []
    host = object()
    monkeypatch.setattr(
        "voidcube.interfaces.cli.autonomous.gate.handle_auto_command",
        lambda passed_host, command, **kwargs: events.append(
            ("activate", passed_host, command, kwargs["cprint"])
        ),
    )
    monkeypatch.setattr(
        "voidcube.interfaces.cli.autonomous.gate.handle_auto_q_command",
        lambda passed_host, **kwargs: events.append(
            ("deactivate", passed_host, kwargs["cprint"])
        ),
    )

    ports = command_handler_registry.autonomous_command_ports_for_host(
        host,
        event_ports=AutonomousPanelEventPorts(
            gate_active=lambda: False,
            execution_events=lambda: [],
            set_execution_events=lambda _events: None,
            trim_status_bar_text=lambda text, _width: text,
            last_supervisor_event_key=lambda: "",
            set_last_supervisor_event_key=lambda _value: None,
        ),
        emit=lambda text: events.append(("emit", text)),
        refresh_gateway_cli_presence=lambda **_kwargs: None,
        interrupt_current_task=lambda **_kwargs: True,
        push_cli_agent_scene=lambda *_args, **_kwargs: True,
        thread_factory=object,
    )
    ports.activate("focus words")
    ports.deactivate()

    assert events[0][:3] == ("activate", host, "/auto focus words")
    assert events[1] == ("deactivate", host, events[1][2])


def test_cli_process_routes_usage_through_information_handler(monkeypatch) -> None:
    output: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_usage_command_ports",
        lambda _host, *, emit: UsageCommandPorts(
            agent_available=lambda: True,
            api_calls=lambda: 1,
            rate_limit_display=lambda: None,
            snapshot=lambda: UsageDisplaySnapshot(
                model="active-model",
                input_tokens=0,
                output_tokens=1,
                cache_read_tokens=0,
                cache_write_tokens=0,
                prompt_tokens=0,
                completion_tokens=1,
                total_tokens=1,
                api_calls=1,
                session_duration="1s",
                cost_status="unknown",
                cost_source="none",
                cost_amount_usd=None,
                context_tokens=1,
                context_length=10,
                context_percent=10,
                message_count=1,
                compressions=0,
            ),
            emit=output.append,
            no_agent_message="no agent",
            no_calls_message="no calls",
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/usage") is True
    assert output[0] == "  📊 Session Token Usage"
    assert output[-1] == "  Note:             Pricing unknown for active-model"


def test_cli_process_routes_doctor_through_operation_handler(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_doctor_command_ports",
        lambda: DoctorCommandPorts(run_diagnosis=lambda: calls.append("run")),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/doctor") is True
    assert calls == ["run"]


def test_cli_process_routes_debug_through_operation_handler(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_debug_command_ports",
        lambda: DebugCommandPorts(run_debug_share=lambda: calls.append("share")),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/debug") is True
    assert calls == ["share"]


def test_cli_process_routes_reload_mcp_through_operation_handler(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        command_handler_registry,
        "reload_mcp_for_host",
        lambda host, *, emit: calls.append((host, emit)),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/reload-mcp") is True
    assert calls and calls[0][0] is app


def test_cli_process_routes_mcp_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    configured: dict[str, object] = {"mcp_servers": {}}
    saves: list[object] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_mcp_command_ports",
        lambda: McpCommandPorts(
            load_config=lambda: configured,
            save_config=saves.append,
            probe_tools=lambda _name, _config: [],
            emit=output.append,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/mcp add local http://localhost:3000") is True
    assert configured["mcp_servers"] == {
        "local": {"url": "http://localhost:3000", "type": "http"}
    }
    assert saves == [configured]
    assert "    ✅ MCP 服务器 'local' 添加成功" in output


def test_mcp_command_ports_bind_config_storage_and_connection_probe(monkeypatch) -> None:
    from voidcube.infrastructure.config import configuration as config_module
    import voidcube.interfaces.cli.mcp_config as mcp_config

    config: dict[str, object] = {"mcp_servers": {}}
    saved: list[object] = []
    probed: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(config_module, "save_config", saved.append)
    monkeypatch.setattr(
        mcp_config,
        "probe_mcp_server",
        lambda name, config: probed.append((name, config))
        or [("remote-tool", "description")],
    )
    ports = command_handler_registry._mcp_command_ports()

    handle_mcp_command(parse_cli_command("/mcp add remote https://mcp.example"), ports=ports)
    handle_mcp_command(parse_cli_command("/mcp test remote"), ports=ports)

    assert saved == [config]
    assert probed == [
        ("remote", {"url": "https://mcp.example", "type": "http"})
    ]


def test_mcp_reload_registry_ports_refresh_agent_and_persist_history(monkeypatch) -> None:
    from voidcube.extensions.tools.mcp import mcp_tool
    from voidcube.extensions.tools import model_tools

    output: list[str] = []
    servers = {"old": object()}
    monkeypatch.setattr(mcp_tool, "_servers", servers)
    monkeypatch.setattr(mcp_tool, "_lock", threading.RLock())
    monkeypatch.setattr(mcp_tool, "shutdown_mcp_servers", servers.clear)
    monkeypatch.setattr(
        mcp_tool,
        "discover_mcp_tools",
        lambda: servers.update({"new": object()}) or [{"name": "mcp"}],
    )
    definitions = [{"function": {"name": "mcp_new"}}]
    monkeypatch.setattr(model_tools, "get_tool_definitions", lambda **_kwargs: definitions)
    persisted: list[list[dict[str, str]]] = []
    agent = SimpleNamespace(
        enabled_toolsets=["all"],
        tools=[],
        valid_tool_names=set(),
        _session_persistence=SimpleNamespace(persist=lambda history: persisted.append(list(history)),),
    )
    host = SimpleNamespace(
        agent=agent,
        conversation_history=[],
        _command_running=True,
    )

    ports = command_handler_registry._mcp_reload_runtime_ports(host, emit=output.append)
    from voidcube.interfaces.cli.commands.handlers.operations import reload_mcp_servers

    reload_mcp_servers(ports=ports)

    assert agent.tools == definitions
    assert agent.valid_tool_names == {"mcp_new"}
    assert len(host.conversation_history) == 1
    assert persisted == [host.conversation_history]
    assert output == [
        "  ➕ Added: new",
        "  ➖ Removed: old",
        "  🔧 1 tool(s) available from 1 server(s)",
        "  ✅ 智能体已更新，可用工具：1 个",
    ]


def test_cli_process_routes_browser_through_explicit_ports(monkeypatch) -> None:
    output: list[str] = []
    configured: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_browser_command_ports",
        lambda _host, *, emit: BrowserCommandPorts(
            current_cdp_url=lambda: "",
            set_cdp_url=configured.append,
            clear_cdp_url=lambda: None,
            cleanup_browsers=lambda: None,
            probe_port=lambda _port: True,
            launch_chrome_debug=lambda _port: pytest.fail("must not launch"),
            system_name=lambda: "Windows",
            chrome_data_dir=lambda: "chrome-data",
            cloud_provider=lambda: None,
            enqueue_system_note=lambda _note: None,
            sleep=lambda _seconds: None,
            emit=emit,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/browser connect ws://example.test:9333") is True
    assert configured == ["ws://example.test:9333"]
    assert "   ✓ Chrome 已在端口 9333 监听" in output


def test_browser_ports_bind_environment_and_host_launch_operation(monkeypatch) -> None:
    output: list[str] = []
    launches: list[tuple[int, str]] = []
    host = SimpleNamespace(
        _try_launch_chrome_debug=lambda port, system: launches.append((port, system))
        or True,
        _pending_input=queue.Queue(),
    )
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Windows")

    ports = command_handler_registry._browser_command_ports(host, emit=output.append)
    ports.set_cdp_url("ws://example.test:9224")
    ports.launch_chrome_debug(9222)
    ports.enqueue_system_note("connected")
    ports.clear_cdp_url()

    assert ports.current_cdp_url() == ""
    assert launches == [(9222, "Windows")]
    assert host._pending_input.get_nowait() == "connected"


def test_debug_ports_bind_the_default_share_request(monkeypatch) -> None:
    observed: list[object] = []
    monkeypatch.setattr("voidcube.interfaces.cli.debug.run_debug", observed.append)

    command_handler_registry._debug_command_ports().run_debug_share()

    assert len(observed) == 1
    args = observed[0]
    assert vars(args) == {
        "debug_command": "share",
        "lines": 200,
        "expire": 7,
        "local": False,
    }


def test_cli_process_routes_api_through_operation_handler(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_api_command_ports",
        lambda _host: ApiCommandPorts(run_wizard=lambda: calls.append("run")),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/api") is True
    assert calls == ["run"]


def test_cli_process_routes_provider_through_display_handler(monkeypatch) -> None:
    output: list[str] = []
    usage: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_provider_display_ports",
        lambda _host, *, emit, translate: ProviderDisplayPorts(
            snapshot=lambda: ProviderDisplaySnapshot(
                active_provider="primary",
                configured_providers=(),
            ),
            current_model=lambda: "active-model",
            translate=translate,
            emit=output.append,
            emit_usage=usage.append,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/provider list") is True
    assert usage[-1] == "  Run /api to configure provider credentials"
    assert output == []


def test_cli_process_routes_memory_through_display_handler(monkeypatch) -> None:
    output: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_memory_display_ports",
        lambda: MemoryDisplayPorts(
            database_path=lambda: "runtime/memory/memory.db",
            emit=output.append,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/memory") is True
    assert output[1] == "  数据库: runtime/memory/memory.db"


def test_cli_process_routes_personality_through_mutation_handler(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_personality_command_ports",
        lambda _host: PersonalityCommandPorts(
            personalities=lambda: {},
            set_system_prompt=lambda value: calls.append(f"prompt:{value}"),
            reset_agent=lambda: calls.append("reset"),
            save_system_prompt=lambda value: calls.append(f"save:{value}") or True,
            emit=lambda value: calls.append(f"emit:{value}"),
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/personality default") is True
    assert calls == [
        "prompt:",
        "reset",
        "save:",
        "emit:(^_^)b Personality cleared (saved to config)",
        "emit:  No personality overlay — using base agent behavior.",
    ]


def test_cli_process_personality_uses_shared_config_persistence(monkeypatch) -> None:
    from voidcube.infrastructure.config import configuration as config_module

    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_module,
        "save_config_value",
        lambda key, value: saved.append((key, value)) or True,
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.personalities = {"calm": "Stay calm."}
    app.system_prompt = ""
    app.agent = object()
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/personality calm") is True
    assert app.system_prompt == "Stay calm."
    assert app.agent is None
    assert saved == [("agent.system_prompt", "Stay calm.")]


def test_cli_process_routes_reasoning_through_runtime_ports(monkeypatch) -> None:
    from voidcube.infrastructure.config import configuration as config_module

    saved: list[tuple[str, object]] = []
    monkeypatch.setattr(
        config_module,
        "save_config_value",
        lambda key, value: saved.append((key, value)) or True,
    )
    output: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.reasoning_config = None
    app.show_reasoning = False
    app.agent = SimpleNamespace(reasoning_callback=None)
    app._current_reasoning_callback = lambda: "reasoning-callback"
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/reasoning show") is True
    assert app.show_reasoning is True
    assert app.agent.reasoning_callback == "reasoning-callback"
    assert saved == [("display.show_reasoning", True)]
    assert output[-2:] == [
        "  \033[1;38;2;48;54;61m✓ Reasoning display: ON (saved)\033[0m",
        "  \033[2m  Model thinking will be shown during and after each response.\033[0m",
    ]


def test_cli_process_routes_fast_through_runtime_ports(monkeypatch) -> None:
    from voidcube.infrastructure.config import configuration as config_module

    saved: list[tuple[str, object]] = []
    monkeypatch.setattr(
        config_module,
        "save_config_value",
        lambda key, value: saved.append((key, value)) or True,
    )
    output: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.service_tier = None
    app.agent = object()
    app._fast_command_available = lambda: True
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/fast fast") is True
    assert app.service_tier == "priority"
    assert app.agent is None
    assert saved == [("agent.service_tier", "fast")]
    assert "Priority Processing set to FAST" in output[-1]


def test_cli_process_routes_compress_through_continuation_sync_ports(monkeypatch) -> None:
    calls: list[object] = []
    compressed = [{"role": "assistant", "content": "summary"}]
    agent = SimpleNamespace(compression_enabled=True, session_id="continuation-id")
    monkeypatch.setattr(
        command_handler_registry,
        "_compression_command_ports",
        lambda _host, *, emit: CompressionCommandPorts(
            conversation_history=lambda: [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
                {"role": "assistant", "content": "four"},
            ],
            agent=lambda: agent,
            compression_enabled=lambda value: value.compression_enabled,
            estimate_tokens=lambda history: len(history),
            compress=lambda _history, _tokens, focus: calls.append(("compress", focus)) or compressed,
            synchronize_compressed_session=lambda history, active_agent: calls.append(
                ("sync", history, active_agent)
            ),
            summarize=lambda *_args: {"noop": False, "headline": "done", "token_line": "tokens", "note": None},
            emit=emit,
        ),
    )
    output: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/compress schema") is True
    assert calls == [("compress", "schema"), ("sync", compressed, agent)]
    assert output[-2:] == ["  ✅ done", "     tokens"]


def test_compression_ports_sync_host_to_the_agent_continuation_session() -> None:
    persisted: list[list[dict[str, str]]] = []
    history = [{"role": "assistant", "content": "summary"}]
    agent = SimpleNamespace(
        session_id="continuation-id",
        persist_compressed_session_history=persisted.append,
    )
    application_runtime = SimpleNamespace(
        state=SimpleNamespace(session_hydration=object()),
        clear_session_hydration=lambda: setattr(
            application_runtime.state, "session_hydration", None
        ),
    )
    host = SimpleNamespace(
        conversation_history=[],
        session_id="previous-id",
        _ensure_application_runtime=lambda: application_runtime,
    )

    ports = command_handler_registry._compression_command_ports(
        host,
        emit=lambda _text: None,
    )
    ports.synchronize_compressed_session(history, agent)

    assert persisted == [history]
    assert host.conversation_history == history
    assert host.session_id == "continuation-id"
    assert application_runtime.state.session_hydration is None


def test_api_command_ports_bind_only_explicit_runtime_updates(monkeypatch) -> None:
    host = SimpleNamespace(
        model="old-model",
        provider="old-provider",
        requested_provider="old-requested-provider",
    )
    observed: list[object] = []

    def fake_wizard(runtime) -> None:
        observed.append(runtime)
        assert runtime.set_model is not None
        assert runtime.set_provider is not None
        assert runtime.set_requested_provider is not None
        runtime.set_model("new-model")
        runtime.set_provider("new-provider")
        runtime.set_requested_provider("new-requested-provider")

    monkeypatch.setattr("voidcube.interfaces.cli.configuration.run_api_config_wizard", fake_wizard)

    command_handler_registry._api_command_ports(host).run_wizard()

    assert len(observed) == 1
    assert host == SimpleNamespace(
        model="new-model",
        provider="new-provider",
        requested_provider="new-requested-provider",
    )


def test_verbose_explicit_mode_applies_logging_levels(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_cprint", lambda _text: None)
    root_logger = logging.getLogger()
    noisy_names = (
        "openai", "openai._base_client", "httpx", "httpcore", "asyncio",
        "hpack", "grpc", "modal",
    )
    noisy_loggers = [logging.getLogger(name) for name in noisy_names]
    original_levels = [root_logger.level, *(logger.level for logger in noisy_loggers)]
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app.tool_progress_mode = "all"
    app.verbose = False
    app.agent = None

    try:
        app._set_verbose_mode("verbose")

        assert app.tool_progress_mode == "verbose"
        assert app.verbose is True
        assert root_logger.level == logging.DEBUG
        assert [logger.level for logger in noisy_loggers] == [logging.WARNING] * len(
            noisy_loggers
        )
    finally:
        root_logger.setLevel(original_levels[0])
        for logger, level in zip(noisy_loggers, original_levels[1:]):
            logger.setLevel(level)


def test_cli_process_routes_verbose_through_explicit_mode_handler() -> None:
    output: list[str] = []
    selected: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.tool_progress_mode = "all"
    app._set_verbose_mode = selected.append
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/verbose") is True
    assert selected == []
    assert output == [
        "  Tool progress: ALL",
        "  Usage: /verbose [off|new|all|verbose]",
    ]

    output.clear()
    assert app.process_command("/verbose off") is True
    assert selected == ["off"]
    assert output == []



def test_busy_lifecycle_restores_nested_and_exceptional_state() -> None:
    invalidations: list[float] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda *, min_interval: invalidations.append(min_interval)
    lifecycle = CommandBusyLifecycle(host)

    with lifecycle.activate("outer"):
        assert (host._command_running, host._command_status) == (True, "outer")
        with lifecycle.activate("inner"):
            assert (host._command_running, host._command_status) == (True, "inner")
        assert (host._command_running, host._command_status) == (True, "outer")

    assert (host._command_running, host._command_status) == (False, "")
    with pytest.raises(RuntimeError):
        with lifecycle.activate("failing"):
            raise RuntimeError("stop")
    assert (host._command_running, host._command_status) == (False, "")
    assert invalidations == [0.0] * 6


def test_busy_spec_wraps_handler_and_restores_state() -> None:
    observed: list[tuple[bool, str, str]] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    initialize_command_execution(
        host,
        command_handlers={
            "skills": lambda request: observed.append(
                (host._command_running, host._command_status, request.original)
            )
        },
    )

    result = host._builtin_command_executor.execute(
        parse_cli_command("/skills search MixedCase")
    )

    assert result.handled is True
    assert observed == [(True, "正在搜索技能……", "/skills search MixedCase")]
    assert (host._command_running, host._command_status) == (False, "")


def test_cli_process_uses_execution_table_for_quit() -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    initialize_command_execution(app)

    assert app.process_command("/quit") is False


def test_cli_process_uses_execution_table_for_queue(monkeypatch) -> None:
    output: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._pending_input = queue.Queue()
    app._agent_running = True
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/queue Keep MixedCase") is True
    assert app._pending_input.get_nowait() == "Keep MixedCase"
    assert output == ["  Queued for the next turn: Keep MixedCase"]


def test_cli_process_routes_resume_through_registry_and_shared_use_case(
    monkeypatch,
) -> None:
    output: list[str] = []
    applied: list[SessionLifecycleState] = []
    observed: list[dict[str, object]] = []
    started_at = datetime(2026, 7, 29, 20, 30, 0)
    state = SessionLifecycleState(
        session_id="target-id",
        session_start=started_at,
        conversation_history=(),
        resumed=True,
    )

    def fake_resume_session(**kwargs):
        observed.append(kwargs)
        return ResumeSessionResult(state=state, metadata={"title": "Saved"})

    repository = object()
    runtime = ApplicationRuntime.create(
        session_id="current-id",
        session_start=started_at,
    )
    monkeypatch.setattr(runtime, "resume_session", fake_resume_session)
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._application_runtime = runtime
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._session_db = repository
    app.session_id = "current-id"
    app.session_start = started_at
    app._list_recent_sessions = lambda *, limit: [{"id": "target-id"}]
    app._show_recent_sessions = lambda *, reason: False
    app._apply_session_lifecycle_state = applied.append
    app._display_resumed_history = lambda: pytest.fail(
        "empty resumed history must not render"
    )
    app._session_hydration = None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/resume 1") is True
    assert observed == [
        {
            "repository": repository,
            "target_session_id": "target-id",
            "session_start": started_at,
        }
    ]
    assert applied == [state]
    assert app._session_hydration.session_id == "target-id"
    assert output == [
        '  ↻ Resumed session target-id "Saved" — no messages, starting fresh.'
    ]


def test_cli_process_bare_resume_defaults_to_first_recent_session(monkeypatch) -> None:
    output: list[str] = []
    observed: list[str] = []
    started_at = datetime(2026, 7, 29, 20, 30, 0)
    state = SessionLifecycleState(
        session_id="latest-id",
        session_start=started_at,
        conversation_history=(),
        resumed=True,
    )
    runtime = ApplicationRuntime.create(
        session_id="current-id",
        session_start=started_at,
    )
    monkeypatch.setattr(
        runtime,
        "resume_session",
        lambda **kwargs: observed.append(kwargs["target_session_id"])
        or ResumeSessionResult(state=state, metadata={}),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._application_runtime = runtime
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._session_db = object()
    app.session_id = "current-id"
    app.session_start = started_at
    app._list_recent_sessions = lambda *, limit: [
        {"id": "empty-id", "message_count": 0},
        {"id": "latest-id", "message_count": 2},
    ]
    app._show_recent_sessions = lambda *, reason: False
    app._apply_session_lifecycle_state = lambda _state: None
    app._display_resumed_history = lambda: None
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/resume") is True
    assert observed == ["empty-id"]
    assert output == [
        "  ↻ Resumed session latest-id — no messages, starting fresh."
    ]


def test_cli_process_routes_branch_through_registry_with_runtime_snapshot(
    monkeypatch,
) -> None:
    output: list[str] = []
    applied: list[SessionLifecycleState] = []
    observed: list[dict[str, object]] = []
    history = [{"role": "user", "content": "question"}]
    state = SessionLifecycleState(
        session_id="branch-id",
        session_start=datetime(2026, 7, 29, 20, 31, 0),
        conversation_history=tuple(history),
        resumed=True,
    )

    def fake_branch_session(**kwargs):
        observed.append(kwargs)
        return BranchSessionResult(
            state=state,
            parent_session_id="current-id",
            title="Mixed Case",
            copied_message_count=1,
        )

    monkeypatch.setenv("VOIDCUBE_SESSION_SOURCE", "integration-test")
    repository = object()
    runtime = ApplicationRuntime.create(
        session_id="current-id",
        session_start=state.session_start,
        conversation_history=history,
    )
    monkeypatch.setattr(runtime, "branch_session", fake_branch_session)
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._application_runtime = runtime
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._session_db = repository
    app.session_id = "current-id"
    app.conversation_history = history
    app.model = "active-model"
    app.max_turns = 7
    app.reasoning_config = {"effort": "medium"}
    app._apply_session_lifecycle_state = applied.append
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/branch Mixed Case") is True
    assert len(observed) == 1
    call = observed[0]
    assert call["repository"] is repository
    assert call["requested_title"] == "Mixed Case"
    assert call["source"] == "integration-test"
    assert call["model"] == "active-model"
    assert call["model_config"] == {
        "max_iterations": 7,
        "reasoning_config": {"effort": "medium"},
    }
    assert isinstance(call["started_at"], datetime)
    assert applied == [state]
    assert output == [
        '  ⑂ Branched session "Mixed Case" (1 user message)',
        "  Original session: current-id",
        "  Branch session:   branch-id",
    ]


def test_cli_process_routes_new_through_shared_session_transition(
    monkeypatch,
) -> None:
    events: list[object] = []
    observed: list[dict[str, object]] = []
    state = SessionLifecycleState(
        session_id="new-id",
        session_start=datetime(2026, 7, 29, 20, 32, 0),
        conversation_history=(),
        resumed=False,
    )

    def fake_start_new_session(**kwargs):
        observed.append(kwargs)
        events.append("start")
        return state

    monkeypatch.setattr(
        command_handler_registry,
        "_notify_session_boundary",
        lambda _host, event: events.append(("hook", event)),
    )
    monkeypatch.setenv("VOIDCUBE_SESSION_SOURCE", "integration-test")
    repository = object()
    agent = object()
    runtime = ApplicationRuntime.create(
        session_id="current-id",
        session_start=state.session_start,
    )
    monkeypatch.setattr(runtime, "start_new_session", fake_start_new_session)
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._application_runtime = runtime
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._session_db = repository
    app.session_id = "current-id"
    app.model = "active-model"
    app.max_turns = 9
    app.reasoning_config = {"effort": "high"}
    app.agent = agent
    app._current_trace_id = "old-trace"
    app._apply_session_lifecycle_state = lambda value: events.append(
        ("apply", value)
    )
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/new") is True
    assert app._current_trace_id == ""
    assert events == [
        ("hook", "on_session_finalize"),
        "start",
        ("apply", state),
        ("hook", "on_session_reset"),
    ]
    assert len(observed) == 1
    call = observed[0]
    assert call["repository"] is repository
    assert call["source"] == "integration-test"
    assert call["model"] == "active-model"
    assert call["model_config"] == {
        "max_iterations": 9,
        "reasoning_config": {"effort": "high"},
    }
    assert call["create_record"] is True
    assert isinstance(call["started_at"], datetime)


def test_cli_process_routes_clear_transition_before_tui_display(monkeypatch) -> None:
    events: list[object] = []
    output: list[str] = []
    state = SessionLifecycleState(
        session_id="new-id",
        session_start=datetime(2026, 7, 29, 20, 33, 0),
        conversation_history=(),
        resumed=False,
    )

    runtime = ApplicationRuntime.create(
        session_id="current-id",
        session_start=state.session_start,
    )
    monkeypatch.setattr(
        runtime,
        "start_new_session",
        lambda **_kwargs: events.append("start") or state,
    )
    terminal_output = SimpleNamespace(
        erase_screen=lambda: events.append("erase"),
        cursor_goto=lambda x, y: events.append(("cursor", x, y)),
        flush=lambda: events.append("flush"),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._application_runtime = runtime
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._session_db = object()
    app.session_id = "current-id"
    app.model = "active-model"
    app.max_turns = 9
    app.reasoning_config = {}
    app.agent = None
    app._current_trace_id = "old-trace"
    app._app = SimpleNamespace(output=terminal_output)
    app.compact = True
    app.enabled_toolsets = []
    app.conversation_history = [{"role": "user"}]
    app.show_banner = lambda: pytest.fail("TUI clear must not show standalone banner")
    app._apply_session_lifecycle_state = lambda value: events.append(
        ("apply", value)
    )
    console = SimpleNamespace(print=lambda value: events.append(("banner", value)))
    install_cli_command_execution(
        app,
        emit=output.append,
        chat_console_factory=lambda: console,
        compact_banner_factory=lambda: "compact-banner",
    )

    assert app.process_command("/clear") is True
    assert app._current_trace_id == ""
    assert events == [
        "start",
        ("apply", state),
        "erase",
        ("cursor", 0, 0),
        "flush",
        ("banner", "compact-banner"),
    ]
    assert output == [
        "  ✨ (◕‿◕)✨ Fresh start! Screen cleared and conversation reset.\n"
    ]


def test_cli_process_projects_plugin_manager_dict_values(monkeypatch, capsys) -> None:
    records = [
        {
                "name": "example",
                "enabled": True,
                "version": "1.0",
                "tools": 1,
                "hooks": 0,
                "error": "",
        }
    ]
    monkeypatch.setattr(command_handler_registry, "_discover_plugins", lambda: None)
    monkeypatch.setattr(command_handler_registry, "_list_plugin_records", lambda: records)
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/plugins") is True

    assert capsys.readouterr().out.splitlines() == [
        "Plugins (1):",
        "  ✓ example v1.0 (1 tools)",
    ]


def test_cli_process_routes_image_arguments_to_host_attachment_state(
    monkeypatch,
) -> None:
    output: list[str] = []
    image = Path("Mixed Image.PNG")
    monkeypatch.setattr(command_handler_registry, "_is_termux", lambda: False)
    monkeypatch.setattr(
        command_handler_registry,
        "_resolve_attachment_path",
        lambda value: image if value == "Mixed Image.PNG" else None,
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app._attached_images = []
    app._try_attach_clipboard_image = lambda: False
    install_cli_command_execution(app, emit=output.append)

    assert (
        app.process_command('/image "Mixed Image.PNG" Describe CamelCase')
        is True
    )

    assert app._attached_images == [image]
    assert output == [
        "  📎 Attached image: Mixed Image.PNG",
        (
            "  \033[2mNow type your prompt (or use --image in single-query mode): "
            "Describe CamelCase\033[0m"
        ),
    ]


def test_cli_approval_choices_only_expose_implemented_decisions() -> None:
    app = VoidcubeCLI.__new__(VoidcubeCLI)

    assert app._approval_choices("echo short") == [
        ApprovalStatus.APPROVED.value,
        ApprovalStatus.DENIED.value,
    ]
    assert app._approval_choices("x" * 71) == [
        ApprovalStatus.APPROVED.value,
        ApprovalStatus.DENIED.value,
        "view",
    ]


def test_cli_applies_shared_session_state_through_public_agent_port() -> None:
    calls: list[tuple[str, datetime]] = []
    agent = SimpleNamespace(
        activate_session=lambda session_id, *, session_start: calls.append(
            (session_id, session_start)
        )
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app.agent = agent
    app._session_hydration = SessionHydration(
        session_id="old",
        status=SessionHydrationStatus.EMPTY,
    )
    started_at = datetime(2026, 7, 29, 20, 4, 0)
    state = SessionLifecycleState(
        session_id="target",
        session_start=started_at,
        conversation_history=({"role": "user", "content": "hello"},),
        resumed=True,
        pending_title=None,
    )

    app._apply_session_lifecycle_state(state)

    assert app.session_id == "target"
    assert app.session_start == started_at
    assert app.conversation_history == [{"role": "user", "content": "hello"}]
    assert app._pending_title is None
    assert app._application_runtime.state.resumed is True
    assert app._session_hydration is None
    assert calls == [("target", started_at)]


def test_cli_reuses_one_shared_hydration_result(monkeypatch) -> None:
    calls: list[tuple[object, str]] = []
    hydration = SessionHydration(
        session_id="target",
        status=SessionHydrationStatus.READY,
        metadata={"id": "target"},
        conversation_history=({"role": "user", "content": "hello"},),
    )

    def fake_hydrate_session(*, repository, session_id):
        calls.append((repository, session_id))
        return hydration

    repository = object()
    runtime = ApplicationRuntime.create(
        session_id="target",
        session_start=datetime(2026, 7, 29, 20, 4, 0),
    )
    monkeypatch.setattr(runtime, "hydrate_session", fake_hydrate_session)
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._application_runtime = runtime
    app._session_db = repository
    app.session_id = "target"
    app.conversation_history = []
    app._session_hydration = None

    first, first_loaded_now = app._hydrate_resumed_session()
    second, second_loaded_now = app._hydrate_resumed_session()

    assert first is second is hydration
    assert first_loaded_now is True
    assert second_loaded_now is False
    assert calls == [(repository, "target")]
    assert app.conversation_history == [{"role": "user", "content": "hello"}]


def test_cli_history_mutation_updates_agent_cursor_and_hydration() -> None:
    repository = SimpleNamespace(truncate_last_user_turn=lambda _session_id: 2)
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._session_db = repository
    app.session_id = "active"
    app.conversation_history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two"},
    ]
    json_history: list[list[dict]] = []
    app.agent = SimpleNamespace(
        replace_persisted_session_history=lambda history: json_history.append(
            list(history)
        ),
    )
    app._session_hydration = SessionHydration(
        session_id="active",
        status=SessionHydrationStatus.READY,
        metadata={"id": "active", "title": "Work"},
        conversation_history=tuple(app.conversation_history),
    )

    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    output: list[str] = []
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/undo") is True

    assert app.conversation_history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
    ]
    assert json_history == [app.conversation_history]
    assert app._session_hydration.metadata["title"] == "Work"
    assert app._session_hydration.conversation_history == tuple(app.conversation_history)
    assert output == [
        '(^_^)b Undid 2 message(s). Removed: "second"',
        "  2 message(s) remaining in history.",
    ]


def test_cli_process_routes_rollback_through_registry_then_shared_undo(
    monkeypatch,
) -> None:
    output: list[str] = []
    restores: list[tuple[str, str, str | None]] = []
    persisted_history: list[list[dict[str, object]]] = []
    manager = SimpleNamespace(
        enabled=True,
        list_checkpoints=lambda _directory: [{"hash": "checkpoint-one"}],
        restore=lambda directory, target, *, file_path=None: restores.append(
            (directory, target, file_path)
        )
        or {
            "success": True,
            "restored_to": "checkpoi",
            "reason": "before edit",
        },
    )
    agent = SimpleNamespace(
        _checkpoint_mgr=manager,
        replace_persisted_session_history=lambda history: persisted_history.append(
            list(history)
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.agent = agent
    app._session_db = None
    app.session_id = "active"
    app._session_hydration = None
    app.conversation_history = [
        {"role": "user", "content": "make change"},
        {"role": "assistant", "content": "done"},
    ]
    monkeypatch.setenv("TERMINAL_CWD", "rollback-workspace")
    install_cli_command_execution(
        app,
        emit=output.append,
        translate=lambda key, default=None, **_kwargs: default or key,
    )

    assert app.process_command("/rollback 1") is True
    assert restores == [("rollback-workspace", "checkpoint-one", None)]
    assert app.conversation_history == []
    assert persisted_history == [[]]
    assert output == [
        "  ✅ prompts.rollback_restored",
        "  prompts.rollback_snapshot_saved",
        '(^_^)b Undid 2 message(s). Removed: "make change"',
        "  0 message(s) remaining in history.",
        "  prompts.rollback_chat_undone",
    ]


def test_cli_process_routes_config_through_display_handler(monkeypatch, tmp_path) -> None:
    output: list[str] = []
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        command_handler_registry,
        "_config_display_ports",
        lambda host, *, emit, translate: ConfigDisplayPorts(
            model=lambda: host.model,
            base_url=lambda: host.base_url,
            api_key=lambda: host.api_key,
            terminal_environment=lambda: "local",
            terminal_working_directory=lambda: "workspace",
            terminal_timeout=lambda: "60",
            ssh_target=lambda: ("", "", ""),
            max_turns=lambda: host.max_turns,
            enabled_toolsets=lambda: host.enabled_toolsets,
            verbose=lambda: host.verbose,
            session_start=lambda: host.session_start,
            config_path=lambda: config_path,
            translate=translate,
            emit=emit,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.model = "active-model"
    app.base_url = "https://api.example/v1"
    app.api_key = "secret-1234"
    app.max_turns = 8
    app.enabled_toolsets = ["web"]
    app.verbose = False
    app.session_start = datetime(2026, 7, 30, 10, 0, 0)
    install_cli_command_execution(
        app,
        emit=output.append,
        translate=lambda key, default=None, **_kwargs: default or key,
    )

    assert app.process_command("/config ignored") is True
    assert output[5:9] == [
        "model",
        "  Model:     active-model",
        "  Base URL:  https://api.example/v1",
        "  API Key:   ********1234",
    ]


def test_cli_process_routes_status_through_display_handler(monkeypatch) -> None:
    output: list[str] = []
    monkeypatch.setattr(
        command_handler_registry,
        "_session_status_display_ports",
        lambda _host: SessionStatusDisplayPorts(
            session_metadata=lambda: {},
            session_id=lambda: "session-1",
            session_start=lambda: datetime(2026, 7, 30, 10, 0, 0),
            home_path=lambda: "home",
            model=lambda: "active-model",
            provider=lambda: "active-provider",
            total_tokens=lambda: 5,
            agent_running=lambda: False,
            subagent_snapshot=lambda: {"active": False},
            autonomous_sections=lambda: (),
            emit=output.append,
        ),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    install_cli_command_execution(app, emit=lambda _text: None)

    assert app.process_command("/status") is True
    assert output == [
        "\n".join(
            [
                "Voidcube CLI Status",
                "",
                "Session ID: session-1",
                "Path: home",
                "Model: active-model (active-provider)",
                "Created: 2026-07-30 10:00",
                "Last Activity: 2026-07-30 10:00",
                "Tokens: 5",
                "Agent Running: No",
                "Subagents: idle",
            ]
        )
    ]


def test_cli_process_status_argument_saves_startup_history_limit(monkeypatch) -> None:
    from voidcube.infrastructure.config import configuration as config_module

    saved: list[tuple[str, object]] = []
    monkeypatch.setattr(
        config_module,
        "save_config_value",
        lambda key, value: saved.append((key, value)) or True,
    )
    output: list[str] = []
    app = VoidcubeCLI.__new__(VoidcubeCLI)
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    app.startup_history_limit = 4
    app._session_status_display_ports = None
    app._session_db = None
    app._get_subagent_observability_snapshot = lambda: {"active": False}
    app.console = SimpleNamespace(
        print=lambda text, **_kwargs: output.append(text),
    )
    app.session_id = "session-1"
    app.session_start = datetime(2026, 7, 30, 10, 0, 0)
    install_cli_command_execution(app, emit=output.append)

    assert app.process_command("/status 8") is True
    assert app.startup_history_limit == 8
    assert saved == [("display.startup_history_limit", 8)]
    assert output == [
        "  Startup history display count set to 8 (saved to config)"
    ]
