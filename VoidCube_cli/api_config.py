"""Compatibility facade for the canonical CLI configuration interface."""

from __future__ import annotations

import sys

try:
    from voidcube.interfaces.cli import configuration as _implementation
except ModuleNotFoundError:
    from src.voidcube.interfaces.cli import configuration as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "api_config", _implementation)
