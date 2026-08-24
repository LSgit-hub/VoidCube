from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.document import Document

from voidcube.interfaces.cli.tui.input_widgets import (
    InputWidgetPorts,
    _PlaceholderProcessor,
    build_input_area,
    install_placeholder_processor,
)


def _ports(
    history_path: str,
    *,
    running: bool = False,
    masked: bool = False,
    locked: bool = False,
) -> InputWidgetPorts:
    return InputWidgetPorts(
        history_path=history_path,
        prompt_fragments=lambda: [("class:prompt", "❯ " )],
        prompt_text=lambda: "❯ ",
        command_available=lambda command: command != "/hidden",
        command_running=lambda: running,
        password_mask_active=lambda: masked,
        input_locked=lambda: locked,
    )


def test_input_area_uses_explicit_ports_for_history_prompt_and_read_only(tmp_path) -> None:
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt"), running=True))

    assert area.read_only() is True
    assert area.completer._command_allowed("/visible") is True
    assert area.completer._command_allowed("/hidden") is False


def test_selection_modal_locks_input_without_enabling_password_mask(tmp_path) -> None:
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt"), locked=True))

    assert area.read_only() is True


def test_input_area_height_stays_within_the_existing_bounds(tmp_path) -> None:
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))
    area.buffer.text = "line one\nline two\nline three"

    assert 1 <= area.window.height() <= 8


def test_input_area_height_preserves_space_in_a_short_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "voidcube.interfaces.cli.tui.input_widgets.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=80, rows=6)
            )
        ),
    )
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))
    area.buffer.text = "\n".join(f"line {index}" for index in range(10))

    assert area.window.height() == 3


def test_input_area_continuation_lines_wrap_against_full_terminal_width(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "voidcube.interfaces.cli.tui.input_widgets.get_app",
        lambda: SimpleNamespace(
            output=SimpleNamespace(
                get_size=lambda: SimpleNamespace(columns=20, rows=24)
            )
        ),
    )
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))
    area.buffer.text = "a\n" + "x" * 40

    # First line keeps 2 prompt cells; the continuation line wraps at 20.
    assert area.window.height() == 1 + 2


def test_input_area_height_cache_reuses_terminal_query_for_unchanged_text(
    tmp_path,
    monkeypatch,
) -> None:
    queries = 0

    def get_size() -> SimpleNamespace:
        nonlocal queries
        queries += 1
        return SimpleNamespace(columns=80, rows=24)

    monkeypatch.setattr(
        "voidcube.interfaces.cli.tui.input_widgets.get_app",
        lambda: SimpleNamespace(output=SimpleNamespace(get_size=get_size)),
    )
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))
    area.buffer.text = "line one\nline two"

    # prompt_toolkit re-measures the height several times per render; a second
    # call with unchanged text must hit the cache instead of querying the
    # terminal (or re-walking the document) again.
    assert area.window.height() == area.window.height()
    assert queries == 1


def test_input_area_height_cache_tracks_text_changes(
    tmp_path,
    monkeypatch,
) -> None:
    queries = 0

    def get_size() -> SimpleNamespace:
        nonlocal queries
        queries += 1
        return SimpleNamespace(columns=80, rows=24)

    monkeypatch.setattr(
        "voidcube.interfaces.cli.tui.input_widgets.get_app",
        lambda: SimpleNamespace(output=SimpleNamespace(get_size=get_size)),
    )
    area = build_input_area(ports=_ports(str(tmp_path / "history.txt")))

    area.buffer.text = "one line"
    first = area.window.height()
    area.buffer.text = "one line\nsecond line"
    second = area.window.height()

    assert second > first
    # The height re-computes for the new text, but the terminal size is only
    # queried once because both calls land in the same render frame.
    assert queries == 1


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
