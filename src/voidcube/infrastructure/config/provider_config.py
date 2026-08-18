"""Provider configuration service used by CLI and non-interactive callers.

This module owns provider-pool persistence, model catalog refresh, credential
status reporting, and route-specific configuration updates.  It deliberately
contains no terminal I/O; the interactive wizard belongs to the CLI interface.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any


def _config_module():
    from . import configuration as config
    return config


def _provider_auth_module():
    try:
        from voidcube.infrastructure.providers import auth
    except (ModuleNotFoundError, ImportError):
        from voidcube.infrastructure.providers import auth  # pragma: no cover
    return auth


def load_current_config() -> dict[str, Any]:
    """Load the persisted VoidCube configuration."""
    try:
        value = _config_module().load_config()
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_config(config: dict[str, Any]) -> bool:
    """Persist configuration and return whether the operation succeeded."""
    try:
        _config_module().save_config(config)
        return True
    except Exception:
        return False


def save_env_value(key: str, value: str) -> bool:
    try:
        _config_module().save_env_value(key, value)
        return True
    except Exception:
        return False


def provider_key_from_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-") or "provider"


def provider_model_catalog(provider_cfg: dict[str, Any]) -> list[str]:
    catalog = provider_cfg.get("model_catalog")
    raw_models = catalog.get("models") if isinstance(catalog, dict) else []
    if not isinstance(raw_models, list):
        return []
    return list(dict.fromkeys(str(item or "").strip() for item in raw_models if str(item or "").strip()))


def provider_pool_api_key(provider_cfg: dict[str, Any]) -> str:
    auth = _provider_auth_module()
    stored = str(provider_cfg.get("api_key") or "").strip()
    if auth.has_usable_secret(stored):
        return stored
    env_name = str(provider_cfg.get("api_key_env") or "").strip()
    if env_name:
        try:
            value = _config_module().get_env_value(env_name)
        except Exception:
            value = os.getenv(env_name, "")
        if auth.has_usable_secret(str(value or "")):
            return str(value).strip()
    return ""


def persist_provider_pool_entry(
    config: dict[str, Any],
    *,
    provider_key: str,
    label: str,
    model_catalog: list[str],
    provider_type: str,
    base_url: str = "",
    api_key_env: str = "",
    api_key: str = "",
    auth_mode: str = "",
) -> dict[str, Any]:
    """Return config with one shared provider credential/catalog entry updated."""
    auth = _provider_auth_module()
    models = list(dict.fromkeys(str(item or "").strip() for item in model_catalog if str(item or "").strip()))
    current_providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    current = current_providers.get(provider_key) if isinstance(current_providers, dict) else {}
    current_model = str(current.get("selected_model") or "").strip() if isinstance(current, dict) else ""
    selected_model = current_model if current_model in models else (models[0] if models else "")
    return _config_module().upsert_provider(
        dict(config or {}),
        provider_key,
        {
            "label": label,
            "type": provider_type,
            "base_url": auth.normalize_openai_compatible_base_url(base_url),
            "selected_model": selected_model,
            "api_key_env": api_key_env,
            "api_key": api_key,
            "auth_mode": auth_mode,
            "model_catalog": {
                "models": models,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        },
        make_active=False,
    )


def save_provider_pool_entry(provider_key: str, **kwargs: Any) -> bool:
    try:
        config = persist_provider_pool_entry(load_current_config(), provider_key=provider_key, **kwargs)
        return save_config(config)
    except Exception:
        return False


def refresh_provider_pool_catalog(config: dict[str, Any], provider_key: str) -> tuple[dict[str, Any], list[str]]:
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_cfg = providers.get(provider_key)
    if not isinstance(provider_cfg, dict):
        return config, []
    models = get_provider_models_from_api(
        provider_key,
        api_key=provider_pool_api_key(provider_cfg),
        base_url=str(provider_cfg.get("base_url") or ""),
    )
    model_ids = [model_id for model_id, _ in models]
    if not model_ids:
        return config, []
    updated_entry = dict(provider_cfg)
    updated_entry["model_catalog"] = {
        "models": model_ids,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if str(updated_entry.get("selected_model") or "").strip() not in model_ids:
        updated_entry["selected_model"] = model_ids[0]
    result = dict(config)
    result["providers"] = {**providers, provider_key: updated_entry}
    return result, model_ids


def persist_api_a_selection(config: dict[str, Any], *, provider: str, model: str) -> dict[str, Any]:
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    if provider not in providers:
        raise ValueError(f"Unknown Provider: {provider}")
    config = _config_module().set_provider_model(dict(config), provider, model, make_active=False)
    return _config_module().set_active_provider(config, provider)


def persist_api_b_config(config: dict[str, Any], *, provider: str, model: str) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    if provider not in providers:
        raise ValueError(f"Unknown Provider: {provider}")
    result = dict(config or {})
    memory = dict(result.get("memory") or {})
    llm = dict(memory.get("llm") or {})
    for stale_key in ("api_key_env", "base_url", "provider_profile"):
        llm.pop(stale_key, None)
    llm.update({"provider": provider, "model": str(model or "").strip()})
    memory["llm"] = llm
    result["memory"] = memory
    return result


def has_configured_api_key(api_key_env: str) -> bool:
    if not api_key_env:
        return True
    try:
        value = _config_module().get_env_value(api_key_env)
    except Exception:
        value = os.getenv(api_key_env, "")
    return _provider_auth_module().has_usable_secret(str(value or ""))


def _secret_status(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "missing"
    return "usable" if _provider_auth_module().has_usable_secret(text) else "present_unusable"


def provider_credential_sources(provider: str, api_key_env: str = "") -> list[dict[str, str]]:
    """Return a secret-free report of runtime-readable credential sources."""
    provider = str(provider or "").strip().lower()
    api_key_env = str(api_key_env or "").strip()
    sources: list[dict[str, str]] = []
    if api_key_env:
        try:
            effective = _config_module().get_env_value(api_key_env)
            sources.append({"source": "effective_env", "status": _secret_status(effective), "detail": api_key_env})
        except Exception as exc:
            sources.append({"source": "effective_env", "status": "error", "detail": f"{api_key_env}: {exc}"})
        sources.append({"source": "process_env", "status": _secret_status(os.getenv(api_key_env)), "detail": api_key_env})
        try:
            env_vars = _config_module().load_env()
            try:
                from voidcube.infrastructure.config.runtime_paths import get_env_path
            except (ModuleNotFoundError, ImportError):
                from voidcube.infrastructure.config.runtime_paths import get_env_path
            sources.append({"source": "voidcube_env", "status": _secret_status(env_vars.get(api_key_env)), "detail": f"{get_env_path()}::{api_key_env}"})
        except Exception as exc:
            sources.append({"source": "voidcube_env", "status": "error", "detail": f"{api_key_env}: {exc}"})
    else:
        sources.append({"source": "memory.llm.api_key_env", "status": "missing", "detail": "未设置"})
    if provider:
        auth = _provider_auth_module()
        try:
            store = auth._load_auth_store()
            state = store.get(provider)
            status = "usable" if isinstance(state, dict) and any(_secret_status(state.get(k)) == "usable" for k in ("api_key", "access_token")) else ("present_unusable" if isinstance(state, dict) else "missing")
            sources.append({"source": "auth_store", "status": status, "detail": provider})
        except Exception as exc:
            sources.append({"source": "auth_store", "status": "error", "detail": f"{provider}: {exc}"})
        try:
            entries = auth.read_credential_pool(provider)
            usable = any(isinstance(item, dict) and any(_secret_status(item.get(k)) == "usable" for k in ("runtime_api_key", "api_key", "access_token")) for item in (entries or []))
            sources.append({"source": "credential_pool", "status": "usable" if usable else ("present_unusable" if entries else "missing"), "detail": provider})
        except Exception as exc:
            sources.append({"source": "credential_pool", "status": "error", "detail": f"{provider}: {exc}"})
    return sources


def credential_sources_have_usable_secret(sources: list[dict[str, str]]) -> bool:
    return any(item.get("status") == "usable" for item in sources)


def provider_has_usable_credential(provider: str, api_key_env: str = "") -> bool:
    if credential_sources_have_usable_secret(provider_credential_sources(provider, api_key_env)):
        return True
    # The credential pool is an agent-owned runtime adapter and is retained as
    # a fallback while providers migrate to the shared infrastructure store.
    try:
        from ..providers.credential_pool import load_pool
        pool = load_pool(provider)
        entry = pool.select() if pool and pool.has_credentials() else None
        if entry is not None:
            auth = _provider_auth_module()
            return any(auth.has_usable_secret(str(getattr(entry, key, "") or "")) for key in ("runtime_api_key", "access_token", "api_key"))
    except Exception:
        pass
    return False


def api_a_key_configured(provider_cfg: dict[str, Any]) -> bool:
    if str(provider_cfg.get("auth_mode") or "").strip().lower() == "none":
        return True
    return _secret_status(provider_cfg.get("api_key")) == "usable" or has_configured_api_key(str(provider_cfg.get("api_key_env") or ""))


def api_b_key_configured(memory_llm_cfg: dict[str, Any], providers: dict[str, Any] | None = None) -> bool:
    provider = str(memory_llm_cfg.get("provider") or "").strip().lower()
    provider_cfg = (providers or {}).get(provider)
    return isinstance(provider_cfg, dict) and api_a_key_configured(provider_cfg)


def get_provider_models_from_api(provider: str, *, api_key: str = "", base_url: str = "") -> list[tuple[str, str]]:
    try:
        auth = _provider_auth_module()
        from ..providers.model_catalog import fetch_api_models
        provider_cfg = auth.PROVIDER_REGISTRY.get(provider, {})
        resolved_base_url = base_url or str(
            provider_cfg.get("inference_base_url") or provider_cfg.get("base_url") or ""
        )
        model_ids = fetch_api_models(api_key.strip(), resolved_base_url) or []
        return [(model_id, "") for model_id in model_ids]
    except Exception:
        return []


__all__ = [name for name in globals() if not name.startswith("_")]
