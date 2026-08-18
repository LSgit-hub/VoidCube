"""Agent client bootstrap from explicit runtime ports."""

from __future__ import annotations

import platform
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...infrastructure.providers.endpoints import OPENROUTER_BASE_URL

from .client_lifecycle import ChatClientLifecycle


_QWEN_CODE_VERSION = "0.14.1"


def build_qwen_portal_headers() -> dict[str, str]:
    """Return the headers required by the Qwen Portal endpoint."""
    user_agent = (
        f"QwenCode/{_QWEN_CODE_VERSION} "
        f"({platform.system().lower()}; {platform.machine()})"
    )
    return {
        "User-Agent": user_agent,
        "X-DashScope-CacheControl": "enable",
        "X-DashScope-UserAgent": user_agent,
        "X-DashScope-AuthType": "qwen-oauth",
    }


def build_client_kwargs_for_credentials(
    api_key: str,
    base_url: str,
) -> dict[str, Any]:
    """Build one canonical OpenAI-compatible client configuration."""
    from ...infrastructure.providers.auxiliary_client import _OR_HEADERS

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": base_url,
    }
    normalized_base_url = (base_url or "").lower()
    if "openrouter" in normalized_base_url:
        client_kwargs["default_headers"] = dict(_OR_HEADERS)
    elif "api.kimi.com" in normalized_base_url:
        client_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
    elif "portal.qwen.ai" in normalized_base_url:
        client_kwargs["default_headers"] = build_qwen_portal_headers()
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
                "variable, or switch to a different provider with `VoidCube model`."
            )
        return {
            "api_key": os.getenv("OPENROUTER_API_KEY", ""),
            "base_url": OPENROUTER_BASE_URL,
            "default_headers": build_client_kwargs_for_credentials(
                "",
                OPENROUTER_BASE_URL,
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
