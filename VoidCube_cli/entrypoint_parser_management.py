"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.parser_management`."""

import sys as _sys

from VoidCube_cli.entrypoints import parser_management as _implementation

_sys.modules[__name__] = _implementation
