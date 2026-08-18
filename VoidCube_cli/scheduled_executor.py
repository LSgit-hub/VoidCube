"""Compatibility facade for the canonical scheduled-task runtime.

Scheduled execution belongs to the application scheduling layer. Keep this
module as a stable import path for CLI integrations until the legacy package
is retired.
"""

try:
    from voidcube.application.scheduling.scheduled_executor import (
        ScheduledTaskExecutorPorts,
        ScheduledTaskExecutorRuntime,
        ScheduledWritebackOutbox,
    )
except (ModuleNotFoundError, ImportError):
    from src.voidcube.application.scheduling.scheduled_executor import (
        ScheduledTaskExecutorPorts,
        ScheduledTaskExecutorRuntime,
        ScheduledWritebackOutbox,
    )

__all__ = [
    "ScheduledTaskExecutorPorts",
    "ScheduledTaskExecutorRuntime",
    "ScheduledWritebackOutbox",
]
