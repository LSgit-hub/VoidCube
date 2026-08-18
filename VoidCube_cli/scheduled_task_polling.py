"""Compatibility facade for canonical scheduled-task polling."""

from __future__ import annotations

import sys

try:
    from voidcube.application.scheduling import scheduled_task_polling as _implementation
except ModuleNotFoundError:
    from src.voidcube.application.scheduling import scheduled_task_polling as _implementation

sys.modules[__name__] = _implementation
setattr(sys.modules[__package__], "scheduled_task_polling", _implementation)
