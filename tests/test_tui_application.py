from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window

import voidcube.interfaces.cli.tui.application as tui_application
import voidcube.interfaces.cli.curses_ui as curses_ui


def test_create_tui_application_owns_style_and_framework_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_application(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(tui_application, "Application", fake_application)
    layout = Layout(Window())
    bindings = KeyBindings()
    cursor = object()

    app = tui_application.create_tui_application(
        layout=layout,
        key_bindings=bindings,
        cursor=cursor,
    )

    assert app.layout is layout
    assert captured["key_bindings"] is bindings
    assert captured["full_screen"] is False
    assert captured["mouse_support"] is False
    assert captured["cursor"] is cursor
    style_selectors = {selector for selector, _value in captured["style"].style_rules}
    assert set(tui_application.TUI_STYLE) <= style_selectors


def test_create_tui_application_omits_optional_cursor(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        tui_application,
        "Application",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(**kwargs),
    )

    tui_application.create_tui_application(
        layout=Layout(Window()),
        key_bindings=KeyBindings(),
        cursor=None,
    )

    assert "cursor" not in captured


def test_resize_reflow_cleanup_inflates_cursor_before_original_handler() -> None:
    calls: list[object] = []
    renderer = SimpleNamespace(
        _last_size=SimpleNamespace(columns=80),
        _last_screen=SimpleNamespace(height=4),
        _cursor_pos=SimpleNamespace(x=3, y=5),
        output=SimpleNamespace(get_size=lambda: SimpleNamespace(columns=40)),
    )
    application = SimpleNamespace(
        renderer=renderer,
        _on_resize=lambda: calls.append(renderer._cursor_pos),
    )

    tui_application.install_resize_reflow_cleanup(application)  # type: ignore[arg-type]
    application._on_resize()

    assert renderer._cursor_pos.x == 3
    assert renderer._cursor_pos.y == 9
    assert calls == [renderer._cursor_pos]


def test_resize_reflow_cleanup_fails_open_when_renderer_api_changes() -> None:
    application = SimpleNamespace(_on_resize=lambda: None)

    tui_application.install_resize_reflow_cleanup(application)  # type: ignore[arg-type]

    application._on_resize()


def test_curses_backend_is_optional_and_single_select_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(curses_ui, "curses", None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    assert curses_ui.curses_single_select("Choose", ["first", "second"], default=0) == 1


def test_checklist_uses_zero_based_indices_and_cancel_defaults(monkeypatch) -> None:
    monkeypatch.setattr(curses_ui, "curses", None)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    result = curses_ui.curses_checklist(
        "Choose",
        ["first", "second"],
        {1},
        cancel_returns={0},
    )

    assert result == {1}


def test_empty_curses_choices_are_safe() -> None:
    assert curses_ui.curses_single_select("Choose", []) is None
    assert curses_ui.curses_checklist("Choose", []) == set()
