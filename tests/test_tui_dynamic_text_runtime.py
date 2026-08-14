import pytest

from VoidCube_cli.i18n import get_i18n, set_locale, t
from VoidCube_cli.tui_dynamic_text_runtime import (
    TuiDynamicTextPorts,
    TuiDynamicTextRuntime,
)


@pytest.fixture(autouse=True)
def _english_locale():
    previous_locale = get_i18n().get_current_locale()
    set_locale("en_US")
    yield
    set_locale(previous_locale)


def _runtime(state):
    return TuiDynamicTextRuntime(
        TuiDynamicTextPorts(
            voice_recording=lambda: state["voice_recording"],
            voice_processing=lambda: state["voice_processing"],
            sudo_active=lambda: state["sudo"],
            secret_active=lambda: state["secret"],
            approval_active=lambda: state["approval"],
            clarify_freetext=lambda: state["clarify_freetext"],
            clarify_active=lambda: state["clarify"],
            command_running=lambda: state["command"],
            command_spinner_frame=lambda: "·",
            command_status=lambda: state["command_status"],
            agent_running=lambda: state["agent"],
            voice_mode=lambda: state["voice_mode"],
            spinner_text=lambda: state["spinner"],
            tool_start_time=lambda: state["tool_start"],
            now=lambda: 125.0,
            agent_spacer_height=lambda: 1,
            spinner_height=lambda: 1,
            sudo_deadline=lambda: 100.0,
            secret_deadline=lambda: 100.0,
            approval_deadline=lambda: 100.0,
            clarify_deadline=lambda: state["clarify_deadline"],
            translate=t,
        )
    )


def _state(**overrides):
    state = {
        "voice_recording": False,
        "voice_processing": False,
        "sudo": False,
        "secret": False,
        "approval": False,
        "clarify_freetext": False,
        "clarify": False,
        "command": False,
        "command_status": "",
        "agent": False,
        "voice_mode": False,
        "spinner": "",
        "tool_start": 0.0,
        "clarify_deadline": 0.0,
    }
    state.update(overrides)
    return state


def test_placeholder_obeys_modal_and_busy_priority():
    state = _state(voice_recording=True, sudo=True)
    runtime = _runtime(state)
    assert runtime.placeholder().startswith("recording")

    state["voice_recording"] = False
    state["sudo"] = False
    state["command"] = True
    state["command_status"] = "Loading"
    assert runtime.placeholder() == "· Loading"


def test_hint_and_spinner_render_countdowns_and_elapsed_time():
    state = _state(sudo=True, spinner="working", tool_start=60.0)
    runtime = _runtime(state)
    assert runtime.hint_fragments() == [
        ("class:hint", "  password hidden · Enter to skip"),
        ("class:clarify-countdown", "  (0s)"),
    ]
    assert runtime.hint_height() == 1
    assert runtime.spinner_fragments() == [("class:hint", "  working  (1m 5s)")]


def test_clarify_freetext_and_idle_spacer_are_projected():
    state = _state(clarify=True, clarify_freetext=True)
    runtime = _runtime(state)
    assert runtime.placeholder() == "type your answer here and press Enter"
    assert runtime.hint_fragments()[0][1] == "  type your answer and press Enter"

    state.update(clarify=False, clarify_freetext=False)
    assert runtime.hint_fragments() == []
    assert runtime.hint_height() == 1


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_state(voice_processing=True), "transcribing..."),
        (_state(sudo=True), "enter password (input hidden), Enter to skip"),
        (_state(secret=True), "enter secret (input hidden), Enter to skip"),
        (_state(command=True), "· processing command..."),
        (_state(agent=True), "agent running... use /cancel to cancel this turn"),
        (_state(voice_mode=True), "type a message, or press Ctrl+B to record"),
    ],
)
def test_placeholder_uses_explicit_english_locale(state, expected):
    assert _runtime(state).placeholder() == expected


def test_dynamic_text_uses_selected_chinese_locale():
    set_locale("zh_CN")
    state = _state(voice_recording=True)
    runtime = _runtime(state)

    assert runtime.placeholder() == "录音中……按 Ctrl+B 停止"

    state.update(voice_recording=False, approval=True)
    assert runtime.hint_fragments()[0] == (
        "class:hint",
        "  ↑/↓ 选择，Enter 确认",
    )
