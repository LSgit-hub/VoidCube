"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.session`."""

import sys as _sys

from VoidCube_cli.entrypoints import session as _implementation

_sys.modules[__name__] = _implementation
