from __future__ import annotations

import json
import sys
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
from VoidCube_cli import autonomous_status_host as autonomous_status_host_module
from VoidCube_cli.autonomous_observation import format_supervisor_status_snapshot
from VoidCube_cli.autonomous_status_host import (
    autonomous_observation_summary_sections,
    format_gateway_agent_activity_snapshot,
)


class _FakeUrlopenResponse:
    def __init__(self, payload: dict | None = None):
        self._payload = payload or {}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_cli_does_not_rewrite_live_agent_base_url_to_gateway(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.api_key = "runtime-key"
    cli.base_url = "https://runtime-base.example/v1"
    cli.provider = "agnesai"
    cli.api_mode = "chat_completions"
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
    cli._on_tool_progress = None
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
            self._print_fn = None

    monkeypatch.setattr("cli._get_AIAgent", lambda: _FakeAgent)
    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda session_id, model, provider: None)

    cli.agent = None
    cli._ensure_runtime_credentials = lambda: True
    cli._resumed = False
    cli.conversation_history = []
    cli._clarify_callback = None
    cli._pending_title = None

    ok = cli._init_agent()

    assert ok is True
    assert cli.agent.base_url == "https://runtime-base.example/v1"


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

    cli._autonomous_runtime().poll_workflow()

    assert requests[0]["url"].endswith("/v1/tasks/learn-1/decision")
    assert requests[0]["data"]["decision"] == "failed"
    assert requests[0]["data"]["context"]["error"] == "LLM upstream error: 502"
    assert cli._current_autonomous_task is None
    assert cli._last_agent_turn_result is None


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

    cli._autonomous_runtime().poll_workflow()

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

    ok = cli._inject_autonomous_execution_prompt(task, "self_learning")

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

    cli._autonomous_runtime().poll_workflow()

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

    cli._autonomous_runtime().poll_workflow()

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

    timed_out = cli._autonomous_runtime().report_current_task_timeout_if_needed(now=1802.0)

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

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requested_urls.append(request.full_url)
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

    cli._autonomous_runtime().poll_workflow()

    assert any("task_type=self_learning" in url for url in requested_urls)
    assert any("execution_kind=body_improvement" in url for url in requested_urls)
    assert any(url.endswith("/admin/activity/touch") for url in requested_urls)
    assert cli._current_autonomous_task is not None
    assert cli._current_autonomous_task["task_id"] == "body-1"
    assert prompts
    assert prompts[0].startswith("[Autonomous Body Improvement Task]")


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
            return _FakeUrlopenResponse({})
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

    cli._autonomous_runtime().poll_workflow()

    run_request = next(item for item in requests if item["url"].endswith("/v1/tasks/learn-7/decision"))
    assert run_request["data"]["decision"] == "running"
    assert run_request["data"]["context"]["session_id"] == "cli-owner-1"
    assert run_request["data"]["metadata"]["owner_session_id"] == "cli-owner-1"
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
            return _FakeUrlopenResponse({})
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

    cli._autonomous_runtime().poll_workflow()

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
            return _FakeUrlopenResponse({})
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

    cli._autonomous_runtime().poll_workflow()

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
        "metadata": {
            "owner_session_id": "cli-owner-restore",
            "execution_source": "cli_agent_pull",
            "execution_started_at": "2026-06-27T11:00:00+08:00",
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

    cli._autonomous_runtime().poll_workflow()

    complete_request = next(
        item for item in requests if item["url"].endswith("/v1/tasks/learn-restore-1/decision")
    )
    assert complete_request["data"]["decision"] == "completed"
    assert "completed by the API-A autonomous executor" in complete_request["data"]["reason"]
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
        "metadata": {
            "owner_session_id": "cli-owner-replay",
            "execution_source": "cli_agent_pull",
            "execution_started_at": "2026-06-27T11:00:00+08:00",
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

    cli._autonomous_runtime().poll_workflow()

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
    cli._pending_tool_info = {"tool": "shell"}
    cli._last_scrollback_tool = "shell"
    cli._voice_mode = False
    cli._voice_continuous = False
    cli._voice_recording = False
    cli._pending_input = type("_Queue", (), {"put": lambda self, payload: None})()

    calls = []

    class _FakeApp:
        def __init__(self):
            self.invalidate_calls = 0

        def invalidate(self):
            self.invalidate_calls += 1

    def fake_chat(user_input, images=None):
        calls.append({"user_input": user_input, "images": images})
        cli._last_agent_turn_result = {"failed": False}

    monkeypatch.setattr("cli._cprint", lambda *args, **kwargs: None)

    app = _FakeApp()
    cli.chat = fake_chat

    handled = cli._execute_pending_input("[Autonomous Learning Task] Learn backlog recovery", app=app)

    assert handled is True
    assert calls == [{"user_input": "[Autonomous Learning Task] Learn backlog recovery", "images": None}]
    assert cli._agent_running is False
    assert cli._spinner_text == ""
    assert cli._tool_start_time == 0.0
    assert cli._current_tool_name == ""
    assert cli._pending_tool_info == {}
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
        cprint=lambda *args, **kwargs: None,
        interrupt_current_task_callback=cli._autonomous_runtime().interrupt_current_task,
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
    cli._stream_started = False
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
        cli,
        "已接管任务 learn-panel-1",
        tone="success",
    )
    autonomous_events_module.append_autonomous_execution_event(
        cli,
        "工具启动: web_search",
        tone="info",
    )

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._supervisor_state_cache = {
        "timeline": [
            {
                "event_type": "task_decided",
                "summary": "Supervisor approved task for execution.",
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "API-A 自主执行面" in rendered
    assert "执行位: 当前执行位 正常" in rendered
    assert "Panel task title" in rendered
    assert "已接管任务 learn-panel-1" in rendered
    assert "工具启动: web_search" in rendered
    assert "Supervisor approved task for execution." in rendered


def test_autonomous_panel_shows_stale_foreign_executor(monkeypatch):
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "执行位: 他处执行位 " in rendered
    assert "已陈旧（静默 120s，场景 executing）" in rendered


def test_autonomous_panel_shows_no_api_a_executable_task_reason(monkeypatch):
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
                        "key": "api_b_backlog",
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
                "stages": [
                    {
                        "key": "api_b_judgement",
                        "label": "API-B 判断",
                        "owner": "API-B",
                        "status": "active",
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "链路项: 当前没有被认领的自主链路项" in rendered
    assert "仍停留在 API-B 治理段" in rendered


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
                        "key": "api_a_ready",
                        "items": [
                            {
                                "task_id": "learn-board-1",
                                "title": "Board approved task",
                                "status": "approved",
                                "task_family": "self_learning",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stages": [
                    {
                        "key": "api_a_execution",
                        "label": "API-A 自主执行",
                        "owner": "API-A",
                        "status": "ready",
                        "focus_task": {
                            "task_id": "learn-board-1",
                            "title": "Board approved task",
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 已放行待认领" in rendered
    assert "Board approved task" in rendered
    assert "等待 API-A 自主执行面认领" in rendered


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
                        "key": "api_a_ready",
                        "items": [
                            {
                                "task_id": "learn-approved-1",
                                "title": "Approved waiting task",
                                "task_type": "self_learning",
                                "status": "approved",
                                "lane": "agent",
                            }
                        ],
                    }
                ]
            },
            "loop": {
                "stages": [
                    {
                        "key": "api_a_execution",
                        "status": "ready",
                        "focus_task": {
                            "task_id": "learn-approved-1",
                            "title": "Approved waiting task",
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 已放行待认领" in rendered
    assert "Approved waiting task" in rendered
    assert "链路: 监督者已放行该链路项，等待 API-A 自主执行面认领" in rendered
    assert "执行流: 监督者已放行链路项，等待 API-A 自主执行面认领" in rendered


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
                "stages": [
                    {
                        "key": "api_a_execution",
                        "status": "ready",
                        "stage": "waiting_api_a_claim",
                        "cli_focus_stage": "waiting_api_a_claim",
                        "status_label": "已放行待认领",
                        "chain_reason": "链路: 监督者已放行该链路项，等待自主执行面认领",
                        "activity_text": "执行流: 监督者等待某个自主执行面开始处理该链路项",
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 已放行待认领" in rendered
    assert "Loop-stage driven task" in rendered
    assert "链路: 监督者已放行该链路项，等待自主执行面认领" in rendered
    assert "执行流: 监督者等待某个自主执行面开始处理该链路项" in rendered


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
                        "key": "api_a_ready",
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
                "stages": [
                    {
                        "key": "api_a_execution",
                        "status": "active",
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 他处执行中" in rendered
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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 已认领待起跑" in rendered
    assert "Claimed not started task" in rendered
    assert "链路: 自主执行面已认领该链路项，等待进入首个模型或工具回合" in rendered
    assert "近因: 已认领链路项，但还没有收到后续执行事件" in rendered
    assert "执行流: API-A 自主执行面已认领链路项，等待进入首个模型或工具回合" in rendered


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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 已认领待起跑" in rendered
    assert "近因: 自主执行已起跑，正在等待首个模型响应" in rendered


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

    rendered = "\n".join(text for _, text in autonomous_panel_module.build_autonomous_execution_panel_rows(cli))

    assert "状态: 等待回写" in rendered
    assert "Writeback waiting task" in rendered
    assert "链路: 自主执行面已完成执行，等待结果回写到自主链路" in rendered
    assert "执行流: API-A 自主执行面已结束本轮执行，等待写回链路状态" in rendered


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

    autonomous_events_module.sync_autonomous_supervisor_event(cli, state)
    autonomous_events_module.sync_autonomous_supervisor_event(cli, state)

    assert len(cli._autonomous_execution_events) == 1
    assert "监督者链路裁决: Approved learning task from supervisor." in cli._autonomous_execution_events[0]["message"]


def test_cli_force_quit_marks_body_improvement_task_interrupted(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._agent_running = False
    cli._autonomous_gate_active = True
    cli._current_autonomous_task = {
        "task_id": "body-1",
        "execution_kind": "body_improvement",
    }
    cli.session_id = ""

    requests = []

    def fake_cprint(*args, **kwargs):
        del args, kwargs

    def fake_urlopen(request, timeout=0):
        del timeout
        if isinstance(request, Request):
            requests.append(
                {
                    "url": request.full_url,
                    "method": request.get_method(),
                    "data": json.loads((request.data or b"{}").decode("utf-8")) if request.data else None,
                }
            )
        return _FakeUrlopenResponse({})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("cli._cprint", fake_cprint)

    result = autonomous_gate_module.force_quit_autonomous_gate(
        cli,
        cprint=fake_cprint,
        interrupt_current_task_callback=cli._autonomous_runtime().interrupt_current_task,
        push_cli_agent_scene_callback=autonomous_presence_module.push_cli_agent_scene,
    )

    assert result is True
    task_request = next(item for item in requests if item["url"].endswith("/v1/tasks/body-1/decision"))
    assert task_request["data"]["decision"] == "failed"
    assert task_request["data"]["context"]["execution_kind"] == "body_improvement"
    assert "改进链路项被用户中断" in task_request["data"]["reason"]
    assert cli._current_autonomous_task is None


def test_auto_command_marks_cli_agent_surface_active(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli.session_id = "cli-session-auto"

    pushed = []
    polled = []
    cycle_calls = []
    presence_refreshes = []

    def fake_cprint(*args, **kwargs):
        del args, kwargs

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

    def fake_cycle(*, focus=""):
        cycle_calls.append(focus)
        return {"summary": {"planned": 1, "handed_off": 0}}

    def fake_poll():
        polled.append(True)

    def fake_refresh_gateway_cli_presence(*, force=False):
        presence_refreshes.append(force)
        fake_push("executing", session_id=cli.session_id)

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(autonomous_gate_module, "trigger_autonomous_cycle", fake_cycle)
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )

    autonomous_gate_module.handle_auto_command(
        cli,
        "/auto",
        cprint=fake_cprint,
        poll_autonomous_workflow_callback=fake_poll,
        refresh_gateway_cli_presence_callback=fake_refresh_gateway_cli_presence,
        thread_factory=_ImmediateThread,
    )

    assert cli._autonomous_gate_active is True
    assert presence_refreshes == [True]
    assert pushed[0]["scene"] == "executing"
    assert pushed[0]["session_id"] == "cli-session-auto"
    assert cycle_calls == [""]
    assert polled == [True]


def test_auto_command_reads_cached_supervisor_snapshot_instead_of_sync_fetch(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._autonomous_gate_active = False
    cli.session_id = "cli-session-auto-cache"
    cli._supervisor_state_cache = {
        "scene": "planning",
        "title": "治理安排",
        "autonomous_observation": {
            "metrics": {"by_path": {}, "running_count": 0, "governance": {}},
            "board": {"primary_focus": {"title": "复核治理在途卫生", "status": "当前在途"}},
            "chain": {"segments": []},
            "loop": {"stages": [], "recent_writebacks": []},
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
        autonomous_gate_module,
        "trigger_autonomous_cycle",
        lambda focus="": {"summary": {"planned": 0, "handed_off": 0}},
    )
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )
    monkeypatch.setattr(
        "VoidCube_cli.autonomous_gate.refresh_supervisor_status",
        lambda host: refresh_calls.append(host),
    )
    monkeypatch.setattr(
        "VoidCube_cli.autonomous_gate.fetch_supervisor_status_snapshot",
        lambda host: (_ for _ in ()).throw(AssertionError("不应再同步读取 supervisor snapshot")),
        raising=False,
    )

    autonomous_gate_module.handle_auto_command(
        cli,
        "/auto",
        cprint=fake_cprint,
        poll_autonomous_workflow_callback=lambda: None,
        refresh_gateway_cli_presence_callback=lambda *, force=False: None,
        thread_factory=_ImmediateThread,
    )

    assert refresh_calls == [cli]
    assert any("闭环焦点: 复核治理在途卫生 (当前在途)" in line for line in printed)
    assert not any("监督者快照将在后台刷新后进入观测面。" in line for line in printed)


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

    autonomous_gate_module.ensure_autonomous_executor_session(cli, logger_debug=lambda *args, **kwargs: None)

    assert db.created == [
        {
            "session_id": "cli-auto-empty",
            "source": "cli_autonomous_executor",
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

    called = {"help": 0}

    monkeypatch.setattr("cli._cprint", lambda *args, **kwargs: None)
    cli.show_help = lambda: called.__setitem__("help", called["help"] + 1)

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

    autonomous_presence_module.push_cli_agent_scene(
        "learning",
        session_id="cli-session-2",
        task_id="learn-2",
    )

    assert requests[0]["url"].endswith("/admin/activity/touch")
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
    cycle_calls = []
    polled = []
    presence_refreshes = []
    pushed = []
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

    def fake_cycle(*, focus=""):
        cycle_calls.append(focus)
        return {"summary": {"planned": 1, "handed_off": 0}}

    def fake_poll():
        polled.append(True)

    def fake_refresh_gateway_cli_presence(*, force=False):
        presence_refreshes.append(force)
        fake_push("executing", session_id=cli.session_id)

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(autonomous_gate_module, "trigger_autonomous_cycle", fake_cycle)
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
        cprint=fake_cprint,
        poll_autonomous_workflow_callback=fake_poll,
        refresh_gateway_cli_presence_callback=fake_refresh_gateway_cli_presence,
        thread_factory=_ImmediateThread,
    )

    assert cli._autonomous_gate_active is True
    assert attempts["count"] >= 2
    assert any("attempting daemon recovery" in line for line in printed)
    assert presence_refreshes == [True]
    assert pushed[0]["scene"] == "executing"
    assert cycle_calls == [""]
    assert polled == [True]


def test_cli_formats_supervisor_status_snapshot():
    lines = format_supervisor_status_snapshot(
        {
            "scene": "planning",
            "title": "义子正在整理任务",
            "autonomous_observation": {
                "metrics": {
                    "by_path": {"learning": 2, "maintenance": 1, "evolution": 3},
                    "running_count": 1,
                    "governance": {
                        "review_actions": 2,
                        "followup_suggestions": 1,
                        "priority_adjustments": 1,
                    },
                },
                "board": {
                    "primary_focus": {
                        "title": "复核治理在途卫生",
                        "status": "当前在途",
                    },
                },
                "loop": {
                    "stages": [
                        {
                            "key": "api_b_judgement",
                            "label": "API-B 判断",
                            "owner": "API-B",
                            "status": "active",
                            "focus_task": {
                                "title": "复核治理在途卫生",
                                "display_status": "当前在途",
                            },
                        },
                        {
                            "key": "mem_writeback",
                            "label": "Mem 写回",
                            "owner": "Mem",
                            "status": "ready",
                            "focus_task": {
                                "title": "改进 shell 替身",
                                "display_status": "等待写回",
                            },
                        },
                    ]
                },
                "chain": {
                    "segments": [
                        {"label": "治理在途", "count": 1},
                        {"label": "待认领窗口", "count": 2},
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

    assert any("场景: 治理安排" in line for line in lines)
    assert any("learning=2" in line and "evolution=3" in line for line in lines)
    assert any("裁定=2" in line and "建议=1" in line and "重排=1" in line for line in lines)
    assert any("闭环焦点: 复核治理在途卫生 (当前在途)" in line for line in lines)
    assert any("闭环分段: 治理在途=1, 待认领窗口=2, 候选形成=3, 写回回流=1" in line for line in lines)
    assert any("执行焦点: 改进 shell 替身 (等待写回)" in line for line in lines)
    assert any("改进 shell 替身" in line for line in lines)
    assert any("最近监督/事件: 批量复核" in line for line in lines)


def test_cli_formats_gateway_agent_activity_snapshot():
    lines = format_gateway_agent_activity_snapshot(
        {
            "last_agent_work_at": "2026-06-26T10:00:00",
            "agent_work_count": 3,
            "agent_work": {
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


def test_cli_autonomous_summary_sections_read_cached_observation_surfaces(monkeypatch):
    host = type("_Host", (), {})()
    host._supervisor_state_cache = {
        "scene": "planning",
        "title": "治理安排",
        "summary": "正在梳理治理在途",
        "autonomous_observation": {
            "counts": {
                "api_b_backlog": 1,
                "api_a_ready": 1,
                "candidates": 0,
                "writebacks": 0,
            },
            "board": {
                "primary_focus": {
                    "title": "复核治理在途卫生",
                    "status": "当前在途",
                }
            },
            "loop": {
                "stages": [],
                "recent_writebacks": [],
            },
            "metrics": {
                "chain_projection": {
                    "api_a_running": 0,
                    "body_improvement": 0,
                },
                "governance": {
                    "review_actions": 0,
                    "followup_suggestions": 0,
                    "priority_adjustments": 0,
                },
            },
            "timeline": [],
        },
    }
    host._autonomous_gateway_activity_cache = {
        "last_agent_work_at": "2026-06-26T10:00:00",
        "agent_work_count": 2,
        "agent_work": {
            "source_service": "cli_agent",
            "task_identity": {
                "task_id": "learn-1",
                "summary": "学习替身基线",
            },
        },
    }
    host._supervisor_state_refreshing = False
    host._autonomous_gateway_activity_refreshing = False

    refresh_calls = []
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_supervisor_status",
        lambda current_host: refresh_calls.append(("supervisor", current_host)),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_gateway_agent_activity_snapshot",
        lambda current_host: refresh_calls.append(("gateway_activity", current_host)),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "fetch_supervisor_status_snapshot",
        lambda current_host: (_ for _ in ()).throw(AssertionError("不应再走同步 supervisor snapshot")),
    )
    monkeypatch.setattr(
        autonomous_status_host_module,
        "fetch_gateway_agent_activity_snapshot",
        lambda current_host: (_ for _ in ()).throw(AssertionError("不应再走同步 gateway activity snapshot")),
    )

    lines = autonomous_observation_summary_sections(host)

    assert refresh_calls == [("supervisor", host), ("gateway_activity", host)]
    assert any("监督者快照:" == line for line in lines)
    assert any("闭环焦点: 复核治理在途卫生 (当前在途)" in line for line in lines)
    assert any("网关执行活动:" == line for line in lines)
    assert any("最近链路项: 学习替身基线" in line for line in lines)


def test_cli_autonomous_summary_sections_show_refreshing_hint_when_cache_empty(monkeypatch):
    host = type("_Host", (), {})()
    host._supervisor_state_cache = {}
    host._autonomous_gateway_activity_cache = {}
    host._supervisor_state_refreshing = True
    host._autonomous_gateway_activity_refreshing = True

    monkeypatch.setattr(autonomous_status_host_module, "refresh_supervisor_status", lambda current_host: None)
    monkeypatch.setattr(
        autonomous_status_host_module,
        "refresh_gateway_agent_activity_snapshot",
        lambda current_host: None,
    )

    lines = autonomous_observation_summary_sections(host)

    assert any("监督者快照:" == line for line in lines)
    assert any("后台刷新中，稍后会回到当前自主闭环快照。" in line for line in lines)
    assert any("网关执行活动:" == line for line in lines)
    assert any("后台刷新中，稍后会回到最近 API-A 执行回报。" in line for line in lines)
