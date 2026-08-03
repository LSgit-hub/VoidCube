from VoidCube_cli.cli_session_browser_runtime import (
    CliSessionBrowserPorts,
    CliSessionBrowserRuntime,
)


def _runtime(sessions, output):
    return CliSessionBrowserRuntime(
        CliSessionBrowserPorts(
            list_sessions=lambda **_: sessions,
            active_session_id=lambda: "active",
            relative_time=lambda value: f"relative:{value}",
            translate=lambda key, **_: f"translated:{key}",
            emit=output.append,
        )
    )


def test_session_browser_filters_active_session_and_scheduled_sessions_are_query_owned():
    calls = []
    output = []
    runtime = CliSessionBrowserRuntime(
        CliSessionBrowserPorts(
            list_sessions=lambda **kwargs: calls.append(kwargs) or [
                {"id": "active"},
                {"id": "other", "title": "Title"},
            ],
            active_session_id=lambda: "active",
            relative_time=lambda value: str(value),
            translate=lambda key, **_: key,
            emit=output.append,
        )
    )

    assert runtime.list_recent(limit=4) == [{"id": "other", "title": "Title"}]
    assert calls == [{
        "source": "cli",
        "exclude_sources": ["tool"],
        "limit": 4,
        "exclude_id_prefixes": ["scheduled_"],
    }]


def test_session_browser_renders_recent_sessions_and_empty_result_is_silent():
    output = []
    runtime = _runtime(
        [{"id": "other", "title": "A title", "preview": "A preview", "last_active": 123}],
        output,
    )

    assert runtime.show_recent(reason="history", limit=2) is True
    assert any("A title" in line and "relative:123" in line for line in output)
    assert output[-3] == "translated:use_resume_session_id_or_title_to_continue_where_you_left_off"
    assert output[-2] == "  You can also use /resume <number> to resume by the number above!"
    assert output[-1] == ""

    empty = _runtime([], [])
    assert empty.show_recent() is False
