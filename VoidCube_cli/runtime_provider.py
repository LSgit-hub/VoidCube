"""Compatibility alias for shared runtime Provider resolution."""

from __future__ import annotations

import sys

from VoidCube_app import runtime_provider as _runtime_provider

sys.modules[__name__] = _runtime_provider
