from types import SimpleNamespace

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import Window

import VoidCube_cli.tui_composition_runtime as composition_module
from VoidCube_cli.tui_composition_runtime import (
    TuiCompositionPorts,
    TuiCompositionRuntime,
    TuiCompositionWidgets,
)


def test_composition_runtime_builds_layout_stores_and_installs_application(monkeypatch):
    calls = []
    expected_children = [Window()]
    application = SimpleNamespace()

    def build_children(**kwargs):
        calls.append(("children", kwargs))
        return expected_children

    def create_application(**kwargs):
        calls.append(("application", kwargs))
        return application

    monkeypatch.setattr(composition_module, "build_tui_layout_children", build_children)
    monkeypatch.setattr(composition_module, "create_tui_application", create_application)

    widgets = TuiCompositionWidgets(
        sudo_widget="sudo",
        secret_widget="secret",
        approval_widget="approval",
        clarify_widget="clarify",
        model_picker_widget="model",
        spinner_widget="spinner",
        spacer="spacer",
        status_bar="status",
        auto_execution_panel="auto",
        input_rule_top="top",
        image_bar="images",
        input_area="input",
        input_rule_bot="bottom",
        voice_status_bar="voice",
    )
    runtime = TuiCompositionRuntime(
        TuiCompositionPorts(
            cursor="cursor",
            store_application=lambda value: calls.append(("store", value)),
            install_resize_cleanup=lambda value: calls.append(("resize", value)),
        )
    )

    result = runtime.compose(
        key_bindings=KeyBindings(),
        widgets=widgets,
        extra_widgets=lambda: ["extra"],
    )

    assert result is application
    assert calls[0][0] == "children"
    assert calls[0][1]["extra_widgets"]() == ["extra"]
    assert calls[1][0] == "application"
    assert calls[1][1]["cursor"] == "cursor"
    assert calls[2:] == [("store", application), ("resize", application)]
