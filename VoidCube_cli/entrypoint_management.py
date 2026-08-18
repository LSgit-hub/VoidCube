"""Deprecated compatibility alias for :mod:`VoidCube_cli.entrypoints.management`."""

import sys as _sys

from VoidCube_cli.entrypoints import management as _implementation

_sys.modules[__name__] = _implementation
