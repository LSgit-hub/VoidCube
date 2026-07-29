"""Shared, user-interface-independent application services for VoidCube."""

from VoidCube_app.configuration import (
    application_config,
    get_application_config,
    reload_application_config,
    set_application_config,
)

__all__ = [
    "application_config",
    "get_application_config",
    "reload_application_config",
    "set_application_config",
]
