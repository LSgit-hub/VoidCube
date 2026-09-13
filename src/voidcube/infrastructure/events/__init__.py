"""Infrastructure adapters for publishing domain events."""

from .supervisor_event_sink import SupervisorDomainEventSink

__all__ = ["SupervisorDomainEventSink"]
