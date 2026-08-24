from __future__ import annotations

from pathlib import Path

import pytest

from voidcube.extensions.plugins.manifest import (
    PluginManifest,
    PluginManifestError,
    discover_plugin_manifests,
)
from voidcube.extensions.plugins.manager import PluginManager


def test_memory_manifest_is_versioned_and_discoverable() -> None:
    manifests = discover_plugin_manifests(Path("plugins"))

    assert manifests == (
        PluginManifest(
            name="memory",
            version="1.0.0",
            api_version="1",
            capabilities=("memory",),
            entrypoint="plugins.memory.mem",
        ),
    )


def test_manifest_rejects_missing_protocol_fields() -> None:
    with pytest.raises(PluginManifestError, match="name, version, and api_version"):
        PluginManifest.from_mapping({"name": "broken"})


def test_register_manifest_only_records_metadata() -> None:
    manager = PluginManager()
    manifest = PluginManifest(
        name="example",
        version="1.2.0",
        api_version="1",
        capabilities=("tools",),
        entrypoint="example.plugin",
    )

    manager.register_manifest(manifest)

    assert manager.get_plugin("example") == manifest.as_dict()
