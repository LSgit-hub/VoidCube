"""Frontend-independent plugin registry and lifecycle hook contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PluginManager:
    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., Any]]] = {}
        self._toolsets: dict[str, Any] = {}
        self._command_handlers: dict[str, Callable[..., Any]] = {}
        self._plugins: dict[str, dict[str, Any]] = {}

    def register_hook(self, event: str, callback: Callable[..., Any]) -> None:
        self._hooks.setdefault(event, []).append(callback)

    def invoke_hook(self, event: str, **kwargs: Any) -> None:
        for callback in self._hooks.get(event, []):
            try:
                callback(**kwargs)
            except Exception:
                pass

    def register_toolset(self, name: str, tools: Any) -> None:
        self._toolsets[name] = tools

    def get_toolsets(self) -> dict[str, Any]:
        return self._toolsets

    def register_command_handler(
        self,
        name: str,
        handler: Callable[..., Any],
    ) -> None:
        self._command_handlers[name] = handler

    def get_command_handler(self, name: str) -> Callable[..., Any] | None:
        return self._command_handlers.get(name)

    def register_plugin(self, name: str, info: dict[str, Any]) -> None:
        self._plugins[name] = info

    def register_manifest(self, manifest: Any) -> None:
        """Register metadata without importing or activating its entrypoint."""
        self._plugins[manifest.name] = manifest.as_dict()

    def list_plugins(self) -> dict[str, dict[str, Any]]:
        return dict(self._plugins)

    def get_plugin(self, name: str) -> dict[str, Any] | None:
        return self._plugins.get(name)


_plugin_manager = PluginManager()


def get_plugin_manager() -> PluginManager:
    return _plugin_manager


def invoke_hook(event: str, **kwargs: Any) -> None:
    _plugin_manager.invoke_hook(event, **kwargs)
