"""Compatibility module for canonical file storage services."""

from __future__ import annotations

import sys

try:
    from voidcube.infrastructure.persistence import file_store as _implementation
except ModuleNotFoundError:
    from src.voidcube.infrastructure.persistence import file_store as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "file_store", _implementation)
