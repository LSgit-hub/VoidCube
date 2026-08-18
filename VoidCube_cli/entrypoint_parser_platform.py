"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.parser_platform`."""

import sys as _sys

from VoidCube_cli.entrypoints import parser_platform as _implementation

_sys.modules[__name__] = _implementation
