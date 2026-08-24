"""Terminal-cell width helpers shared by CLI presentation adapters."""

from __future__ import annotations

import shutil


def display_width(text: str) -> int:
    try:
        from prompt_toolkit.utils import get_cwidth

        return get_cwidth(text or "")
    except Exception:
        return len(text or "")


def terminal_size(default: tuple[int, int] = (80, 24)) -> tuple[int, int]:
    """Return (columns, rows) preferring the active prompt_toolkit renderer."""
    try:
        from prompt_toolkit.application import get_app

        size = get_app().output.get_size()
        return size.columns, size.rows
    except Exception:
        size = shutil.get_terminal_size(default)
        return size.columns, size.lines


def terminal_columns(default: tuple[int, int] = (80, 24)) -> int:
    return terminal_size(default)[0]


def terminal_rows(default: tuple[int, int] = (80, 24)) -> int:
    return terminal_size(default)[1]


def trim_to_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text

    ellipsis = "..."
    ellipsis_width = display_width(ellipsis)
    if max_width <= ellipsis_width:
        return ellipsis[:max_width]

    output: list[str] = []
    current_width = 0
    for char in text:
        char_width = display_width(char)
        if current_width + char_width + ellipsis_width > max_width:
            break
        output.append(char)
        current_width += char_width
    return "".join(output).rstrip() + ellipsis


def pad_to_width(text: str, width: int) -> str:
    text = trim_to_width(text, width)
    return text + (" " * max(0, width - display_width(text)))


def wrap_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
    """Wrap text to a terminal-cell width, honoring wide and combining characters."""
    width = max(8, width)
    lines: list[str] = []
    continuation_width = max(1, width - display_width(subsequent_indent))
    for source_line in (text.splitlines() or [""]):
        if not source_line:
            lines.append("")
            continue
        chunks: list[str] = []
        current: list[str] = []
        current_width = 0
        target = width
        for char in source_line:
            char_width = display_width(char)
            if current and current_width + char_width > target:
                chunks.append("".join(current))
                current = []
                current_width = 0
                target = continuation_width
            current.append(char)
            current_width += char_width
        if current:
            chunks.append("".join(current))
        lines.extend([chunks[0]] + [subsequent_indent + chunk for chunk in chunks[1:]])
    return lines or [""]


# ---------------------------------------------------------------------------
# Modal panel construction — shared by the TUI modal overlay (tui/modal_widgets)
# and the shared CLI interaction adapter (interaction_adapter.py).
# ---------------------------------------------------------------------------

def modal_panel_max_height(max_rows: int = 20) -> int:
    """Return the maximum panel body height in rows (min 6, leaves 4 rows headroom)."""
    rows = terminal_rows((100, 20))
    return max(6, min(max_rows, rows - 4))


def completion_menu_max_height(default_max: int = 12, reserved: int = 6) -> int:
    """Return the maximum completion-menu height in rows, bounded by the terminal.

    The menu sits below the input area, which itself may grow up to
    ``min(8, terminal_rows - 3)`` rows, so ``reserved`` rows are kept for the
    input area and status bars before the menu is allowed to expand. The result
    is clamped to at least 3 rows so the menu stays usable on tiny terminals.
    """
    rows = terminal_rows((80, 24))
    return max(3, min(default_max, rows - reserved))


def panel_box_width(
    title: str,
    content_lines: list[str],
    min_width: int = 46,
    max_width: int = 76,
) -> int:
    """Compute the box width for a modal panel from its title and content lines."""
    terminal_columns_count = terminal_columns((100, 20))
    # The modal overlay floats with left=2/right=2 insets and every panel line
    # carries two border cells, so the box must fit into terminal_columns - 6.
    available_inner = max(8, terminal_columns_count - 8)
    longest = max(
        [display_width(title)]
        + [display_width(line) for line in content_lines]
    )
    inner = min(
        max(longest + 4, min_width - 2),
        max_width - 2,
        available_inner,
    )
    return inner + 2


def append_panel_line(
    lines: list[tuple[str, str]],
    border_style: str,
    content_style: str,
    text: str,
    box_width: int,
) -> None:
    lines.extend(
        [
            (border_style, "│ "),
            (content_style, pad_to_width(text, max(0, box_width - 2))),
            (border_style, " │\n"),
        ]
    )


def append_blank_panel_line(
    lines: list[tuple[str, str]],
    border_style: str,
    box_width: int,
) -> None:
    lines.append((border_style, "│" + (" " * box_width) + "│\n"))


__all__ = [
    "append_blank_panel_line",
    "append_panel_line",
    "completion_menu_max_height",
    "display_width",
    "modal_panel_max_height",
    "pad_to_width",
    "panel_box_width",
    "terminal_columns",
    "terminal_rows",
    "terminal_size",
    "trim_to_width",
    "wrap_text",
]
