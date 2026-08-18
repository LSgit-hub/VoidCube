"""Compatibility facade for canonical runtime layout services."""

try:
    from voidcube.infrastructure.runtime.layout import (
        LegacyProjectRuntimeLayout, RuntimeLayout,
        get_legacy_project_runtime_layout, get_runtime_layout,
    )
except (ModuleNotFoundError, ImportError):
    from src.voidcube.infrastructure.runtime.layout import (
        LegacyProjectRuntimeLayout, RuntimeLayout,
        get_legacy_project_runtime_layout, get_runtime_layout,
    )

__all__ = [
    "LegacyProjectRuntimeLayout",
    "RuntimeLayout",
    "get_legacy_project_runtime_layout",
    "get_runtime_layout",
]
