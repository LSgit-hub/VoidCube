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


def test_model_picker_limits_the_visible_window_and_shows_position(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "prompt_toolkit.application.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=100, rows=40)
            )
        ),
    )
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


def test_panel_box_width_never_exceeds_the_narrow_overlay(monkeypatch) -> None:
    from types import SimpleNamespace

    from voidcube.interfaces.cli.tui.modal_widgets import _panel_box_width

    monkeypatch.setattr(
        "prompt_toolkit.application.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=20, rows=8)
            )
        ),
    )

    box = _panel_box_width("Voidcube needs your input", ["choice text"] * 12)

    assert 8 <= box <= 20 - 4  # Float insets are left=2 and right=2


def test_panel_box_width_expands_to_fit_wide_content(monkeypatch) -> None:
    from types import SimpleNamespace

    from voidcube.interfaces.cli.tui.modal_widgets import _panel_box_width

    monkeypatch.setattr(
        "prompt_toolkit.application.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=100, rows=40)
            )
        ),
    )

    box = _panel_box_width("T", ["x" * 60])

    assert box >= 62
    assert box <= 78  # default max_width 76 -> inner 74 + borders


def test_secret_panel_wraps_long_help_instead_of_truncating(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "prompt_toolkit.application.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=100, rows=40)
            )
        ),
    )
    help_line = "This help text is deliberately long and should wrap across several panel rows. " * 6
    widgets = build_modal_widgets(
        ports=_ports(secret={"prompt": "API token", "metadata": {"help": help_line}})
    )

    text = _widget_text(widgets.secret)

    assert "deliberately long" in text
    assert not text.rstrip().endswith("...")


def test_model_picker_panel_lines_share_one_width(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "prompt_toolkit.application.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=100, rows=40)
            )
        ),
    )
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
    widths = {
        display_width(line)
        for line in text.split("\n")
        if line and not line.endswith("╮") and not line.endswith("╯")
    }

    assert len(widths) == 1


def test_panel_lines_are_limited_with_a_visible_indicator(monkeypatch) -> None:
    from types import SimpleNamespace

    from voidcube.interfaces.cli.tui.modal_widgets import _limit_panel_lines

    monkeypatch.setattr(
        "prompt_toolkit.application.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=100, rows=10)
            )
        ),
    )
    lines = [
        ("class:a", "one\n"),
        ("class:a", "two\n"),
        ("class:a", "three\n"),
        ("class:a", "four\n"),
        ("class:a", "close\n"),
    ]

    limited = _limit_panel_lines(
        lines,
        3,
        border_style="class:a",
        content_style="class:b",
        box_width=30,
    )

    assert limited[-1] == ("class:a", "close\n")
    assert any("3 more lines" in text for _style, text in limited)
    assert display_width(limited[-3][1]) == 30


def test_scroll_indicator_uses_callers_style_classes() -> None:
    from voidcube.interfaces.cli.tui.modal_widgets import _append_scroll_indicator

    lines: list[tuple[str, str]] = []
    _append_scroll_indicator(
        lines,
        box_width=12,
        border_style="class:sudo-border",
        content_style="class:sudo-text",
    )

    assert lines[0] == ("class:sudo-border", "│")
    assert lines[1][0] == "class:sudo-text"
    assert "..." in lines[1][1]
    assert display_width(lines[1][1]) == 12
    assert lines[2] == ("class:sudo-border", "│\n")


def test_scroll_indicator_defaults_to_clarify_styles_for_backward_compat() -> None:
    from voidcube.interfaces.cli.tui.modal_widgets import _append_scroll_indicator

    lines: list[tuple[str, str]] = []
    _append_scroll_indicator(lines, box_width=10)

    assert lines[0][0] == "class:clarify-border"
    assert lines[1][0] == "class:clarify-choice"
