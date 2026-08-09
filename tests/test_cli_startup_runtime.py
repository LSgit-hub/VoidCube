from VoidCube_cli.cli_startup_runtime import CliStartupPorts, CliStartupRuntime


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
