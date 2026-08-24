from __future__ import annotations

from collections.abc import Callable

from voidcube.domain.contracts.interaction import ClarificationRequest
from voidcube.interfaces.cli.tui.modal_widgets import ModalWidgetPorts, build_modal_widgets
from voidcube.interfaces.cli.tui.modal_widgets import _wrap_panel_text
from voidcube.interfaces.cli.terminal_text_layout import display_width


def _ports(
    *,
    clarify: dict[str, object] | None = None,
    freetext: bool = False,
    secret: dict[str, object] | None = None,
    picker: dict[str, object] | None = None,
    approval_fragments: Callable[[], list[tuple[str, str]]] = lambda: [],
) -> ModalWidgetPorts:
    return ModalWidgetPorts(
        clarify_state=lambda: clarify,
        clarify_freetext_active=lambda: freetext,
        sudo_state=lambda: None,
        secret_state=lambda: secret,
        approval_state=lambda: None,
        approval_fragments=approval_fragments,
        model_picker_state=lambda: picker,
    )


def _widget_text(widget: object) -> str:
    window = getattr(widget, "content")
    control = getattr(window, "content")
    return "".join(text for _style, text in control.text())


def test_modal_widgets_are_built_from_explicit_read_only_ports() -> None:
    clarification = {
        "request": ClarificationRequest.create("Choose a path", ["First", "Second"]),
        "choices": ["First", "Second"],
        "selected": 2,
    }
    widgets = build_modal_widgets(ports=_ports(clarify=clarification))

    text = _widget_text(widgets.clarify)

    assert "Choose a path" in text
    assert "Other (type your answer)" in text
    assert "class:clarify-selected" in [style for style, _text in widgets.clarify.content.content.text()]


def test_secret_panel_keeps_prompt_help_and_body_separated() -> None:
    widgets = build_modal_widgets(
        ports=_ports(secret={"prompt": "API token", "metadata": {"help": "Stored locally"}})
    )

    text = _widget_text(widgets.secret)

    assert text.index("API token") < text.index("Stored locally")
    assert text.index("Stored locally") < text.index("Enter secret below")


def test_model_picker_limits_the_visible_window_and_shows_position() -> None:
    providers = [
        {"name": f"Provider {index}", "models": ["default"]}
        for index in range(15)
    ]
    widgets = build_modal_widgets(
        ports=_ports(
            picker={
                "stage": "provider",
                "providers": providers,
                "selected": 8,
                "current_model": "default",
                "current_provider": "Provider 0",
            }
        )
    )

    text = _widget_text(widgets.model_picker)

    assert "Provider 8" in text
    assert "9/16" in text
    assert "..." in text


def test_panel_wrapping_uses_terminal_cell_width_for_wide_text() -> None:
    lines = _wrap_panel_text("中文界面与 🎤 状态", 12, subsequent_indent="  ")

    assert lines
    assert all(display_width(line) <= 12 for line in lines)
