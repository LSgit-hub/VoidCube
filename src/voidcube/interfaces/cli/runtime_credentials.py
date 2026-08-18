"""Resolve and project CLI runtime credentials without owning CLI state."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from VoidCube_app.model_normalization import (
    AGGREGATOR_PROVIDERS,
    normalize_model_for_provider,
)
from VoidCube_app.runtime_provider import (
    format_runtime_provider_error,
    resolve_runtime_provider,
)


@dataclass(frozen=True, slots=True)
class CliRuntimeCredentialsPorts:
    """Provider inputs and current state supplied by the CLI host."""

    requested_provider: Optional[str]
    explicit_api_key: Optional[str]
    explicit_base_url: Optional[str]
    current: Mapping[str, Any]
    resolve_provider: Callable[..., Mapping[str, Any]] = resolve_runtime_provider
    format_error: Callable[[Exception], str] = format_runtime_provider_error
    aggregator_providers: Collection[str] = AGGREGATOR_PROVIDERS


@dataclass(frozen=True, slots=True)
class CliRuntimeCredentialsResolution:
    """Result of one credential refresh attempt."""

    ready: bool
    runtime: Mapping[str, Any]
    model: str
    api_key: str
    base_url: str
    provider: str
    command: Optional[str]
    args: tuple[str, ...]
    credential_pool: Any
    source: Optional[str]
    credentials_changed: bool
    routing_changed: bool
    model_changed: bool
    error: Optional[str] = None


class CliRuntimeCredentialsRuntime:
    """Resolve credentials and calculate the host state transition."""

    def __init__(self, ports: CliRuntimeCredentialsPorts) -> None:
        self.ports = ports

    def resolve(self) -> CliRuntimeCredentialsResolution:
        try:
            runtime = dict(
                self.ports.resolve_provider(
                    requested=self.ports.requested_provider,
                    explicit_api_key=self.ports.explicit_api_key,
                    explicit_base_url=self.ports.explicit_base_url,
                )
            )
        except Exception as error:
            return self._failure(self.ports.format_error(error))

        api_key = runtime.get("api_key")
        base_url = runtime.get("base_url")
        provider = str(runtime.get("provider") or "openrouter")
        if not isinstance(api_key, str) or not api_key:
            has_custom_base = (
                isinstance(base_url, str)
                and bool(base_url)
                and "openrouter.ai" not in base_url
            )
            if has_custom_base:
                api_key = "no-key-required"
            else:
                return self._failure(
                    "Provider resolver returned an empty API key. "
                    "Set OPENROUTER_API_KEY or run: /api"
                )
        if not isinstance(base_url, str) or not base_url:
            return self._failure(
                "Provider resolver returned an empty base URL. "
                "Check your provider config or run: /api"
            )

        current = self.ports.current
        command = runtime.get("command")
        args = tuple(str(arg) for arg in (runtime.get("args") or []))
        current_args = tuple(str(arg) for arg in (current.get("args") or []))
        model = str(current.get("model") or "").strip()
        runtime_model = runtime.get("model")
        if isinstance(runtime_model, str) and runtime_model.strip():
            model = runtime_model.strip()

        model_changed = False
        if provider not in self.ports.aggregator_providers:
            try:
                normalized = normalize_model_for_provider(model, provider)
            except Exception:
                normalized = model
            if normalized and normalized != model:
                model = normalized
                model_changed = True

        if not model:
            return self._failure(
                "No model selected for the active provider. Run: /model"
            )

        credentials_changed = (
            api_key != current.get("api_key")
            or base_url != current.get("base_url")
        )
        routing_changed = (
            provider != current.get("provider")
            or command != current.get("command")
            or args != current_args
        )
        if model != str(current.get("model") or "").strip() and not model_changed:
            model_changed = True

        return CliRuntimeCredentialsResolution(
            ready=True,
            runtime=runtime,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider=provider,
            command=command,
            args=args,
            credential_pool=runtime.get("credential_pool"),
            source=runtime.get("source"),
            credentials_changed=credentials_changed,
            routing_changed=routing_changed,
            model_changed=model_changed,
        )

    def _failure(self, error: str) -> CliRuntimeCredentialsResolution:
        return CliRuntimeCredentialsResolution(
            ready=False,
            runtime={},
            model="",
            api_key="",
            base_url="",
            provider="",
            command=None,
            args=(),
            credential_pool=None,
            source=None,
            credentials_changed=False,
            routing_changed=False,
            model_changed=False,
            error=error,
        )
