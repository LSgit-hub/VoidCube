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

    pushed = []

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

    monkeypatch.setattr("cli._cprint", fake_cprint)
    monkeypatch.setattr("cli._push_cli_agent_scene", fake_push)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "VoidCube_cli.config.load_config",
        lambda: {"supervisor": {"host": "127.0.0.1", "port": 6002}},
    )

    cli._handle_auto_command("/auto")

    assert cli._auto_mode_active is True
    assert pushed[0]["scene"] == "executing"


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
