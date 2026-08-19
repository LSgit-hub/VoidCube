"""Translate VoidCube configuration and credentials into Mem host callbacks."""

from __future__ import annotations

from typing import Any

from memai.host_integration import MemHostIntegration, configure_mem_host_integration


def configure_voidcube_mem_host() -> None:
    from ..config.configuration import get_env_value, load_config
    from ..providers.auth import has_usable_secret
    from ..providers.runtime import resolve_runtime_provider
    from ...domain.contracts.integration_policy import require_active_integration

    def config_loader() -> dict[str, Any]:
        loaded = load_config()
        return dict(loaded) if isinstance(loaded, dict) else {}

    def credential_resolver(provider: str) -> str:
        try:
            runtime = resolve_runtime_provider(requested=provider)
        except Exception:
            return ""
        api_key = str(runtime.get("api_key") or "").strip()
        return api_key if api_key == "no-key-required" or has_usable_secret(api_key) else ""

    configure_mem_host_integration(
        MemHostIntegration(
            load_config=config_loader,
            get_env_value=lambda name: str(get_env_value(name) or ""),
            resolve_provider_credential=credential_resolver,
            validate_integration=require_active_integration,
            is_usable_secret=has_usable_secret,
        )
    )
