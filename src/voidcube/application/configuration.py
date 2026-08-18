"""Canonical runtime access to application configuration.

Persistence and migration still live behind an injected loader during the
CLI-0 transition. Consumers share this runtime object instead of reaching into
the root CLI module and its globals.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import RLock
from typing import Any


Config = dict[str, Any]
ConfigLoader = Callable[[], Config]


class ApplicationConfigRuntime:
    """Own the process-wide normalized application configuration snapshot."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._config: Config | None = None

    def get(self, loader: ConfigLoader | None = None) -> Config:
        """Return the current snapshot, loading it once when necessary."""
        with self._lock:
            if self._config is None:
                if loader is None:
                    raise RuntimeError("application configuration has not been loaded")
                self._config = self._validate(loader())
            return self._config

    def set(self, config: Mapping[str, Any]) -> Config:
        """Replace the runtime snapshot with an explicitly supplied mapping."""
        normalized = self._validate(config)
        with self._lock:
            self._config = normalized
            return normalized

    def reload(self, loader: ConfigLoader) -> Config:
        """Reload persistent configuration while preserving live references."""
        fresh = self._validate(loader())
        with self._lock:
            if self._config is None:
                self._config = fresh
            elif self._config is not fresh:
                self._config.clear()
                self._config.update(fresh)
            return self._config

    def section(
        self,
        name: str,
        *,
        loader: ConfigLoader | None = None,
    ) -> dict[str, Any]:
        """Return one configuration section as an isolated mutable mapping."""
        value = self.get(loader).get(name)
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _validate(config: Mapping[str, Any]) -> Config:
        if not isinstance(config, dict):
            raise TypeError("application configuration loader must return a dict")
        return config


application_config = ApplicationConfigRuntime()


def get_application_config(loader: ConfigLoader | None = None) -> Config:
    return application_config.get(loader)


def set_application_config(config: Mapping[str, Any]) -> Config:
    return application_config.set(config)


def reload_application_config(loader: ConfigLoader) -> Config:
    return application_config.reload(loader)
