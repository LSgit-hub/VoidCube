"""CLI command parsing, dispatch, and command handlers.

This package is the canonical owner of the CLI command-entry layer.  The
modules at ``VoidCube_cli.entrypoint_*`` are compatibility aliases only.
"""

from VoidCube_cli.entrypoints.dispatch import dispatch_cli
from VoidCube_cli.entrypoints.parser import build_parser

__all__ = ["build_parser", "dispatch_cli"]
