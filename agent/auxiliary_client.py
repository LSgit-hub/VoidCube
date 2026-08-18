"""Compatibility module alias for the canonical auxiliary provider router."""

import sys

try:
    from voidcube.infrastructure.providers import auxiliary_client as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.providers import auxiliary_client as _implementation

# Alias the module object so monkeypatches and legacy imports operate on the
# canonical implementation's globals rather than a copied facade namespace.
sys.modules[__name__] = _implementation
