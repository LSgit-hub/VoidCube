"""Provider credential storage and API-key lookup.

This boundary owns process overrides, persisted environment values, and the
small auth-store credential pool.  Provider metadata and authentication state
remain in :mod:`auth`; runtime routing consumes these functions through ports.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .registry import PROVIDER_REGISTRY
from ..config.environment import is_placeholder_secret


_STORE_LOCK = threading.RLock()


def configured_env_value(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return ""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        from ..config.configuration import get_env_value

        return str(get_env_value(name) or "").strip()
    except Exception:
        return ""


def has_usable_secret(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate or is_placeholder_secret(candidate) or len(candidate) < 10:
        return False
    prefixes = ("sk-", "pk-", "api-", "key-", "token-", "OPENROUTER-", "DEEPSEEK-")
    return candidate.startswith(prefixes) or len(candidate) >= 32


def auth_store_path() -> Path:
    from ..config.runtime_paths import get_VoidCube_home

    home = get_VoidCube_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / "auth_store.json"


def load_auth_store() -> dict[str, Any]:
    try:
        with auth_store_path().open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_auth_store(store: dict[str, Any]) -> None:
    path = auth_store_path()
    temporary = path.with_suffix(".tmp")
    try:
        with _STORE_LOCK:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(store, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def resolve_api_key_provider_credentials(provider: str) -> dict[str, str] | None:
    provider_name = str(provider or "").strip().lower()
    config = PROVIDER_REGISTRY.get(provider_name)
    if config is None:
        return None
    api_key = ""
    for env_name in getattr(config, "api_key_env_vars", config.get("api_key_env_vars", [])):
        candidate = configured_env_value(env_name)
        if has_usable_secret(candidate):
            api_key = candidate
            break
    if not api_key:
        state = load_auth_store().get(provider_name)
        if isinstance(state, dict):
            for key_name in ("api_key", "access_token"):
                candidate = str(state.get(key_name) or "").strip()
                if has_usable_secret(candidate):
                    api_key = candidate
                    break
    base_env = str(getattr(config, "base_url_env_var", config.get("base_url_env_var", "")) or "").strip()
    configured_base = configured_env_value(base_env) if base_env else ""
    default_base = getattr(config, "inference_base_url", None) or config.get("inference_base_url") or config.get("base_url", "")
    return {"api_key": api_key, "base_url": configured_base or str(default_base or "")}


def read_credential_pool(provider: str | None = None) -> Any:
    pool = load_auth_store().get("credential_pool")
    if not isinstance(pool, dict):
        pool = {}
    if provider is None:
        return pool
    entries = pool.get(str(provider).strip().lower(), [])
    return entries if isinstance(entries, list) else []


def write_credential_pool(provider: str, entries: Any) -> None:
    with _STORE_LOCK:
        store = load_auth_store()
        pool = store.get("credential_pool")
        if not isinstance(pool, dict):
            pool = {}
        pool[str(provider or "").strip().lower()] = entries if isinstance(entries, list) else []
        store["credential_pool"] = pool
        save_auth_store(store)


__all__ = [
    "auth_store_path",
    "configured_env_value",
    "has_usable_secret",
    "load_auth_store",
    "read_credential_pool",
    "resolve_api_key_provider_credentials",
    "save_auth_store",
    "write_credential_pool",
]
