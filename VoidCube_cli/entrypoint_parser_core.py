"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.parser_core`."""

import sys as _sys

from VoidCube_cli.entrypoints import parser_core as _implementation

_sys.modules[__name__] = _implementation
