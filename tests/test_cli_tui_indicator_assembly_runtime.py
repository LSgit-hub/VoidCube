from types import SimpleNamespace

from voidcube.interfaces.cli.tui.image_indicator import CliTuiImageIndicatorPorts
from voidcube.interfaces.cli.tui.indicator_assembly import (
    CliTuiIndicatorAssemblyPorts,
    CliTuiIndicatorAssemblyRuntime,
)


def test_indicator_assembly_maps_dynamic_image_and_status_projections() -> None:
    calls = []
    dynamic_text = SimpleNamespace(
        spinner_fragments=lambda: [("class:hint", "spin")],
        spinner_widget_height=lambda: 2,
        hint_fragments=lambda: [("class:hint", "hint")],
        hint_height=lambda: 1,
    )
    runtime = CliTuiIndicatorAssemblyRuntime(
        CliTuiIndicatorAssemblyPorts(
            dynamic_text=dynamic_text,
            layout_input_rule_height=lambda position: 1 if position == "top" else 2,
            image=CliTuiImageIndicatorPorts(
                attached_images=lambda: [],
                image_counter=lambda: 0,
                format_badges=lambda _paths, _counter: "unused",
            ),
            voice_fragments=lambda: [("class:voice", "voice")],
            voice_visible=lambda: True,
            autonomous_fragments=lambda: [("class:auto", "auto")],
            autonomous_visible=lambda: True,
            status_fragments=lambda: [("class:status", "status")],
            status_visible=lambda: True,
        )
    ).build()

    assert runtime.spinner_fragments() == [("class:hint", "spin")]
    assert runtime.spinner_height() == 2
    assert runtime.hint_fragments() == [("class:hint", "hint")]
    assert runtime.input_rule_height("bottom") == 2
    assert runtime.images_visible() is False
    assert runtime.voice_fragments() == [("class:voice", "voice")]
    assert runtime.autonomous_visible() is True
    assert runtime.status_fragments() == [("class:status", "status")]
