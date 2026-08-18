"""Compatibility alias for canonical runtime timing thresholds."""

import sys

try:
    from voidcube.domain.tasks import runtime_thresholds as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.domain.tasks import runtime_thresholds as _implementation

sys.modules[__name__] = _implementation
