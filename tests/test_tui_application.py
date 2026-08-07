from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window

import VoidCube_cli.tui_application as tui_application


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


def test_create_tui_application_does_not_claim_terminal_copy_or_paste(monkeypatch) -> None:
    monkeypatch.setattr(
        tui_application,
        "Application",
        lambda **kwargs: SimpleNamespace(
            **kwargs,
            _default_bindings=load_key_bindings(),
        ),
    )
    application = tui_application.create_tui_application(
        layout=Layout(Window()),
        key_bindings=KeyBindings(),
        cursor=None,
    )

    sequences = {binding.keys for binding in application._default_bindings.bindings}
    assert (Keys.ControlC,) not in sequences
    assert (Keys.ControlV,) not in sequences


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
