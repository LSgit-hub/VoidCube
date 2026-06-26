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
