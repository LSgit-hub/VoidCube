#!/usr/bin/env python3
"""
VoidCube CLI UI Components

Contains UI rendering, ANSI color handling, and rich formatting utilities.
"""

import sys
import shutil
from contextlib import contextmanager
from typing import Callable

from rich.console import Console
from rich.text import Text as _RichText
from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI

from VoidCube_cli.style import ACCENT


_ACCENT_ANSI_DEFAULT = "\033[1;38;2;105;217;232m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


def _hex_to_ansi_bold(hex_color: str) -> str:
    """Convert a hex color like '#268bd2' to a bold true-color ANSI escape."""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"\033[1;38;2;{r};{g};{b}m"
    except (ValueError, IndexError):
        return _ACCENT_ANSI_DEFAULT


def _accent_hex() -> str:
    """Return the single built-in accent color."""
    return ACCENT


def _rich_text_from_ansi(text: str) -> _RichText:
    """Safely render assistant/tool output that may contain ANSI escapes.

    Using Rich Text.from_ansi preserves literal bracketed text like
    ``[not markup]`` while still interpreting real ANSI color codes.
    """
    return _RichText.from_ansi(text or "")


def _cprint(text: str):
    """Print ANSI-colored text through prompt_toolkit's native renderer.

    Raw ANSI escapes written via print() are swallowed by patch_stdout's
    StdoutProxy.  Routing through print_formatted_text(ANSI(...)) lets
    prompt_toolkit parse the escapes and render real colors.
    """
    _pt_print(_PT_ANSI(text))


class ChatConsole:
    """Route Rich output through prompt_toolkit's active renderer."""

    def __init__(self, *, emit: Callable[[str], None] = _cprint):
        from io import StringIO

        self._buffer = StringIO()
        self._inner = Console(
            file=self._buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
        )
        self._emit = emit

    def print(self, *args, **kwargs):
        self._buffer.seek(0)
        self._buffer.truncate()
        self._inner.width = shutil.get_terminal_size((80, 24)).columns
        self._inner.print(*args, **kwargs)
        output = self._buffer.getvalue()
        for line in output.rstrip("\n").split("\n"):
            self._emit(line)

    @contextmanager
    def status(self, *_args, **_kwargs):
        """Expose the Rich status protocol without a second spinner."""
        yield self


_ACCENT = _hex_to_ansi_bold(ACCENT)
