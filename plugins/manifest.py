"""Compatibility facade for canonical plugin manifest contracts."""

try:
    from voidcube.extensions.plugins.manifest import (
        PluginManifest,
        PluginManifestError,
        discover_plugin_manifests,
    )
except ModuleNotFoundError:
    from src.voidcube.extensions.plugins.manifest import (
        PluginManifest,
        PluginManifestError,
        discover_plugin_manifests,
    )

__all__ = [
    "PluginManifest",
    "PluginManifestError",
    "discover_plugin_manifests",
]
