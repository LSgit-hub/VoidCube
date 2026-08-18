"""CLI command parsing, dispatch, and command handlers.

This package is the canonical owner of the CLI command-entry layer.
"""

from .dispatch import dispatch_cli
from .parser import build_parser

__all__ = ["build_parser", "dispatch_cli"]
