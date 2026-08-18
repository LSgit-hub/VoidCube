"""Small process-boundary helpers shared by canonical CLI entrypoints."""

from __future__ import annotations

import sys
from pathlib import Path

from ..i18n import t


PROJECT_ROOT = Path(__file__).resolve().parents[5]


def _require_tty(command_name: str) -> None:
    """Reject interactive commands when stdin is redirected or piped."""
    if sys.stdin.isatty():
        return
    error_msg = t(
        "errors.no_tty",
        default=(
            "Voidcube CLI requires an interactive terminal (TTY). "
            "Do not pipe or redirect input."
        ),
    )
    print(error_msg, file=sys.stderr)
    raise SystemExit(1)


__all__ = ["PROJECT_ROOT", "_require_tty"]
