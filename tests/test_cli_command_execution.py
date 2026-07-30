from __future__ import annotations

import queue
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli as cli_module
from cli import VoidcubeCLI
import VoidCube_cli.command_handlers.registry as command_handler_registry
from VoidCube_cli.command_execution import (
    BUILTIN_COMMAND_SPECS,
    CommandBusyLifecycle,
    initialize_command_execution,
)
from VoidCube_cli.command_router import parse_cli_command
from VoidCube_cli.command_handlers.registry import install_cli_command_execution
from VoidCube_cli.command_handlers.display import (
    ConfigDisplayPorts,
    HelpDisplayPorts,
    HelpDisplayText,
    MemoryDisplayPorts,
    ProviderDisplayPorts,
    ProviderDisplaySnapshot,
    SessionStatusDisplayPorts,
)
from VoidCube_cli.command_handlers.info import UsageCommandPorts, UsageDisplaySnapshot
from VoidCube_cli.command_handlers.operations import ApiCommandPorts, DoctorCommandPorts
from VoidCube_cli.command_handlers.personality import PersonalityCommandPorts
from VoidCube_cli.commands import COMMAND_REGISTRY
from VoidCube_app.session_lifecycle import (
    BranchSessionResult,
    ResumeSessionResult,
    SessionHydration,
    SessionHydrationStatus,
    SessionLifecycleState,
)
from VoidCube_app.interaction_contract import ApprovalStatus
from VoidCube_app.turn_queue import interrupt_text
from VoidCube_cli.turn_queue_adapter import requeue_interrupted_inputs


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


EXPECTED_BUILTINS = {
    "api",
    "auto",
    "auto-q",
    "background",
    "branch",
    "browser",
    "btw",
    "clear",
    "compress",
    "config",
    "connect",
    "debug",
    "doctor",
    "fast",
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


def test_interrupted_text_payloads_are_combined_for_the_next_turn() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    interrupts.put("second")

    batch = requeue_interrupted_inputs(
        pending,
        interrupts,
        "first",
    )

    assert batch.payloads == ("first", "second")
    assert pending.get_nowait() == "first\nsecond"


def test_interrupted_multimodal_payload_keeps_attachments_and_order() -> None:
    pending = queue.Queue()
    interrupts = queue.Queue()
    first = ("inspect this", ["screen.png"])
    interrupts.put("then summarize")

    batch = requeue_interrupted_inputs(
        pending,
        interrupts,
        first,
    )

    assert interrupt_text(first) == "inspect this"
    assert batch.payloads == (first, "then summarize")
    assert pending.get_nowait() == first
    assert pending.get_nowait() == "then summarize"


def test_builtin_table_is_complete_and_contains_no_removed_commands() -> None:
    assert set(BUILTIN_COMMAND_SPECS) == EXPECTED_BUILTINS
    assert "cron" not in BUILTIN_COMMAND_SPECS
    assert "insights" not in BUILTIN_COMMAND_SPECS
    for spec in BUILTIN_COMMAND_SPECS.values():
        if spec.exits:
            continue
        if spec.handler_key:
            assert not spec.handler_name
        else:
            assert hasattr(VoidcubeCLI, spec.handler_name), spec.handler_name


def test_retired_cron_integration_has_no_active_runtime_or_config_surface() -> None:
    active_surfaces = (
        "config.yaml",
        "agent/display.py",
        "agent/memory_provider.py",
        "agent/prompt_builder.py",
        "VoidCube_cli/config.py",
        "VoidCube_cli/main.py",
        "VoidCube_cli/status.py",
        "VoidCube_cli/locales/en_US.json",
        "VoidCube_cli/locales/zh_CN.json",
        "VoidCube_core/logging.py",
    )

    for path in active_surfaces:
        source = Path(path).read_text(encoding="utf-8").casefold()
        assert "cron" not in source, path


def test_every_discoverable_cli_builtin_has_an_execution_spec() -> None:
    discoverable = {
        command.name for command in COMMAND_REGISTRY if not command.gateway_only
    }

    assert discoverable <= set(BUILTIN_COMMAND_SPECS)


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


def test_executor_passes_original_command_only_when_declared() -> None:
    calls: list[tuple[str, str | None]] = []
    host = SimpleNamespace(_command_running=False, _command_status="")
    host._invalidate = lambda **kwargs: None
    host._handle_tools_command = lambda command: calls.append(("tools", command))
    initialize_command_execution(
        host,
        command_handlers={"help": lambda _request: calls.append(("help", None))},
    )

    host._builtin_command_executor.execute(
        parse_cli_command("/tools Enable MixedCase")
    )
    host._builtin_command_executor.execute(parse_cli_command("/help"))

    assert calls == [("tools", "/tools Enable MixedCase"), ("help", None)]


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
    from VoidCube_app import config as config_module

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
    from VoidCube_app import config as config_module

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
    from VoidCube_app import config as config_module

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

    monkeypatch.setattr("VoidCube_cli.api_config.run_api_config_wizard", fake_wizard)

    command_handler_registry._api_command_ports(host).run_wizard()

    assert len(observed) == 1
    assert host == SimpleNamespace(
        model="new-model",
        provider="new-provider",
        requested_provider="new-requested-provider",
    )


def test_verbose_toggle_applies_logging_levels_without_usage_command(monkeypatch) -> None:
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
        app._toggle_verbose()

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


def test_tools_catalog_command_uses_the_shared_host_renderer(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "render_tools_for_host",
        lambda host, *, emit, translate: calls.append((host, emit, translate)),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)

    app._handle_tools_command("/tools")

    assert calls == [(app, print, cli_module.t)]


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
    host._handle_skills_command = lambda command: observed.append(
        (host._command_running, host._command_status, command)
    )
    initialize_command_execution(host)

    result = host._builtin_command_executor.execute(
        parse_cli_command("/skills search MixedCase")
    )

    assert result.handled is True
    assert observed == [(True, "Searching skills...", "/skills search MixedCase")]
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

    monkeypatch.setattr(command_handler_registry, "resume_session", fake_resume_session)
    repository = object()
    app = VoidcubeCLI.__new__(VoidcubeCLI)
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
            "current_session_id": "current-id",
            "target_session_id": "target-id",
            "session_start": started_at,
        }
    ]
    assert applied == [state]
    assert app._session_hydration.session_id == "target-id"
    assert output == [
        '  ↻ Resumed session target-id "Saved" — no messages, starting fresh.'
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

    monkeypatch.setattr(command_handler_registry, "branch_session", fake_branch_session)
    monkeypatch.setenv("VOIDCUBE_SESSION_SOURCE", "integration-test")
    repository = object()
    app = VoidcubeCLI.__new__(VoidcubeCLI)
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
    assert call["current_session_id"] == "current-id"
    assert call["conversation_history"] is history
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
        "start_new_session",
        fake_start_new_session,
    )
    monkeypatch.setattr(
        command_handler_registry,
        "_notify_session_boundary",
        lambda _host, event: events.append(("hook", event)),
    )
    monkeypatch.setenv("VOIDCUBE_SESSION_SOURCE", "integration-test")
    repository = object()
    agent = object()
    app = VoidcubeCLI.__new__(VoidcubeCLI)
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
    assert call["current_session_id"] == "current-id"
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

    monkeypatch.setattr(
        command_handler_registry,
        "start_new_session",
        lambda **_kwargs: events.append("start") or state,
    )
    terminal_output = SimpleNamespace(
        erase_screen=lambda: events.append("erase"),
        cursor_goto=lambda x, y: events.append(("cursor", x, y)),
        flush=lambda: events.append("flush"),
    )
    app = VoidcubeCLI.__new__(VoidcubeCLI)
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
    manager = SimpleNamespace(
        list_plugins=lambda: {
            "plugin-id": {
                "name": "example",
                "enabled": True,
                "version": "1.0",
                "tools": 1,
                "hooks": 0,
                "error": "",
            }
        }
    )
    monkeypatch.setattr(command_handler_registry, "_discover_plugins", lambda: None)
    monkeypatch.setattr(
        "VoidCube_cli.plugins.get_plugin_manager",
        lambda: manager,
    )
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
    assert app._resumed is True
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

    monkeypatch.setattr(cli_module, "hydrate_session", fake_hydrate_session)
    repository = object()
    app = VoidcubeCLI.__new__(VoidcubeCLI)
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
    calls: list[int] = []
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
        mark_session_history_persisted=calls.append,
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
    assert calls == [2]
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
    persisted_counts: list[int] = []
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
        mark_session_history_persisted=persisted_counts.append,
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
    assert persisted_counts == [0]
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
