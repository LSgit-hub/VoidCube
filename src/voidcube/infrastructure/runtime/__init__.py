"""Process/runtime environment adapters."""

from .environment import is_container, is_termux, is_wsl
from .layout import (
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
    "is_container",
    "is_termux",
    "is_wsl",
]
