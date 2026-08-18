"""Compatibility facade for the canonical plugin manager."""

try:
    from voidcube.extensions.plugins.manager import (
        PluginManager,
        get_plugin_manager,
        invoke_hook,
    )
except ModuleNotFoundError:
    from src.voidcube.extensions.plugins.manager import (
        PluginManager,
        get_plugin_manager,
        invoke_hook,
    )

__all__ = ["PluginManager", "get_plugin_manager", "invoke_hook"]
