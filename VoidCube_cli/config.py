"""Compatibility alias for the canonical shared configuration module."""

from __future__ import annotations

import sys

from VoidCube_app import config as _shared_config

sys.modules[__name__] = _shared_config
