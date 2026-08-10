from __future__ import annotations

from unittest.mock import Mock

import pytest

from VoidCube_app.companion_workers import (
    companion_worker_catalog,
    resolve_companion_worker_role,
    resolve_companion_worker_route,
)


def _base_route() -> dict:
    return {
        "model": "primary-model",
        "runtime": {
            "provider": "primary",
            "base_url": "https://primary.example/v1",
            "api_key": "primary-key",
            "args": [],
        },
    }


def test_default_worker_catalog_exposes_secret_free_roles() -> None:
    catalog = companion_worker_catalog({})

    assert catalog["default_role"] == "general"
    assert [item["role"] for item in catalog["roles"]] == [
        "general",
        "research",
        "coding",
        "media",
    ]
    assert all("provider" not in item for item in catalog["roles"])
    assert all("model" not in item for item in catalog["roles"])
    assert {
        item["role"]: item["toolsets"] for item in catalog["roles"]
    } == {
        "general": ["web", "file", "skills", "todo"],
        "research": ["learn"],
        "coding": ["file", "terminal", "code_execution", "skills", "todo"],
        "media": ["web"],
    }


def test_unknown_or_disabled_worker_role_falls_back_to_configured_default() -> None:
    config = {
        "companion_workers": {
            "default_role": "coding",
            "roles": {"research": {"enabled": False}},
        }
    }

    assert resolve_companion_worker_role(config, "research").role == "coding"
    assert resolve_companion_worker_role(config, "not-configured").role == "coding"


def test_worker_route_inherits_primary_api_a_and_role_default_toolsets() -> None:
    provider_resolver = Mock()

    route = resolve_companion_worker_route(
        config={},
        requested_role="research",
        base_route=_base_route(),
        resolve_provider=provider_resolver,
    )

    assert route["worker_role"] == "research"
    assert route["worker_label"] == "调研员工"
    assert route["model"] == "primary-model"
    assert route["runtime"]["provider"] == "primary"
    assert route["enabled_toolsets"] == ["learn"]
    provider_resolver.assert_not_called()


def test_worker_route_resolves_configured_provider_model_and_toolsets() -> None:
    provider_resolver = Mock(
        return_value={
            "provider": "research-provider",
            "base_url": "https://research.example/v1",
            "api_key": "employee-key",
            "args": [],
        }
    )
    config = {
        "providers": {
            "research-provider": {"selected_model": "research-model"},
        },
        "companion_workers": {
            "default_role": "general",
            "roles": {
                "research": {
                    "provider": "research-provider",
                    "toolsets": ["web", "skills", "web"],
                }
            },
        },
    }

    route = resolve_companion_worker_route(
        config=config,
        requested_role="research",
        base_route=_base_route(),
        resolve_provider=provider_resolver,
    )

    assert route["model"] == "research-model"
    assert route["runtime"]["provider"] == "research-provider"
    assert route["enabled_toolsets"] == ["web", "skills"]
    assert route["worker_provider_explicit"] is True
    provider_resolver.assert_called_once_with(requested="research-provider")


def test_worker_route_rejects_unknown_explicit_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider 'missing-provider'"):
        resolve_companion_worker_route(
            config={
                "companion_workers": {
                    "roles": {
                        "coding": {"provider": "missing-provider"},
                    }
                }
            },
            requested_role="coding",
            base_route=_base_route(),
            resolve_provider=Mock(),
        )
