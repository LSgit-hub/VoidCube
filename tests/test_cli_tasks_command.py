from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from VoidCube_cli.commands import COMMANDS_BY_CATEGORY, resolve_command
from VoidCube_cli.command_execution import initialize_command_execution
from agent.subagent_display import SubagentStatus


class _FakeConsole:
    def __init__(self, sink: list[str]):
        self._sink = sink

    def print(self, value, *args, **kwargs):
        del args, kwargs
        self._sink.append(str(value))


class _FakeThread:
    def __init__(self, name: str, alive: bool = True):
        self.name = name
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_tasks_command_is_registered():
    cmd = resolve_command("tasks")
    assert cmd is not None
    assert cmd.name == "tasks"
    assert cmd.args_hint == ""
    assert cmd.defer_subcommands_until_prefix is True


def test_tasks_command_help_entry_hides_manual_debug_usage():
    session_commands = COMMANDS_BY_CATEGORY.get("会话管理", {})
    description = session_commands.get("/tasks", "")

    assert "高级调试" in description
    assert "/tasks bg" not in description
    assert "/tasks fg" not in description


def test_handle_tasks_command_prefers_active_subagent_display(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(cli, "ChatConsole", lambda: _FakeConsole(rendered))

    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app.agent = SimpleNamespace(
        _subagent_display_manager=SimpleNamespace(
            render_tasks_command=lambda: "Subagent Panel\n  task-1"
        )
    )
    app._background_tasks = {}
    app._background_task_info = {}

    app._handle_tasks_command()

    assert any("Subagent Panel" in line for line in rendered)


def test_handle_tasks_command_renders_all_active_subagent_displays(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(cli, "ChatConsole", lambda: _FakeConsole(rendered))

    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app.agent = SimpleNamespace(
        _subagent_display_managers={
            "a": SimpleNamespace(render_tasks_command=lambda: "Panel A"),
            "b": SimpleNamespace(render_tasks_command=lambda: "Panel B"),
        },
        _subagent_display_manager=None,
    )
    app._background_tasks = {}
    app._background_task_info = {}

    app._handle_tasks_command()

    assert any("Panel A" in line and "Panel B" in line for line in rendered)


def test_handle_tasks_command_falls_back_to_background_summary(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(cli, "ChatConsole", lambda: _FakeConsole(rendered))
    monkeypatch.setattr(cli.time, "time", lambda: 200.0)

    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app.agent = None
    app._background_tasks = {
        "bg_task_1": _FakeThread(name="bg-task-bg_task_1", alive=True),
    }
    app._background_task_info = {
        "bg_task_1": {
            "task_num": 3,
            "prompt_preview": "Summarize the repo",
            "started_at": 180.0,
        }
    }

    app._handle_tasks_command()

    assert any("CLI Background Tasks" in line for line in rendered)
    assert any("Summarize the repo" in line for line in rendered)


def test_process_command_routes_tasks(monkeypatch):
    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app._autonomous_gate_active = False
    app._command_running = False
    app._command_status = ""
    app._invalidate = lambda **kwargs: None
    initialize_command_execution(app)

    called = {"tasks": 0}
    app._handle_tasks_command = lambda cmd="/tasks": called.__setitem__("tasks", called["tasks"] + 1)

    keep_running = app.process_command("/tasks")

    assert keep_running is True
    assert called["tasks"] == 1


def test_background_task_timeout_interrupts_agent_and_reports_failure(monkeypatch):
    interrupted = threading.Event()
    completed = threading.Event()
    callback_result = []

    class _FakeAgent:
        def __init__(self, **kwargs):
            self.request_overrides = kwargs["request_overrides"]
            self.persist_session = kwargs["persist_session"]
            self._print_fn = None
            self.thinking_callback = None

        def interrupt(self, message=None):
            self.interrupt_message = message
            interrupted.set()

        def run_conversation(self, **kwargs):
            assert kwargs["task_id"] == "scheduled-test"
            assert interrupted.wait(timeout=2)
            return {"error": "interrupted"}

    fake_agent = _FakeAgent.__new__(_FakeAgent)
    monkeypatch.setattr(
        cli,
        "_get_AIAgent",
        lambda: lambda **kwargs: _capture_agent(fake_agent, kwargs),
    )
    monkeypatch.setattr(cli, "_cprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "ChatConsole", lambda: _FakeConsole([]))

    app = _background_test_app()
    app._resolve_turn_agent_config = lambda _prompt: {
        "model": "safe-model",
        "runtime": {
            "api_key": "test-key",
            "base_url": "https://api.example/v1",
            "provider": "custom",
            "command": None,
            "args": [],
        },
        "request_overrides": {"temperature": 0.1},
    }

    def on_complete(success, response, error):
        callback_result.append((success, response, error))
        completed.set()

    assert app._start_background_agent_task(
        "run scheduled work",
        task_id="scheduled-test",
        request_timeout_seconds=3,
        timeout_seconds=0.05,
        persist_session=False,
        on_complete=on_complete,
    )
    assert completed.wait(timeout=2)

    assert fake_agent.request_overrides == {"temperature": 0.1, "timeout": 3.0}
    assert fake_agent.persist_session is False
    assert "timed out after 0.1 seconds" in fake_agent.interrupt_message
    assert callback_result == [
        (False, "", "API-A background execution timed out after 0.1 seconds")
    ]


def test_background_task_timeout_includes_agent_initialization(monkeypatch):
    completed = threading.Event()
    callback_result = []

    class _SlowAgent:
        def __init__(self, **_kwargs):
            threading.Event().wait(0.15)

        def interrupt(self, _message=None):
            raise AssertionError("conversation did not start, so no interrupt is needed")

        def run_conversation(self, **_kwargs):
            raise AssertionError("timed-out agent initialization must not start a conversation")

    monkeypatch.setattr(cli, "_get_AIAgent", lambda: _SlowAgent)
    monkeypatch.setattr(cli, "_cprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "ChatConsole", lambda: _FakeConsole([]))

    app = _background_test_app()

    def on_complete(success, response, error):
        callback_result.append((success, response, error))
        completed.set()

    assert app._start_background_agent_task(
        "initialize scheduled work",
        task_id="scheduled-init-timeout",
        timeout_seconds=0.05,
        on_complete=on_complete,
    )
    assert completed.wait(timeout=2)
    assert callback_result == [
        (False, "", "API-A background execution timed out after 0.1 seconds")
    ]


def _capture_agent(agent, kwargs):
    agent.__init__(**kwargs)
    return agent


def _background_test_app():
    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app._background_task_counter = 0
    app._background_tasks = {}
    app._background_task_info = {}
    app._ensure_runtime_credentials = lambda: True
    app._resolve_turn_agent_config = lambda _prompt: {
        "model": "safe-model",
        "runtime": {
            "api_key": "test-key",
            "base_url": "https://api.example/v1",
            "provider": "custom",
            "command": None,
            "args": [],
        },
        "request_overrides": {},
    }
    app.max_turns = 2
    app.enabled_toolsets = []
    app._session_db = None
    app.reasoning_config = None
    app.service_tier = None
    app._providers_only = []
    app._providers_ignore = []
    app._providers_order = []
    app._provider_sort = None
    app._provider_require_params = False
    app._provider_data_collection = None
    app._fallback_model = None
    app._agent_running = False
    app._app = None
    app.bell_on_complete = False
    app._spinner_text = ""
    app._invalidate = lambda **_kwargs: None
    return app


def test_handle_tasks_command_can_send_subagent_to_background(monkeypatch):
    printed: list[str] = []
    monkeypatch.setattr(cli, "_cprint", lambda text: printed.append(str(text)))

    manager = SimpleNamespace(
        resolve_task_ref=lambda ref: SimpleNamespace(task_id="delegate-1"),
        send_to_background=lambda task_id: task_id == "delegate-1",
    )

    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app.agent = SimpleNamespace(_subagent_display_manager=manager)
    app._app = None

    app._handle_tasks_command("/tasks bg 1")

    assert printed == []


def test_handle_tasks_command_can_bring_subagent_to_foreground(monkeypatch):
    printed: list[str] = []
    monkeypatch.setattr(cli, "_cprint", lambda text: printed.append(str(text)))

    manager = SimpleNamespace(
        resolve_task_ref=lambda ref: SimpleNamespace(task_id="delegate-2"),
        bring_to_foreground=lambda task_id: task_id == "delegate-2",
    )

    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app.agent = SimpleNamespace(_subagent_display_manager=manager)
    app._app = None

    app._handle_tasks_command("/tasks fg 2")

    assert printed == []


def test_get_subagent_observability_snapshot_summarizes_active_tasks():
    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app.agent = SimpleNamespace(
        _subagent_display_manager=SimpleNamespace(
            list_tasks=lambda include_background=False: [
                SimpleNamespace(
                    task_id="delegate-1",
                    task_index=0,
                    status=SubagentStatus.TOOL_CALL,
                    current_tool="read_file",
                    current_tool_preview="README.md",
                    current_thinking="",
                    goal_preview="Inspect docs",
                    goal="Inspect docs",
                ),
            ],
            list_background_tasks=lambda: [
                SimpleNamespace(
                    task_id="delegate-2",
                    task_index=1,
                    status=SubagentStatus.RUNNING,
                    current_tool="",
                    current_tool_preview="",
                    current_thinking="Searching code paths",
                    goal_preview="Trace command routing",
                    goal="Trace command routing",
                ),
            ],
        )
    )

    snapshot = app._get_subagent_observability_snapshot()

    assert snapshot["active"] is True
    assert snapshot["foreground_count"] == 1
    assert snapshot["background_count"] == 1
    assert snapshot["counts_label"] == "1+1"
    assert snapshot["focus_task_id"] == "delegate-1"
    assert snapshot["focus_tool"] == "read_file"
    assert snapshot["focus_preview"] == "read_file"


def test_middle_status_fragments_include_subagent_summary():
    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app._use_ascii_fallback_cached = lambda: True
    app._fetch_supervisor_status = lambda: {"scene": "idle", "mem_usage": {}, "error_count": 0}
    app._cached_load_config = lambda: {"memory": {"provider": "mem"}}
    app._get_subagent_observability_snapshot = lambda: {
        "active": True,
        "counts_label": "2+1",
        "compact_preview": "read_file",
    }

    frags = app._get_middle_status_fragments()
    rendered = "".join(text for _, text in frags)

    assert "[SA]" in rendered
    assert "2+1" in rendered
    assert "read_file" in rendered
    assert "辅助" in rendered
    assert "休眠" not in rendered


def test_show_session_status_includes_subagent_summary(monkeypatch):
    rendered: list[str] = []
    app = cli.VoidcubeCLI.__new__(cli.VoidcubeCLI)
    app._session_db = None
    app.session_id = "cli-session-1"
    app.session_start = cli.datetime(2026, 6, 28, 12, 0, 0)
    app.provider = "agnesai"
    app.model = "api-a-model"
    app._agent_running = True
    app.agent = SimpleNamespace(session_total_tokens=1234)
    app.console = _FakeConsole(rendered)
    app._fetch_supervisor_status_snapshot = lambda: {}
    app._fetch_gateway_autonomous_execute_snapshot = lambda: {}
    app._get_subagent_observability_snapshot = lambda: {
        "active": True,
        "foreground_count": 2,
        "background_count": 1,
        "focus_preview": "read_file",
    }

    monkeypatch.setattr(cli, "display_VoidCube_home", lambda: "F:/My_code/Traecode/VoidCube")

    app._show_session_status()

    output = "\n".join(rendered)
    assert "Subagents: 2 foreground, 1 background" in output
    assert "Subagent Focus: read_file" in output
