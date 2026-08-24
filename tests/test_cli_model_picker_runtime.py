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
            current_model=lambda: "current-model",
            current_base_url=lambda: "https://current.example/v1",
            current_api_key=lambda: "current-key",
        )
    ), holder


def test_model_picker_moves_from_provider_to_model_stage():
    events = []
    runtime, holder = _runtime(
        {
            "stage": "provider",
            "selected": 1,
            "providers": [{"slug": "one"}, {"slug": "two", "models": ["m2"]}],
        },
        events,
    )

    runtime.submit()

    assert holder["state"]["stage"] == "model"
    assert holder["state"]["provider_data"]["slug"] == "two"
    assert holder["state"]["model_list"] == ["m2"]
    assert events == ["invalidate"]


def test_model_picker_back_and_cancel_keep_navigation_explicit():
    events = []
    runtime, holder = _runtime(
        {
            "stage": "model",
            "selected": 2,
            "providers": [{"slug": "one"}, {"slug": "two"}],
            "provider_data": {"slug": "two"},
            "model_list": ["m1", "m2"],
        },
        events,
    )
    runtime.submit()
    assert holder["state"]["stage"] == "provider"
    assert holder["state"]["selected"] == 1
    assert events == ["invalidate"]

    events.clear()
    holder["state"]["selected"] = 3
    runtime.submit()
    assert events == ["close"]


def test_model_picker_selects_model_and_applies_result_after_closing():
    events = []
    runtime, _ = _runtime(
        {
            "stage": "model",
            "selected": 0,
            "provider_data": {"slug": "target"},
            "model_list": ["target-model"],
            "user_provs": ["saved"],
        },
        events,
    )

    runtime.submit(persist_global=False)

    assert events[0][0] == "switch"
    assert events[0][1]["raw_input"] == "target-model"
    assert events[0][1]["explicit_provider"] == "target"
    assert events[0][1]["is_global"] is False
    assert events[1:] == ["close", ("apply", "result", False)]
