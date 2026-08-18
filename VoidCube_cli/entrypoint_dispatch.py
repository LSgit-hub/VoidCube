"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.dispatch`."""

import sys as _sys

from VoidCube_cli.entrypoints import dispatch as _implementation

_sys.modules[__name__] = _implementation
