"""Configuration and routing for API-B managed API-A worker roles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from typing import Any


DEFAULT_COMPANION_WORKER_ROLES: dict[str, dict[str, Any]] = {
    "general": {
        "label": "通用员工",
        "description": "处理不属于专门角色的综合工作",
        "enabled": True,
        "provider": "",
        "model": "",
        "toolsets": ["web", "file", "skills", "todo"],
        "concurrency_limit": 1,
    },
    "research": {
        "label": "调研员工",
        "description": "负责检索、核实、比较和整理外部信息",
        "enabled": True,
        "provider": "",
        "model": "",
        "toolsets": ["learn"],
        "concurrency_limit": 1,
    },
    "coding": {
        "label": "工程员工",
        "description": "负责读取项目、编写或修改代码并运行验证",
        "enabled": True,
        "provider": "",
        "model": "",
        "toolsets": ["file", "terminal", "code_execution", "skills", "todo"],
        "concurrency_limit": 1,
    },
    "media": {
        "label": "媒体员工",
        "description": "负责生成、查找、组织、交付和播放媒体产物",
        "enabled": True,
        "provider": "",
        "model": "",
        "toolsets": ["media", "web"],
        "concurrency_limit": 1,
    },
}
_ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


@dataclass(frozen=True, slots=True)
class CompanionWorkerRole:
    role: str
    label: str
    description: str
    provider: str
    model: str
    toolsets: tuple[str, ...]
    concurrency_limit: int


def _enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _toolsets(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(
            str(item).strip()
            for item in value
            if str(item).strip()
        )
    )


def _concurrency_limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 8))
    except (TypeError, ValueError):
        return 1


def companion_worker_roles(config: Mapping[str, Any] | None) -> dict[str, CompanionWorkerRole]:
    section = (config or {}).get("companion_workers")
    section = section if isinstance(section, Mapping) else {}
    configured = section.get("roles")
    configured = configured if isinstance(configured, Mapping) else {}
    merged = {
        role: {**defaults, **(dict(configured.get(role) or {}))}
        for role, defaults in DEFAULT_COMPANION_WORKER_ROLES.items()
    }
    for role, values in configured.items():
        normalized_role = str(role or "").strip().lower()
        if (
            not _ROLE_RE.fullmatch(normalized_role)
            or normalized_role in merged
            or not isinstance(values, Mapping)
        ):
            continue
        merged[normalized_role] = dict(values)

    roles: dict[str, CompanionWorkerRole] = {}
    for role, values in merged.items():
        if not _enabled(values.get("enabled", True)):
            continue
        roles[role] = CompanionWorkerRole(
            role=role,
            label=str(values.get("label") or role).strip()[:80] or role,
            description=str(values.get("description") or "").strip()[:300],
            provider=str(values.get("provider") or "").strip().lower(),
            model=str(values.get("model") or "").strip(),
            toolsets=_toolsets(values.get("toolsets")),
            concurrency_limit=_concurrency_limit(values.get("concurrency_limit", 1)),
        )
    return roles


def resolve_companion_worker_role(
    config: Mapping[str, Any] | None,
    requested_role: str = "",
) -> CompanionWorkerRole:
    roles = companion_worker_roles(config)
    if not roles:
        raise ValueError("companion worker roles are disabled")
    section = (config or {}).get("companion_workers")
    section = section if isinstance(section, Mapping) else {}
    default_role = str(section.get("default_role") or "general").strip().lower()
    if default_role not in roles:
        default_role = next(iter(roles))
    requested = str(requested_role or "").strip().lower()
    return roles.get(requested) or roles[default_role]


def companion_worker_catalog(config: Mapping[str, Any] | None) -> dict[str, Any]:
    resolved_default = resolve_companion_worker_role(config)
    roles = companion_worker_roles(config)
    return {
        "default_role": resolved_default.role,
        "roles": [
            {
                "role": role.role,
                "label": role.label,
                "description": role.description,
                "toolsets": list(role.toolsets),
                "concurrency_limit": role.concurrency_limit,
            }
            for role in roles.values()
        ],
    }


def resolve_companion_worker_route(
    *,
    config: Mapping[str, Any],
    requested_role: str,
    base_route: Mapping[str, Any],
    resolve_provider: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    role = resolve_companion_worker_role(config, requested_role)
    route = dict(base_route)
    base_runtime = dict(route.get("runtime") or {})
    model = role.model or str(route.get("model") or "").strip()

    if role.provider:
        providers = config.get("providers")
        providers = providers if isinstance(providers, Mapping) else {}
        provider_config = providers.get(role.provider)
        if not isinstance(provider_config, Mapping):
            raise ValueError(
                f"worker role '{role.role}' references unknown provider '{role.provider}'"
            )
        runtime = dict(resolve_provider(requested=role.provider))
        model = role.model or str(provider_config.get("selected_model") or "").strip()
        if not model:
            raise ValueError(
                f"worker role '{role.role}' has no model and provider "
                f"'{role.provider}' has no selected_model"
            )
        route["runtime"] = {
            "api_key": runtime.get("api_key"),
            "base_url": runtime.get("base_url"),
            "provider": runtime.get("provider"),
            "command": runtime.get("command"),
            "args": list(runtime.get("args") or []),
            "credential_pool": runtime.get("credential_pool"),
        }
    else:
        route["runtime"] = base_runtime

    if not model:
        raise ValueError(f"worker role '{role.role}' cannot resolve an API-A model")
    route["model"] = model
    route["worker_role"] = role.role
    route["worker_label"] = role.label
    route["worker_provider_explicit"] = bool(role.provider)
    if role.toolsets:
        route["enabled_toolsets"] = list(role.toolsets)
    return route


__all__ = [
    "CompanionWorkerRole",
    "DEFAULT_COMPANION_WORKER_ROLES",
    "companion_worker_catalog",
    "companion_worker_roles",
    "resolve_companion_worker_role",
    "resolve_companion_worker_route",
]
