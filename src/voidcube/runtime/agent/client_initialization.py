"""Agent client bootstrap from explicit runtime ports."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...infrastructure.providers.endpoints import OPENROUTER_BASE_URL

from .client_lifecycle import ChatClientLifecycle


def build_client_kwargs_for_credentials(
    api_key: str,
    base_url: str,
    *,
    provider: str = "",
) -> dict[str, Any]:
    """Build one canonical OpenAI-compatible client configuration."""
    from ...infrastructure.providers.request_headers import (
        resolve_provider_headers,
    )

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
    }
    template = resolve_provider_headers(provider, base_url=base_url)
    # No conversation id exists yet at client construction, so session-scoped
    # headers resolve away here and are attached per request instead.
    if template:
        client_kwargs["default_headers"] = template
    return client_kwargs


@dataclass(frozen=True, slots=True)
class AgentClientInitializationPorts:
    """Inputs and live state readers required to bootstrap an Agent client."""

    requested_api_key: str | None
    requested_base_url: str | None
    provider: str
    model: str
    acp_command: str | None
    acp_args: Sequence[str]
    provider_client_resolver: Callable[..., tuple[Any, Any]]
    lifecycle_factory: Callable[..., ChatClientLifecycle]
    provider_reader: Callable[[], str]
    model_reader: Callable[[], str]
    base_url_reader: Callable[[], str]
    client_factory: Callable[[dict[str, Any]], Any] | None = None


@dataclass(frozen=True, slots=True)
class AgentClientInitializationResult:
    """Structured result returned after the primary client is ready."""

    client_kwargs: Mapping[str, Any]
    api_key: Any
    base_url: Any
    lifecycle: ChatClientLifecycle


class AgentClientInitializationRuntime:
    """Resolve credentials and own initial primary-client construction."""

    def __init__(self, ports: AgentClientInitializationPorts) -> None:
        self.ports = ports

    def initialize(self) -> AgentClientInitializationResult:
        client_kwargs = self._resolve_client_kwargs()
        lifecycle = self._build_lifecycle(client_kwargs)
        try:
            lifecycle.initialize_primary(reason="agent_init")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialize OpenAI-compatible client: {exc}"
            ) from exc

        return AgentClientInitializationResult(
            client_kwargs=dict(client_kwargs),
            api_key=client_kwargs.get("api_key", ""),
            base_url=client_kwargs.get(
                "base_url", self.ports.requested_base_url or ""
            ),
            lifecycle=lifecycle,
        )

    def _resolve_client_kwargs(self) -> dict[str, Any]:
        ports = self.ports
        if ports.requested_api_key and ports.requested_base_url:
            client_kwargs = build_client_kwargs_for_credentials(
                ports.requested_api_key,
                ports.requested_base_url,
                provider=ports.provider,
            )
            if ports.provider == "copilot-acp":
                client_kwargs["command"] = ports.acp_command
                client_kwargs["args"] = list(ports.acp_args)
            return client_kwargs

        routed_client, _ = ports.provider_client_resolver(
            ports.provider or "auto",
            model=ports.model,
        )
        if routed_client is not None:
            client_kwargs: dict[str, Any] = {
                "api_key": routed_client.api_key,
                "base_url": str(routed_client.base_url),
            }
            default_headers = getattr(routed_client, "_default_headers", None)
            if default_headers:
                client_kwargs["default_headers"] = dict(default_headers)
            return client_kwargs

        explicit_provider = (ports.provider or "").strip().lower()
        if explicit_provider and explicit_provider not in (
            "auto",
            "openrouter",
            "custom",
        ):
            raise RuntimeError(
                f"Provider '{explicit_provider}' is set in config.yaml but no API key "
                f"was found. Set the {explicit_provider.upper()}_API_KEY environment "
                "variable, or switch to a different provider with `/model`."
            )
        return {
            "api_key": os.getenv("OPENROUTER_API_KEY", ""),
            "base_url": OPENROUTER_BASE_URL,
            "default_headers": build_client_kwargs_for_credentials(
                "",
                OPENROUTER_BASE_URL,
                provider="openrouter",
            )["default_headers"],
        }

    def _build_lifecycle(
        self,
        client_kwargs: Mapping[str, Any],
    ) -> ChatClientLifecycle:
        return self.ports.lifecycle_factory(
            client_kwargs=client_kwargs,
            provider=self.ports.provider_reader,
            model=self.ports.model_reader,
            base_url=self.ports.base_url_reader,
            client_factory=self.ports.client_factory,
        )
