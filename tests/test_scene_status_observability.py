from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from VoidCube_cli.ops import dashboard
from VoidCube_cli import status as cli_status


class _FakeUrlopenResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


def test_dashboard_agent_segment_includes_subagent_summary():
    rendered = dashboard._format_segment_line(
        {"key": "agent", "icon": "🤖", "name": "API-A"},
        {
            "agent": {
                "scene": "executing",
                "reachable": True,
                "scene_task_id": "learn-12345678",
                "subagent_foreground_count": 2,
                "subagent_background_count": 1,
                "subagent_focus_tool": "read_file",
            }
        },
    )

    assert "API-A: executing" in rendered
    assert "task learn-12" in rendered
    assert "SA 2+1" in rendered
    assert "read_file" in rendered


def test_status_scene_bar_includes_subagent_summary(monkeypatch, capsys):
    payload = {
        "scenes": {
            "supervisor": {"scene": "idle", "reachable": True},
            "agent": {
                "scene": "executing",
                "reachable": True,
                "scene_task_id": "learn-12345678",
                "subagent_foreground_count": 2,
                "subagent_background_count": 1,
                "subagent_focus_preview": "scan workspace",
            },
            "executor": {"scene": "idle", "reachable": True},
        }
    }

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=0: _FakeUrlopenResponse(payload),
    )

    cli_status._print_three_segment_scene_bar()

    output = capsys.readouterr().out
    assert "🤖 API-A: executing" in output
    assert "SA 2+1" in output
    assert "scan workspace" in output


def test_build_dashboard_uses_supervisor_execution_eligibility_without_local_window_gate(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_gateway_activity",
        lambda: {
            "last_user_request_at": None,
            "last_agent_work_at": None,
            "last_memory_task_at": None,
            "last_self_evolution_plan_at": None,
            "last_self_evolution_execute_at": None,
        },
    )
    monkeypatch.setattr(dashboard, "fetch_supervisor_state", lambda: {})
    monkeypatch.setattr(dashboard, "fetch_supervisor_tasks", lambda status_filter="": {"tasks": []})
    monkeypatch.setattr(
        dashboard,
        "fetch_idle_window",
        lambda task_family="general_self_evolution": {
            "checks": {"in_execution_window": True, "has_user_idle": False},
            "idle_seconds": {},
            "thresholds": {
                "user_idle_seconds": 600,
                "memory_idle_seconds": 600,
                "workflow_idle_seconds": 600,
            },
            "decisions": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
        },
    )

    built = dashboard.build_dashboard()

    assert built["eligibility"]["can_execute"] is True
    assert built["countdowns"]["execution_window"]["display"] == "always on"
    assert built["execution_window"]["label"] == "continuous"
