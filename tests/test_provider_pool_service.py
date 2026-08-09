from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from systems.supervisor.provider_pool_service import (
    CompanionWorkerAssignmentRequest,
    CompanionWorkerAssignmentsRequest,
    ProviderPoolConflictError,
    ProviderPoolEntryRequest,
    ProviderPoolService,
)
from systems.supervisor.supervisor import Supervisor
from systems.supervisor.config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)


pytestmark = pytest.mark.unit


def _configure_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("VOIDCUBE_MANAGED", raising=False)
    return home


def _provider_request(**overrides) -> ProviderPoolEntryRequest:
    values = {
        "label": "Research Endpoint",
        "type": "openai_compatible",
        "base_url": "https://models.example/v1/chat/completions",
        "selected_model": "research-model",
        "auth_mode": "env",
        "api_key_env": "RESEARCH_API_KEY",
        "api_key": "sk-research-secret-value",
    }
    values.update(overrides)
    return ProviderPoolEntryRequest(**values)


def _all_worker_assignments(provider: str = "") -> CompanionWorkerAssignmentsRequest:
    return CompanionWorkerAssignmentsRequest(
        default_role="general",
        roles={
            role: CompanionWorkerAssignmentRequest(
                enabled=True,
                provider=provider if role == "research" else "",
                model="research-override" if role == "research" else "",
                toolsets=["web", "search"] if role == "research" else [],
            )
            for role in ("general", "research", "coding", "media")
        },
    )


def _supervisor_client(tmp_path: Path) -> TestClient:
    supervisor = Supervisor(
        SupervisorConfig(
            execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
            soul_store_path=str(tmp_path / ".soul-runtime"),
            body_runtime=SupervisorBodyRuntimeConfig(
                state_root=str(tmp_path / "body-state")
            ),
            service_runtime=SupervisorServiceRuntimeConfig(
                governor_llm_advisory_enabled=False,
                endogenous_drive_lm_task_generation_enabled=False,
            ),
        )
    )
    return TestClient(supervisor.app)


def test_provider_pool_saves_named_entries_and_never_returns_secrets(
    tmp_path,
    monkeypatch,
):
    home = _configure_home(tmp_path, monkeypatch)
    service = ProviderPoolService()

    snapshot = service.upsert_provider("research-endpoint", _provider_request())

    assert snapshot["status"] == "saved"
    assert snapshot["providers"] == [
        {
            "key": "research-endpoint",
            "label": "Research Endpoint",
            "type": "openai_compatible",
            "base_url": "https://models.example/v1",
            "selected_model": "research-model",
            "auth_mode": "env",
            "api_key_env": "RESEARCH_API_KEY",
            "credential_configured": True,
            "active": False,
            "references": [],
        }
    ]
    assert "api_key" not in snapshot["providers"][0]
    assert "RESEARCH_API_KEY=sk-research-secret-value" in (
        home / ".env"
    ).read_text(encoding="utf-8")
    saved = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert "api_key" not in saved["providers"]["research-endpoint"]


def test_provider_pool_assigns_roles_and_protects_referenced_provider(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    service = ProviderPoolService()
    service.upsert_provider("research-endpoint", _provider_request(api_key=""))

    snapshot = service.save_worker_assignments(
        _all_worker_assignments("research-endpoint")
    )

    research = next(role for role in snapshot["roles"] if role["role"] == "research")
    assert research["provider"] == "research-endpoint"
    assert research["model"] == "research-override"
    assert research["toolsets"] == ["web", "search"]
    with pytest.raises(ProviderPoolConflictError, match="员工角色 research"):
        service.delete_provider("research-endpoint")


def test_provider_pool_protects_active_provider_and_deletes_unused_entry(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    service = ProviderPoolService()
    service.upsert_provider(
        "primary",
        _provider_request(label="Primary", make_active=True, api_key=""),
    )
    service.upsert_provider(
        "spare",
        _provider_request(
            label="Spare",
            api_key_env="SPARE_API_KEY",
            api_key="",
        ),
    )

    with pytest.raises(ProviderPoolConflictError, match="API-A"):
        service.delete_provider("primary")
    deleted = service.delete_provider("spare")

    assert deleted["deleted_provider"] == "spare"
    assert [provider["key"] for provider in deleted["providers"]] == ["primary"]


def test_worker_assignment_rejects_unknown_provider_or_toolset(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    service = ProviderPoolService()

    with pytest.raises(ValueError, match="unknown Provider"):
        service.save_worker_assignments(_all_worker_assignments("missing"))

    assignments = _all_worker_assignments()
    assignments.roles["coding"].toolsets = ["missing-toolset"]
    with pytest.raises(ValueError, match="unknown toolsets"):
        service.save_worker_assignments(assignments)


def test_named_provider_runtime_uses_its_own_environment_variable(monkeypatch):
    from VoidCube_app.runtime_provider import resolve_runtime_provider

    config = {
        "providers": {
            "research-endpoint": {
                "label": "Research Endpoint",
                "base_url": "https://models.example/v1",
                "api_key_env": "RESEARCH_API_KEY",
                "auth_mode": "env",
                "selected_model": "research-model",
            }
        },
        "runtime": {},
        "agent": {},
    }
    monkeypatch.setattr("VoidCube_app.runtime_provider.load_config", lambda: config)
    monkeypatch.setattr(
        "VoidCube_app.config.get_env_value",
        lambda name: "sk-role-specific-secret" if name == "RESEARCH_API_KEY" else "",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong-global-secret")

    runtime = resolve_runtime_provider(requested="research-endpoint")

    assert runtime["api_key"] == "sk-role-specific-secret"
    assert runtime["base_url"] == "https://models.example/v1"
    assert runtime["model"] == "research-model"


def test_non_active_builtin_provider_uses_its_pool_entry(monkeypatch):
    from VoidCube_app.runtime_provider import resolve_runtime_provider

    config = {
        "providers": {
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "selected_model": "primary-model",
            },
            "deepseek": {
                "base_url": "https://deepseek-proxy.example/v1",
                "api_key_env": "DEEPSEEK_EMPLOYEE_API_KEY",
                "selected_model": "deepseek-worker",
            },
        },
        "runtime": {"active_provider": "openrouter"},
        "agent": {},
    }
    monkeypatch.setattr("VoidCube_app.runtime_provider.load_config", lambda: config)
    monkeypatch.setattr(
        "VoidCube_app.config.get_env_value",
        lambda name: "sk-deepseek-employee" if name == "DEEPSEEK_EMPLOYEE_API_KEY" else "",
    )

    runtime = resolve_runtime_provider(requested="deepseek")

    assert runtime["provider"] == "deepseek"
    assert runtime["base_url"] == "https://deepseek-proxy.example/v1"
    assert runtime["api_key"] == "sk-deepseek-employee"


def test_supervisor_provider_pool_routes_use_sanitized_contract(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    client = _supervisor_client(tmp_path)

    saved = client.put(
        "/provider-pool/providers/research-endpoint",
        json=_provider_request().model_dump(),
    )
    snapshot = client.get("/provider-pool")
    conflict = client.delete("/provider-pool/providers/research-endpoint")

    assert saved.status_code == 200
    assert snapshot.status_code == 200
    provider = snapshot.json()["providers"][0]
    assert "api_key" not in provider
    assert provider["credential_configured"] is True
    assert conflict.status_code == 200


def test_supervisor_provider_pool_routes_reject_managed_writes(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    client = _supervisor_client(tmp_path)
    monkeypatch.setenv("VOIDCUBE_MANAGED", "true")

    response = client.put(
        "/provider-pool/providers/research-endpoint",
        json=_provider_request().model_dump(),
    )

    assert response.status_code == 409
    assert "managed by NixOS" in response.json()["detail"]
