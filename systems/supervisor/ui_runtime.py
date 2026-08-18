"""Compatibility alias for the canonical Supervisor UI runtime."""

# The canonical endpoint remains `return HTMLResponse(load_supervisor_ui_html())`.

import sys

try:
    from voidcube.systems.supervisor import ui_runtime as _implementation
except (ModuleNotFoundError, ImportError):
    from src.voidcube.systems.supervisor import ui_runtime as _implementation

sys.modules[__name__] = _implementation
