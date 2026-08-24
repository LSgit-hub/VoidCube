from types import SimpleNamespace

import voidcube.interfaces.cli.tui.runtime_factory as factory_module
from voidcube.interfaces.cli.tui.runtime_factory import (
    TuiRuntimeFactory,
    TuiRuntimeFactoryPorts,
)


def test_factory_connects_paste_keybindings_widgets_and_composition(monkeypatch):
    calls = []

    class FakePaste:
        def __init__(self, ports):
            calls.append(("paste", ports))

        def handle_text_changed(self, buffer):
            calls.append(("text-changed", buffer))

    class FakeKeybindingAssembly:
        def __init__(self, ports):
            calls.append(("keybindings", ports))

        def install(self):
            calls.append("install-keybindings")

    graph = SimpleNamespace(
        input_area="input",
        modal_widgets=SimpleNamespace(
            sudo="sudo",
            secret="secret",
            approval="approval",
            clarify="clarify",
            model_picker="model",
        ),
        indicator_widgets=SimpleNamespace(
            spinner="spinner",
            spacer="spacer",
            status_bar="status",
            autonomous_execution_panel="auto",
            input_rule_top="top",
            image_bar="images",
            input_rule_bottom="bottom",
            voice_status_bar="voice",
        ),
    )

    class FakeWidgetGraph:
        def __init__(self, ports):
            calls.append(("widgets", ports))

        def build(self):
            calls.append("build-widgets")
            return graph

    class FakeComposition:
        def __init__(self, ports):
            calls.append(("composition", ports))

        def compose(self, **kwargs):
            calls.append(("compose", kwargs))
            return "application"

    monkeypatch.setattr(factory_module, "TuiPasteRuntime", FakePaste)
    monkeypatch.setattr(
        factory_module,
        "TuiKeybindingAssemblyRuntime",
        FakeKeybindingAssembly,
    )
    monkeypatch.setattr(factory_module, "TuiWidgetGraphRuntime", FakeWidgetGraph)
    monkeypatch.setattr(factory_module, "TuiCompositionRuntime", FakeComposition)

    registered = []

    def register_extra(bindings, *, input_area):
        registered.append((bindings, input_area))

    result = TuiRuntimeFactory(
        TuiRuntimeFactoryPorts(
            enter=lambda _event: None,
            ctrl_z=lambda _event: None,
            voice_key="c-b",
            voice=lambda _event: None,
            paste=None,
            modal_navigation=None,
            normal_input_active=lambda: True,
            input=None,
            placeholder_text=lambda: "",
            modal=None,
            indicators=None,
            register_extra_keybindings=register_extra,
            composition=None,
            extra_widgets=lambda: ["extra"],
        )
    ).build()

    assert result == "application"
    assert [item for item in calls if isinstance(item, str)] == [
        "install-keybindings",
        "build-widgets",
    ]
    assert len(registered) == 1
    assert registered[0][1] == "input"
    compose_call = next(item for item in calls if item[0] == "compose")
    assert compose_call[1]["extra_widgets"]() == ["extra"]
    assert compose_call[1]["widgets"].input_area == "input"
    assert compose_call[1]["widgets"].status_bar == "status"
