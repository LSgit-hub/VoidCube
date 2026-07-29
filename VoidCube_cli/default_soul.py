"""Compatibility alias for the canonical default identity template."""

from __future__ import annotations

import sys

from VoidCube_app import default_identity as _default_identity

sys.modules[__name__] = _default_identity
