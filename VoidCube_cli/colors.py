"""Shared ANSI color utilities for Voidcube CLI modules."""

import os
import sys

from VoidCube_cli.style import DANGER, GOOD, INFO, MUTED, PRIMARY, SECONDARY, TEXT, WARN


def _truecolor(hex_color: str, *, bold: bool = False, dim: bool = False) -> str:
    """Translate a theme token into a true-color ANSI foreground sequence."""
    red = int(hex_color[1:3], 16)
    green = int(hex_color[3:5], 16)
    blue = int(hex_color[5:7], 16)
    attributes = []
    if bold:
        attributes.append("1")
    if dim:
        attributes.append("2")
    attributes.append(f"38;2;{red};{green};{blue}")
    return f"\033[{';'.join(attributes)}m"


def should_use_color() -> bool:
    """Return True when colored output is appropriate.

    Respects the NO_COLOR environment variable (https://no-color.org/)
    and TERM=dumb, in addition to the existing TTY check.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return True


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = _truecolor(MUTED, dim=True)
    RED = _truecolor(DANGER)
    GREEN = _truecolor(GOOD)
    YELLOW = _truecolor(WARN)
    BLUE = _truecolor(INFO)
    MAGENTA = _truecolor(SECONDARY)
    CYAN = _truecolor(PRIMARY)
    TEXT = _truecolor(TEXT)


def color(text: str, *codes) -> str:
    """Apply color codes to text (only when color output is appropriate)."""
    if not should_use_color():
        return text
    return "".join(codes) + text + Colors.RESET
