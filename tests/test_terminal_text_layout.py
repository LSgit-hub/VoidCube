from types import SimpleNamespace

import pytest

import voidcube.interfaces.cli.terminal_text_layout as ttl
from voidcube.interfaces.cli.terminal_text_layout import (
    append_blank_panel_line,
    append_panel_line,
    display_width,
    modal_panel_max_height,
    pad_to_width,
    panel_box_width,
    terminal_columns,
    terminal_rows,
    terminal_size,
    trim_to_width,
)


def test_trim_and_pad_are_cell_width_stable():
    trimmed = trim_to_width("模型状态很长", 8)
    assert display_width(trimmed) <= 8
    assert display_width(pad_to_width(trimmed, 8)) == 8


class _SizedOutput:
    def __init__(self, columns, rows):
        self._size = (columns, rows)

    def get_size(self):
        return SimpleNamespace(columns=self._size[0], rows=self._size[1])


def _app_with_output(columns, rows, monkeypatch):
    class _FakeApp:
        def __init__(self):
            self.output = _SizedOutput(columns, rows)

    monkeypatch.setattr("prompt_toolkit.application.get_app", lambda: _FakeApp())


def test_terminal_size_uses_active_application(monkeypatch):
    _app_with_output(120, 40, monkeypatch)
    assert terminal_size() == (120, 40)
    assert terminal_columns() == 120
    assert terminal_rows() == 40


def test_terminal_size_falls_back_when_no_application(monkeypatch):
    def _raise():
        raise RuntimeError("no app")

    monkeypatch.setattr("prompt_toolkit.application.get_app", _raise)
    monkeypatch.setattr(
        ttl.shutil, "get_terminal_size", lambda default=None: SimpleNamespace(columns=100, lines=30)
    )
    assert terminal_size() == (100, 30)
    assert terminal_columns() == 100
    assert terminal_rows() == 30


def test_modal_panel_max_height_clamps_to_terminal(monkeypatch):
    _app_with_output(120, 40, monkeypatch)
    assert modal_panel_max_height(max_rows=20) == 20
    _app_with_output(120, 10, monkeypatch)
    assert modal_panel_max_height(max_rows=20) == 6  # terminal_rows(10) - 4 = 6


def test_completion_menu_max_height_clamps_to_terminal(monkeypatch):
    _app_with_output(120, 40, monkeypatch)
    assert ttl.completion_menu_max_height() == 12  # default_max, 40 - 6 >= 12
    _app_with_output(120, 15, monkeypatch)
    assert ttl.completion_menu_max_height() == 9  # 15 - 6
    _app_with_output(120, 8, monkeypatch)
    assert ttl.completion_menu_max_height() == 3  # max(3, min(12, 2))


def test_panel_box_width_clamps_to_terminal_and_content(monkeypatch):
    _app_with_output(80, 24, monkeypatch)
    # Long CJK line forces the box wide but capped at max_width - 2.
    assert panel_box_width("标题", ["中" * 60], min_width=46, max_width=76) <= 76
    # Terminal narrower than min width still leaves an inner gutter.
    _app_with_output(30, 24, monkeypatch)
    assert panel_box_width("标题", ["内容"], min_width=46, max_width=76) <= 30


def test_panel_line_helpers_keep_cell_width_consistent(monkeypatch):
    _app_with_output(60, 24, monkeypatch)
    lines = []
    append_panel_line(lines, "border", "content", "中文字", 10)
    append_blank_panel_line(lines, "border", 10)
    rendered = "".join(text for _style, text in lines)
    for line in rendered.splitlines():
        assert display_width(line) == 10 + 2

