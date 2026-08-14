import pytest

from VoidCube_cli.cli_chat_error_runtime import (
    CliChatErrorPorts,
    CliChatErrorRuntime,
)
from VoidCube_cli.i18n import get_i18n, set_locale, t


@pytest.fixture(autouse=True)
def _english_locale():
    previous_locale = get_i18n().get_current_locale()
    set_locale("en_US")
    yield
    set_locale(previous_locale)


def _runtime(events, **overrides):
    values = {
        "autonomous_timeout_reported": False,
        "autonomous_task_run_id": "",
        "autonomous_timeout_writeback_succeeded": False,
        "current_autonomous_task": lambda: None,
        "set_last_agent_turn_result": lambda result: events.append(("result", result)),
        "should_emit": lambda: True,
        "translate": t,
        "emit": lambda text: events.append(("emit", text)),
    }
    values.update(overrides)
    return CliChatErrorRuntime(CliChatErrorPorts(**values))


def test_chat_error_runtime_projects_normal_error_and_last_result():
    events = []
    result = _runtime(events).handle(ValueError("bad"))

    assert result["error"] == "bad"
    assert events[0] == ("result", result)
    assert events[1] == ("emit", "Error: bad")


def test_chat_error_runtime_marks_timeout_task_writeback_failure():
    events = []
    result = _runtime(
        events,
        autonomous_timeout_reported=True,
        autonomous_task_run_id="run-1",
        autonomous_timeout_writeback_succeeded=False,
    ).handle(RuntimeError("transport"))

    assert result["interrupted"] is True
    assert result["error"] == "Autonomous task timed out after 30 minutes."
    assert result["autonomous_task_run_id"] == "run-1"
    assert events[0][1] is result


def test_chat_error_runtime_does_not_emit_when_host_suppresses_scrollback():
    events = []
    _runtime(events, should_emit=lambda: False).handle(RuntimeError("hidden"))

    assert events and events[0][0] == "result"
    assert not any(event[0] == "emit" for event in events)


def test_chat_error_runtime_uses_selected_chinese_locale():
    set_locale("zh_CN")
    events = []

    result = _runtime(
        events,
        autonomous_timeout_reported=True,
    ).handle(RuntimeError("transport"))

    assert result["error"] == "自主任务运行超过 30 分钟，已超时。"
    assert events[1] == ("emit", "错误：transport")
