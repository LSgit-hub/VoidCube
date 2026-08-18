"""Compatibility facade for application observability."""

try:
    from voidcube.infrastructure.observability.logging import (
        COMPONENT_PREFIXES, clear_session_context, set_session_context,
        setup_logging, setup_verbose_logging,
    )
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.observability.logging import (
        COMPONENT_PREFIXES, clear_session_context, set_session_context,
        setup_logging, setup_verbose_logging,
    )

__all__ = [
    "COMPONENT_PREFIXES",
    "clear_session_context",
    "set_session_context",
    "setup_logging",
    "setup_verbose_logging",
]
