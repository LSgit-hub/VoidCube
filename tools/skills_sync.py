"""Compatibility alias for canonical bundled-skill synchronization."""

import sys

try:
    from voidcube.extensions.skills import sync as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.extensions.skills import sync as _implementation

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.sync_skills(quiet=False))
