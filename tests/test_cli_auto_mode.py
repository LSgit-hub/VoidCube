from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli import VoidcubeCLI


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


def test_cli_auto_mode_marks_learning_task_failed_after_agent_error(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_auto_task = {"task_id": "learn-1"}
    cli._current_auto_task_started_at = 1.0
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

    cli._poll_auto_mode_workflow()

    assert requests[0]["url"].endswith("/v1/tasks/learn-1/decision")
    assert requests[0]["data"]["decision"] == "failed"
    assert requests[0]["data"]["context"]["error"] == "LLM upstream error: 502"
    assert cli._current_auto_task is None
    assert cli._last_agent_turn_result is None


def test_cli_auto_mode_pulls_body_improvement_tasks(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_auto_task = None
    cli._current_auto_task_started_at = 0.0
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
                            "title": "Improve shell body",
                            "summary": "Apply learned refactor to shell body",
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

    cli._poll_auto_mode_workflow()

    assert any("task_type=self_learning" in url for url in requested_urls)
    assert any("execution_kind=body_improvement" in url for url in requested_urls)
    assert any(url.endswith("/admin/activity/touch") for url in requested_urls)
    assert cli._current_auto_task is not None
    assert cli._current_auto_task["task_id"] == "body-1"
    assert prompts
    assert prompts[0].startswith("[AUTO Body Improvement Task]")


def test_cli_auto_mode_running_decision_records_owner_session(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_auto_task = None
    cli._current_auto_task_started_at = 0.0
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

    cli._poll_auto_mode_workflow()

    run_request = next(item for item in requests if item["url"].endswith("/v1/tasks/learn-7/decision"))
    assert run_request["data"]["decision"] == "running"
    assert run_request["data"]["context"]["session_id"] == "cli-owner-1"
    assert run_request["data"]["metadata"]["owner_session_id"] == "cli-owner-1"


def test_cli_auto_mode_recovers_owned_running_task_before_completion_writeback(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._current_auto_task = None
    cli._current_auto_task_started_at = 0.0
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
        "title": "Recovered AUTO task",
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

    cli._poll_auto_mode_workflow()

    complete_request = next(
        item for item in requests if item["url"].endswith("/v1/tasks/learn-restore-1/decision")
    )
    assert complete_request["data"]["decision"] == "completed"
    assert "completed by CLI Agent" in complete_request["data"]["reason"]
    assert cli._current_auto_task is None
    assert cli._last_agent_turn_result is None


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

    handled = cli._execute_pending_input("[AUTO Learning Task] Learn queue recovery", app=app)

    assert handled is True
    assert calls == [{"user_input": "[AUTO Learning Task] Learn queue recovery", "images": None}]
    assert cli._agent_running is False
    assert cli._spinner_text == ""
    assert cli._tool_start_time == 0.0
    assert cli._current_tool_name == ""
    assert cli._pending_tool_info == {}
    assert cli._last_scrollback_tool == ""
    assert app.invalidate_calls >= 2


def test_refresh_gateway_cli_presence_registers_session_and_scene(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-keepalive"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._auto_mode_active = True
    cli._current_auto_task = {
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
        lambda scene, *, session_id=None, task_id=None, execution_kind=None: scenes.append(
            (scene, session_id, task_id, execution_kind)
        ) or True,
    )
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    cli._refresh_gateway_cli_presence(force=True)

    assert registrations == [("cli-session-keepalive", "api-a-model", "agnesai")]
    assert scenes == [("learning", "cli-session-keepalive", "learn-11", "self_learning")]
    assert cli._last_gateway_presence_refresh_at == 100.0


def test_refresh_gateway_cli_presence_respects_refresh_interval(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-keepalive"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._auto_mode_active = False
    cli._current_auto_task = None
    cli._gateway_presence_refresh_interval_seconds = 30.0
    cli._last_gateway_presence_refresh_at = 90.0

    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not register")))
    monkeypatch.setattr("cli._push_cli_agent_scene", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not push scene")))
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    cli._refresh_gateway_cli_presence()

    assert cli._last_gateway_presence_refresh_at == 90.0


def test_refresh_gateway_cli_presence_retries_quickly_after_register_failure(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli.session_id = "cli-session-keepalive"
    cli.model = "api-a-model"
    cli.provider = "agnesai"
    cli._auto_mode_active = False
    cli._current_auto_task = None
    cli._gateway_presence_refresh_interval_seconds = 30.0
    cli._last_gateway_presence_refresh_at = 0.0

    scenes = []

    monkeypatch.setattr("cli._is_gateway_running", lambda timeout=0.3: True)
    monkeypatch.setattr("cli._register_with_gateway", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "cli._push_cli_agent_scene",
        lambda scene, *, session_id=None, task_id=None, execution_kind=None: scenes.append(
            (scene, session_id, task_id, execution_kind)
        ) or True,
    )
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    cli._refresh_gateway_cli_presence(force=True)

    assert scenes == [("idle", "cli-session-keepalive", None, None)]
    assert cli._last_gateway_presence_refresh_at == 72.0


def test_auto_panel_fragments_include_focus_task_and_recent_events(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "20260627_focus1234"
    cli._current_auto_task = {
        "task_id": "learn-panel-1",
        "title": "Panel task title",
        "task_type": "self_learning",
    }
    cli._current_auto_task_started_at = 0.0
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""
    cli._append_auto_execution_event("已接管任务 learn-panel-1", tone="success")
    cli._append_auto_execution_event("工具启动: web_search", tone="info")

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {
        "timeline": [
            {
                "event_type": "task_decided",
                "summary": "Supervisor approved task for execution.",
            }
        ],
        "tasks": [],
    }
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "20260627_focus1234",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 4,
            "scene": "learning",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "AUTO Execution Panel" in rendered
    assert "Executor: this CLI healthy" in rendered
    assert "Panel task title" in rendered
    assert "已接管任务 learn-panel-1" in rendered
    assert "工具启动: web_search" in rendered
    assert "Supervisor approved task for execution." in rendered


def test_auto_panel_shows_stale_foreign_executor(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_auto_task = None
    cli._current_auto_task_started_at = 0.0
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {"timeline": [], "tasks": []}
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "cli-session-remote",
            "lease_status": "stale",
            "is_stale": True,
            "idle_seconds": 120,
            "scene": "executing",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "Executor: CLI " in rendered
    assert "stale (120s idle, scene=executing)" in rendered


def test_auto_panel_shows_approved_task_waiting_for_claim(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_auto_task = None
    cli._current_auto_task_started_at = 0.0
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {
        "timeline": [],
        "tasks": [
            {
                "task_id": "learn-approved-1",
                "title": "Approved waiting task",
                "task_type": "self_learning",
                "status": "approved",
            }
        ],
    }
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 2,
            "scene": "idle",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "状态: 已放行待认领" in rendered
    assert "Approved waiting task" in rendered
    assert "队列: 监督者已放行该任务，等待活跃 AUTO CLI 认领" in rendered
    assert "执行流: 监督者已放行任务，等待活跃 AUTO CLI 认领" in rendered


def test_auto_panel_shows_running_task_owned_elsewhere(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_auto_task = None
    cli._current_auto_task_started_at = 0.0
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {
        "timeline": [],
        "tasks": [
            {
                "task_id": "learn-running-2",
                "title": "Running elsewhere task",
                "task_type": "self_learning",
                "status": "running",
            }
        ],
    }
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "cli-session-remote",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 6,
            "scene": "learning",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "状态: 他处执行中" in rendered
    assert "Running elsewhere task" in rendered
    assert "队列: 该任务已被其他 AUTO 执行体认领" in rendered
    assert "执行流: 任务正在其他 AUTO 执行体中运行" in rendered


def test_auto_panel_shows_claimed_task_waiting_to_start(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_auto_task = {
        "task_id": "learn-claimed-1",
        "title": "Claimed not started task",
        "task_type": "self_learning",
    }
    cli._current_auto_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {"timeline": [], "tasks": []}
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 1,
            "scene": "learning",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "状态: 已认领待起跑" in rendered
    assert "Claimed not started task" in rendered
    assert "队列: 当前 CLI 已认领该任务，等待进入首个模型或工具回合" in rendered
    assert "近因: 已认领任务，但还没有收到后续执行事件" in rendered
    assert "执行流: 当前 CLI 已认领任务，等待进入首个模型或工具回合" in rendered


def test_auto_panel_shows_waiting_start_cause_after_prompt_enqueued(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_auto_task = {
        "task_id": "learn-claimed-2",
        "title": "Prompt enqueued task",
        "task_type": "self_learning",
    }
    cli._current_auto_task_started_at = 0.0
    cli._last_agent_turn_result = None
    cli._auto_execution_events = [
        {
            "at": "12:00:00",
            "message": "执行提示已注入前台 CLI，等待模型响应",
            "tone": "info",
            "stage": "prompt_enqueued",
        }
    ]
    cli._auto_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {"timeline": [], "tasks": []}
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 1,
            "scene": "learning",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "状态: 已认领待起跑" in rendered
    assert "近因: 执行提示已入队，正在等待首个模型响应" in rendered


def test_auto_panel_shows_claimed_task_waiting_for_writeback(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._agent_running = False
    cli._spinner_text = ""
    cli.session_id = "cli-session-local"
    cli._current_auto_task = {
        "task_id": "learn-writeback-1",
        "title": "Writeback waiting task",
        "task_type": "self_learning",
    }
    cli._current_auto_task_started_at = 0.0
    cli._last_agent_turn_result = {
        "failed": False,
        "partial": False,
        "interrupted": False,
        "error": "",
    }
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""

    monkeypatch.setattr(VoidcubeCLI, "_get_tui_terminal_width", staticmethod(lambda default=(80, 24): 80))
    cli._fetch_supervisor_status = lambda: {"timeline": [], "tasks": []}
    cli._fetch_auto_gateway_status = lambda: {
        "active_cli_executor": {
            "session_id": "cli-session-local",
            "lease_status": "healthy",
            "is_stale": False,
            "idle_seconds": 3,
            "scene": "learning",
        }
    }

    fragments = cli._get_auto_execution_panel_fragments()
    rendered = "".join(text for _, text in fragments)

    assert "状态: 等待回写" in rendered
    assert "Writeback waiting task" in rendered
    assert "队列: 当前 CLI 已完成执行，等待结果回写到任务链" in rendered
    assert "执行流: 当前 CLI 已结束本轮执行，等待写回任务状态" in rendered


def test_sync_auto_supervisor_event_records_latest_timeline_once():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = True
    cli._auto_execution_events = []
    cli._auto_last_supervisor_event_key = ""

    state = {
        "timeline": [
            {
                "created_at": "2026-06-27T03:30:00",
                "event_type": "task_decided",
                "summary": "Approved learning task from supervisor.",
            }
        ]
    }

    cli._sync_auto_supervisor_event(state)
    cli._sync_auto_supervisor_event(state)

    assert len(cli._auto_execution_events) == 1
    assert "监督者 task_decided: Approved learning task from supervisor." in cli._auto_execution_events[0]["message"]


def test_cli_force_quit_marks_body_improvement_task_interrupted(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._agent_running = False
    cli._auto_mode_active = True
    cli._current_auto_task = {
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

    result = cli._force_quit_auto_mode()

    assert result is True
    task_request = next(item for item in requests if item["url"].endswith("/v1/tasks/body-1/decision"))
    assert task_request["data"]["decision"] == "failed"
    assert task_request["data"]["context"]["execution_kind"] == "body_improvement"
    assert "body improvement task" in task_request["data"]["reason"]
    assert cli._current_auto_task is None


def test_auto_command_marks_cli_agent_surface_active(monkeypatch):
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._auto_mode_active = False
    cli.session_id = "cli-session-auto"

    pushed = []
    polled = []
    cycle_calls = []
    presence_refreshes = []

    def fake_cprint(*args, **kwargs):
        del args, kwargs

    def fake_push(scene, *, session_id=None, task_id=None, execution_kind=None):
        pushed.append(
            {
                "scene": scene,
                "session_id": session_id,
                "task_id": task_id,
                "execution_kind": execution_kind,
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
                "governor_mode_active": True,
                "drive_loop_running": True,
                "review_loop_running": True,
                "endogenous_drive_enabled": True,
            }
        )

    def fake_cycle(*, focus=""):
        cycle_calls.append(focus)
        return {"summary": {"planned": 1, "dispatched": 0}}

    def fake_poll():
        polled.append(True)

    def fake_refresh_gateway_cli_presence(*, force=False):
        presence_refreshes.append(force)
        fake_push("executing", session_id=cli.session_id)

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("cli._push_cli_agent_scene", fake_push)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(cli, "_trigger_auto_mode_cycle", fake_cycle)
    monkeypatch.setattr(cli, "_poll_auto_mode_workflow", fake_poll)
    monkeypatch.setattr(cli, "_refresh_gateway_cli_presence", fake_refresh_gateway_cli_presence)
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )

    cli._handle_auto_command("/auto")

    assert cli._auto_mode_active is True
    assert presence_refreshes == [True]
    assert pushed[0]["scene"] == "executing"
    assert pushed[0]["session_id"] == "cli-session-auto"
    assert cycle_calls == [""]
    assert polled == [True]


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

    from cli import _push_cli_agent_scene

    _push_cli_agent_scene("learning", session_id="cli-session-2", task_id="learn-2")

    assert requests[0]["url"].endswith("/admin/activity/touch")
    assert requests[0]["data"]["session_id"] == "cli-session-2"
    assert requests[0]["data"]["metadata"]["scene"] == "learning"


def test_cli_formats_supervisor_status_snapshot():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    lines = cli._format_supervisor_status_snapshot(
        {
            "scene": "planning",
            "title": "义子正在整理任务",
            "metrics": {
                "by_path": {"learning": 2, "maintenance": 1, "evolution": 3},
                "running_count": 1,
                "governance": {
                    "direct_lm_actions": 2,
                    "shadow_recommendations": 1,
                    "priority_updates": 1,
                },
            },
            "active_executions": [
                {
                    "title": "Improve shell body",
                    "execution_kind": "body_improvement",
                }
            ],
            "timeline": [
                {
                    "event_type": "tasks_reviewed",
                    "summary": "Supervisor reviewed 3 task(s): approved. Priority updates: 1.",
                }
            ],
        }
    )

    assert any("Scene: planning" in line for line in lines)
    assert any("learning=2" in line and "evolution=3" in line for line in lines)
    assert any("priority_updates=1" in line for line in lines)
    assert any("Improve shell body" in line for line in lines)
    assert any("tasks_reviewed" in line for line in lines)


def test_cli_formats_gateway_agent_activity_snapshot():
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    lines = cli._format_gateway_agent_activity_snapshot(
        {
            "last_agent_work_at": "2026-06-26T10:00:00",
            "agent_work_count": 3,
            "agent_work": {
                "source_service": "cli_agent",
                "task_id": "body-1",
                "task_identity": {
                    "task_id": "body-1",
                    "summary": "Improve shell body (body_improvement)",
                },
            },
        }
    )

    assert any("Recent Task: Improve shell body (body_improvement)" in line for line in lines)
    assert any("source=cli_agent" in line and "count=3" in line and "task_id=body-1" in line for line in lines)
