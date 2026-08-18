"""Primitive ports used by the shared application runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .events import ApplicationEvent


class EventSink(Protocol):
    def __call__(self, event: ApplicationEvent) -> None: ...


class ApplicationClock(Protocol):
    def now(self) -> datetime: ...


__all__ = ["ApplicationClock", "EventSink"]
