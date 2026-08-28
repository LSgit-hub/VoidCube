"""Host callbacks used by Mem without importing a concrete application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


ConfigLoader = Callable[[], dict[str, Any]]
EnvLoader = Callable[[str], str]
CredentialResolver = Callable[[str], str]
IntegrationValidator = Callable[[str, str, str], None]
SecretValidator = Callable[[str], bool]
ThinkingSink = Callable[[str], None]


def _empty_config() -> dict[str, Any]:
    return {}


def _environment_value(name: str) -> str:
    return os.getenv(name, "")


def _no_credential(_provider: str) -> str:
    return ""


def _allow_integration(_provider: str, _model: str, _base_url: str) -> None:
    return None


def _nonempty_secret(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) < 10:
        return False
    normalized = candidate.lower().replace("_", "-")
    placeholder_markers = (
        "your-key",
        "your-api-key",
        "replace-me",
        "changeme",
        "example-key",
    )
    return not any(marker in normalized for marker in placeholder_markers)


@dataclass(frozen=True, slots=True)
class MemHostIntegration:
    load_config: ConfigLoader = _empty_config
    get_env_value: EnvLoader = _environment_value
    resolve_provider_credential: CredentialResolver = _no_credential
    validate_integration: IntegrationValidator = _allow_integration
    is_usable_secret: SecretValidator = _nonempty_secret
    api_b_thinking_sink: ThinkingSink | None = None


_host_integration = MemHostIntegration()


def configure_mem_host_integration(integration: MemHostIntegration) -> None:
    global _host_integration
    _host_integration = integration


def get_mem_host_integration() -> MemHostIntegration:
    return _host_integration
