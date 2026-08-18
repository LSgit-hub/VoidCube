"""Compatibility alias for canonical detached process spooling."""

import sys

try:
    from voidcube.infrastructure.execution import process_spool_wrapper as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.execution import process_spool_wrapper as _implementation

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())
