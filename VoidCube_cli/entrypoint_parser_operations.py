"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.parser_operations`."""

import sys as _sys

from VoidCube_cli.entrypoints import parser_operations as _implementation

_sys.modules[__name__] = _implementation
