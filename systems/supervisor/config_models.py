"""Compatibility module alias for canonical supervisor configuration models."""

import sys

try:
    from voidcube.systems.supervisor import config_models as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import config_models as _implementation

sys.modules[__name__] = _implementation
