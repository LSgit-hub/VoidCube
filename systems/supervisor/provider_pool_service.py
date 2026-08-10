"""Configuration service for the shared Provider pool and companion workers."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from VoidCube_app.companion_workers import DEFAULT_COMPANION_WORKER_ROLES
from VoidCube_app.config import (
    format_managed_message,
    get_env_value,
    is_managed,
    read_raw_config,
    save_config,
    save_env_value,
)
from VoidCube_app.environment import is_placeholder_secret
from VoidCube_app.provider_auth import (
    AuthError,
    PROVIDER_REGISTRY,
    normalize_openai_compatible_base_url,
)
from tools.toolsets import get_all_toolsets


_PROVIDER_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_ALLOWED_PROVIDER_TYPES = frozenset(PROVIDER_REGISTRY) | {"openai_compatible"}


class ProviderPoolConflictError(ValueError):
    """Raised when a Provider is still referenced by active configuration."""


class ProviderPoolManagedError(RuntimeError):
    """Raised when the user configuration is externally managed."""


class ProviderPoolProbeError(RuntimeError):
    """A sanitized Provider connectivity or model-catalog failure."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderPoolEntryRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    type: str = Field(default="openai_compatible", min_length=1, max_length=40)
    base_url: str = Field(default="", max_length=2048)
    selected_model: str = Field(min_length=1, max_length=300)
    auth_mode: Literal["env", "none"] = "env"
    api_key_env: str = Field(default="", max_length=120)
    api_key: str = Field(default="", max_length=8192)
    make_active: bool = False

    @field_validator("label", "type", "base_url", "selected_model", "api_key_env")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return str(value or "").strip()


class CompanionWorkerAssignmentRequest(BaseModel):
    enabled: bool = True
    provider: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=300)
    toolsets: list[str] = Field(default_factory=list, max_length=40)
    concurrency_limit: int = Field(default=1, ge=1, le=8)

    @field_validator("provider", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("toolsets")
    @classmethod
    def normalize_toolsets(cls, value: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(item or "").strip().lower()
                for item in value
                if str(item or "").strip()
            )
        )


class CompanionWorkerAssignmentsRequest(BaseModel):
    default_role: str = Field(min_length=1, max_length=40)
    max_concurrent: int = Field(default=4, ge=1, le=16)
    roles: dict[str, CompanionWorkerAssignmentRequest] = Field(min_length=1)

    @field_validator("default_role")
    @classmethod
    def normalize_default_role(cls, value: str) -> str:
        return str(value or "").strip().lower()


def _raw_config() -> dict[str, Any]:
    config = read_raw_config()
    return dict(config) if isinstance(config, dict) else {}


def _provider_map(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    providers = config.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    return {
        str(key): dict(value)
        for key, value in providers.items()
        if isinstance(value, Mapping)
    }


def _default_env_key(provider_key: str) -> str:
    if provider_key in PROVIDER_REGISTRY:
        values = PROVIDER_REGISTRY[provider_key].get("api_key_env_vars") or []
        if values:
            return str(values[0])
    normalized = re.sub(r"[^A-Z0-9]+", "_", provider_key.upper()).strip("_")
    return f"VOIDCUBE_PROVIDER_{normalized}_API_KEY"


def _validate_provider_key(provider_key: str) -> str:
    key = str(provider_key or "").strip().lower()
    if not _PROVIDER_KEY_RE.fullmatch(key):
        raise ValueError(
            "provider key must use lowercase letters, numbers, hyphens, or underscores"
        )
    return key


def _validate_base_url(provider_key: str, value: str) -> str:
    base_url = normalize_openai_compatible_base_url(value)
    if not base_url and provider_key in PROVIDER_REGISTRY:
        base_url = str(PROVIDER_REGISTRY[provider_key].get("inference_base_url") or "")
    if not base_url:
        raise ValueError("base_url is required for a named Provider")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an http or https URL")
    return base_url.rstrip("/")


def _configured_worker_entries(config: Mapping[str, Any]) -> tuple[str, dict[str, dict[str, Any]]]:
    section = config.get("companion_workers")
    section = dict(section) if isinstance(section, Mapping) else {}
    configured = section.get("roles")
    configured = dict(configured) if isinstance(configured, Mapping) else {}
    roles: dict[str, dict[str, Any]] = {
        role: {**defaults, **dict(configured.get(role) or {})}
        for role, defaults in DEFAULT_COMPANION_WORKER_ROLES.items()
    }
    for role, values in configured.items():
        role_key = str(role or "").strip().lower()
        if role_key not in roles and isinstance(values, Mapping):
            roles[role_key] = dict(values)
    default_role = str(section.get("default_role") or "general").strip().lower()
    return default_role, roles


def _worker_max_concurrent(config: Mapping[str, Any]) -> int:
    section = config.get("companion_workers")
    section = section if isinstance(section, Mapping) else {}
    try:
        return max(1, min(int(section.get("max_concurrent", 4)), 16))
    except (TypeError, ValueError):
        return 4


def _worker_role_concurrency(values: Mapping[str, Any]) -> int:
    try:
        return max(1, min(int(values.get("concurrency_limit", 1)), 8))
    except (TypeError, ValueError):
        return 1


def _provider_references(config: Mapping[str, Any], provider_key: str) -> list[str]:
    references: list[str] = []
    runtime = config.get("runtime")
    if isinstance(runtime, Mapping) and runtime.get("active_provider") == provider_key:
        references.append("API-A 当前 Provider")

    _, roles = _configured_worker_entries(config)
    for role, values in roles.items():
        if str(values.get("provider") or "").strip().lower() == provider_key:
            references.append(f"员工角色 {role}")

    fallback = config.get("fallback_providers")
    if isinstance(fallback, list):
        for item in fallback:
            if isinstance(item, Mapping) and item.get("provider") == provider_key:
                references.append("API-A 回退链")
                break

    smart = config.get("smart_model_routing")
    cheap = smart.get("cheap_model") if isinstance(smart, Mapping) else None
    if isinstance(cheap, Mapping) and cheap.get("provider") == provider_key:
        references.append("智能模型路由")
    return references


def _stored_model_catalog(entry: Mapping[str, Any]) -> dict[str, Any]:
    raw_catalog = entry.get("model_catalog")
    if not isinstance(raw_catalog, Mapping):
        return {"models": [], "updated_at": ""}
    models: list[str] = []
    raw_models = raw_catalog.get("models")
    if isinstance(raw_models, list):
        for item in raw_models[:1000]:
            model_id = str(item or "").strip()
            if model_id and len(model_id) <= 300 and model_id not in models:
                models.append(model_id)
    return {
        "models": models,
        "updated_at": str(raw_catalog.get("updated_at") or "").strip()[:64],
    }


def _toolset_catalog(config: Mapping[str, Any]) -> list[dict[str, str]]:
    names = set(get_all_toolsets())
    mcp_servers = config.get("mcp_servers")
    if isinstance(mcp_servers, Mapping):
        names.update(str(name).strip().lower() for name in mcp_servers if str(name).strip())
    return [{"name": name} for name in sorted(names)]


class ProviderPoolService:
    """Read and mutate the canonical ``providers`` and worker-role subtrees."""

    def __init__(
        self,
        *,
        http_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._http_client_factory = http_client_factory or httpx.AsyncClient

    @staticmethod
    def _ensure_writable(action: str) -> None:
        if is_managed():
            raise ProviderPoolManagedError(format_managed_message(action))

    def snapshot(self) -> dict[str, Any]:
        config = _raw_config()
        providers = _provider_map(config)
        active = str((config.get("runtime") or {}).get("active_provider") or "").strip()
        default_role, roles = _configured_worker_entries(config)

        public_providers = []
        for key, entry in providers.items():
            provider_type = str(entry.get("type") or "openai_compatible").strip()
            auth_mode = str(entry.get("auth_mode") or "").strip().lower()
            if not auth_mode:
                auth_mode = "none" if provider_type in {"ollama", "lm-studio"} else "env"
            api_key_env = str(entry.get("api_key_env") or "").strip()
            if auth_mode == "env" and not api_key_env:
                api_key_env = _default_env_key(key)
            stored_key = str(entry.get("api_key") or "").strip()
            credential_configured = auth_mode == "none" or (
                bool(stored_key and not is_placeholder_secret(stored_key))
                or bool(
                    api_key_env
                    and (value := str(get_env_value(api_key_env) or "").strip())
                    and not is_placeholder_secret(value)
                )
            )
            public_providers.append(
                {
                    "key": key,
                    "label": str(entry.get("label") or key),
                    "type": provider_type,
                    "base_url": str(entry.get("base_url") or ""),
                    "selected_model": str(entry.get("selected_model") or ""),
                    "model_catalog": _stored_model_catalog(entry),
                    "auth_mode": auth_mode,
                    "api_key_env": api_key_env,
                    "credential_configured": credential_configured,
                    "active": key == active,
                    "references": _provider_references(config, key),
                }
            )

        public_roles = []
        for role, values in roles.items():
            toolsets = [
                str(item).strip().lower()
                for item in (values.get("toolsets") or [])
                if str(item).strip()
            ]
            defaults = DEFAULT_COMPANION_WORKER_ROLES.get(role)
            recommended_toolsets = (
                list(defaults.get("toolsets") or []) if defaults else list(toolsets)
            )
            public_roles.append(
                {
                    "role": role,
                    "label": str(values.get("label") or role),
                    "description": str(values.get("description") or ""),
                    "enabled": bool(values.get("enabled", True)),
                    "provider": str(values.get("provider") or "").strip().lower(),
                    "model": str(values.get("model") or "").strip(),
                    "toolsets": toolsets,
                    "recommended_toolsets": recommended_toolsets,
                    "concurrency_limit": _worker_role_concurrency(values),
                }
            )

        presets = []
        for key, preset in PROVIDER_REGISTRY.items():
            env_vars = list(preset.get("api_key_env_vars") or [])
            presets.append(
                {
                    "type": key,
                    "label": str(preset.get("name") or key),
                    "base_url": str(preset.get("inference_base_url") or ""),
                    "api_key_env": str(env_vars[0]) if env_vars else "",
                    "auth_mode": "none" if preset.get("auth_type") == "none" else "env",
                }
            )
        presets.append(
            {
                "type": "openai_compatible",
                "label": "OpenAI Compatible",
                "base_url": "",
                "api_key_env": "",
                "auth_mode": "env",
            }
        )
        return {
            "status": "ok",
            "managed": is_managed(),
            "active_provider": active,
            "providers": public_providers,
            "provider_presets": presets,
            "default_role": default_role,
            "max_concurrent": _worker_max_concurrent(config),
            "roles": public_roles,
            "toolsets": _toolset_catalog(config),
        }

    def upsert_provider(
        self,
        provider_key: str,
        request: ProviderPoolEntryRequest,
    ) -> dict[str, Any]:
        self._ensure_writable("change the Provider pool")
        key = _validate_provider_key(provider_key)
        provider_type = str(request.type or "openai_compatible").strip().lower()
        if provider_type not in _ALLOWED_PROVIDER_TYPES:
            raise ValueError("unsupported Provider type")
        base_url = _validate_base_url(key, request.base_url)
        api_key_env = ""
        if request.auth_mode == "env":
            api_key_env = request.api_key_env or _default_env_key(key)
            if not _ENV_KEY_RE.fullmatch(api_key_env):
                raise ValueError("api_key_env must be an uppercase environment variable name")
            if request.api_key:
                save_env_value(api_key_env, request.api_key)

        config = _raw_config()
        providers = _provider_map(config)
        current = dict(providers.get(key) or {})
        previous_type = str(current.get("type") or "openai_compatible").strip().lower()
        previous_base_url = normalize_openai_compatible_base_url(
            str(current.get("base_url") or "")
        ).rstrip("/")
        catalog_invalidated = bool(current) and (
            previous_type != provider_type or previous_base_url != base_url
        )
        current.update(
            {
                "label": request.label,
                "type": provider_type,
                "base_url": base_url,
                "selected_model": request.selected_model,
                "auth_mode": request.auth_mode,
            }
        )
        if catalog_invalidated:
            current.pop("model_catalog", None)
        current.pop("api_key", None)
        if api_key_env:
            current["api_key_env"] = api_key_env
        else:
            current.pop("api_key_env", None)
        providers[key] = current
        config["providers"] = providers
        if request.make_active:
            runtime = dict(config.get("runtime") or {})
            runtime["active_provider"] = key
            config["runtime"] = runtime
        save_config(config, preserve_structure=True)
        return {**self.snapshot(), "status": "saved", "saved_provider": key}

    def delete_provider(self, provider_key: str) -> dict[str, Any]:
        self._ensure_writable("delete a Provider")
        key = _validate_provider_key(provider_key)
        config = _raw_config()
        providers = _provider_map(config)
        if key not in providers:
            raise KeyError(key)
        references = _provider_references(config, key)
        if references:
            raise ProviderPoolConflictError(
                f"Provider '{key}' is still used by: {', '.join(references)}"
            )
        providers.pop(key)
        config["providers"] = providers
        save_config(config, preserve_structure=True)
        return {**self.snapshot(), "status": "deleted", "deleted_provider": key}

    def save_worker_assignments(
        self,
        request: CompanionWorkerAssignmentsRequest,
    ) -> dict[str, Any]:
        self._ensure_writable("change companion worker assignments")
        config = _raw_config()
        providers = _provider_map(config)
        _, existing_roles = _configured_worker_entries(config)
        known_toolsets = {item["name"] for item in _toolset_catalog(config)}

        normalized_roles: dict[str, dict[str, Any]] = {}
        for raw_role, assignment in request.roles.items():
            role = str(raw_role or "").strip().lower()
            if role not in existing_roles:
                raise ValueError(f"unknown worker role '{role}'")
            provider = assignment.provider.lower()
            if provider and provider not in providers:
                raise ValueError(
                    f"worker role '{role}' references unknown Provider '{provider}'"
                )
            invalid_toolsets = sorted(set(assignment.toolsets) - known_toolsets)
            if invalid_toolsets:
                raise ValueError(
                    f"worker role '{role}' has unknown toolsets: {', '.join(invalid_toolsets)}"
                )
            values = dict(existing_roles[role])
            values.update(
                {
                    "enabled": assignment.enabled,
                    "provider": provider,
                    "model": assignment.model,
                    "toolsets": assignment.toolsets,
                    "concurrency_limit": assignment.concurrency_limit,
                }
            )
            normalized_roles[role] = values

        enabled_roles = {
            role for role, values in normalized_roles.items() if values.get("enabled")
        }
        if request.default_role not in normalized_roles:
            raise ValueError("default_role must identify a configured worker role")
        if request.default_role not in enabled_roles:
            raise ValueError("default_role must be enabled")

        config["companion_workers"] = {
            "default_role": request.default_role,
            "max_concurrent": request.max_concurrent,
            "roles": normalized_roles,
        }
        save_config(config, preserve_structure=True)
        return {**self.snapshot(), "status": "saved"}

    def dispatch_policy(self) -> dict[str, Any]:
        config = _raw_config()
        active_provider = str(
            (config.get("runtime") or {}).get("active_provider") or ""
        ).strip().lower()
        _, roles = _configured_worker_entries(config)
        role_limits: dict[str, int] = {}
        role_providers: dict[str, str] = {}
        for role, values in roles.items():
            if not bool(values.get("enabled", True)):
                continue
            role_limits[role] = _worker_role_concurrency(values)
            role_providers[role] = str(
                values.get("provider") or active_provider
            ).strip().lower()
        return {
            "max_concurrent": _worker_max_concurrent(config),
            "role_limits": role_limits,
            "role_providers": role_providers,
        }

    @staticmethod
    def _provider_runtime(provider_key: str) -> dict[str, Any]:
        key = _validate_provider_key(provider_key)
        if key not in _provider_map(_raw_config()):
            raise KeyError(key)
        from VoidCube_app.runtime_provider import resolve_runtime_provider

        try:
            runtime = resolve_runtime_provider(requested=key)
        except AuthError as exc:
            raise ProviderPoolProbeError(str(exc), status_code=400) from exc
        base_url = str(runtime.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            raise ProviderPoolProbeError("Provider has no usable API address")
        return {**runtime, "provider_key": key, "base_url": base_url}

    @staticmethod
    def _model_ids(payload: Any) -> list[str]:
        if not isinstance(payload, Mapping):
            raise ProviderPoolProbeError("Provider returned an invalid model catalog")
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            raise ProviderPoolProbeError("Provider returned an invalid model catalog")

        model_ids: list[str] = []
        for item in raw_models[:2000]:
            if isinstance(item, str):
                model_id = item.strip()
            elif isinstance(item, Mapping):
                model_id = str(
                    item.get("id") or item.get("name") or item.get("model") or ""
                ).strip()
            else:
                continue
            if model_id and len(model_id) <= 300 and model_id not in model_ids:
                model_ids.append(model_id)
            if len(model_ids) >= 1000:
                break
        return model_ids

    async def _request_model_catalog(
        self,
        provider_key: str,
        *,
        require_catalog: bool,
    ) -> tuple[dict[str, Any], list[str], int]:
        runtime = self._provider_runtime(provider_key)
        api_key = str(runtime.get("api_key") or "").strip()
        headers = {"Accept": "application/json"}
        if api_key and api_key != "no-key-required":
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{runtime['base_url']}/models"
        started = time.perf_counter()
        try:
            async with self._http_client_factory(timeout=15.0) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderPoolProbeError(
                "Provider connection timed out", status_code=504
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderPoolProbeError("Provider connection failed") from exc
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        if response.status_code >= 400:
            raise ProviderPoolProbeError(
                f"Provider returned HTTP {response.status_code}"
            )

        try:
            model_ids = self._model_ids(response.json())
        except (ValueError, ProviderPoolProbeError):
            if require_catalog:
                raise ProviderPoolProbeError(
                    "Provider returned an invalid model catalog"
                ) from None
            model_ids = []
        return runtime, model_ids, latency_ms

    async def test_provider(self, provider_key: str) -> dict[str, Any]:
        runtime, model_ids, latency_ms = await self._request_model_catalog(
            provider_key,
            require_catalog=False,
        )
        return {
            "status": "ok",
            "provider": runtime["provider_key"],
            "base_url": runtime["base_url"],
            "latency_ms": latency_ms,
            "model_count": len(model_ids),
        }

    async def refresh_model_catalog(self, provider_key: str) -> dict[str, Any]:
        self._ensure_writable("refresh the Provider model catalog")
        runtime, model_ids, latency_ms = await self._request_model_catalog(
            provider_key,
            require_catalog=True,
        )
        key = runtime["provider_key"]
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        config = _raw_config()
        providers = _provider_map(config)
        if key not in providers:
            raise KeyError(key)
        entry = dict(providers[key])
        entry["model_catalog"] = {
            "models": model_ids,
            "updated_at": updated_at,
        }
        providers[key] = entry
        config["providers"] = providers
        save_config(config, preserve_structure=True)
        return {
            "status": "refreshed",
            "provider": key,
            "base_url": runtime["base_url"],
            "latency_ms": latency_ms,
            "count": len(model_ids),
            "models": model_ids,
            "updated_at": updated_at,
        }


__all__ = [
    "CompanionWorkerAssignmentsRequest",
    "ProviderPoolConflictError",
    "ProviderPoolEntryRequest",
    "ProviderPoolManagedError",
    "ProviderPoolProbeError",
    "ProviderPoolService",
]
