from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli as cli_module
from cli import VoidcubeCLI
from VoidCube_cli import autonomous_events as autonomous_events_module
from VoidCube_cli import autonomous_executor as autonomous_executor_module
from VoidCube_cli import autonomous_gate as autonomous_gate_module
from VoidCube_cli import autonomous_panel as autonomous_panel_module
from VoidCube_cli import autonomous_presence as autonomous_presence_module
from VoidCube_cli import autonomous_runtime_host as autonomous_runtime_host_module
from VoidCube_cli import autonomous_status_host as autonomous_status_host_module
from VoidCube_cli.autonomous_events import AutonomousPanelEventPorts
from VoidCube_cli.autonomous_panel import (
    AutonomousPanelRenderPorts,
    AutonomousPanelStatePorts,
)
from VoidCube_cli.autonomous_observation import format_supervisor_status_snapshot
from VoidCube_cli.autonomous_status_host import (
    autonomous_observation_summary_sections,
    format_gateway_autonomous_execute_snapshot,
)
from VoidCube_cli.cli_handlers import _git_head_commit, _git_improvement_diff
from VoidCube_cli.chat_render_state import CliStreamRenderState
from VoidCube_cli.chat_stream_renderer import CliStreamRenderer
from VoidCube_cli.command_execution import initialize_command_execution
from VoidCube_cli.tui_layout import build_tui_layout_children
from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState


class _FakeUrlopenResponse:
    def __init__(self, payload: dict | None = None):
        self._payload = payload or {}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _autonomous_runtime(cli: VoidcubeCLI):
    return autonomous_runtime_host_module.autonomous_executor_runtime(
        cli,
        push_cli_agent_scene=cli_module._push_cli_agent_scene,
        git_head_commit=_git_head_commit,
        git_improvement_diff=_git_improvement_diff,
        cprint=cli_module._cprint,
    )


def _panel_state_ports(host) -> AutonomousPanelStatePorts:
    state_host = getattr(host, "_autonomous_execution_host", None) or host

    class _Pending:
        def empty(self):
            return True

    pending_input = getattr(state_host, "_pending_input", _Pending())
    return AutonomousPanelStatePorts(
        gate_active=lambda: bool(getattr(host, "_autonomous_gate_active", False)),
        session_id=lambda: str(getattr(state_host, "session_id", "") or ""),
        current_task=lambda: getattr(state_host, "_current_autonomous_task", None),
        current_task_started_at=lambda: float(
            getattr(state_host, "_current_autonomous_task_started_at", 0.0) or 0.0
        ),
        agent_running=lambda: bool(getattr(state_host, "_agent_running", False)),
        last_agent_turn_result=lambda: getattr(state_host, "_last_agent_turn_result", None),
        pending_input_nonempty=lambda: not pending_input.empty(),
        execution_events=lambda: list(
            getattr(state_host, "_autonomous_execution_events", []) or []
        ),
        spinner_text=lambda: str(getattr(state_host, "_spinner_text", "") or ""),
        companion_tasks=lambda: tuple(
            getattr(
                getattr(host, "_scheduled_execution_snapshot", None),
                "active_tasks",
                (),
            )
        ),
    )


def _panel_render_ports(host) -> AutonomousPanelRenderPorts:
    return AutonomousPanelRenderPorts(
        terminal_width=host._get_tui_terminal_width,
        trim_status_bar_text=host._trim_status_bar_text,
        pad_status_bar_text=host._pad_status_bar_text,
    )


def _panel_event_ports(host) -> AutonomousPanelEventPorts:
    state_host = getattr(host, "_autonomous_execution_host", None) or host
    return AutonomousPanelEventPorts(
        gate_active=lambda: bool(getattr(host, "_autonomous_gate_active", False)),
        execution_events=lambda: list(
            getattr(state_host, "_autonomous_execution_events", []) or []
        ),
        set_execution_events=lambda events: setattr(
            state_host,
            "_autonomous_execution_events",
            list(events),
        ),
        trim_status_bar_text=host._trim_status_bar_text,
        last_supervisor_event_key=lambda: str(
            getattr(state_host, "_autonomous_last_supervisor_event_key", "") or ""
        ),
        set_last_supervisor_event_key=lambda value: setattr(
            state_host,
            "_autonomous_last_supervisor_event_key",
            str(value or ""),
        ),
    )


def test_autonomous_execution_panel_is_below_input_without_legacy_gate_bar():
    widgets = {
        name: object()
        for name in (
            "sudo_widget",
            "secret_widget",
            "approval_widget",
            "clarify_widget",
            "model_picker_widget",
            "spinner_widget",
            "spacer",
            "status_bar",
            "auto_execution_panel",
            "input_rule_top",
            "image_bar",
            "input_area",
            "input_rule_bot",
            "voice_status_bar",
            "completions_menu",
        )
    }

    children = build_tui_layout_children(extra_widgets=lambda: [], **widgets)

    assert children.index(widgets["status_bar"]) < children.index(widgets["input_area"])
    assert children.index(widgets["input_area"]) < children.index(
        widgets["auto_execution_panel"]
    )
    assert children.index(widgets["voice_status_bar"]) < children.index(
        widgets["auto_execution_panel"]
    )
    assert "autonomous_gate_bar" not in str(build_tui_layout_children)
    assert not hasattr(VoidcubeCLI, "_build_tui_layout_children")


def test_cli_does_not_rewrite_live_agent_base_url_to_gateway(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.api_key = "runtime-key"
    cli.base_url = "https://runtime-base.example/v1"
    cli.provider = "agnesai"
    cli.acp_command = None
    cli.acp_args = []
    cli.max_turns = 8
    cli.enabled_toolsets = []
    cli.verbose = False
    cli.system_prompt = None
    cli.prefill_messages = []
    cli.reasoning_config = None
    cli.service_tier = None
    cli._providers_only = None
    cli._providers_ignore = None
    cli._providers_order = None
    cli._provider_sort = None
    cli._provider_require_params = None
    cli._provider_data_collection = None
    cli._fallback_model = None
    cli._pending_title = None
    cli._session_db = None
    cli._current_reasoning_callback = lambda: None
    cli._on_thinking = None
    cli.checkpoints_enabled = False
    cli.checkpoint_max_snapshots = 0
    cli.pass_session_id = False
    cli._on_tool_event = None
    cli._inline_diffs_enabled = False
    cli.streaming_enabled = False
    cli._stream_delta = None
    cli._on_tool_gen_start = None
    cli._config_mtime = 0.0
    cli.session_id = "session-cli-direct"
    cli.model = "agnes-2.0-flash"

    class _FakeAgent:
        def __init__(self, **kwargs):
            self.base_url = kwargs["base_url"]
            self.enabled_toolsets = kwargs["enabled_toolsets"]
            self.autonomous_task_provider = kwargs["autonomous_task_provider"]
            self.validate_execution_lease = kwargs["validate_execution_lease"]
            self._print_fn = None

    monkeypatch.setattr("cli._get_AIAgent", lambda: _FakeAgent)
    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda session_id, model, provider: None)

    cli.agent = None
    cli._ensure_runtime_credentials = lambda: True
    cli.conversation_history = []
    cli._clarification_sink = None
    cli._pending_title = None

    ok = cli._init_agent(enabled_toolsets_override=["learn"])

    assert ok is True
    assert cli.agent.base_url == "https://runtime-base.example/v1"
    assert cli.agent.enabled_toolsets == ["learn"]
    assert cli.agent.autonomous_task_provider() is None
    assert callable(cli.agent.validate_execution_lease)


def test_main_cli_does_not_mount_autonomous_execution_panel_in_default_tui_widgets():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)

    assert cli._get_extra_tui_widgets() == []


def test_cli_autonomous_gate_marks_learning_task_failed_after_agent_error(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = {"task_id": "learn-1"}
    cli._current_autonomous_task_started_at = 1.0
    cli._last_agent_turn_result = {
        "failed": True,
        "partial": False,
        "interrupted": False,
        "error": "LLM upstream error: 502",
    }
    cli._agent_running = False

    requests = []

    def fake_time() -> float:
        return 31.0

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requests.append(
                {
                    "url": request.full_url,
                    "data": json.loads((request.data or b"{}").decode("utf-8")),
                }
            )
            return _FakeUrlopenResponse({})
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert requests[0]["url"].endswith("/v1/tasks/learn-1/decision")
    assert requests[0]["data"]["decision"] == "failed"
    assert requests[0]["data"]["context"]["error"] == "LLM upstream error: 502"
    assert cli._current_autonomous_task is None
    assert cli._last_agent_turn_result is None


def test_cli_autonomous_gate_rejects_exploratory_completion_without_web_evidence(
    monkeypatch,
):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = {
        "task_id": "learn-no-web-evidence",
        "task_type": "self_learning",
        "metadata": {"learning_branch": "exploratory"},
    }
    cli._current_autonomous_task_started_at = 1.0
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": "",
        "response": "Unsupported research summary",
    }
    cli._agent_running = False
    cli._autonomous_execution_events = []
    cli.session_id = "cli-owner-no-web"
    requests = []

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requests.append(
                {
                    "url": request.full_url,
                    "data": json.loads((request.data or b"{}").decode("utf-8")),
                }
            )
        return _FakeUrlopenResponse({})

    monkeypatch.setattr("time.time", lambda: 31.0)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    decision = next(
        item
        for item in requests
        if item["url"].endswith("/v1/tasks/learn-no-web-evidence/decision")
    )
    assert decision["data"]["decision"] == "failed"
    assert "web_search" in decision["data"]["context"]["error"]
    assert cli._current_autonomous_task is None


def test_cli_autonomous_gate_keeps_completed_task_when_writeback_fails(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = {"task_id": "learn-writeback-fail", "task_type": "self_learning"}
    cli._current_autonomous_task_started_at = 1.0
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": "",
        "response": "Finished findings",
    }
    cli._agent_running = False
    cli.session_id = "cli-owner-writeback"
    cli._autonomous_execution_events = []

    def fake_time() -> float:
        return 31.0

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            raise OSError("gateway down")
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("cli._cprint", lambda *args, **kwargs: None)

    _autonomous_runtime(cli).poll_workflow()

    assert cli._current_autonomous_task is not None
    assert cli._current_autonomous_task["task_id"] == "learn-writeback-fail"
    assert cli._last_agent_turn_result is not None
    assert any(event.get("stage") == "writeback_failed" for event in cli._autonomous_execution_events)


def test_cli_autonomous_execution_prompt_injection_binds_local_run_id():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    prompts = []
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()
    cli._autonomous_execution_events = []
    task = {
        "task_id": "learn-run-id",
        "title": "Bind autonomous run",
        "summary": "Prompt should map to one run id",
        "task_type": "self_learning",
    }

    ok = _autonomous_runtime(cli).inject_execution_prompt(task, "self_learning")

    assert ok is True
    assert task["_autonomous_task_run_id"]
    assert cli._current_autonomous_task_run_id == task["_autonomous_task_run_id"]
    assert task["_autonomous_execution_start_text"] == prompts[0]
    cli._current_autonomous_task = task
    assert autonomous_executor_module.autonomous_task_run_id_for_message(task, prompts[0]) == task["_autonomous_task_run_id"]
    assert autonomous_executor_module.autonomous_task_run_id_for_message(task, "ordinary background chat") == ""


def test_cli_autonomous_gate_ignores_turn_result_from_different_run(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = {
        "task_id": "learn-current-run",
        "task_type": "self_learning",
        "_autonomous_task_run_id": "run-current",
    }
    cli._current_autonomous_task_started_at = 1.0
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": "",
        "response": "Wrong task response",
        "autonomous_task_run_id": "run-other",
    }
    cli._agent_running = False
    calls = []

    def fake_time() -> float:
        return 31.0

    def fake_urlopen(request, timeout=0):
        calls.append(request)
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert calls == []
    assert cli._current_autonomous_task["task_id"] == "learn-current-run"
    assert cli._last_agent_turn_result["autonomous_task_run_id"] == "run-other"


def test_cli_autonomous_gate_does_not_pull_new_task_while_current_task_is_running(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = {"task_id": "learn-running", "task_type": "self_learning"}
    cli._current_autonomous_task_started_at = 1.0
    cli._last_agent_turn_result = None
    cli._agent_running = True
    calls = []

    def fake_time() -> float:
        return 31.0

    def fake_urlopen(request, timeout=0):
        calls.append(request)
        return _FakeUrlopenResponse({"tasks": [{"task_id": "learn-new"}]})

    monkeypatch.setattr("time.time", fake_time)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert calls == []
    assert cli._current_autonomous_task["task_id"] == "learn-running"


def test_cli_auto_timeout_helper_reports_failed_and_clears_state(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = {
        "task_id": "learn-timeout-live",
        "task_type": "self_learning",
        "_autonomous_task_run_id": "run-timeout",
    }
    cli._current_autonomous_task_started_at = 1.0
    cli._current_autonomous_task_run_id = "run-timeout"
    cli._last_agent_turn_result = None
    cli.session_id = "cli-timeout"
    cli._autonomous_execution_events = []
    requests = []

    def fake_urlopen(request, timeout=0):
        del timeout
        requests.append(
            {
                "url": request.full_url,
                "data": json.loads((request.data or b"{}").decode("utf-8")),
            }
        )
        return _FakeUrlopenResponse({})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("cli._cprint", lambda *args, **kwargs: None)

    timed_out = _autonomous_runtime(cli).report_current_task_timeout_if_needed(now=1802.0)

    assert timed_out is True
    assert requests[0]["url"].endswith("/v1/tasks/learn-timeout-live/decision")
    assert requests[0]["data"]["decision"] == "failed"
    assert requests[0]["data"]["context"]["error"] == "timeout"
    assert requests[0]["data"]["context"]["autonomous_task_run_id"] == "run-timeout"
    assert cli._current_autonomous_task is None
    assert cli._current_autonomous_task_run_id == ""
    assert any(event.get("stage") == "writeback" for event in cli._autonomous_execution_events)


def test_cli_autonomous_gate_pulls_body_improvement_tasks(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._agent_running = False
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()

    prompts = []
    requested_urls = []
    prepared_worktrees = []

    monkeypatch.setattr(
        autonomous_runtime_host_module,
        "prepare_task_git_worktree",
        lambda task_id, path, *, expected_head: prepared_worktrees.append(
            (task_id, path, expected_head)
        ),
    )

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requested_urls.append(request.full_url)
            if request.full_url.endswith("/v1/tasks/body-1/decision"):
                return _FakeUrlopenResponse({
                    "task": {
                        "task_id": "body-1",
                        "title": "改进 shell 替身",
                        "summary": "把学习到的重构应用到 shell 替身",
                        "execution_kind": "body_improvement",
                        "constraints": {
                            "worktree_path": "F:/tmp/worktree",
                            "editable_dirs": ["agent/", "tools/"],
                            "forbidden_patterns": ["systems/**"],
                            "max_files_changed": 3,
                        },
                        "execution_lease": {
                            "generation": 1,
                            "attempt_id": "attempt-body-1",
                            "owner_session_id": cli.session_id,
                            "state": "active",
                        },
                    }
                })
            return _FakeUrlopenResponse({})
        requested_urls.append(str(request))
        url = str(request)
        if "task_type=self_learning" in url:
            return _FakeUrlopenResponse({"tasks": []})
        if "execution_kind=body_improvement" in url:
            return _FakeUrlopenResponse(
                {
                    "tasks": [
                        {
                            "task_id": "body-1",
                            "title": "改进 shell 替身",
                            "summary": "把学习到的重构应用到 shell 替身",
                            "execution_kind": "body_improvement",
                            "constraints": {
                                "worktree_path": "F:/tmp/worktree",
                                "editable_dirs": ["agent/", "tools/"],
                                "forbidden_patterns": ["systems/**"],
                                "max_files_changed": 3,
                            },
                        }
                    ]
                }
            )
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert any("task_type=self_learning" in url for url in requested_urls)
    assert any("execution_kind=body_improvement" in url for url in requested_urls)
    assert any(url.endswith("/admin/activity/touch") for url in requested_urls)
    assert cli._current_autonomous_task is not None
    assert cli._current_autonomous_task["task_id"] == "body-1"
    assert prompts
    assert prompts[0].startswith("[Autonomous Body Improvement Task]")
    assert prepared_worktrees == [
        (cli.session_id, "F:/tmp/worktree", ""),
    ]


def test_cli_autonomous_gate_running_decision_records_owner_session(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._agent_running = False
    cli.session_id = "cli-owner-1"
    prompts = []
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()

    requests = []

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requests.append(
                {
                    "url": request.full_url,
                    "data": json.loads((request.data or b"{}").decode("utf-8")) if request.data else None,
                }
            )
            return _FakeUrlopenResponse({
                "task": {
                    "task_id": "learn-7",
                    "title": "Study one unresolved thread",
                    "summary": "Produce evidence-backed learning notes",
                    "task_type": "self_learning",
                    "execution_lease": {
                        "generation": 1,
                        "attempt_id": "attempt-learn-7",
                        "owner_session_id": "cli-owner-1",
                        "state": "active",
                    },
                }
            })
        url = str(request)
        if "task_type=self_learning" in url:
            return _FakeUrlopenResponse(
                {
                    "tasks": [
                        {
                            "task_id": "learn-7",
                            "title": "Study one unresolved thread",
                            "summary": "Produce evidence-backed learning notes",
                            "task_type": "self_learning",
                        }
                    ]
                }
            )
        if "execution_kind=body_improvement" in url:
            return _FakeUrlopenResponse({"tasks": []})
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    run_request = next(item for item in requests if item["url"].endswith("/v1/tasks/learn-7/decision"))
    assert run_request["data"]["decision"] == "running"
    assert run_request["data"]["context"]["session_id"] == "cli-owner-1"
    assert "owner_session_id" not in run_request["data"]["metadata"]
    assert cli._current_autonomous_task["execution_lease"]["attempt_id"] == "attempt-learn-7"
    assert prompts
    assert "Shell slot baseline:" not in prompts[0]


def test_cli_autonomous_gate_learning_prompt_includes_shell_baseline_when_present(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._agent_running = False
    cli.session_id = "cli-owner-2"
    prompts = []
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            return _FakeUrlopenResponse({
                "task": {
                    "task_id": "learn-shell-1",
                    "title": "Understand the current shell body codebase",
                    "summary": "Inspect the shell body codebase and record its baseline.",
                    "task_type": "self_learning",
                    "metadata": {"learning_branch": "codebase_baseline"},
                    "constraints": {
                        "baseline_slot_id": "slot-B",
                        "baseline_worktree_path": "F:/tmp/shell-worktree",
                    },
                    "execution_lease": {
                        "generation": 1,
                        "attempt_id": "attempt-shell",
                        "owner_session_id": "cli-owner-2",
                        "state": "active",
                    },
                }
            })
        url = str(request)
        if "task_type=self_learning" in url:
            return _FakeUrlopenResponse(
                {
                    "tasks": [
                        {
                            "task_id": "learn-shell-1",
                            "title": "Understand the current shell body codebase",
                            "summary": "Inspect the shell body codebase and record its baseline.",
                            "task_type": "self_learning",
                            "metadata": {
                                "learning_branch": "codebase_baseline",
                            },
                            "constraints": {
                                "baseline_slot_id": "slot-B",
                                "baseline_worktree_path": "F:/tmp/shell-worktree",
                            },
                        }
                    ]
                }
            )
        if "execution_kind=body_improvement" in url:
            return _FakeUrlopenResponse({"tasks": []})
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert prompts
    assert "Learning branch: shell codebase baseline" in prompts[0]
    assert "Shell slot baseline: slot-B" in prompts[0]
    assert "Shell worktree baseline: F:/tmp/shell-worktree" in prompts[0]


def test_cli_autonomous_gate_learning_prompt_shows_exploratory_branch(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._agent_running = False
    cli.session_id = "cli-owner-3"
    prompts = []
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            return _FakeUrlopenResponse({
                "task": {
                    "task_id": "learn-explore-1",
                    "title": "Research: current open-source memory compaction strategies",
                    "summary": "Survey external references and identify promising directions.",
                    "task_type": "self_learning",
                    "metadata": {"learning_branch": "exploratory"},
                    "execution_lease": {
                        "generation": 1,
                        "attempt_id": "attempt-explore",
                        "owner_session_id": "cli-owner-3",
                        "state": "active",
                    },
                }
            })
        url = str(request)
        if "task_type=self_learning" in url:
            return _FakeUrlopenResponse(
                {
                    "tasks": [
                        {
                            "task_id": "learn-explore-1",
                            "title": "Research: current open-source memory compaction strategies",
                            "summary": "Survey external references and identify promising directions.",
                            "task_type": "self_learning",
                            "metadata": {
                                "learning_branch": "exploratory",
                            },
                        }
                    ]
                }
            )
        if "execution_kind=body_improvement" in url:
            return _FakeUrlopenResponse({"tasks": []})
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert prompts
    assert "Learning branch: exploratory" in prompts[0]


def test_cli_autonomous_gate_recovers_owned_running_task_before_completion_writeback(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": "",
    }
    cli._agent_running = False
    cli.session_id = "cli-owner-restore"
    prompts = []
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()

    requests = []

    running_task = {
        "task_id": "learn-restore-1",
        "title": "Recovered autonomous task",
        "summary": "Continue and write back completion",
        "task_type": "self_learning",
        "execution_lease": {
            "generation": 4,
            "attempt_id": "attempt-restore",
            "owner_session_id": "cli-owner-restore",
            "state": "active",
        },
    }

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requests.append(
                {
                    "url": request.full_url,
                    "data": json.loads((request.data or b"{}").decode("utf-8")) if request.data else None,
                }
            )
            return _FakeUrlopenResponse({})
        url = str(request)
        if "status=running" in url:
            return _FakeUrlopenResponse({"tasks": [running_task]})
        if "task_type=self_learning" in url:
            return _FakeUrlopenResponse({"tasks": []})
        if "execution_kind=body_improvement" in url:
            return _FakeUrlopenResponse({"tasks": []})
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    complete_request = next(
        item
        for item in requests
        if item["url"].endswith("/v1/tasks/learn-restore-1/decision")
        and item["data"]["decision"] == "completed"
    )
    assert complete_request["data"]["decision"] == "completed"
    assert complete_request["data"]["execution_lease"]["generation"] == 4
    assert "API-A 自主执行面已完成学习链路项" in complete_request["data"]["reason"]
    assert cli._current_autonomous_task is None
    assert cli._last_agent_turn_result is None


def test_cli_autonomous_gate_replays_recovered_running_task_prompt(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._agent_running = False
    cli.session_id = "cli-owner-replay"
    cli._autonomous_execution_events = []
    prompts = []
    cli._pending_input = type("_Queue", (), {"put": lambda self, prompt: prompts.append(prompt)})()

    running_task = {
        "task_id": "learn-replay-1",
        "title": "Replay recovered autonomous task",
        "summary": "Recovered task must run again after restart",
        "task_type": "self_learning",
        "execution_lease": {
            "generation": 2,
            "attempt_id": "attempt-replay",
            "owner_session_id": "cli-owner-replay",
            "state": "active",
        },
    }

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            return _FakeUrlopenResponse({})
        url = str(request)
        if "status=running" in url:
            return _FakeUrlopenResponse({"tasks": [running_task]})
        return _FakeUrlopenResponse({"tasks": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    _autonomous_runtime(cli).poll_workflow()

    assert cli._current_autonomous_task is not None
    assert cli._current_autonomous_task["task_id"] == "learn-replay-1"
    assert prompts
    assert prompts[0].startswith("[Autonomous Learning Task] Replay recovered autonomous task")
    assert cli._current_autonomous_task["_autonomous_execution_started"] is True


def test_execute_pending_input_runs_agent_turn_and_cleans_runtime(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._agent_running = False
    cli._spinner_text = "working"
    cli._tool_start_time = 12.0
    cli._current_tool_name = "shell"
    cli._last_scrollback_tool = "shell"
    cli._voice_runtime_state = CliVoiceRuntimeState()
    cli._pending_input = type("_Queue", (), {"put": lambda self, payload: None})()

    calls = []

    class _FakeApp:
        def __init__(self):
            self.invalidate_calls = 0

        def invalidate(self):
            self.invalidate_calls += 1

    def fake_execute(request, _token):
        user_input, images = request.prompt
        calls.append({"user_input": user_input, "images": images})
        cli._last_agent_turn_result = {"failed": False}

    monkeypatch.setattr("cli._cprint", lambda *args, **kwargs: None)

    app = _FakeApp()
    cli._execute_agent_turn_request = fake_execute
    cli._scheduler_runtime = lambda: type(
        "_Runtime",
        (),
        {
            "scheduler": type(
                "_Scheduler",
                (),
                {
                    "snapshot": lambda _scheduler: type(
                        "_Snapshot",
                        (),
                        {"autonomous_gate": True},
                    )()
                },
            )(),
            "submit_autonomous": lambda _runtime, _host, payload: (
                fake_execute(
                    type("_Request", (), {"prompt": payload})(),
                    None,
                )
                or True
            ),
            "submit_user": lambda _runtime, _host, payload, on_finished=None: (
                fake_execute(
                    type("_Request", (), {"prompt": payload})(),
                    None,
                )
                or (on_finished() if on_finished is not None else None)
                or True
            ),
        },
    )()

    handled = cli._execute_pending_input("[Autonomous Learning Task] Learn backlog recovery", app=app)

    assert handled is True
    assert calls == [{"user_input": "[Autonomous Learning Task] Learn backlog recovery", "images": None}]
    assert cli._agent_running is False
    assert cli._spinner_text == ""
    assert cli._tool_start_time == 0.0
    assert cli._current_tool_name == ""
    assert cli._last_scrollback_tool == ""
    assert app.invalidate_calls >= 2


def test_auto_q_fast_path_marks_current_task_interrupted(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._current_autonomous_task = {"task_id": "learn-autoq-1", "task_type": "self_learning"}
    cli._current_autonomous_task_started_at = 10.0
    cli._last_agent_turn_result = None
    cli.session_id = "cli-autoq"
    cli._autonomous_execution_events = []

    requests = []

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            payload = json.loads((request.data or b"{}").decode("utf-8")) if request.data else {}
            requests.append({"url": request.full_url, "data": payload})
            if request.full_url.endswith("/autonomous-chain-gate/deactivate"):
                return _FakeUrlopenResponse({"autonomous_chain_gate_active": False})
            return _FakeUrlopenResponse({})
        return _FakeUrlopenResponse({})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("cli._push_cli_agent_scene", lambda *args, **kwargs: True)
    monkeypatch.setattr("cli._cprint", lambda *args, **kwargs: None)
    cli._record_supervisor_ui_activity_safe = lambda *args, **kwargs: None

    assert autonomous_gate_module.exit_autonomous_gate_fast(
        cli,
        event_ports=_panel_event_ports(cli),
        cprint=lambda *args, **kwargs: None,
        interrupt_current_task_callback=_autonomous_runtime(cli).interrupt_current_task,
        push_cli_agent_scene_callback=autonomous_presence_module.push_cli_agent_scene,
    ) is True

    decision_request = next(
        item for item in requests if item["url"].endswith("/v1/tasks/learn-autoq-1/decision")
    )
    assert decision_request["data"]["decision"] == "failed"
    assert decision_request["data"]["context"]["interrupted"] is True
    assert decision_request["data"]["session_id"] == "cli-autoq"
    assert cli._autonomous_gate_active is False
    assert cli._current_autonomous_task is None
    assert cli._last_agent_turn_result is None


def test_auto_q_fast_path_deactivates_supervisor_without_status_probe(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._current_autonomous_task = None
    cli.session_id = "cli-autoq-stop"
    cli._autonomous_execution_events = []

    requests = []
    printed = []
    pushed = []

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requests.append(request.full_url)
            if request.full_url.endswith("/autonomous-chain-gate/deactivate"):
                return _FakeUrlopenResponse({"autonomous_chain_gate_active": False})
        return _FakeUrlopenResponse({})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert autonomous_gate_module.exit_autonomous_gate_fast(
        cli,
        event_ports=_panel_event_ports(cli),
        cprint=lambda *args, **kwargs: printed.append(" ".join(str(arg) for arg in args)),
        interrupt_current_task_callback=lambda **kwargs: True,
        push_cli_agent_scene_callback=lambda *args, **kwargs: pushed.append((args, kwargs)) or True,
    ) is True

    assert cli._autonomous_gate_active is False
    assert len(requests) == 1
    assert requests[0].endswith("/autonomous-chain-gate/deactivate")
    assert any("已停止" in line for line in printed)
    assert pushed


def test_refresh_gateway_cli_presence_registers_session_and_scene(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-keepalive"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._autonomous_gate_active = True
    cli._current_autonomous_task = {
        "task_id": "learn-11",
        "task_type": "self_learning",
    }
    cli._gateway_presence_refresh_interval_seconds = 30.0
    cli._last_gateway_presence_refresh_at = 0.0

    registrations = []
    scenes = []

    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr(
        "cli._register_with_gateway",
        lambda session_id, model, provider: registrations.append((session_id, model, provider)) or True,
    )
    monkeypatch.setattr(
        "cli._push_cli_agent_scene",
        lambda scene, *, session_id=None, task_id=None, execution_kind=None, subagent_summary=None, agent_role=None: scenes.append(
            (scene, session_id, task_id, execution_kind, subagent_summary, agent_role)
        ) or True,
    )
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    autonomous_presence_module.refresh_gateway_cli_presence(
        cli,
        force=True,
        is_gateway_running=cli_module._is_gateway_running,
        register_with_gateway=cli_module._register_with_gateway,
        push_cli_agent_scene=cli_module._push_cli_agent_scene,
        monotonic_time=time.monotonic,
    )

    assert registrations == [("cli-session-keepalive", "api-a-model", "agnesai")]
    assert scenes == [("learning", "cli-session-keepalive", "learn-11", "self_learning", {"active": False, "foreground_count": 0, "background_count": 0, "total_count": 0, "counts_label": "0", "focus_task_id": "", "focus_tool": "", "focus_preview": "", "compact_preview": ""}, "supervisor_task")]
    assert cli._last_gateway_presence_refresh_at == 100.0


def test_refresh_gateway_cli_presence_keeps_user_turn_in_user_chat_lane_during_auto(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-user-turn"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._autonomous_gate_active = True
    cli._current_autonomous_task = {
        "task_id": "learn-22",
        "task_type": "self_learning",
    }
    cli._active_chat_agent_role = "user_chat"
    cli._agent_running = True
    cli._command_running = False
    cli._stream_render_state = CliStreamRenderState()
    cli._gateway_presence_refresh_interval_seconds = 30.0
    cli._last_gateway_presence_refresh_at = 0.0

    scenes = []

    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "cli._push_cli_agent_scene",
        lambda scene, *, session_id=None, task_id=None, execution_kind=None, subagent_summary=None, agent_role=None: scenes.append(
            {
                "scene": scene,
                "session_id": session_id,
                "task_id": task_id,
                "execution_kind": execution_kind,
                "agent_role": agent_role,
                "subagent_summary": subagent_summary,
            }
        ) or True,
    )
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    autonomous_presence_module.refresh_gateway_cli_presence(
        cli,
        force=True,
        is_gateway_running=cli_module._is_gateway_running,
        register_with_gateway=cli_module._register_with_gateway,
        push_cli_agent_scene=cli_module._push_cli_agent_scene,
        monotonic_time=time.monotonic,
    )

    assert scenes == [
        {
            "scene": "executing",
            "session_id": "cli-session-user-turn",
            "task_id": None,
            "execution_kind": None,
            "agent_role": "user_chat",
            "subagent_summary": {
                "active": False,
                "foreground_count": 0,
                "background_count": 0,
                "total_count": 0,
                "counts_label": "0",
                "focus_task_id": "",
                "focus_tool": "",
                "focus_preview": "",
                "compact_preview": "",
            },
        }
    ]


def test_refresh_gateway_cli_presence_respects_refresh_interval(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-keepalive"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._autonomous_gate_active = False
    cli._current_autonomous_task = None
    cli._gateway_presence_refresh_interval_seconds = 30.0
    cli._last_gateway_presence_refresh_at = 90.0

    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not register")))
    monkeypatch.setattr("cli._push_cli_agent_scene", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not push scene")))
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    autonomous_presence_module.refresh_gateway_cli_presence(
        cli,
        force=False,
        is_gateway_running=cli_module._is_gateway_running,
        register_with_gateway=cli_module._register_with_gateway,
        push_cli_agent_scene=cli_module._push_cli_agent_scene,
        monotonic_time=time.monotonic,
    )

    assert cli._last_gateway_presence_refresh_at == 90.0


def test_refresh_gateway_cli_presence_retries_quickly_after_register_failure(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-keepalive"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._autonomous_gate_active = False
    cli._current_autonomous_task = None
    cli._gateway_presence_refresh_interval_seconds = 30.0
    cli._last_gateway_presence_refresh_at = 0.0

    scenes = []

    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "cli._push_cli_agent_scene",
        lambda scene, *, session_id=None, task_id=None, execution_kind=None, subagent_summary=None, agent_role=None: scenes.append(
            (scene, session_id, task_id, execution_kind, subagent_summary)
        ) or True,
    )
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    autonomous_presence_module.refresh_gateway_cli_presence(
        cli,
        force=True,
        is_gateway_running=cli_module._is_gateway_running,
        register_with_gateway=cli_module._register_with_gateway,
        push_cli_agent_scene=cli_module._push_cli_agent_scene,
        monotonic_time=time.monotonic,
    )

    assert scenes == [("idle", "cli-session-keepalive", None, None, {"active": False, "foreground_count": 0, "background_count": 0, "total_count": 0, "counts_label": "0", "focus_task_id": "", "focus_tool": "", "focus_preview": "", "compact_preview": ""})]
    assert cli._last_gateway_presence_refresh_at == 72.0


def test_autonomous_panel_fragments_include_focus_task_and_recent_events(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "20260627_focus1234"
    cli._current_autonomous_task = {
        "task_id": "learn-panel-1",
        "title": "Panel task title",
        "task_type": "self_learning",
    }
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""
    autonomous_events_module.append_autonomous_execution_event(
        event_ports=_panel_event_ports(cli),
        message="已接管任务 learn-panel-1",
        tone="success",
    )
    autonomous_events_module.append_autonomous_execution_event(
        event_ports=_panel_event_ports(cli),
        message="工具启动: web_search",
        tone="info",
    )

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 160))
    cli._supervisor_state_cache = {
        "timeline": [
            {
                "event_type": "task_decided",
                "summary": "API-B handed task off for API-A claim.",
            }
        ],
        "tasks": [],
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "20260627_focus1234",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 4,
            "scene": "learning",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "自主链路迷你 CLI" in rendered
    assert "Panel task title" in rendered
    assert "已接管任务 learn-panel-1" in rendered
    assert "工具启动: web_search" in rendered
    assert "API-B handed task off for API-A claim." in rendered
    assert "执行面:" not in rendered


def test_autonomous_panel_does_not_duplicate_gateway_lease_monitoring(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 160))
    cli._supervisor_state_cache = {"timeline": [], "tasks": []}
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-remote",
            "lease_status": "stale",
            "is_stale": True,
            "idle_seconds": 120,
            "scene": "executing",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "执行面:" not in rendered
    assert "静默 120s" not in rendered


def test_autonomous_panel_does_not_expand_api_b_judgement_reason(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_b_judgement",
                        "items": [
                            {
                                "task_id": "deferred-1",
                                "title": "Deferred task",
                                "status": "deferred",
                                "lane": "supervisor",
                                "task_family": "self_learning",
                                "governance_task_type": "self_learning",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_b_judgement",
                        "title": "API-B 判断",
                        "source_label": "API-B",
                        "status": "active",
                        "display_status": "当前在途",
                        "summary": "API-B 仍在判断下一步动作。",
                    }
                ],
            },
        },
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "executing",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "API-B 判断中" in rendered
    assert "暂无被认领的链路项" in rendered
    assert "仍由 API-B 判断" not in rendered


def test_autonomous_panel_prefers_loop_focus_when_present(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_a_handoff",
                        "items": [
                            {
                                "task_id": "learn-board-1",
                                "title": "Board handoff task",
                                "status": "approved",
                                "task_family": "self_learning",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_a_execution",
                        "title": "Board handoff task",
                        "source_label": "API-A",
                        "status": "ready",
                        "display_status": "API-B 已转交",
                        "status_label": "API-B 已转交",
                        "chain_reason": "链路: API-B 已转交该链路项，可由 API-A 自主执行面接手",
                        "activity_text": "执行流: API-A 认领后执行，结果写回 Mem",
                        "focus_task": {
                            "task_id": "learn-board-1",
                            "title": "Board handoff task",
                            "status": "approved",
                            "task_family": "self_learning",
                            "lane": "agent",
                        },
                    }
                ]
            },
        },
        "tasks": [],
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "idle",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "○ API-B 已转交" in rendered
    assert "Board handoff task" in rendered
    assert "可由 API-A 自主执行面接手" in rendered


def test_autonomous_panel_shows_approved_task_waiting_for_claim(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_a_handoff",
                        "items": [
                            {
                                "task_id": "learn-approved-1",
                                "title": "Handoff waiting task",
                                "task_type": "self_learning",
                                "status": "approved",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_a_execution",
                        "status": "ready",
                        "display_status": "API-B 已转交",
                        "status_label": "API-B 已转交",
                        "chain_reason": "链路: API-B 已转交该链路项，可由 API-A 自主执行面接手",
                        "activity_text": "执行流: API-A 认领后执行，结果写回 Mem",
                        "focus_task": {
                            "task_id": "learn-approved-1",
                            "title": "Handoff waiting task",
                            "task_type": "self_learning",
                            "status": "approved",
                            "lane": "agent",
                        },
                    }
                ]
            },
        },
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "idle",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "○ API-B 已转交" in rendered
    assert "Handoff waiting task" in rendered
    assert "链路: API-B 已转交该链路项，可由 API-A 自主执行面接手" in rendered
    assert "执行流: API-A 认领后执行，结果写回 Mem" in rendered


def test_autonomous_panel_reads_stage_card_projection_without_loop_stage(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_a_handoff",
                        "items": [
                            {
                                "task_id": "learn-approved-stage-card-1",
                                "title": "Stage-card waiting task",
                                "task_type": "self_learning",
                                "status": "approved",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_a_execution",
                        "status": "ready",
                        "status_label": "API-B 已转交",
                        "display_status": "API-B 已转交",
                        "chain_reason": "链路: API-B 已转交该链路项，可由 API-A 自主执行面接手",
                        "activity_text": "执行流: API-A 认领后执行，结果写回 Mem",
                        "reason_style": "warn",
                        "focus_task": {
                            "task_id": "learn-approved-stage-card-1",
                            "title": "Stage-card waiting task",
                            "task_type": "self_learning",
                            "status": "approved",
                            "lane": "agent",
                        },
                    }
                ]
            },
        },
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "idle",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "○ API-B 已转交" in rendered
    assert "Stage-card waiting task" in rendered
    assert "链路: API-B 已转交该链路项，可由 API-A 自主执行面接手" in rendered
    assert "执行流: API-A 认领后执行，结果写回 Mem" in rendered


def test_autonomous_panel_reads_stage_card_projection(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_a_handoff",
                        "items": [
                            {
                                "task_id": "learn-stage-card-wins-1",
                                "title": "Stage-card wins task",
                                "task_type": "self_learning",
                                "status": "approved",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_a_execution",
                        "status": "ready",
                        "status_label": "API-B 已转交",
                        "display_status": "API-B 已转交",
                        "chain_reason": "链路: 以 stage_cards 正式投影为准，可由 API-A 自主执行面接手",
                        "activity_text": "执行流: 该提示来自正式 stage_cards",
                        "focus_task": {
                            "task_id": "learn-stage-card-wins-1",
                            "title": "Stage-card wins task",
                            "task_type": "self_learning",
                            "status": "approved",
                            "lane": "agent",
                        },
                    }
                ],
            },
        },
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "idle",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "Stage-card wins task" in rendered
    assert "○ API-B 已转交" in rendered
    assert "以 stage_cards 正式投影为准" in rendered


def test_autonomous_panel_keeps_api_b_planning_details_out_of_mini_cli(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "scene": "planning",
        "timeline": [],
        "lm_input": {
            "generation_enabled": False,
            "proposal_count": 0,
        },
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_b_candidates",
                        "items": [
                            {
                                "task_id": "candidate-1",
                                "title": "Review endogenous cognition",
                                "status": "candidate",
                                "display_status": "候选形成",
                            }
                        ],
                    }
                ]
            },
            "loop": {"stage_cards": []},
            "runtime": {"api_a_handoff_count": 0, "api_a_running_count": 0},
        },
    }
    cli._autonomous_gateway_status_cache = {}

    assert autonomous_panel_module.has_visible_autonomous_work(
        cli,
        state_ports=_panel_state_ports(cli),
    ) is True
    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "AUTO 模式" in rendered
    assert "LM生成" not in rendered
    assert "候选 1" not in rendered
    assert "Review endogenous cognition" not in rendered


def test_autonomous_panel_keeps_api_b_model_health_in_web_monitor(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "scene": "planning",
        "timeline": [],
        "lm_input": {
            "generation_enabled": True,
            "proposal_count": 0,
        },
        "tier1_stats": {
            "llm_healthy": False,
            "llm_model": "deepseek-v4-flash",
            "llm_error": "HTTPError: HTTP Error 401: Authorization Required",
        },
        "autonomous_observation": {
            "chain": {"segments": []},
            "loop": {"stage_cards": []},
            "runtime": {"api_a_handoff_count": 0, "api_a_running_count": 0},
        },
    }
    cli._autonomous_gateway_status_cache = {}

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "AUTO 模式" in rendered
    assert "模型异常" not in rendered
    assert "HTTPError" not in rendered


def test_autonomous_panel_prefers_loop_stage_descriptor_for_non_local_reasoning(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_a_execution",
                        "status": "ready",
                        "stage": "waiting_api_a_claim",
                        "cli_focus_stage": "waiting_api_a_claim",
                        "status_label": "API-B 已转交",
                        "display_status": "API-B 已转交",
                        "chain_reason": "链路: API-B 已转交该链路项，可由自主执行面接手",
                        "activity_text": "执行流: API-A 自主执行面可开始处理该链路项",
                        "reason_style": "warn",
                        "focus_task": {
                            "task_id": "learn-approved-loop-stage-1",
                            "title": "Loop-stage driven task",
                            "task_type": "self_learning",
                            "status": "approved",
                            "lane": "agent",
                        },
                    }
                ]
            }
        },
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "idle",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "○ API-B 已转交" in rendered
    assert "Loop-stage driven task" in rendered
    assert "链路: API-B 已转交该链路项，可由自主执行面接手" in rendered
    assert "执行流: API-A 自主执行面可开始处理该链路项" in rendered


def test_autonomous_panel_shows_running_task_owned_elsewhere(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = None
    cli._current_autonomous_task_started_at = 0.0
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [],
        "autonomous_observation": {
            "chain": {
                "segments": [
                    {
                        "key": "api_a_handoff",
                        "items": [
                            {
                                "task_id": "learn-running-2",
                                "title": "Running elsewhere task",
                                "task_type": "self_learning",
                                "status": "running",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stage_cards": [
                    {
                        "stage_key": "api_a_execution",
                        "status": "active",
                        "display_status": "他处执行中",
                        "status_label": "他处执行中",
                        "chain_reason": "链路: 该链路项已被其他 API-A 自主执行面认领",
                        "activity_text": "执行流: 链路项正在其他 API-A 自主执行面中运行",
                        "focus_task": {
                            "task_id": "learn-running-2",
                            "title": "Running elsewhere task",
                            "task_type": "self_learning",
                            "status": "running",
                            "lane": "agent",
                        },
                    }
                ]
            },
        },
    }
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-remote",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 6,
            "scene": "learning",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "他处执行中" in rendered
    assert "Running elsewhere task" in rendered
    assert "链路: 该链路项已被其他 API-A 自主执行面认领" in rendered
    assert "执行流: 链路项正在其他 API-A 自主执行面中运行" in rendered


def test_autonomous_panel_shows_claimed_task_waiting_to_start(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = {
        "task_id": "learn-claimed-1",
        "title": "Claimed not started task",
        "task_type": "self_learning",
    }
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {"timeline": [], "tasks": []}
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 1,
            "scene": "learning",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "已认领 · 待起跑" in rendered
    assert "Claimed not started task" in rendered
    assert "已认领，等待进入首个回合" in rendered
    assert "已认领但未收到后续事件" in rendered
    assert "已认领链路项，等待首个回合" in rendered


def test_autonomous_panel_shows_waiting_start_cause_after_autonomous_execution_started(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = {
        "task_id": "learn-claimed-2",
        "title": "Execution prompt injected task",
        "task_type": "self_learning",
    }
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._autonomous_execution_events = [
        {
            "at": "12:00:00",
            "message": "自主执行已起跑，等待模型响应",
            "tone": "info",
            "stage": "autonomous_execution_started",
        }
    ]
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {"timeline": [], "tasks": []}
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 1,
            "scene": "learning",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "已认领 · 待起跑" in rendered
    assert "已起跑，等待首个模型响应" in rendered


def test_autonomous_panel_shows_claimed_task_waiting_for_writeback(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_autonomous_task = {
        "task_id": "learn-writeback-1",
        "title": "Writeback waiting task",
        "task_type": "self_learning",
    }
    cli._current_autonomous_task_started_at = 0.0
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": "",
    }
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {"timeline": [], "tasks": []}
    cli._autonomous_gateway_status_cache = {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 3,
            "scene": "learning",
        }
    }

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli, state_ports=_panel_state_ports(cli), render_ports=_panel_render_ports(cli)))

    assert "等待回写" in rendered
    assert "Writeback waiting task" in rendered
    assert "执行完成，等待结果写回" in rendered
    assert "本轮结束，写回链路状态中" in rendered


def test_sync_autonomous_supervisor_event_records_latest_timeline_once():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""

    state = {
        "timeline": [
            {
                "created_at": "2026-06-27T03:30:00",
                "event_type": "task_decided",
                "summary": "Approved learning task from supervisor.",
            }
        ]
    }

    autonomous_events_module.sync_autonomous_supervisor_event(
        state,
        event_ports=_panel_event_ports(cli),
    )
    autonomous_events_module.sync_autonomous_supervisor_event(
        state,
        event_ports=_panel_event_ports(cli),
    )

    assert len(cli._autonomous_execution_events) == 1
    assert "监督者链路裁决: Approved learning task from supervisor." in cli._autonomous_execution_events[0]["message"]


def test_body_improvement_completion_requires_improvement_report(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._agent_running = False
    cli.session_id = "cli-body-report-required"
    cli._current_autonomous_task = {
        "task_id": "body-report-1",
        "execution_kind": "body_improvement",
        "_autonomous_task_run_id": "run-body-report-1",
    }
    cli._current_autonomous_task_started_at = time.time() - 5
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "response": "done",
        "autonomous_task_run_id": "run-body-report-1",
    }
    cli._autonomous_execution_events = []

    runtime = _autonomous_runtime(cli)
    decisions = []
    runtime.submit_body_improvement_report = lambda *args, **kwargs: False
    runtime.post_task_decision = lambda task_id, **kwargs: decisions.append((task_id, kwargs)) or True
    runtime._push_cli_agent_scene = lambda *args, **kwargs: True

    runtime.poll_workflow()

    assert decisions[0][0] == "body-report-1"
    assert decisions[0][1]["decision"] == "failed"
    assert decisions[0][1]["context"]["error"] == "missing_or_failed_body_improvement_report"
    assert cli._current_autonomous_task is None


def test_body_improvement_report_includes_verified_baseline_contract(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-body-report-contract"
    cli._autonomous_execution_events = []
    runtime = _autonomous_runtime(cli)
    runtime._git_improvement_diff = lambda worktree, baseline: {
        "commit_hash": "a" * 40,
        "changed_files": ["agent/stream_handler.py"],
        "diff_summary": "agent/stream_handler.py | 2 +-",
    }
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
        return _FakeUrlopenResponse({"status": "reviewed"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    task = {
        "_improvement_worktree": "F:/body/slot-B",
        "_baseline_head": "b" * 40,
        "_improvement_slot_id": "slot-B",
        "evidence": {
            "learning_refs": [
                {
                    "mem_id": "learning-1",
                    "timestamp": "2026-07-18T00:00:00+00:00",
                    "relevance": 0.9,
                }
            ]
        },
    }

    submitted = runtime.submit_body_improvement_report(
        task,
        "body-report-contract",
        "http://127.0.0.1:6000",
        improvement_description="Verified improvement",
    )

    assert submitted is True
    assert captured["url"].endswith("/v1/body/improvement-report")
    assert captured["payload"]["baseline_commit"] == "b" * 40
    assert captured["payload"]["commit_hash"] == "a" * 40
    assert captured["payload"]["changed_files"] == ["agent/stream_handler.py"]
    assert captured["payload"]["learning_refs"][0]["mem_id"] == "learning-1"


def test_body_improvement_completion_posts_after_successful_report(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._agent_running = False
    cli.session_id = "cli-body-report-ok"
    cli._current_autonomous_task = {
        "task_id": "body-report-ok",
        "execution_kind": "body_improvement",
        "_autonomous_task_run_id": "run-body-report-ok",
    }
    cli._current_autonomous_task_started_at = time.time() - 5
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "response": "done",
        "autonomous_task_run_id": "run-body-report-ok",
    }
    cli._autonomous_execution_events = []

    runtime = _autonomous_runtime(cli)
    reports = []
    decisions = []
    runtime.submit_body_improvement_report = lambda *args, **kwargs: reports.append((args, kwargs)) or True
    runtime.post_task_decision = lambda task_id, **kwargs: decisions.append((task_id, kwargs)) or True
    runtime._push_cli_agent_scene = lambda *args, **kwargs: True

    runtime.poll_workflow()

    assert reports
    assert decisions[0][0] == "body-report-ok"
    assert decisions[0][1]["decision"] == "completed"
    assert decisions[0][1]["context"]["failed"] is False
    assert cli._current_autonomous_task is None


def test_auto_command_activates_gate_and_execution_loop(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli.session_id = "cli-session-auto"

    pushed = []
    presence_refreshes = []
    printed = []
    launches = []

    def fake_cprint(*args, **kwargs):
        del kwargs
        printed.append(" ".join(str(arg) for arg in args))

    def fake_push(scene, *, session_id=None, task_id=None, execution_kind=None, subagent_summary=None, agent_role=None):
        pushed.append(
            {
                "scene": scene,
                "session_id": session_id,
                "task_id": task_id,
                "execution_kind": execution_kind,
                "subagent_summary": subagent_summary,
            }
        )

    class _ImmediateThread:
        def __init__(self, target=None, **kwargs):
            del kwargs
            self._target = target

        def start(self):
            if self._target:
                self._target()

    def fake_urlopen(request, timeout=0):
        del request, timeout
        return _FakeUrlopenResponse(
            {
                "autonomous_chain_gate_active": True,
                "drive_loop_running": True,
                "review_loop_running": True,
                "endogenous_drive_enabled": True,
            }
        )

    def fake_refresh_gateway_cli_presence(*, force=False):
        presence_refreshes.append(force)
        fake_push("executing", session_id=cli.session_id)

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        autonomous_gate_module,
        "activate_autonomous_execution",
        lambda host: (launches.append(host), (True, "API-A 自主执行组件已接入当前 CLI。"))[1],
    )
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )

    autonomous_gate_module.handle_auto_command(
        cli,
        "/auto",
        event_ports=_panel_event_ports(cli),
        cprint=fake_cprint,
        refresh_gateway_cli_presence_callback=fake_refresh_gateway_cli_presence,
        thread_factory=_ImmediateThread,
    )

    assert cli._autonomous_gate_active is True
    assert presence_refreshes == [True]
    assert pushed[0]["scene"] == "executing"
    assert pushed[0]["session_id"] == "cli-session-auto"
    assert launches == [cli]
    assert any("组件已接入当前 CLI" in line for line in printed)


def test_auto_command_waits_for_active_companion_worker(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli._scheduled_companion_active = True
    cli._autonomous_activation_pending = False
    cli._autonomous_mode_lock = threading.Lock()
    cli._autonomous_execution_events = []
    cli._autonomous_last_supervisor_event_key = ""
    printed = []
    threads = []

    autonomous_gate_module.handle_auto_command(
        cli,
        "/auto",
        event_ports=_panel_event_ports(cli),
        cprint=lambda text: printed.append(str(text)),
        refresh_gateway_cli_presence_callback=lambda **_kwargs: None,
        thread_factory=lambda **kwargs: threads.append(kwargs),
    )

    assert cli._autonomous_gate_active is False
    assert threads == []
    assert any("辅助模式员工任务仍在执行" in line for line in printed)


def test_auto_command_reads_cached_supervisor_snapshot_instead_of_sync_fetch(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli.session_id = "cli-session-auto-cache"
    cli._supervisor_state_cache = {
        "scene": "planning",
        "title": "判断安排",
        "autonomous_observation": {
            "metrics": {
                "chain_projection": {
                    "api_b_judgement": 0,
                    "api_a_running": 0,
                    "api_a_handoff": 0,
                    "candidate_signals": 0,
                    "writeback_history": 0,
                },
                "observation": {},
            },
            "board": {"primary_focus": {"title": "观察 API-B 判断在途", "status": "当前在途"}},
            "chain": {"segments": []},
            "loop": {"stage_cards": [], "rail_entries": [], "recent_writebacks": []},
            "counts": {},
            "timeline": [],
        },
    }

    printed = []
    refresh_calls = []

    def fake_cprint(*args, **kwargs):
        del kwargs
        printed.append(" ".join(str(arg) for arg in args))

    class _ImmediateThread:
        def __init__(self, target=None, **kwargs):
            del kwargs
            self._target = target

        def start(self):
            if self._target:
                self._target()

    def fake_urlopen(request, timeout=0):
        del request, timeout
        return _FakeUrlopenResponse(
            {
                "autonomous_chain_gate_active": True,
                "drive_loop_running": True,
                "review_loop_running": True,
                "endogenous_drive_enabled": True,
            }
        )

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )
    monkeypatch.setattr(
        "VoidCube_cli.autonomous_gate.preview_supervisor_status_lines",
        lambda host, limit=4: (
            refresh_calls.append(host),
            [
                "场景: 判断安排",
                "闭环焦点: 观察 API-B 判断在途 (当前在途)",
                "闭环分段: API-B 判断在途=0, API-B 已转交=0, 候选形成=0, 写回回流=0",
                "最近监督/事件: 暂无",
            ][:limit],
        )[1],
    )

    autonomous_gate_module.handle_auto_command(
        cli,
        "/auto",
        event_ports=_panel_event_ports(cli),
        cprint=fake_cprint,
        refresh_gateway_cli_presence_callback=lambda *, force=False: None,
        thread_factory=_ImmediateThread,
    )

    assert refresh_calls == [cli]
    assert any("闭环焦点: 观察 API-B 判断在途 (当前在途)" in line for line in printed)
    assert not any("监督者快照将在后台刷新后进入观测面。" in line for line in printed)


def test_activate_autonomous_execution_reuses_running_thread(monkeypatch):
    host = type("_Host", (), {})()
    starts = []
    host._start_autonomous_execution = lambda: starts.append("start") or True

    launched, message = autonomous_gate_module.activate_autonomous_execution(host)

    assert launched is True
    assert starts == ["start"]
    assert "自主链路迷你 CLI" in message


def test_activate_autonomous_execution_reports_start_failure():
    host = type("_Host", (), {})()
    host._start_autonomous_execution = lambda: False

    launched, message = autonomous_gate_module.activate_autonomous_execution(host)

    assert launched is False
    assert "未启动" in message


def test_autonomous_execution_runs_while_foreground_cli_is_busy(monkeypatch):
    parent = VoidcubeCLI.__new__(VoidcubeCLI)
    parent._autonomous_gate_active = True
    parent._agent_running = True
    parent.conversation_history = [{"role": "user", "content": "parent turn"}]
    parent._autonomous_execution_thread = None
    parent._invalidate = lambda *args, **kwargs: None

    class _FakeStopEvent:
        def __init__(self):
            self._set = False

        def clear(self):
            self._set = False

        def set(self):
            self._set = True

        def is_set(self):
            return self._set

        def wait(self, _timeout):
            self._set = True
            return True

    class _ImmediateThread:
        def __init__(self, target=None, **kwargs):
            del kwargs
            self._target = target
            self._alive = False

        def start(self):
            self._alive = True
            if self._target:
                self._target()
            self._alive = False

        def is_alive(self):
            return self._alive

    class _FakeRuntime:
        def __init__(self):
            self.poll_calls = 0

        def poll_workflow(self):
            self.poll_calls += 1

    component = type("_AutonomousOwner", (), {})()
    component._autonomous_gate_active = True
    component._agent_running = False
    component._pending_input = queue.Queue()
    component._pending_input.put("[Autonomous Learning Task] execution owner turn")
    component.conversation_history = []
    component._execute_pending_input_calls = []

    def fake_execute_pending_input(user_input, *, app=None):
        component._execute_pending_input_calls.append((user_input, app))
        component.conversation_history.append({"role": "assistant", "content": "component handled"})
        return True

    component._execute_pending_input = fake_execute_pending_input

    stop_event = _FakeStopEvent()
    runtime = _FakeRuntime()
    pushed = []

    parent._autonomous_execution_stop = stop_event
    parent._ensure_autonomous_execution_host = lambda: component
    parent._autonomous_execution_runtime = lambda: runtime

    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("cli._refresh_supervisor_status_view", lambda host: None)
    monkeypatch.setattr("cli._refresh_autonomous_gateway_status_view", lambda host: None)
    monkeypatch.setattr("cli._refresh_gateway_autonomous_execute_snapshot_view", lambda host: None)
    monkeypatch.setattr(
        "cli._refresh_gateway_cli_presence_view",
        lambda host, **kwargs: pushed.append(("presence", host, kwargs)),
    )
    monkeypatch.setattr(
        "cli._push_cli_agent_scene",
        lambda scene, **kwargs: pushed.append((scene, kwargs)) or True,
    )

    started = parent._start_autonomous_execution()

    assert started is True
    assert component._execute_pending_input_calls == [
        ("[Autonomous Learning Task] execution owner turn", None)
    ]
    assert runtime.poll_calls >= 2
    assert parent.conversation_history == [{"role": "user", "content": "parent turn"}]
    assert component.conversation_history == [{"role": "assistant", "content": "component handled"}]
    assert any(item[0] == "idle" for item in pushed if isinstance(item, tuple))


def test_autonomous_execution_panel_stays_hidden_when_idle():
    host = type("_Host", (), {})()
    host._autonomous_gate_active = True
    host._agent_running = False
    host._current_autonomous_task = None
    host._last_agent_turn_result = None
    host._autonomous_execution_events = []

    class _EmptyQueue:
        def empty(self):
            return True

    host._pending_input = _EmptyQueue()

    assert autonomous_panel_module.has_visible_autonomous_work(
        host,
        state_ports=_panel_state_ports(host),
    ) is False


def test_autonomous_execution_panel_becomes_visible_for_execution_events():
    host = type("_Host", (), {})()
    host._autonomous_gate_active = True
    host._agent_running = False
    host._current_autonomous_task = None
    host._last_agent_turn_result = None
    host._autonomous_execution_events = [{"stage": "claim", "message": "已接手"}]

    class _EmptyQueue:
        def empty(self):
            return True

    host._pending_input = _EmptyQueue()

    assert autonomous_panel_module.has_visible_autonomous_work(
        host,
        state_ports=_panel_state_ports(host),
    ) is True


def test_autonomous_panel_reads_execution_owner_snapshot():
    host = type("_Host", (), {})()
    component = type("_Component", (), {})()
    host._autonomous_gate_active = True
    host._autonomous_execution_host = component
    component._agent_running = False
    component._current_autonomous_task = None
    component._last_agent_turn_result = None
    component._autonomous_execution_events = [{"stage": "claim", "message": "已接手"}]

    class _EmptyQueue:
        def empty(self):
            return True

    component._pending_input = _EmptyQueue()

    assert autonomous_panel_module.has_visible_autonomous_work(
        host,
        state_ports=_panel_state_ports(host),
    ) is True


def test_autonomous_executor_session_is_persisted_before_agent_pull():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-auto-empty"
    cli.model = "test-model"

    class _FakeSessionDB:
        def __init__(self):
            self.created = []

        def get_session(self, session_id):
            assert session_id == "cli-auto-empty"
            return None

        def create_session(self, **kwargs):
            self.created.append(kwargs)

    db = _FakeSessionDB()
    cli._session_db = db

    autonomous_gate_module.ensure_supervisor_task_session(cli, logger_debug=lambda *args, **kwargs: None)

    assert db.created == [
        {
            "session_id": "cli-auto-empty",
            "source": "cli_supervisor_task_lane",
            "model": "test-model",
        }
    ]


def test_current_cli_agent_role_stays_supervisor_task_while_autonomous_task_is_unwinding():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli._current_autonomous_task = {"task_id": "task-1"}

    assert autonomous_presence_module.current_cli_agent_role(cli) == "supervisor_task"


def test_current_cli_agent_role_treats_autonomous_gate_without_task_as_user_chat():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._current_autonomous_task = None

    assert autonomous_presence_module.current_cli_agent_role(cli) == "user_chat"


def test_process_command_allows_regular_slash_commands_while_autonomous_gate_active(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = True
    cli._current_autonomous_task = {"task_id": "learn-allow-help"}
    cli._command_running = False
    cli._command_status = ""
    cli._invalidate = lambda **kwargs: None
    called = {"help": 0}
    initialize_command_execution(
        cli,
        command_handlers={
            "help": lambda _request: called.__setitem__("help", called["help"] + 1)
        },
    )

    assert cli.process_command("/help") is True
    assert called["help"] == 1


def test_push_cli_agent_scene_includes_session_id(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout=0):
        del timeout
        requests.append(
            {
                "url": request.full_url,
                "data": json.loads((request.data or b"{}").decode("utf-8")),
            }
        )
        return _FakeUrlopenResponse({})

    class _ImmediateThread:
        def __init__(self, target=None, **kwargs):
            del kwargs
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setenv("GATEWAY_HOST", "127.0.0.9")
    monkeypatch.setenv("GATEWAY_PORT", "6123")

    autonomous_presence_module.push_cli_agent_scene(
        "learning",
        session_id="cli-session-2",
        task_id="learn-2",
    )

    assert requests[0]["url"] == "http://127.0.0.9:6123/admin/activity/touch"
    assert requests[0]["data"]["session_id"] == "cli-session-2"
    assert requests[0]["data"]["metadata"]["scene"] == "learning"


def test_push_cli_agent_scene_includes_subagent_summary(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout=0):
        del timeout
        requests.append(
            {
                "url": request.full_url,
                "data": json.loads((request.data or b"{}").decode("utf-8")),
            }
        )
        return _FakeUrlopenResponse({})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    autonomous_presence_module.push_cli_agent_scene(
        "executing",
        session_id="cli-session-3",
        subagent_summary={
            "foreground_count": 2,
            "background_count": 1,
            "total_count": 3,
            "focus_task_id": "delegate-1",
            "focus_tool": "read_file",
            "focus_preview": "read_file",
        },
    )

    metadata = requests[0]["data"]["metadata"]
    assert metadata["subagent_foreground_count"] == 2
    assert metadata["subagent_background_count"] == 1
    assert metadata["subagent_total_count"] == 3
    assert metadata["subagent_focus_task_id"] == "delegate-1"
    assert metadata["subagent_focus_tool"] == "read_file"


def test_auto_command_recovers_supervisor_before_failing(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli.session_id = "cli-session-auto-recover"

    printed = []
    presence_refreshes = []
    pushed = []
    launches = []
    attempts = {"count": 0}

    def fake_cprint(*args, **kwargs):
        del kwargs
        printed.append(" ".join(str(arg) for arg in args))

    def fake_push(scene, *, session_id=None, task_id=None, execution_kind=None, subagent_summary=None, agent_role=None):
        pushed.append(
            {
                "scene": scene,
                "session_id": session_id,
                "task_id": task_id,
                "execution_kind": execution_kind,
                "subagent_summary": subagent_summary,
            }
        )
        return True

    class _ImmediateThread:
        def __init__(self, target=None, **kwargs):
            del kwargs
            self._target = target

        def start(self):
            if self._target:
                self._target()

    def fake_urlopen(request, timeout=0):
        del timeout
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("connection refused")
        return _FakeUrlopenResponse(
            {
                "autonomous_chain_gate_active": True,
                "drive_loop_running": True,
                "review_loop_running": True,
                "endogenous_drive_enabled": True,
            }
        )

    def fake_refresh_gateway_cli_presence(*, force=False):
        presence_refreshes.append(force)
        fake_push("executing", session_id=cli.session_id)

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        autonomous_gate_module,
        "activate_autonomous_execution",
        lambda host: (launches.append(host), (True, "API-A 自主执行组件已接入当前 CLI。"))[1],
    )
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )
    monkeypatch.setattr(
        "VoidCube_cli.ops.serve.ensure_running",
        lambda silent=False: {
            "supervisor": {"running": True, "healthy": True, "started": True}
        },
    )

    autonomous_gate_module.handle_auto_command(
        cli,
        "/auto",
        event_ports=_panel_event_ports(cli),
        cprint=fake_cprint,
        refresh_gateway_cli_presence_callback=fake_refresh_gateway_cli_presence,
        thread_factory=_ImmediateThread,
    )

    assert cli._autonomous_gate_active is True
    assert attempts["count"] >= 2
    assert any("attempting daemon recovery" in line for line in printed)
    assert presence_refreshes == [True]
    assert pushed[0]["scene"] == "executing"
    assert launches == [cli]
    assert any("组件已接入当前 CLI" in line for line in printed)


def test_cli_formats_supervisor_status_snapshot():
    lines = format_supervisor_status_snapshot(
        {
            "scene": "planning",
            "title": "义子正在整理任务",
            "autonomous_observation": {
                "metrics": {
                    "chain_projection": {
                        "api_b_judgement": 1,
                        "api_a_running": 1,
                        "api_a_handoff": 2,
                        "candidate_signals": 3,
                        "writeback_history": 1,
                    },
                    "observation": {
                        "judgement_records": 2,
                        "followup_signals": 1,
                        "priority_change_signals": 1,
                    },
                },
                "board": {
                    "primary_focus": {
                        "title": "观察 API-B 判断在途",
                        "status": "当前在途",
                    },
                },
                "loop": {
                    "stage_cards": [
                        {
                            "stage_key": "api_b_judgement",
                            "title": "观察 API-B 判断在途",
                            "source_label": "API-B",
                            "status": "active",
                            "display_status": "当前在途",
                            "focus_task": {
                                "title": "观察 API-B 判断在途",
                                "display_status": "当前在途",
                            },
                        },
                        {
                            "stage_key": "mem_writeback",
                            "title": "改进 shell 替身",
                            "source_label": "Mem",
                            "status": "ready",
                            "display_status": "等待写回",
                            "focus_task": {
                                "title": "改进 shell 替身",
                                "display_status": "等待写回",
                            },
                        },
                    ]
                },
                "chain": {
                    "segments": [
                        {"label": "API-B 判断在途", "count": 1},
                        {"label": "API-B 已转交", "count": 2},
                        {"label": "候选形成", "count": 3},
                        {"label": "写回回流", "count": 1},
                    ]
                },
            },
            "timeline": [
                {
                    "event_type": "tasks_reviewed",
                    "summary": "监督者已复核 3 个链路项: approved。 优先级重排 1 次。",
                }
            ],
        }
    )

    assert any("场景: 判断安排" in line for line in lines)
    assert any("API-B 判断在途=1" in line and "API-B 已转交=2" in line for line in lines)
    assert any("判断记录=2" in line and "后续信号=1" in line and "优先级变化=1" in line for line in lines)
    assert any("闭环焦点: 观察 API-B 判断在途 (当前在途)" in line for line in lines)
    assert any("闭环分段: API-B 判断在途=1, API-B 已转交=2, 候选形成=3, 写回回流=1" in line for line in lines)
    assert any("执行焦点: 改进 shell 替身 (等待写回)" in line for line in lines)
    assert any("改进 shell 替身" in line for line in lines)
    assert any("最近监督/事件: API-B 复核记录" in line for line in lines)


def test_cli_formats_gateway_autonomous_execute_snapshot():
    lines = format_gateway_autonomous_execute_snapshot(
        {
            "last_autonomous_chain_execute_at": "2026-06-26T10:00:00",
            "autonomous_chain_execute_count": 3,
            "autonomous_chain_execute": {
                "source_service": "cli_agent",
                "task_id": "body-1",
                "task_identity": {
                    "task_id": "body-1",
                    "summary": "改进 shell 替身 (替身改进)",
                },
            },
        }
    )

    assert any("最近链路项: 改进 shell 替身 (替身改进)" in line for line in lines)
    assert any("来源=cli_agent" in line and "次数=3" in line and "task_id=body-1" in line for line in lines)


def test_initialize_autonomous_status_caches_sets_observation_cache_fields():
    host = type("_Host", (), {})()

    autonomous_status_host_module.initialize_autonomous_status_caches(host)

    assert host._supervisor_state_cache == {}
    assert host._supervisor_state_ts == 0.0
    assert host._supervisor_state_refreshing is False
    assert host._supervisor_url == ""
    assert host._autonomous_gateway_status_cache == {}
    assert host._autonomous_gateway_status_ts == 0.0
    assert host._autonomous_gateway_status_refreshing is False
    assert host._autonomous_gateway_execute_cache == {}
    assert host._autonomous_gateway_execute_ts == 0.0
    assert host._autonomous_gateway_execute_refreshing is False


def test_supervisor_activity_snapshot_prefers_host_override():
    host = type("_Host", (), {})()
    host._fetch_supervisor_status = lambda: {
        "scene": "planning",
        "mem_usage": {"context_percent": 42},
    }

    snapshot = autonomous_status_host_module.supervisor_activity_snapshot(host)

    assert snapshot["scene"] == "planning"
    assert snapshot["is_active"] is True
    assert snapshot["mem_usage"]["context_percent"] == 42


def test_preview_supervisor_status_lines_reads_cached_snapshot(monkeypatch):
    host = type("_Host", (), {})()
    host._supervisor_state_cache = {
        "scene": "planning",
        "title": "判断安排",
        "summary": "正在观察 API-B 判断在途",
        "autonomous_observation": {
            "board": {
                "primary_focus": {
                    "title": "观察 API-B 判断在途",
                    "status": "当前在途",
                }
            },
            "counts": {
                "api_b_judgement": 1,
                "api_a_handoff": 0,
                "candidates": 0,
                "writebacks": 0,
            },
            "loop": {"stage_cards": [], "rail_entries": [], "recent_writebacks": []},
            "metrics": {"chain_projection": {}, "observation": {}},
            "timeline": [],
        },
    }

    refresh_calls = []
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_supervisor_status",
        lambda current_host: refresh_calls.append(current_host),
    )

    lines = autonomous_status_host_module.preview_supervisor_status_lines(host, limit=4)

    assert refresh_calls == [host]
    assert len(lines) == 4
    assert any("闭环焦点: 观察 API-B 判断在途 (当前在途)" in line for line in lines)


def test_cli_autonomous_summary_sections_read_cached_observation_surfaces(monkeypatch):
    host = type("_Host", (), {})()
    host._supervisor_state_cache = {
        "scene": "planning",
        "title": "判断安排",
        "summary": "正在观察 API-B 判断在途",
        "autonomous_observation": {
            "counts": {
                "api_b_judgement": 1,
                "api_a_handoff": 1,
                "candidates": 0,
                "writebacks": 0,
            },
            "board": {
                "primary_focus": {
                    "title": "观察 API-B 判断在途",
                    "status": "当前在途",
                }
            },
            "loop": {
                "stage_cards": [],
                "rail_entries": [],
                "recent_writebacks": [],
            },
            "metrics": {
                "chain_projection": {
                    "api_a_running": 0,
                    "body_improvement": 0,
                },
                "observation": {
                    "judgement_records": 0,
                    "followup_signals": 0,
                    "priority_change_signals": 0,
                },
            },
            "timeline": [],
        },
    }
    host._autonomous_gateway_execute_cache = {
        "last_autonomous_chain_execute_at": "2026-06-26T10:00:00",
        "autonomous_chain_execute_count": 2,
        "autonomous_chain_execute": {
            "source_service": "cli_agent",
            "task_identity": {
                "task_id": "learn-1",
                "summary": "学习替身基线",
            },
        },
    }
    host._supervisor_state_refreshing = False
    host._autonomous_gateway_execute_refreshing = False

    refresh_calls = []
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_supervisor_status",
        lambda current_host: refresh_calls.append(("supervisor", current_host)),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_gateway_autonomous_execute_snapshot",
        lambda current_host: refresh_calls.append(("gateway_execute", current_host)),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "fetch_supervisor_status_snapshot",
        lambda current_host: (_ for _ in ()).throw(AssertionError("不应再走同步 supervisor snapshot")),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "fetch_gateway_autonomous_execute_snapshot",
        lambda current_host: (_ for _ in ()).throw(AssertionError("不应再走同步 gateway execute snapshot")),
    )

    lines = autonomous_observation_summary_sections(host)

    assert refresh_calls == [("supervisor", host), ("gateway_execute", host)]
    assert any("监督者快照:" == line for line in lines)
    assert any("闭环焦点: 观察 API-B 判断在途 (当前在途)" in line for line in lines)
    assert any("最近自主执行回报:" == line for line in lines)
    assert any("最近链路项: 学习替身基线" in line for line in lines)


def test_cli_autonomous_summary_sections_show_refreshing_hint_when_cache_empty(monkeypatch):
    host = type("_Host", (), {})()
    host._supervisor_state_cache = {}
    host._autonomous_gateway_execute_cache = {}
    host._supervisor_state_refreshing = True
    host._autonomous_gateway_execute_refreshing = True

    monkeypatch.setattr(autonomous_status_host_module, "refresh_supervisor_status", lambda current_host: None)
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_gateway_autonomous_execute_snapshot",
        lambda current_host: None,
    )

    lines = autonomous_observation_summary_sections(host)

    assert any("监督者快照:" == line for line in lines)
    assert any("后台刷新中，稍后会回到当前自主闭环快照。" in line for line in lines)
    assert any("最近自主执行回报:" == line for line in lines)
    assert any("后台刷新中，稍后会回到最近自主执行回报。" in line for line in lines)


def test_main_cli_observation_refresh_does_not_poll_autonomous_tasks(monkeypatch):
    host = type("_Host", (), {})()
    host._autonomous_gate_active = True
    calls = []

    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_supervisor_status",
        lambda current_host: calls.append(("supervisor", current_host)),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_autonomous_gateway_status",
        lambda current_host: calls.append(("gateway_status", current_host)),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_gateway_autonomous_execute_snapshot",
        lambda current_host: calls.append(("gateway_execute", current_host)),
    )

    autonomous_status_host_module.refresh_autonomous_observation_surfaces(
        host,
        refresh_gateway_cli_presence=lambda: calls.append(("presence", host)),
    )

    assert calls == [
        ("supervisor", host),
        ("gateway_status", host),
        ("gateway_execute", host),
        ("presence", host),
    ]
