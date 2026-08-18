"""Compatibility alias for canonical Supervisor module."""

import sys

try:
    from voidcube.systems.supervisor import endogenous_cognitive_history_summary_service as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import endogenous_cognitive_history_summary_service as _implementation

sys.modules[__name__] = _implementation
