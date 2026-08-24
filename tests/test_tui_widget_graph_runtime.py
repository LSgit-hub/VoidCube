from __future__ import annotations

from voidcube.interfaces.cli.tui.indicator_widgets import IndicatorWidgetPorts
from voidcube.interfaces.cli.tui.input_widgets import InputWidgetPorts
from voidcube.interfaces.cli.tui.modal_widgets import ModalWidgetPorts
from voidcube.interfaces.cli.tui.widget_graph_runtime import (
    TuiWidgetGraphPorts,
    TuiWidgetGraphRuntime,
)


def test_widget_graph_builds_all_groups_and_installs_placeholder(tmp_path) -> None:
    graph = TuiWidgetGraphRuntime(
        TuiWidgetGraphPorts(
            input=InputWidgetPorts(
                history_path=str(tmp_path / "history.txt"),
                prompt_fragments=lambda: [("class:prompt", "> ")],
                prompt_text=lambda: "> ",
                command_available=lambda _command: True,
                command_running=lambda: False,
                password_mask_active=lambda: False,
            ),
            placeholder_text=lambda: "ready",
            on_text_changed=lambda _buffer: None,
            modal=ModalWidgetPorts(
                clarify_state=lambda: None,
                clarify_freetext_active=lambda: False,
                sudo_state=lambda: None,
                secret_state=lambda: None,
                approval_state=lambda: None,
                approval_fragments=lambda: [],
                model_picker_state=lambda: None,
            ),
            indicators=IndicatorWidgetPorts(
                spinner_fragments=lambda: [],
                spinner_height=lambda: 0,
                hint_fragments=lambda: [],
                hint_height=lambda: 0,
                input_rule_height=lambda _position: 0,
                image_fragments=lambda: [],
                images_visible=lambda: False,
                voice_fragments=lambda: [],
                voice_visible=lambda: False,
                autonomous_fragments=lambda: [],
                autonomous_visible=lambda: False,
                status_fragments=lambda: [],
                status_visible=lambda: False,
            ),
        )
    ).build()

    assert graph.input_area is not None
    assert graph.modal_widgets.clarify is not None
    assert graph.indicator_widgets.status_bar is not None
    assert graph.input_area.control.input_processors[-1].__class__.__name__ == "_PlaceholderProcessor"
