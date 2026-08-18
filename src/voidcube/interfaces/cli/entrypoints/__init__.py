"""CLI command parsing, dispatch, and command handlers.

This package is the canonical owner of the CLI command-entry layer.  The
modules at ``VoidCube_cli.entrypoint_*`` are compatibility aliases only.
"""

from .dispatch import dispatch_cli
from .parser import build_parser

__all__ = ["build_parser", "dispatch_cli"]
