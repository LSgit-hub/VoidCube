"""Resolve one turn's model route from explicit runtime data."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...application.model_routing import resolve_turn_route
from ...infrastructure.providers.model_catalog import resolve_fast_mode_overrides
from ...infrastructure.providers.runtime import resolve_runtime_provider


@dataclass(frozen=True, slots=True)
class CliTurnAgentRoutePorts:
    """Routing state supplied by the CLI host."""

    smart_model_routing: Any
    runtime_credentials: Mapping[str, Any]
    service_tier: object


class CliTurnAgentRouteRuntime:
    """Own per-turn model route projection without CLI state access."""

    def __init__(self, ports: CliTurnAgentRoutePorts) -> None:
        self.ports = ports

    def resolve(self, user_message: str) -> dict[str, Any]:
        credentials = self.ports.runtime_credentials
        route = resolve_turn_route(
            user_message,
            self.ports.smart_model_routing,
            {
                "model": credentials.get("model"),
                "api_key": credentials.get("api_key"),
                "base_url": credentials.get("base_url"),
                "provider": credentials.get("provider"),
                "command": credentials.get("command"),
                "args": list(credentials.get("args") or []),
                "credential_pool": credentials.get("credential_pool"),
            },
            runtime_resolver=resolve_runtime_provider,
            env_reader=os.getenv,
        )

        if not self.ports.service_tier:
            route["request_overrides"] = None
            return route

        try:
            overrides = resolve_fast_mode_overrides(route.get("model"))
        except Exception:
            overrides = None
        route["request_overrides"] = overrides
        return route
