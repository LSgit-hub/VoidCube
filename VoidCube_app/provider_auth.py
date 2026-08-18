"""Compatibility module for the canonical Provider authentication adapter.

The implementation lives in ``VoidCube_app.infrastructure.providers.auth``.
This module aliases the loaded module object so existing imports and test
patches continue to address the same functions and private state.
"""

from __future__ import annotations

import sys

from VoidCube_app.infrastructure.providers import auth as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "provider_auth", _implementation)
