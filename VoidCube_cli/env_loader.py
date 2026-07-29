"""Compatibility alias for shared environment loading helpers."""

from __future__ import annotations

import sys

from VoidCube_app import environment as _shared_environment

sys.modules[__name__] = _shared_environment
