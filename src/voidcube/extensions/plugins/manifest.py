"""Versioned plugin manifest and explicit discovery helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PluginManifestError(ValueError):
    """Raised when a plugin manifest is missing or invalid."""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Stable metadata used before an extension entrypoint is imported."""

    name: str
    version: str
    api_version: str
    capabilities: tuple[str, ...] = ()
    entrypoint: str = ""
    description: str = ""
    config_key: str = ""
    data_owner: str = ""
    data_root: str = ""
    tools: dict[str, Any] | None = None
    service: dict[str, Any] | None = None
    web: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "PluginManifest":
        if not isinstance(raw, dict):
            raise PluginManifestError("plugin manifest must be a JSON object")
        name = str(raw.get("name") or "").strip()
        version = str(raw.get("version") or "").strip()
        api_version = str(raw.get("api_version") or "").strip()
        if not name or not version or not api_version:
            raise PluginManifestError(
                "plugin manifest requires name, version, and api_version"
            )
        capabilities = raw.get("capabilities") or []
        if not isinstance(capabilities, list) or any(
            not isinstance(value, str) or not value.strip() for value in capabilities
        ):
            raise PluginManifestError("capabilities must be a list of non-empty strings")
        entrypoint = str(raw.get("entrypoint") or "").strip()
        return cls(
            name=name,
            version=version,
            api_version=api_version,
            capabilities=tuple(dict.fromkeys(value.strip() for value in capabilities)),
            entrypoint=entrypoint,
            description=str(raw.get("description") or "").strip(),
            config_key=str(raw.get("config_key") or "").strip(),
            data_owner=str(raw.get("data_owner") or "").strip(),
            data_root=str(raw.get("data_root") or "").strip(),
            tools=dict(raw["tools"]) if isinstance(raw.get("tools"), dict) else None,
            service=dict(raw["service"]) if isinstance(raw.get("service"), dict) else None,
            web=dict(raw["web"]) if isinstance(raw.get("web"), dict) else None,
        )

    @classmethod
    def from_path(cls, path: Path) -> "PluginManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginManifestError(f"invalid plugin manifest {path}: {exc}") from exc
        return cls.from_mapping(raw)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "api_version": self.api_version,
            "capabilities": list(self.capabilities),
            "entrypoint": self.entrypoint,
        }
        for key in ("description", "config_key", "data_owner", "data_root"):
            value = getattr(self, key)
            if value:
                result[key] = value
        for key in ("tools", "service", "web"):
            value = getattr(self, key)
            if value is not None:
                result[key] = dict(value)
        return result


def discover_plugin_manifests(root: Path) -> tuple[PluginManifest, ...]:
    """Read manifests explicitly; discovery never imports entrypoints."""
    return tuple(
        PluginManifest.from_path(path)
        for path in sorted(root.rglob("plugin.json"))
    )


__all__ = [
    "PluginManifest",
    "PluginManifestError",
    "discover_plugin_manifests",
]
