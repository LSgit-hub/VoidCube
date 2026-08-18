"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.provider`."""

import sys as _sys

from VoidCube_cli.entrypoints import provider as _implementation

_sys.modules[__name__] = _implementation
