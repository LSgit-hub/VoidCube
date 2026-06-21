"""
Interactive setup helpers for VoidCube CLI.

Provides curses-based and fallback selection UIs used by the provider/model
selection flow and other interactive configuration commands.
"""

from __future__ import annotations

from typing import Optional


def _curses_prompt_choice(
    title: str,
    choices: list[str],
    default: int = 0,
) -> int:
    """Display a curses-based arrow-key selection menu.

    Args:
        title: Header text shown above the choices.
        choices: List of choice strings to display.
        default: Index of the initially-selected choice (0-based).

    Returns:
        Selected index (0-based), or -1 if the user cancelled (Esc).
    """
    import curses

    result_holder: list[int] = [-1]

    def _run(stdscr) -> None:
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)   # selected
            curses.init_pair(2, curses.COLOR_YELLOW, -1)  # header

        cursor = default if 0 <= default < len(choices) else 0
        scroll_offset = 0

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            if max_y < 3 or max_x < 20:
                try:
                    stdscr.addstr(0, 0, "Terminal too small")
                except curses.error:
                    pass
                stdscr.refresh()
                stdscr.getch()
                return

            # Header
            header = f"  {title}"
            try:
                attr = curses.A_BOLD
                if curses.has_colors():
                    attr |= curses.color_pair(2)
                stdscr.addnstr(0, 0, header, max_x - 1, attr)
            except curses.error:
                pass

            # Visible area
            visible_rows = max_y - 2  # header + footer
            if visible_rows < 1:
                visible_rows = 1

            # Clamp cursor and scroll
            if cursor >= len(choices):
                cursor = len(choices) - 1
            if cursor < 0:
                cursor = 0
            if cursor < scroll_offset:
                scroll_offset = cursor
            elif cursor >= scroll_offset + visible_rows:
                scroll_offset = cursor - visible_rows + 1

            # Render choices
            for draw_i, i in enumerate(
                range(scroll_offset, min(len(choices), scroll_offset + visible_rows))
            ):
                y = draw_i + 1
                if y >= max_y:
                    break
                prefix = " → " if i == cursor else "   "
                line = f"{prefix}{choices[i]}"
                attr = curses.A_NORMAL
                if i == cursor:
                    attr = curses.A_BOLD
                    if curses.has_colors():
                        attr |= curses.color_pair(1)
                try:
                    stdscr.addnstr(y, 0, line, max_x - 1, attr)
                except curses.error:
                    pass

            # Footer hint
            footer_y = max_y - 1
            footer = "  ↑↓ navigate  Enter select  Esc cancel"
            try:
                stdscr.addnstr(footer_y, 0, footer, max_x - 1, curses.A_DIM)
            except curses.error:
                pass

            stdscr.refresh()
            key = stdscr.getch()

            if key in (curses.KEY_UP,):
                cursor = (cursor - 1) % len(choices) if choices else 0
            elif key in (curses.KEY_DOWN,):
                cursor = (cursor + 1) % len(choices) if choices else 0
            elif key in (curses.KEY_ENTER, 10, 13):
                result_holder[0] = cursor
                return
            elif key == 27:  # Esc
                return

    curses.wrapper(_run)
    return result_holder[0]
