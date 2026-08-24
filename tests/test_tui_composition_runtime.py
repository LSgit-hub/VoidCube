import os
import threading
import time
from types import SimpleNamespace

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.layout.containers import Window

import voidcube.interfaces.cli.tui.composition_runtime as composition_module
from voidcube.interfaces.cli.tui.composition_runtime import (
    TuiCompositionPorts,
    TuiCompositionRuntime,
    TuiCompositionWidgets,
)


class _SizedRecordingOutput(DummyOutput):
    def __init__(self, columns: int, rows: int) -> None:
        super().__init__()
        self.size = Size(columns, rows)
        self.writes: list[str] = []

    def get_size(self) -> Size:
        return self.size

    def write(self, data: str) -> None:
        self.writes.append(data)


def _window_widgets(*, modal_visible: bool) -> TuiCompositionWidgets:
    return TuiCompositionWidgets(
        sudo_widget=Window(),
        secret_widget=Window(),
        approval_widget=Window(),
        clarify_widget=Window(),
        model_picker_widget=Window(),
        spinner_widget=Window(),
        spacer=Window(),
        status_bar=Window(),
        auto_execution_panel=Window(),
        input_rule_top=Window(),
        image_bar=Window(),
        input_area=Window(),
        input_rule_bot=Window(),
        voice_status_bar=Window(),
        modal_visible=lambda: modal_visible,
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
    assert type(calls[1][1]["layout"].container).__name__ == "FloatContainer"
    assert calls[2:] == [("store", application), ("resize", application)]


def test_composed_tui_runs_with_a_narrow_modal_layout() -> None:
    output = _SizedRecordingOutput(20, 8)
    bindings = KeyBindings()

    with create_pipe_input() as pipe:
        application = TuiCompositionRuntime(
            TuiCompositionPorts(
                cursor=None,
                store_application=lambda _application: None,
                install_resize_cleanup=lambda _application: None,
                input=pipe,
                output=output,
            )
        ).compose(
            key_bindings=bindings,
            widgets=_window_widgets(modal_visible=True),
            extra_widgets=lambda: [],
        )

        @bindings.add("c-q")
        def exit_application(event) -> None:
            event.app.exit()

        thread = threading.Thread(target=application.run)
        thread.start()
        time.sleep(0.05)
        pipe.send_text("\x11")
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert output.writes
    assert type(application.layout.container).__name__ == "FloatContainer"


def test_composed_tui_survives_runtime_resize() -> None:
    output = _SizedRecordingOutput(80, 24)
    bindings = KeyBindings()

    with create_pipe_input() as pipe:
        application = TuiCompositionRuntime(
            TuiCompositionPorts(
                cursor=None,
                store_application=lambda _application: None,
                install_resize_cleanup=lambda _application: None,
                input=pipe,
                output=output,
            )
        ).compose(
            key_bindings=bindings,
            widgets=_window_widgets(modal_visible=False),
            extra_widgets=lambda: [],
        )

        @bindings.add("c-q")
        def exit_application(event) -> None:
            event.app.exit()

        thread = threading.Thread(target=application.run)
        thread.start()
        time.sleep(0.05)
        output.size = Size(20, 8)
        application._on_resize()
        application.invalidate()
        pipe.send_text("\x11")
        thread.join(timeout=2)

    assert not thread.is_alive()


def test_composed_tui_runs_through_real_pty_on_unix() -> None:
    if os.name == "nt":
        import pytest

        pytest.skip("Unix PTY is unavailable on Windows")

    import pty
    from prompt_toolkit.input.defaults import create_input

    master_fd, slave_fd = pty.openpty()
    slave = os.fdopen(slave_fd, "r+", buffering=1)
    output = _SizedRecordingOutput(80, 24)
    bindings = KeyBindings()

    try:
        with create_input(slave) as terminal_input:
            application = TuiCompositionRuntime(
                TuiCompositionPorts(
                    cursor=None,
                    store_application=lambda _application: None,
                    install_resize_cleanup=lambda _application: None,
                    input=terminal_input,
                    output=output,
                )
            ).compose(
                key_bindings=bindings,
                widgets=_window_widgets(modal_visible=True),
                extra_widgets=lambda: [],
            )

            @bindings.add("c-q")
            def exit_application(event) -> None:
                event.app.exit()

            thread = threading.Thread(target=application.run)
            thread.start()
            time.sleep(0.05)
            os.write(master_fd, b"\x11")
            thread.join(timeout=2)

        assert not thread.is_alive()
        assert output.writes
    finally:
        os.close(master_fd)


def test_completion_menu_height_tracks_terminal_budget(monkeypatch) -> None:
    from prompt_toolkit.layout.dimension import Dimension

    captured: dict = {}

    def build_children(**kwargs):
        captured.update(kwargs)
        return [Window()]

    monkeypatch.setattr(composition_module, "build_tui_layout_children", build_children)
    monkeypatch.setattr(
        composition_module,
        "create_tui_application",
        lambda **kwargs: SimpleNamespace(),
    )

    runtime = TuiCompositionRuntime(
        TuiCompositionPorts(
            cursor=None,
            store_application=lambda _application: None,
            install_resize_cleanup=lambda _application: None,
        )
    )
    runtime.compose(
        key_bindings=KeyBindings(),
        widgets=_window_widgets(modal_visible=False),
        extra_widgets=lambda: [],
    )

    menu = captured["completions_menu"]
    # The menu height is re-evaluated per frame against the terminal budget,
    # not fixed at construction time.
    assert callable(menu.content.height)
    height = menu.content.height()
    assert isinstance(height, Dimension)
    assert height.min == 1
    assert height.max == composition_module._completion_menu_max_height()

    # A smaller terminal shrinks the menu cap through the same helper.
    monkeypatch.setattr(
        composition_module,
        "_completion_menu_max_height",
        lambda: 4,
    )
    assert menu.content.height().max == 4
