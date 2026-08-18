"""Compatibility alias for the canonical CLI model picker runtime."""

import sys
try:
    from voidcube.interfaces.cli import model_picker_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.interfaces.cli import model_picker_runtime as _implementation
sys.modules[__name__] = _implementation
