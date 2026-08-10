from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
import httpx
from fastapi.testclient import TestClient

from systems.supervisor.provider_pool_service import (
    CompanionWorkerAssignmentRequest,
    CompanionWorkerAssignmentsRequest,
    ProviderPoolConflictError,
    ProviderPoolEntryRequest,
    ProviderPoolProbeError,
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
            "model_catalog": {"models": [], "updated_at": ""},
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
    assert research["recommended_toolsets"] == ["learn"]
    assert research["concurrency_limit"] == 1
    assert snapshot["max_concurrent"] == 4
    assert service.dispatch_policy()["role_providers"]["research"] == "research-endpoint"
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
    scheduler = client.get("/provider-pool/scheduler")
    conflict = client.delete("/provider-pool/providers/research-endpoint")

    assert saved.status_code == 200
    assert snapshot.status_code == 200
    assert scheduler.status_code == 200
    assert scheduler.json()["max_concurrent"] == 4
    assert scheduler.json()["active_count"] == 0
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
    refresh = client.post("/provider-pool/providers/research-endpoint/models")

    assert response.status_code == 409
    assert refresh.status_code == 409
    assert "managed by NixOS" in response.json()["detail"]


def test_supervisor_worker_test_routes_queue_isolated_task_and_report_assignment(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    client = _supervisor_client(tmp_path)
    saved = client.put(
        "/provider-pool/providers/research-endpoint",
        json=_provider_request().model_dump(),
    )
    assert saved.status_code == 200
    assignments = _all_worker_assignments("research-endpoint")
    assigned = client.put(
        "/provider-pool/worker-roles",
        json=assignments.model_dump(),
    )
    assert assigned.status_code == 200

    queued = client.post(
        "/provider-pool/worker-tests/research",
        json={"instruction": "只回复：员工测试成功"},
    )

    assert queued.status_code == 200
    payload = queued.json()
    assert payload["status"] == "queued"
    assert payload["worker_role"] == "research"
    assert payload["provider"] == "research-endpoint"
    assert payload["model"] == "research-override"

    status = client.get("/provider-pool/worker-tests/" + payload["test_id"])
    assert status.status_code == 200
    status_payload = status.json()
    assert status_payload["status"] == "queued"
    assert status_payload["provider"] == "research-endpoint"
    assert status_payload["result"] == ""
    assert status_payload["error"] == ""

    claim = client.post(
        "/scheduled-tasks/claim",
        json={"owner_session_id": "cli-test", "lease_seconds": 300},
    ).json()["claim"]
    finished = client.post(
        "/scheduled-task-runs/" + claim["run"]["run_id"] + "/finish",
        json={
            "owner_session_id": "cli-test",
            "success": True,
            "result_summary": "员工测试成功",
            "execution_provider": "actual-provider",
            "execution_model": "actual-model",
            "elapsed_ms": 845,
        },
    )
    assert finished.status_code == 200
    completed = client.get("/provider-pool/worker-tests/" + payload["test_id"]).json()
    assert completed["status"] == "completed"
    assert completed["provider"] == "actual-provider"
    assert completed["model"] == "actual-model"
    assert completed["elapsed_ms"] == 845
    assert completed["result"] == "员工测试成功"

    history = client.get("/provider-pool/worker-tests")
    assert history.status_code == 200
    history_payload = history.json()
    assert history_payload["tests"] == [completed]
    assert history_payload["provider_health"] == [
        {
            "provider": "actual-provider",
            "status": "healthy",
            "model": "actual-model",
            "elapsed_ms": 845,
            "tested_at": completed["recorded_at"],
            "worker_role": "research",
        }
    ]

    failed_test = client.post(
        "/provider-pool/worker-tests/research",
        json={"instruction": "返回测试失败"},
    ).json()
    failed_claim = client.post(
        "/scheduled-tasks/claim",
        json={"owner_session_id": "cli-test", "lease_seconds": 300},
    ).json()["claim"]
    client.post(
        "/scheduled-task-runs/" + failed_claim["run"]["run_id"] + "/finish",
        json={
            "owner_session_id": "cli-test",
            "success": False,
            "error": "manual worker test failed",
            "execution_provider": "actual-provider",
            "execution_model": "actual-model",
            "elapsed_ms": 300,
        },
    )
    failed_result = client.get(
        "/provider-pool/worker-tests/" + failed_test["test_id"]
    ).json()
    failed_history = client.get("/provider-pool/worker-tests").json()
    assert failed_history["tests"] == [failed_result]
    assert failed_history["provider_health"] == []

    task = client.get("/scheduled-tasks/" + payload["test_id"])
    assert task.status_code == 200
    assert task.json()["task"]["requested_via"] == "provider_pool_test"
    assert client.get("/scheduled-tasks").json()["tasks"] == []


class _ProbeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ProbeClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, *, headers):
        self.requests.append((url, headers))
        if self.error:
            raise self.error
        return self.response


def _probe_service(monkeypatch, response=None, error=None):
    client = _ProbeClient(response=response, error=error)
    service = ProviderPoolService(http_client_factory=lambda **_kwargs: client)
    monkeypatch.setattr(
        service,
        "_provider_runtime",
        lambda key: {
            "provider_key": key,
            "provider": "custom",
            "base_url": "https://models.example/v1",
            "api_key": "sk-private-provider-secret",
        },
    )
    return service, client


@pytest.mark.asyncio
async def test_provider_probe_uses_secret_only_in_outbound_header(monkeypatch):
    service, client = _probe_service(
        monkeypatch,
        response=_ProbeResponse({"data": [{"id": "model-a"}, {"id": "model-b"}]}),
    )

    result = await service.test_provider("research-endpoint")

    assert result["status"] == "ok"
    assert result["model_count"] == 2
    assert result["base_url"] == "https://models.example/v1"
    assert "api_key" not in result
    assert client.requests == [
        (
            "https://models.example/v1/models",
            {
                "Accept": "application/json",
                "Authorization": "Bearer sk-private-provider-secret",
            },
        )
    ]


@pytest.mark.asyncio
async def test_provider_model_refresh_persists_common_catalog_shapes(
    tmp_path,
    monkeypatch,
):
    home = _configure_home(tmp_path, monkeypatch)
    service, _client = _probe_service(
        monkeypatch,
        response=_ProbeResponse(
            {
                "models": [
                    {"name": "model-a"},
                    {"model": "model-b"},
                    "model-c",
                    {"id": "model-a"},
                ]
            }
        ),
    )
    service.upsert_provider(
        "research-endpoint",
        _provider_request(api_key=""),
    )

    result = await service.refresh_model_catalog("research-endpoint")

    assert result["status"] == "refreshed"
    assert result["models"] == ["model-a", "model-b", "model-c"]
    assert result["count"] == 3
    datetime.fromisoformat(result["updated_at"])
    saved = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert saved["providers"]["research-endpoint"]["model_catalog"] == {
        "models": ["model-a", "model-b", "model-c"],
        "updated_at": result["updated_at"],
    }
    assert service.snapshot()["providers"][0]["model_catalog"] == saved["providers"][
        "research-endpoint"
    ]["model_catalog"]

    retained = service.upsert_provider(
        "research-endpoint",
        _provider_request(selected_model="model-b", api_key=""),
    )
    assert retained["providers"][0]["model_catalog"]["models"] == [
        "model-a",
        "model-b",
        "model-c",
    ]
    invalidated = service.upsert_provider(
        "research-endpoint",
        _provider_request(
            base_url="https://other-models.example/v1",
            api_key="",
        ),
    )
    assert invalidated["providers"][0]["model_catalog"] == {
        "models": [],
        "updated_at": "",
    }


@pytest.mark.asyncio
async def test_provider_probe_returns_sanitized_timeout_and_http_errors(monkeypatch):
    request = httpx.Request("GET", "https://models.example/v1/models")
    timed_out, _client = _probe_service(
        monkeypatch,
        error=httpx.ReadTimeout("contains transport detail", request=request),
    )
    unauthorized, _client = _probe_service(
        monkeypatch,
        response=_ProbeResponse(
            {"error": "response body must not be exposed"}, status_code=401
        ),
    )

    with pytest.raises(ProviderPoolProbeError) as timeout_error:
        await timed_out.test_provider("research-endpoint")
    with pytest.raises(ProviderPoolProbeError) as http_error:
        await unauthorized.refresh_model_catalog("research-endpoint")

    assert timeout_error.value.status_code == 504
    assert str(timeout_error.value) == "Provider connection timed out"
    assert http_error.value.status_code == 502
    assert str(http_error.value) == "Provider returned HTTP 401"


def test_supervisor_provider_probe_routes_preserve_safe_contract(
    tmp_path,
    monkeypatch,
):
    _configure_home(tmp_path, monkeypatch)
    test_provider = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "research-endpoint",
            "base_url": "https://models.example/v1",
            "latency_ms": 37,
            "model_count": 2,
        }
    )
    refresh_model_catalog = AsyncMock(
        return_value={
            "status": "refreshed",
            "provider": "research-endpoint",
            "base_url": "https://models.example/v1",
            "latency_ms": 41,
            "count": 2,
            "models": ["model-a", "model-b"],
            "updated_at": "2026-08-10T01:02:03+00:00",
        }
    )
    monkeypatch.setattr(ProviderPoolService, "test_provider", test_provider)
    monkeypatch.setattr(
        ProviderPoolService,
        "refresh_model_catalog",
        refresh_model_catalog,
    )
    client = _supervisor_client(tmp_path)

    tested = client.post("/provider-pool/providers/research-endpoint/test")
    models = client.post("/provider-pool/providers/research-endpoint/models")
    stale_get = client.get("/provider-pool/providers/research-endpoint/models")

    assert tested.status_code == 200
    assert tested.json()["model_count"] == 2
    assert "api_key" not in tested.json()
    assert models.status_code == 200
    assert models.json()["models"] == ["model-a", "model-b"]
    assert stale_get.status_code == 405
    test_provider.assert_awaited_once_with("research-endpoint")
    refresh_model_catalog.assert_awaited_once_with("research-endpoint")


def test_supervisor_provider_probe_route_maps_timeout_to_504(tmp_path, monkeypatch):
    _configure_home(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ProviderPoolService,
        "test_provider",
        AsyncMock(
            side_effect=ProviderPoolProbeError(
                "Provider connection timed out", status_code=504
            )
        ),
    )
    client = _supervisor_client(tmp_path)

    response = client.post("/provider-pool/providers/research-endpoint/test")

    assert response.status_code == 504
    assert response.json() == {"detail": "Provider connection timed out"}
