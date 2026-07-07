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

    assert "API-A: 执行中" in rendered
    assert "链路项 learn-12" in rendered
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

    assert "API-A: 自主学习" in rendered
    assert "链路项 learn-su" in rendered
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
    assert "🤖 API-A: 执行中" in output
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
    assert "🤖 API-A: 执行中" in output
    assert "user-cha" in output
    assert "SA 1" in output
    assert "grep app.py" in output
    assert "read_file" not in output


def test_build_dashboard_reads_supervisor_observation_input_without_legacy_guard_shell(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_supervisor_state",
        lambda: {
            "autonomous_observation": {
                "runtime": {
                    "user_chain_signal": {
                        "scope": "soft_signal_only",
                        "is_quiet": False,
                        "active_sessions": 3,
                        "quiet_after_seconds": 900,
                    },
                    "snapshot_source": "live",
                }
            }
        },
    )

    built = dashboard.build_dashboard()

    assert built["observation_input"]["headline"] == "API-B 判断输入"
    assert built["observation_input"]["user_chain_quiet"] is False
    assert built["observation_input"]["user_chain_state"] == "活跃软信号"
    assert built["observation_input"]["active_sessions"] == 3
    assert built["observation_input"]["quiet_after_seconds"] == 900
    assert built["observation_input"]["snapshot_source"] == "live"
    assert built["observation_input"]["scope"] == "soft_signal_only"
    assert built["chain"]["mode"] == "observation_unavailable"


def test_build_dashboard_prefers_supervisor_autonomous_observation_board(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_supervisor_state",
        lambda: {
            "autonomous_observation": {
                "counts": {
                    "api_b_backlog": 1,
                    "api_a_running": 1,
                    "api_a_ready": 2,
                    "candidates": 3,
                    "writebacks": 1,
                },
                "board": {
                    "headline": "API-B 主视角自主闭环总览",
                    "hero_summary": "Supervisor projected hero summary",
                    "primary_focus": {
                        "title": "API-B 判断",
                        "status": "当前在途",
                        "summary": "API-B 正在处理当前链路焦点。",
                    },
                    "hero_pills": [
                        {"key": "focus", "text": "当前落点 · API-B 判断", "tone": "accent"}
                    ],
                },
                "loop": {
                    "stages": [
                        {
                            "key": "api_b_judgement",
                            "label": "API-B 判断",
                            "source_label": "API-B",
                            "status": "active",
                            "status_label": "当前在途",
                            "focus_task": {"title": "API-B 判断", "display_status": "当前在途"},
                        },
                        {
                            "key": "api_a_execution",
                            "label": "API-A 待认领窗口",
                            "source_label": "API-A",
                            "status": "ready",
                            "status_label": "待认领",
                            "focus_task": {"title": "API-A execution", "display_status": "待认领"},
                        },
                    ]
                },
                "chain": {
                    "headline": "自主闭环分段观察",
                    "segments": [
                        {
                            "key": "api_b_backlog",
                            "label": "治理在途投影",
                            "source_label": "API-B",
                            "stage_label": "判断与治理",
                            "items": [{"title": "Governance backlog task", "display_status": "待审核"}],
                        },
                        {
                            "key": "api_a_ready",
                            "label": "待认领窗口投影",
                            "source_label": "API-A",
                            "stage_label": "执行前窗口",
                            "items": [{"title": "Autonomous ready task", "display_status": "待执行"}],
                        },
                        {
                            "key": "api_b_candidates",
                            "label": "候选形成投影",
                            "source_label": "API-B",
                            "stage_label": "刚形成",
                            "items": [{"title": "Candidate decision", "display_status": "候选形成"}],
                        },
                        {
                            "key": "mem_recent",
                            "label": "写回回流投影",
                            "source_label": "Mem",
                            "stage_label": "写回回流",
                            "items": [{"title": "Mem writeback", "display_status": "已观察到"}],
                        },
                    ],
                },
                "runtime": {
                    "user_chain_signal": {"scope": "soft_signal_only", "is_quiet": True},
                    "snapshot_source": "cached",
                },
            }
        },
    )

    built = dashboard.build_dashboard()

    assert built["chain"]["mode"] == "autonomous_chain_board"
    assert built["chain"]["headline"] == "API-B 主视角自主闭环总览"
    assert built["chain"]["hero_summary"] == "Supervisor projected hero summary"
    assert built["chain"]["primary_focus"]["title"] == "API-B 判断"
    assert built["chain"]["hero_pills"][0]["text"] == "当前落点 · API-B 判断"
    assert built["chain"]["api_b_backlog"] == 1
    assert built["chain"]["api_a_running"] == 1
    assert built["chain"]["api_a_ready"] == 2
    assert built["chain"]["candidates"] == 3
    assert built["chain"]["writebacks"] == 1
    assert built["chain"]["segments_headline"] == "自主闭环分段观察"
    assert built["chain"]["loop_stages"][0]["title"] == "API-B 判断"
    assert built["chain"]["loop_stages"][0]["status"] == "当前在途"
    assert built["chain"]["segments"][0]["label"] == "治理在途投影"
    assert built["chain"]["segments"][0]["stage_label"] == "判断与治理"
    assert built["chain"]["segments"][1]["label"] == "待认领窗口投影"
    assert built["chain"]["segments"][0]["title"] == "Governance backlog task"


def test_build_dashboard_does_not_fallback_to_gateway_recent_activity_projection(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_supervisor_state",
        lambda: {
            "autonomous_observation": {
                "runtime": {
                    "user_chain_signal": {"scope": "soft_signal_only", "is_quiet": True},
                    "snapshot_source": "default",
                }
            }
        },
    )

    built = dashboard.build_dashboard()

    assert built["recent_activity"] == {}


def test_build_dashboard_prefers_supervisor_recent_activity_projection(monkeypatch):
    monkeypatch.setattr(dashboard, "fetch_gateway_services", lambda: {"services": {}})
    monkeypatch.setattr(
        dashboard,
        "fetch_supervisor_state",
        lambda: {
            "autonomous_observation": {
                "board": {
                    "recent_activity": {
                        "phase_label": "治理放行",
                        "title": "Supervisor projected summary",
                        "summary": "API-B 已更新 替身改进 的治理判断，并决定是否继续放行。",
                        "source_label": "API-B",
                        "recorded_at": "2026-07-06T10:20:00",
                    }
                },
                "runtime": {
                    "user_chain_signal": {"scope": "soft_signal_only", "is_quiet": True},
                    "snapshot_source": "live",
                },
            }
        },
    )

    built = dashboard.build_dashboard()

    assert built["recent_activity"]["phase_label"] == "治理放行"
    assert built["recent_activity"]["title"] == "Supervisor projected summary"
    assert built["recent_activity"]["summary"] == "API-B 已更新 替身改进 的治理判断，并决定是否继续放行。"
    assert built["recent_activity"]["source_label"] == "API-B"


def test_print_dashboard_shows_api_b_observation_input(monkeypatch, capsys):
    monkeypatch.setattr(
        dashboard,
        "build_dashboard",
        lambda: {
            "services": {"agents": 0, "supervisor": True, "memory": True},
            "chain": {
                "mode": "observation_unavailable",
                "headline": "自主链路观测暂不可用",
                "summary": "监督者尚未提供 API-B 主视角的自主链路读模型。",
            },
            "observation_input": {
                "headline": "API-B 判断输入",
                "user_chain_quiet": True,
                "user_chain_state": "安静软信号",
                "active_sessions": 0,
                "quiet_after_seconds": 600,
                "snapshot_source": "live",
                "scope": "soft_signal_only",
                "summary": "用户链路只作为 API-B 判断让路参考，不展示聊天内容。",
            },
        },
    )
    monkeypatch.setattr(dashboard, "print_three_segment_status_bar", lambda: None)

    dashboard.print_dashboard()
    output = capsys.readouterr().out

    assert "API-B 判断输入" in output
    assert "安静软信号" in output
    assert "仅软感知用户链路" in output


def test_print_dashboard_shows_chain_segments_headline(monkeypatch, capsys):
    monkeypatch.setattr(
        dashboard,
        "build_dashboard",
        lambda: {
            "services": {"agents": 0, "supervisor": True, "memory": True},
            "chain": {
                "mode": "autonomous_chain_board",
                "headline": "API-B 主视角自主闭环总览",
                "segments_headline": "自主闭环分段观察",
                "hero_summary": "Supervisor projected hero summary",
                "primary_focus": {"title": "API-B 判断", "status": "当前在途"},
                "hero_pills": [{"text": "当前落点 · API-B 判断"}],
                "api_b_backlog": 1,
                "api_a_running": 1,
                "api_a_ready": 0,
                "candidates": 2,
                "writebacks": 0,
                "loop_stages": [],
                "segments": [],
            },
            "observation_input": {
                "headline": "API-B 判断输入",
                "user_chain_quiet": False,
                "user_chain_state": "活跃软信号",
                "active_sessions": 2,
                "quiet_after_seconds": 600,
                "snapshot_source": "cached",
                "scope": "soft_signal_only",
                "summary": "用户链路只作为 API-B 判断让路参考，不展示聊天内容。",
            },
        },
    )
    monkeypatch.setattr(dashboard, "print_three_segment_status_bar", lambda: None)

    dashboard.print_dashboard()
    output = capsys.readouterr().out

    assert "自主闭环分段观察" in output
    assert "执行中 1" in output


def test_print_dashboard_shows_recent_autonomous_activity(monkeypatch, capsys):
    monkeypatch.setattr(
        dashboard,
        "build_dashboard",
        lambda: {
            "services": {"agents": 0, "supervisor": True, "memory": True},
            "chain": {
                "mode": "autonomous_chain_board",
                "headline": "API-B 主视角自主闭环总览",
                "segments_headline": "自主闭环分段观察",
                "hero_summary": "Supervisor projected hero summary",
                "primary_focus": {"title": "API-B 判断", "status": "当前在途"},
                "hero_pills": [{"text": "当前落点 · API-B 判断"}],
                "api_b_backlog": 1,
                "api_a_running": 0,
                "api_a_ready": 0,
                "candidates": 2,
                "writebacks": 0,
                "loop_stages": [],
                "segments": [],
            },
            "recent_activity": {
                "phase_label": "治理放行",
                "title": "替身改进验收 (替身改进)",
                "summary": "API-B 已更新 替身改进 的治理判断，并决定是否继续放行。",
                "display_at": "10:15:30",
            },
            "observation_input": {
                "headline": "API-B 判断输入",
                "user_chain_quiet": True,
                "user_chain_state": "安静软信号",
                "active_sessions": 0,
                "quiet_after_seconds": 600,
                "snapshot_source": "live",
                "scope": "soft_signal_only",
                "summary": "用户链路只作为 API-B 判断让路参考，不展示聊天内容。",
            },
        },
    )
    monkeypatch.setattr(dashboard, "print_three_segment_status_bar", lambda: None)

    dashboard.print_dashboard()
    output = capsys.readouterr().out

    assert "最近自主动作" in output
    assert "治理放行" in output
    assert "替身改进验收 (替身改进)" in output
