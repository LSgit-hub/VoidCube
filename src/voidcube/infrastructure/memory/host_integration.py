"""Translate VoidCube configuration and credentials into Mem host callbacks."""

from __future__ import annotations

from typing import Any

from memai.host_integration import MemHostIntegration, configure_mem_host_integration


def configure_voidcube_mem_host() -> None:
    from ..config.configuration import get_env_value, load_config
    from ..providers.auth import (
        has_usable_secret,
        resolve_api_key_provider_credentials,
    )
    from ..providers.credential_pool import load_pool
    from ...domain.contracts.integration_policy import require_active_integration

    def config_loader() -> dict[str, Any]:
        loaded = load_config()
        return dict(loaded) if isinstance(loaded, dict) else {}

    def credential_resolver(provider: str) -> str:
        credentials = resolve_api_key_provider_credentials(provider) or {}
        if str(credentials.get("auth_mode") or "").strip().lower() == "none":
            return "no-key-required"
        direct = str(credentials.get("api_key") or "").strip()
        if has_usable_secret(direct):
            return direct
        pool = load_pool(provider)
        entry = pool.select() if pool and pool.has_credentials() else None
        if entry is None:
            return ""
        for value in (
            getattr(entry, "runtime_api_key", ""),
            getattr(entry, "access_token", ""),
        ):
            candidate = str(value or "").strip()
            if has_usable_secret(candidate):
                return candidate
        return ""

    configure_mem_host_integration(
        MemHostIntegration(
            load_config=config_loader,
            get_env_value=lambda name: str(get_env_value(name) or ""),
            resolve_provider_credential=credential_resolver,
            validate_integration=require_active_integration,
            is_usable_secret=has_usable_secret,
        )
    )
