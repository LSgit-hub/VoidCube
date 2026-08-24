from __future__ import annotations

from voidcube.interfaces.cli.tui.indicator_widgets import IndicatorWidgetPorts, build_indicator_widgets


def test_indicator_widgets_use_only_explicit_view_ports() -> None:
    visible = {"images": True, "voice": False, "autonomous": True, "status": True}
    ports = IndicatorWidgetPorts(
        spinner_fragments=lambda: [("class:hint", "working")],
        spinner_height=lambda: 1,
        hint_fragments=lambda: [("class:hint", "hint")],
        hint_height=lambda: 2,
        input_rule_height=lambda position: 1 if position == "top" else 0,
        image_fragments=lambda: [("class:image-badge", "image")],
        images_visible=lambda: visible["images"],
        voice_fragments=lambda: [("class:voice-status", "voice")],
        voice_visible=lambda: visible["voice"],
        autonomous_fragments=lambda: [("class:auto-panel-text", "auto")],
        autonomous_visible=lambda: visible["autonomous"],
        status_fragments=lambda: [("class:status-bar", "status")],
        status_visible=lambda: visible["status"],
    )

    widgets = build_indicator_widgets(ports=ports)

    assert widgets.spinner.content.text() == [("class:hint", "working")]
    assert widgets.spacer.content.text() == [("class:hint", "hint")]
    assert widgets.input_rule_top.height() == 1
    assert widgets.input_rule_bottom.height() == 0
    assert widgets.image_bar.height() is True
    assert widgets.voice_status_bar.filter() is False
    assert widgets.autonomous_execution_panel.filter() is True
    assert widgets.status_bar.filter() is True
    assert widgets.status_bar.content.wrap_lines() is False
