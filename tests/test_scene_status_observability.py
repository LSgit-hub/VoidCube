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


def test_dashboard_agent_segment_prefers_supervisor_task_lane():
    rendered = dashboard._format_segment_line(
        {"key": "agent", "icon": "🤖", "name": "API-A"},
        {
            "agent": {
                "scene": "executing",
                "reachable": True,
                "subagent_foreground_count": 1,
                "subagent_focus_tool": "grep",
                "lanes": {
                    "supervisor_task": {
                        "scene": "learning",
                        "reachable": True,
                        "scene_task_id": "learn-supervisor-1",
                        "subagent_foreground_count": 3,
                        "subagent_background_count": 1,
                        "subagent_focus_tool": "read_file",
                    },
                    "user_chat": {
                        "scene": "executing",
                        "reachable": True,
                        "subagent_foreground_count": 1,
                        "subagent_focus_tool": "grep",
                    },
                },
            }
        },
    )

    assert "API-A: learning" in rendered
    assert "task learn-su" in rendered
    assert "SA 3+1" in rendered
    assert "read_file" in rendered
    assert "grep" not in rendered


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


def test_status_scene_bar_prefers_user_chat_lane(monkeypatch, capsys):
    payload = {
        "scenes": {
            "supervisor": {"scene": "idle", "reachable": True},
            "agent": {
                "scene": "learning",
                "reachable": True,
                "scene_task_id": "learn-top-level",
                "subagent_foreground_count": 4,
                "subagent_focus_tool": "read_file",
                "lanes": {
                    "supervisor_task": {
                        "scene": "learning",
                        "reachable": True,
                        "scene_task_id": "learn-supervisor-1",
                        "subagent_foreground_count": 4,
                        "subagent_focus_tool": "read_file",
                    },
                    "user_chat": {
                        "scene": "executing",
                        "reachable": True,
                        "scene_task_id": "user-chat-1",
                        "subagent_foreground_count": 1,
                        "subagent_focus_preview": "grep app.py",
                    },
                },
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
    assert "user-cha" in output
    assert "SA 1" in output
    assert "grep app.py" in output
    assert "read_file" not in output


def test_build_dashboard_uses_supervisor_execution_eligibility_without_local_window_gate(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_gateway_activity",
        lambda: {
            "last_user_request_at": None,
            "last_agent_work_at": None,
            "last_memory_task_at": None,
            "last_autonomous_chain_plan_at": None,
            "last_autonomous_chain_execute_at": None,
        },
    )
    monkeypatch.setattr(
        dashboard,
        "fetch_supervisor_state",
        lambda: {
            "autonomous_observation": {
                "runtime": {
                    "activity_guards": {
                        "checks": {},
                        "idle_seconds": {},
                        "thresholds": {
                            "user_idle_seconds": 600,
                            "memory_idle_seconds": 600,
                            "workflow_idle_seconds": 600,
                        },
                        "user_chain_signal": {"scope": "soft_signal_only", "is_quiet": False},
                    },
                    "eligibility": {
                        "eligible_for_planning": True,
                        "eligible_for_execution": True,
                    },
                }
            }
        },
    )

    built = dashboard.build_dashboard()

    assert built["eligibility"]["can_execute"] is True
    assert built["countdowns"]["autonomous_chain"]["display"] == "continuous"
    assert built["autonomous_chain_policy"]["label"] == "continuous"
    assert built["chain"]["mode"] == "observation_unavailable"


def test_build_dashboard_prefers_supervisor_autonomous_observation_board(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_gateway_activity",
        lambda: {
            "last_user_request_at": None,
            "last_agent_work_at": None,
            "last_memory_task_at": None,
            "last_autonomous_chain_plan_at": None,
            "last_autonomous_chain_execute_at": None,
        },
    )
    monkeypatch.setattr(
        dashboard,
        "fetch_supervisor_state",
        lambda: {
            "autonomous_observation": {
                "counts": {
                    "api_b_backlog": 1,
                    "api_a_ready": 2,
                    "candidates": 3,
                    "writebacks": 1,
                },
                "board": {
                    "headline": "自主链路闭环观测",
                    "current_cards": [
                        {
                            "title": "API-B judgement",
                            "display_status": "当前在途",
                            "observation_role": "api_b_judgement",
                        },
                        {
                            "title": "API-A execution",
                            "display_status": "已观察到",
                            "observation_role": "api_a_execution",
                        },
                    ],
                },
                "chain": {
                    "headline": "自主链路分段观察",
                    "segments": [
                        {
                            "key": "api_b_backlog",
                            "items": [{"title": "Governance backlog task", "display_status": "待审核"}],
                        },
                        {
                            "key": "api_a_ready",
                            "items": [{"title": "Autonomous ready task", "display_status": "待执行"}],
                        },
                        {
                            "key": "api_b_candidates",
                            "items": [{"title": "Candidate decision", "display_status": "API-B 候选判断"}],
                        },
                        {
                            "key": "mem_recent",
                            "items": [{"title": "Mem writeback", "display_status": "已观察到"}],
                        },
                    ],
                },
                "runtime": {
                    "activity_guards": {
                        "checks": {},
                        "idle_seconds": {},
                        "thresholds": {
                            "user_idle_seconds": 600,
                            "memory_idle_seconds": 600,
                            "workflow_idle_seconds": 600,
                        },
                        "user_chain_signal": {"scope": "soft_signal_only", "is_quiet": True},
                    },
                    "eligibility": {
                        "eligible_for_planning": True,
                        "eligible_for_execution": False,
                    },
                },
            }
        },
    )

    built = dashboard.build_dashboard()

    assert built["chain"]["mode"] == "autonomous_chain_board"
    assert built["chain"]["headline"] == "自主链路闭环观测"
    assert built["chain"]["api_b_backlog"] == 1
    assert built["chain"]["api_a_ready"] == 2
    assert built["chain"]["candidates"] == 3
    assert built["chain"]["writebacks"] == 1
    assert built["chain"]["segments_headline"] == "自主链路分段观察"
    assert built["chain"]["current_cards"][0]["title"] == "API-B judgement"
    assert built["chain"]["segments"][0]["label"] == "API-B"
    assert built["chain"]["segments"][0]["title"] == "Governance backlog task"


def test_print_dashboard_uses_autonomous_chain_countdown_keys(monkeypatch, capsys):
    monkeypatch.setattr(
        dashboard,
        "build_dashboard",
        lambda: {
            "services": {"agents": 0, "supervisor": True, "memory": True},
            "chain": {
                "mode": "observation_unavailable",
                "headline": "自主链路观测暂不可用",
                "summary": "等待 Supervisor 观测板数据",
            },
            "countdowns": {
                "user_chain_quiet": {"display": "10s", "met": False, "threshold_s": 600},
                "agent_idle": {"display": "20s", "met": False, "threshold_s": 600},
                "memory_idle": {"display": "30s", "met": False, "threshold_s": 600},
                "autonomous_chain_plan_idle": {"display": "40s", "met": False, "threshold_s": 600},
                "autonomous_chain_execute_idle": {"display": "50s", "met": False, "threshold_s": 600},
                "autonomous_chain": {"display": "continuous", "scope": "soft_signal_only"},
            },
            "eligibility": {
                "can_execute": False,
                "eligible_for_planning": True,
                "eligible_for_execution": False,
            },
            "next_review_cycle_display": "4m00s",
            "autonomous_chain_policy": {"label": "continuous", "scope": "soft_signal_only"},
        },
    )
    monkeypatch.setattr(dashboard, "print_three_segment_status_bar", lambda: None)

    dashboard.print_dashboard()
    output = capsys.readouterr().out

    assert "Plan idle" in output and "40s" in output
    assert "Exec idle" in output and "50s" in output


def test_print_dashboard_shows_chain_segments_headline(monkeypatch, capsys):
    monkeypatch.setattr(
        dashboard,
        "build_dashboard",
        lambda: {
            "services": {"agents": 0, "supervisor": True, "memory": True},
            "chain": {
                "mode": "autonomous_chain_board",
                "headline": "自主链路闭环观测",
                "segments_headline": "自主链路分段观察",
                "api_b_backlog": 1,
                "api_a_ready": 0,
                "candidates": 2,
                "writebacks": 0,
                "current_cards": [],
                "segments": [],
            },
            "countdowns": {
                "user_chain_quiet": {"display": "10s", "met": False, "threshold_s": 600},
                "agent_idle": {"display": "20s", "met": False, "threshold_s": 600},
                "memory_idle": {"display": "30s", "met": False, "threshold_s": 600},
                "autonomous_chain_plan_idle": {"display": "40s", "met": False, "threshold_s": 600},
                "autonomous_chain_execute_idle": {"display": "50s", "met": False, "threshold_s": 600},
                "autonomous_chain": {"display": "continuous", "scope": "soft_signal_only"},
            },
            "eligibility": {
                "can_execute": False,
                "eligible_for_planning": True,
                "eligible_for_execution": False,
            },
            "next_review_cycle_display": "4m00s",
            "autonomous_chain_policy": {"label": "continuous", "scope": "soft_signal_only"},
        },
    )
    monkeypatch.setattr(dashboard, "print_three_segment_status_bar", lambda: None)

    dashboard.print_dashboard()
    output = capsys.readouterr().out

    assert "自主链路分段观察" in output
