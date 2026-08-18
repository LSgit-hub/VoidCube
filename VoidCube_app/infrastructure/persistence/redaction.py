"""Compatibility alias for canonical persistence redaction."""

import sys

try:
    from voidcube.infrastructure.persistence import redaction as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.persistence import redaction as _implementation

sys.modules[__name__] = _implementation
