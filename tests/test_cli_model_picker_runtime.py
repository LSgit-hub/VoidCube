from voidcube.interfaces.cli.model_picker_runtime import (
    CliModelPickerPorts,
    CliModelPickerRuntime,
)


def _runtime(state, events):
    holder = {"state": state}
    return CliModelPickerRuntime(
        CliModelPickerPorts(
            state=lambda: holder["state"],
            set_state=lambda value: holder.__setitem__("state", value),
            close_picker=lambda: events.append("close"),
            invalidate=lambda: events.append("invalidate"),
            switch_model=lambda **kwargs: events.append(("switch", kwargs)) or "result",
            apply_switch_result=lambda result, persist: events.append(
                ("apply", result, persist)
            ),
            current_provider=lambda: "current-provider",
            current_base_url=lambda: "https://current.example/v1",
            current_api_key=lambda: "current-key",
        )
    ), holder


def test_model_picker_selects_model_and_applies_result_after_closing():
    events = []
    runtime, _ = _runtime(
        {
            "stage": "model",
            "selected": 0,
            "model_list": ["target-model"],
            "user_provs": ["saved"],
        },
        events,
    )

    runtime.submit(persist_global=False)

    assert events[0][0] == "switch"
    assert events[0][1]["raw_input"] == "target-model"
    assert events[0][1]["current_provider"] == "current-provider"
    assert events[0][1]["is_global"] is False
    # The picker never changes provider: no explicit_provider is forwarded.
    assert "explicit_provider" not in events[0][1]
    assert events[1:] == ["close", ("apply", "result", False)]


def test_model_picker_cancel_row_closes_without_switching():
    events = []
    runtime, _ = _runtime(
        {
            "stage": "model",
            "selected": 1,
            "model_list": ["only-model"],
        },
        events,
    )

    runtime.submit()

    assert events == ["close"]


def test_model_picker_ignores_submit_without_state():
    events = []
    runtime, _ = _runtime(None, events)

    runtime.submit()

    assert events == []
