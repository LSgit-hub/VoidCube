from __future__ import annotations

from pathlib import Path

import pytest

from voidcube.extensions.plugins.manifest import (
    PluginManifest,
    PluginManifestError,
    discover_plugin_manifests,
)
from voidcube.extensions.plugins import registry as plugin_registry
from voidcube.extensions.plugins.manager import PluginManager


def test_memory_manifest_is_versioned_and_discoverable() -> None:
    manifests = discover_plugin_manifests(Path("plugins"))

    by_name = {manifest.name: manifest for manifest in manifests}
    assert by_name["memory"] == PluginManifest(
        name="memory",
        version="1.0.0",
        api_version="1",
        capabilities=("memory",),
        entrypoint="plugins.memory.mem",
    )
    assert by_name["goal_manager"].capabilities == ("tools", "service", "web")
    assert by_name["goal_manager"].service["port"] == 6003


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


def test_user_plugin_home_is_discovered(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    plugin = home / "plugins" / "user_goal"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        """{
          "name": "user_goal",
          "version": "0.1.0",
          "api_version": "1",
          "entrypoint": "plugins.user_goal",
          "capabilities": ["tools"]
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    plugin_registry.reset_scan_cache()
    try:
        names = {descriptor.name for descriptor in plugin_registry.discover_plugin_manifests()}
    finally:
        plugin_registry.reset_scan_cache()

    assert "user_goal" in names
