from voidcube.interfaces.cli.terminal_text_layout import display_width, pad_to_width, trim_to_width


def test_trim_and_pad_are_cell_width_stable():
    trimmed = trim_to_width("模型状态很长", 8)
    assert display_width(trimmed) <= 8
    assert display_width(pad_to_width(trimmed, 8)) == 8
