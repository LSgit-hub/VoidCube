"""Auto-open lifecycle adapter for the Supervisor UI."""

from __future__ import annotations

import os
import threading
import webbrowser
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SupervisorUIOpenLifecycleContext:
    """Configuration required to schedule one optional browser open."""

    ui_enabled: bool
    auto_open: bool
    url: str
    delay_seconds: float


def maybe_open_supervisor_ui(
    *,
    context: SupervisorUIOpenLifecycleContext,
) -> None:
    if not context.ui_enabled or not context.auto_open:
        return
    if os.getenv("VOIDCUBE_DESKTOP") == "1":
        return
    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    def open_later() -> None:
        try:
            webbrowser.open(context.url)
        except Exception:
            return

    timer: Any = threading.Timer(
        max(float(context.delay_seconds), 0.0),
        open_later,
    )
    timer.daemon = True
    timer.start()
