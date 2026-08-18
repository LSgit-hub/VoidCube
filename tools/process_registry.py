"""Compatibility alias for the canonical background process registry."""

import importlib
import sys

try:
    _implementation = importlib.import_module(
        "voidcube.infrastructure.execution.process_registry"
    )
except (ModuleNotFoundError, ImportError):
    _implementation = importlib.import_module(
        "src.voidcube.infrastructure.execution.process_registry"
    )

sys.modules[__name__] = _implementation
