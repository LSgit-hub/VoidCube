"""Compatibility facade for canonical runtime layout services."""

from VoidCube_app.infrastructure.runtime.layout import (
    LegacyProjectRuntimeLayout,
    RuntimeLayout,
    get_legacy_project_runtime_layout,
    get_runtime_layout,
)

__all__ = [
    "LegacyProjectRuntimeLayout",
    "RuntimeLayout",
    "get_legacy_project_runtime_layout",
    "get_runtime_layout",
]
