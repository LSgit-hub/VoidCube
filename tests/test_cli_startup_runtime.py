from voidcube.interfaces.cli.lifecycle.startup import (
    CliStartupPorts,
    CliStartupRuntime,
    render_compact_history_panel,
)


def _runtime(calls, state):
    return CliStartupRuntime(
        CliStartupPorts(
            terminal_lines=lambda: 5,
            write_blank_lines=lambda count: calls.append(("blank", count)),
            show_banner=lambda: calls.append("banner"),
            resumed=lambda: state["resumed"],
            preload_resumed_session=lambda: calls.append("preload") or state["preload"],
            display_resumed_history=lambda: calls.append("history"),
            recent_sessions=lambda: state["sessions"],
            history_limit=lambda: state.get("history_limit", 4),
            terminal_width=lambda: 80,
            render_history_panel=lambda lines: calls.append(("panel", lines)),
            tools_count=lambda: 3,
            skills_count=lambda: 2,
            session_id=lambda: "current",
            preloaded_skills=lambda: state["skills"],
            startup_skills_line_shown=lambda: state["skills_shown"],
            set_startup_skills_line_shown=lambda value: state.__setitem__(
                "skills_shown", value
            ),
            accent_hex=lambda: "#fff",
            emit=lambda value: calls.append(("emit", value)),
        )
    )


def test_startup_runtime_orders_resume_history_and_summary():
    calls = []
    state = {
        "resumed": True,
        "preload": True,
        "sessions": [
            {"id": "current", "title": "ignored"},
            {"id": "old", "title": "Earlier"},
        ],
        "skills": ["shell"],
        "skills_shown": False,
    }

    _runtime(calls, state).run()

    assert calls[:4] == [("blank", 4), "banner", "preload", "history"]
    assert ("panel", ["[bold #fff]历史会话列表[/]", "", "  1.ID: old | Earlier"]) in calls
    assert ("emit", "[#FFF8DC]3 个工具 · 2 技能 · 当前会话: current[/]") in calls
    assert state["skills_shown"] is True


def test_startup_runtime_uses_welcome_and_no_history_for_new_session():
    calls = []
    state = {
        "resumed": False,
        "preload": False,
        "sessions": [],
        "skills": [],
        "skills_shown": False,
    }

    _runtime(calls, state).run()

    assert "preload" not in calls
    assert ("emit", "[dim]暂无对话历史[/]") in calls
    assert ("emit", "[#FFF8DC]3 个工具 · 2 技能 · 当前会话: current[/]") in calls


def test_history_panel_does_not_reserve_unused_height(monkeypatch):
    captured = {}

    class FakePanel:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    class FakeConsole:
        def print(self, value):
            captured["panel"] = value

    monkeypatch.setattr("rich.panel.Panel", FakePanel)

    render_compact_history_panel(FakeConsole(), ["标题", "列表行"])

    assert captured["args"] == ("标题\n列表行",)
    assert captured["kwargs"] == {"border_style": "dim", "padding": (0, 1)}


def test_startup_runtime_uses_configured_history_limit():
    calls = []
    state = {
        "resumed": False,
        "preload": False,
        "sessions": [
            {"id": "one", "preview": "one"},
            {"id": "two", "preview": "two"},
            {"id": "three", "preview": "three"},
        ],
        "history_limit": 2,
        "skills": [],
        "skills_shown": False,
    }

    _runtime(calls, state).run()

    panel = next(value for value in calls if value[0] == "panel")
    assert len(panel[1]) == 4
