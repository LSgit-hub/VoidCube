"""Compatibility alias for the canonical Agent runner."""

import importlib
import sys

try:
    _implementation = importlib.import_module("voidcube.runtime.agent.runner")
except (ModuleNotFoundError, ImportError):
    _implementation = importlib.import_module("src.voidcube.runtime.agent.runner")

sys.modules[__name__] = _implementation

if __name__ == "__main__":
    _implementation.main()
