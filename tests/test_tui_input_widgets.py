from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.document import Document

from VoidCube_cli.tui_input_widgets import (
    InputWidgetPorts,
    _PlaceholderProcessor,
    build_input_area,
    install_placeholder_processor,
)


def _ports(history_path: str, *, running: bool = False, masked: bool = False) -> InputWidgetPorts:
    return InputWidgetPorts(
        history_path=history_path,
        prompt_fragments=lambda: [("class:prompt", "❯ " )],
        prompt_text=lambda: "❯ ",
        command_available=lambda command: command != "/hidden",
        command_running=lambda: running,
        password_mask_active=lambda: masked,
    )


def test_input_area_uses_explicit_ports_for_history_prompt_and_read_only(tmp_path) -> None:
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt"), running=True))

    assert area.read_only() is True
    assert area.completer._command_allowed("/visible") is True
    assert area.completer._command_allowed("/hidden") is False


def test_input_area_height_stays_within_the_existing_bounds(tmp_path) -> None:
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))
    area.buffer.text = "line one\nline two\nline three"

    assert 1 <= area.window.height() <= 8


def test_placeholder_processor_only_appends_to_an_empty_first_line(tmp_path) -> None:
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))
    install_placeholder_processor(area, placeholder_text=lambda: "ready")
    processor = area.control.input_processors[-1]
    empty = SimpleNamespace(
        document=Document(""),
        fragments=[("class:prompt", "❯ " )],
        lineno=0,
    )
    nonempty = SimpleNamespace(
        document=Document("text"),
        fragments=[("", "text")],
        lineno=0,
    )

    assert processor.apply_transformation(empty).fragments[-1] == ("class:placeholder", "ready")
    assert processor.apply_transformation(nonempty).fragments == [("", "text")]
    assert isinstance(processor, _PlaceholderProcessor)
