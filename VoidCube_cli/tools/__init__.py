"""
VoidCube CLI Tool Definitions.

This package contains JSON Schema definitions for tools that can be
registered with the VoidCube agent runtime. Each tool module exports:

    TOOL_SCHEMA: dict   — JSON Schema for the tool's function definition
    register(): None    — registers the tool with the plugin manager

The runtime reads these schemas to generate the system prompt.
"""

from typing import Dict, Any, List

# Registry of all tool schemas defined in this package.
# Populated by each tool module's register() call.
_registry: Dict[str, Dict[str, Any]] = {}


def register_tool(name: str, schema: Dict[str, Any]) -> None:
    """Register a tool schema in the global registry."""
    _registry[name] = schema


def get_tool_schema(name: str) -> Dict[str, Any]:
    """Get a registered tool schema by name."""
    return _registry.get(name, {})


def list_tools() -> List[str]:
    """List all registered tool names."""
    return sorted(_registry.keys())


def get_all_schemas() -> Dict[str, Dict[str, Any]]:
    """Get all registered tool schemas."""
    return dict(_registry)
