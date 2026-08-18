"""Compatibility alias for canonical ANSI output sanitization."""

import sys

try:
    from voidcube.infrastructure.execution import ansi_strip as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import ansi_strip as _implementation

sys.modules[__name__] = _implementation
