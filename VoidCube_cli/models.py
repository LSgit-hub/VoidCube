"""Compatibility alias for the canonical shared model catalog."""

from __future__ import annotations

import sys

from VoidCube_app import models as _shared_models

sys.modules[__name__] = _shared_models
