"""Compatibility facade for the canonical extension tool registry."""

try:
    from voidcube.extensions.tools.registry import ToolRegistry, registry, tool_error
except ModuleNotFoundError:
    from src.voidcube.extensions.tools.registry import ToolRegistry, registry, tool_error

__all__ = ["ToolRegistry", "registry", "tool_error"]
