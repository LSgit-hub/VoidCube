from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.execution import build_execution_route_hint
from systems.supervisor.supervisor import (
    Supervisor,
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from systems.supervisor.autonomous_chain_store import (
    AutonomousChainExecutionRequest,
    AutonomousChainStore,
)
from systems.supervisor.service_runtime import StellarMode
from systems.supervisor.ui_runtime import UI_HTML
from systems.self_learning import LearningRecommendation
from systems.self_learning.conclusion_store import SelfLearningConclusionStore


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
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


def _make_supervisor(tmp_path: Path) -> Supervisor:
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    return supervisor


def _make_probe_ready_supervisor(tmp_path: Path) -> Supervisor:
    (tmp_path / "run_agent.py").write_text("print('agent entrypoint')\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(exist_ok=True)
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "model_tools.py").write_text("# probe smoke\n", encoding="utf-8")
    return _make_supervisor(tmp_path)


@pytest.mark.unit
def test_supervisor_can_disable_governor_llm_advisory(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    assert supervisor.config.service_runtime.governor_llm_advisory_enabled is False
    assert supervisor._governor._engine._llm_reasoner is None


def _runtime_drive_input_payload(*, active_sessions: int = 0, quiet_after_seconds: int = 600) -> dict:
    is_quiet = active_sessions == 0
    return {
        "checks": {
            "has_api_a_execution_idle": True,
            "has_memory_idle": True,
        },
        "idle_seconds": {
            "user": 900,
            "api_a_execution": 900,
            "memory": 900,
        },
        "user_chain_signal": {
            "scope": "soft_signal_only",
            "active_sessions": active_sessions,
            "is_quiet": is_quiet,
            "recent_user_idle_seconds": 900,
            "quiet_after_seconds": quiet_after_seconds,
        },
        "activity": {
            "active_sessions": active_sessions,
            "counts": {},
        },
        "decisions": {
            "eligible_for_planning": True,
            "eligible_for_execution": is_quiet,
        },
        "task_family_decisions": {
            "self_learning": {
                "eligible_for_planning": True,
                "eligible_for_execution": is_quiet,
            },
            "general_self_evolution": {
                "eligible_for_planning": True,
                "eligible_for_execution": False,
            },
        },
        "governance_task_type_decisions": {
            "self_learning": {
                "eligible_for_planning": True,
                "eligible_for_execution": is_quiet,
            },
            "self_evolution": {
                "eligible_for_planning": True,
                "eligible_for_execution": False,
            },
        },
    }


def _find_autonomous_observation_task(state: dict, *, title: str = "", task_id: str = "") -> dict:
    observation = dict(state.get("autonomous_observation") or {})
    candidates: list[dict] = []

    def _append(item):
        if isinstance(item, dict) and item:
            candidates.append(item)

    def _append_many(items):
        for item in list(items or []):
            _append(item)

    loop = dict(observation.get("loop") or {})
    for stage_card in list(loop.get("stage_cards") or []):
        if isinstance(stage_card, dict):
            _append(stage_card.get("focus_task"))
    chain = dict(observation.get("chain") or {})
    for section in list(chain.get("segments") or []):
        if isinstance(section, dict):
            _append_many(section.get("items"))

    for item in candidates:
        if title and str(item.get("title") or "") == title:
            return item
        if task_id and str(item.get("task_id") or "") == task_id:
            return item
    raise AssertionError(f"task not found in autonomous observation: title={title!r} task_id={task_id!r}")


def _observation_section(observation: dict, key: str) -> dict:
    chain = dict(observation.get("chain") or {})
    for section in list(chain.get("segments") or []):
        if isinstance(section, dict) and str(section.get("key") or "").strip() == key:
            return section
    raise AssertionError(f"section not found: {key!r}")


def _observation_stage_card(observation: dict, key: str) -> dict:
    loop = dict(observation.get("loop") or {})
    for card in list(loop.get("stage_cards") or []):
        if isinstance(card, dict) and str(card.get("stage_key") or "").strip() == key:
            return card
    raise AssertionError(f"stage card not found: {key!r}")


def _observation_loop_stage(observation: dict, key: str) -> dict:
    card = _observation_stage_card(observation, key)
    projected = dict(card)
    projected["key"] = key
    if not str(projected.get("status_label") or "").strip():
        projected["status_label"] = str(projected.get("display_status") or "").strip()
    if "focus_task" not in projected:
        projected["focus_task"] = dict(card.get("focus_task") or {})
    return projected


async def _trigger_memory_compression(supervisor: Supervisor, request: dict | None = None):
    return await supervisor._execution_facade.trigger_memory_compression(request)


async def _execute_body_upgrade(supervisor: Supervisor, request: dict | None = None):
    return await supervisor._execution_facade.execute_body_upgrade(request)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_health_exposes_runtime_state_without_deprecated_runtime_catalog(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    health = await supervisor.health_check()

    assert health["status"] == "healthy"
    assert health["service"] == "supervisor"
    assert health["body_runtime"]["active_slot"] == "slot-A"
    assert health["body_runtime"]["healthy"] is True
    assert health["body_runtime"]["violations"] == []
    assert "transitional_interfaces" not in health


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_health_degrades_when_body_manifest_is_missing(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._body_registry.slot_worktree_manifest_path("slot-A").unlink()

    health = await supervisor.health_check()
    periodic = await supervisor.run_health_checks()
    violation_codes = {
        item["code"] for item in health["body_runtime"]["violations"]
    }

    assert health["status"] == "degraded"
    assert health["body_runtime"]["healthy"] is False
    assert "slot_not_materialized" in violation_codes
    assert periodic["healthy"] is False
    assert periodic["body_runtime"]["healthy"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_healthy_body_integrity(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    state = await supervisor.get_supervisor_ui_state()

    body_status = state["body_status"]
    assert body_status["integrity"]["healthy"] is True
    assert body_status["integrity"]["violations"] == []
    assert body_status["active_slot"] == "slot-A"
    assert body_status["shell_slot"] == "slot-B"
    assert body_status["slot_cards"]
    assert all(card["integrity_healthy"] is True for card in body_status["slot_cards"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_projects_body_manifest_violation_to_slot_card(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    manifest_path = supervisor._body_registry.slot_worktree_manifest_path("slot-A")
    manifest_path.unlink()

    state = await supervisor.get_supervisor_ui_state()

    body_status = state["body_status"]
    violations = body_status["integrity"]["violations"]
    active_card = next(
        card for card in body_status["slot_cards"] if card["slot_id"] == "slot-A"
    )
    assert body_status["integrity"]["healthy"] is False
    assert any(item["code"] == "slot_not_materialized" for item in violations)
    assert active_card["integrity_healthy"] is False
    assert active_card["integrity_materialized"] is False
    assert active_card["integrity_violations"][0]["code"] == "slot_not_materialized"
    assert manifest_path.exists() is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_keeps_unreadable_registry_diagnostic_read_only(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._body_registry.registry_path.write_text("{", encoding="utf-8")

    state = await supervisor.get_supervisor_ui_state()
    health = await supervisor.health_check()

    body_status = state["body_status"]
    assert state["status"] == "ok"
    assert health["status"] == "degraded"
    assert health["body_runtime"]["active_slot"] is None
    assert health["body_runtime"]["violations"][0]["code"] == "registry_unreadable"
    assert body_status["integrity"]["healthy"] is False
    assert body_status["integrity"]["registry"] is None
    assert body_status["integrity"]["violations"][0]["code"] == "registry_unreadable"
    assert body_status["slot_cards"] == []
    assert supervisor._body_registry.registry_path.read_text(encoding="utf-8") == "{"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_registers_embedded_executor_with_gateway(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    registrations = []

    class _Response:
        def __init__(self, service_type):
            self.status = 201
            self._service_type = service_type

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"service_id": f"{self._service_type}-service"}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, timeout):
            registrations.append((url, json, timeout))
            return _Response(json["service_type"])

    monkeypatch.setitem(sys.modules, "aiohttp", SimpleNamespace(ClientSession=_Session))

    service_id = await supervisor.register_with_gateway()

    assert service_id == "supervisor-service"
    assert [payload["service_type"] for _, payload, _ in registrations] == [
        "supervisor",
        "executor",
    ]
    assert registrations[1][1]["health_endpoint"] == "/executor/health"
    assert registrations[1][1]["metadata"]["embedded_in"] == "supervisor"
    assert supervisor._gateway_service_id == "supervisor-service"
    assert supervisor._gateway_executor_service_id == "executor-service"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_gateway_verification_isolates_single_request_failure(
    tmp_path,
    monkeypatch,
):
    supervisor = _make_supervisor(tmp_path)
    supervisor._gateway_service_id = "supervisor-service"
    supervisor._gateway_executor_service_id = "executor-service"
    requested_ids = []

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, timeout):
            service_id = url.rsplit("/", 1)[-1]
            requested_ids.append((service_id, timeout))
            if service_id == "executor-service":
                raise OSError("transient executor verification failure")
            return _Response()

    monkeypatch.setitem(sys.modules, "aiohttp", SimpleNamespace(ClientSession=_Session))

    missing_service_types = await supervisor._missing_gateway_service_types()
    supervisor._register_gateway_service_type = AsyncMock(  # type: ignore[method-assign]
        return_value="restored-executor-service"
    )
    await supervisor._restore_gateway_registrations(missing_service_types)

    assert requested_ids == [
        ("supervisor-service", 5),
        ("executor-service", 5),
    ]
    assert missing_service_types == {"executor"}
    supervisor._register_gateway_service_type.assert_awaited_once_with("executor")  # type: ignore[attr-defined]


@pytest.mark.unit
def test_supervisor_wires_execution_facade_to_canonical_executors(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    assert supervisor._execution_facade.body_lifecycle is supervisor._body_lifecycle_executor
    assert supervisor._execution_facade.body_upgrade is supervisor._body_upgrade_executor
    assert supervisor._execution_facade.memory_maintenance is supervisor._memory_maintenance_executor
    assert supervisor._execution_service.app is supervisor.app


@pytest.mark.unit
def test_supervisor_mounts_embedded_executor_surface(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    client = TestClient(supervisor.app)

    health = client.get("/executor/health")
    registry = client.get("/executor/body/registry")

    assert health.status_code == 200
    assert health.json()["service"] == "executor"
    assert health.json()["status"] == "healthy"
    assert health.json()["body_runtime"]["healthy"] is True
    assert registry.status_code == 200
    assert registry.json()["registry"]["active_slot"] == "slot-A"
    assert registry.json()["integrity"]["violations"] == []


@pytest.mark.unit
def test_supervisor_room_frontend_uses_chain_panel_contract():
    assert 'id="panelChain"' in UI_HTML
    assert 'id="panelChainBody"' in UI_HTML
    assert 'data-panel="chain"' in UI_HTML
    assert 'renderChainPanel' in UI_HTML
    assert 'chain-stage-rail' in UI_HTML
    assert '判断参考' in UI_HTML
    assert '当前判断' in UI_HTML
    assert '替身与统计' in UI_HTML
    assert 'data-chain-group="' in UI_HTML
    assert 'data-chain-trace="' in UI_HTML
    assert 'body-integrity-row' in UI_HTML
    assert 'body-integrity-violation' in UI_HTML


@pytest.mark.unit
def test_supervisor_room_frontend_uses_canonical_reminder_policy_contract():
    assert 'id="panelSettings"' in UI_HTML
    assert 'id="reminderPolicyForm"' in UI_HTML
    assert 'id="reminderPolicyEnabled"' in UI_HTML
    assert 'id="reminderPolicyTts"' in UI_HTML
    assert 'id="reminderPolicyCooldown"' in UI_HTML
    assert 'id="reminderPolicyDndStart"' in UI_HTML
    assert 'id="reminderPolicyDndEnd"' in UI_HTML
    assert "fetch('/companion/reminder-policy'" in UI_HTML
    assert "localStorage" not in UI_HTML


@pytest.mark.unit
def test_companion_reminder_policy_route_persists_only_its_canonical_subtree(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    original_provider = {
        "base_url": "https://example.invalid/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "selected_model": "deepseek-chat",
    }
    original_memory = {"db_path": "custom-memory.db", "recall_default_limit": 17}
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": {"deepseek": original_provider},
                "memory": original_memory,
                "supervisor": {
                    "ui_enabled": True,
                    "service_runtime": {"health_check_interval": 45},
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    supervisor = _make_supervisor(tmp_path)
    client = TestClient(supervisor.app)

    initial = client.get("/companion/reminder-policy")
    response = client.post(
        "/companion/reminder-policy",
        json={
            "enabled": False,
            "tts_enabled": True,
            "cooldown_seconds": 1800,
            "dnd_start": "22:00",
            "dnd_end": "08:00",
        },
    )

    assert initial.status_code == 200
    assert initial.json()["managed"] is False
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "tts_enabled": True,
        "cooldown_seconds": 1800,
        "dnd_start": "22:00",
        "dnd_end": "08:00",
        "managed": False,
        "status": "saved",
    }
    assert supervisor.config.service_runtime.companion_proactive_reminder_enabled is False
    assert supervisor.config.service_runtime.companion_proactive_dnd_start == "22:00"

    saved = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert saved["providers"]["deepseek"] == original_provider
    assert saved["memory"] == original_memory
    assert saved["supervisor"]["ui_enabled"] is True
    assert saved["supervisor"]["service_runtime"] == {
        "health_check_interval": 45,
        "companion_proactive_reminder_enabled": False,
        "companion_proactive_reminder_tts_enabled": True,
        "companion_proactive_reminder_cooldown_seconds": 1800,
        "companion_proactive_dnd_start": "22:00",
        "companion_proactive_dnd_end": "08:00",
    }


@pytest.mark.unit
def test_companion_reminder_policy_route_rejects_invalid_values_and_managed_writes(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    supervisor = _make_supervisor(tmp_path)
    client = TestClient(supervisor.app)
    base = {
        "enabled": True,
        "tts_enabled": True,
        "cooldown_seconds": 900,
        "dnd_start": "",
        "dnd_end": "",
    }

    invalid_time = client.post(
        "/companion/reminder-policy",
        json={**base, "dnd_start": "25:00"},
    )
    invalid_cooldown = client.post(
        "/companion/reminder-policy",
        json={**base, "cooldown_seconds": 86401},
    )
    monkeypatch.setenv("VOIDCUBE_MANAGED", "true")
    managed = client.post("/companion/reminder-policy", json=base)

    assert invalid_time.status_code == 422
    assert invalid_cooldown.status_code == 422
    assert managed.status_code == 409
    assert "managed by NixOS" in managed.json()["detail"]
    assert "slot.integrity_healthy === false" in UI_HTML
    assert 'data-chain-trace-expanded="' in UI_HTML
    assert 'data-chain-trace-source="' in UI_HTML
    assert "body_tree" in UI_HTML
    assert "data-body-slot" in UI_HTML
    assert "renderBodyTreeDrawer" in UI_HTML
    assert "context.bodySlot = String(trigger.dataset.bodySlot)" in UI_HTML
    assert "openDrawer(trigger.dataset.drill, context)" in UI_HTML
    assert 'data-action-btn=' not in UI_HTML
    assert "__manual__" not in UI_HTML
    assert 'panelTasks' not in UI_HTML
    assert 'renderTasksPanel' not in UI_HTML


@pytest.mark.unit
def test_supervisor_exposes_segmented_runtime_config_views_and_uses_them_for_execution_wiring(tmp_path):
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(
            git_repo_path=str(tmp_path),
            gateway_address="http://gateway.segmented.local",
            agent_base_port=9100,
            probe_watch_window_seconds=180,
        ),
        soul_store_path=str(tmp_path / ".soul-runtime"),
        service_runtime=SupervisorServiceRuntimeConfig(
            health_check_interval=45,
            autonomous_chain_review_interval=900,
            endogenous_drive_enabled=True,
            endogenous_drive_interval=600,
            endogenous_drive_max_candidates=2,
        ),
        body_runtime=SupervisorBodyRuntimeConfig(
            state_root=str(tmp_path / "body-state"),
            slot_a_name="slot-blue",
            slot_b_name="slot-green",
        ),
    )
    supervisor = Supervisor(config)

    assert config.execution.gateway_address == "http://gateway.segmented.local"
    assert config.execution.agent_base_port == 9100
    assert config.execution.probe_watch_window_seconds == 180
    assert config.service_runtime.health_check_interval == 45
    assert config.service_runtime.governor_llm_advisory_enabled is True
    assert config.service_runtime.autonomous_chain_review_interval == 900
    assert config.service_runtime.endogenous_drive_enabled is True
    assert config.service_runtime.endogenous_drive_interval == 600
    assert config.service_runtime.endogenous_drive_max_candidates == 2
    assert config.ui_enabled is True
    assert config.ui_auto_open is True
    assert config.ui_event_interval_seconds == 3.0
    assert config.ui_activity_buffer_size == 100
    assert config.body_runtime.state_root == str(tmp_path / "body-state")
    assert config.body_runtime.slot_a_name == "slot-blue"
    assert config.body_runtime.slot_b_name == "slot-green"
    assert supervisor._body_upgrade_executor.config.probe_watch_window_seconds == 180
    registry = supervisor._body_registry.load_registry()
    assert registry.active_slot == "slot-blue"
    assert registry.shell_slot == "slot-green"
    active_worktree = Path(supervisor._body_registry.load_slot_meta("slot-blue").worktree_path)
    assert not (active_worktree / ".slots-segmented").exists()
    assert not (active_worktree / ".registry-segmented.json").exists()


@pytest.mark.unit
def test_supervisor_routes_no_longer_publish_deprecated_execution_surface(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    deprecated_routes = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in supervisor.app.routes
        if getattr(route, "deprecated", False)
    }
    route_paths = {route.path for route in supervisor.app.routes}

    assert deprecated_routes == set()
    assert "/upgrade/history" not in route_paths
    assert "/upgrade/legacy" not in route_paths
    assert "/autonomous-chain/cycle" in route_paths
    assert "/autonomous-chain/tasks" in route_paths
    assert "/autonomous-chain/tasks/{task_id}" in route_paths
    assert "/autonomous-chain/tasks/{task_id}/decision" in route_paths
    assert "/autonomous-chain/tasks/review" in route_paths
    assert "/autonomous-chain/tasks/clear" in route_paths
    assert "/self-evolution/autonomous-cycle" not in route_paths
    assert "/self-evolution/tasks" not in route_paths
    assert "/self-evolution/tasks/{task_id}" not in route_paths
    assert "/self-evolution/tasks/{task_id}/decision" not in route_paths
    deprecated_autonomous_cycle_route = "/self-evolution/" + "auto" + "-cycle"
    assert deprecated_autonomous_cycle_route not in route_paths
    assert "/autonomous-chain-gate/activate" in route_paths
    assert "/autonomous-chain-gate/deactivate" in route_paths
    assert "/autonomous-chain-gate/status" in route_paths
    deprecated_gate_prefix = "/" + "governor" + "-mode"
    assert f"{deprecated_gate_prefix}/activate" not in route_paths
    assert f"{deprecated_gate_prefix}/deactivate" not in route_paths
    assert f"{deprecated_gate_prefix}/status" not in route_paths


@pytest.mark.unit
def test_supervisor_mounts_built_in_room_ui_when_enabled(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    route_paths = {route.path for route in supervisor.app.routes}

    assert "/ui" in route_paths
    assert "/ui/state" in route_paths
    assert "/ui/events" in route_paths
    assert "/ui/identity/archive" in route_paths
    assert "/ui/identity/turns" in route_paths
    assert "/ui/identity/experiences/verify" in route_paths
    assert "/runtime/timeline" in route_paths
    assert "/runtime/traces" in route_paths
    assert "/runtime/traces/{trace_id}" in route_paths
    assert "/runtime/observation-input" in route_paths
    assert "/runtime/drive-input/evaluate" in route_paths
    assert "/runtime/activity-guards/evaluate" not in route_paths
    assert "/runtime/idle-window/evaluate" not in route_paths

    with TestClient(supervisor.app) as client:
        page = client.get("/ui")
        state = client.get("/ui/state")

    assert page.status_code == 200
    assert "VoidCube Supervisor Room" in page.text
    assert 'EventSource("/ui/events")' in page.text
    assert 'data-drill="identity"' in page.text
    assert "renderIdentityDrawer" in page.text
    assert "identity-evidence" in page.text
    assert "evidence.length" in page.text
    assert "verifyIdentityTurn" in page.text
    assert "data-identity-verify-turn" in page.text
    assert state.status_code == 200
    payload = state.json()
    assert payload["status"] == "ok"
    assert payload["scene"] in {"idle", "planning", "drive", "memory", "maintenance", "handoff"}
    assert "timeline" in payload


@pytest.mark.unit
def test_autonomous_chain_store_migrates_legacy_unauditable_execution_request(tmp_path):
    storage_path = tmp_path / "autonomous-chain.json"
    storage_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "task_id": "legacy-approved",
                        "trace_id": "trace-approved",
                        "title": "Legacy approved task",
                        "status": "approved",
                        "execution_request": {
                            "request_id": "request-approved",
                            "task_id": "legacy-approved",
                            "trace_id": "trace-approved",
                            "kind": "general_self_evolution",
                            "status": "approved_for_execution",
                            "git_lineage": {},
                        },
                    },
                    {
                        "task_id": "legacy-completed",
                        "trace_id": "trace-completed",
                        "title": "Legacy completed task",
                        "status": "completed",
                        "execution_request": {
                            "request_id": "request-completed",
                            "task_id": "legacy-completed",
                            "trace_id": "trace-completed",
                            "kind": "general_self_evolution",
                            "status": "approved_for_execution",
                            "git_lineage": {},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    store = AutonomousChainStore(storage_path)
    tasks = {task.task_id: task for task in store.list_tasks()}

    approved = tasks["legacy-approved"]
    assert approved.status == "awaiting_review"
    assert approved.execution_request is None
    assert approved.metadata["snapshot_migration"]["review_required"] is True
    assert approved.metadata["snapshot_migration"]["missing_fields"] == [
        "target_slot_id",
        "git_lineage.source_commit",
        "git_lineage.candidate_commit",
        "git_lineage.rollback_commit",
        "git_lineage.changed_files",
    ]
    assert approved.decision_history[-1].actor == "snapshot_migration"

    completed = tasks["legacy-completed"]
    assert completed.status == "completed"
    assert completed.execution_request is None
    assert completed.metadata["snapshot_migration"]["review_required"] is False

    persisted = json.loads(storage_path.read_text(encoding="utf-8"))
    assert all(task["execution_request"] is None for task in persisted["tasks"])
    first_migration = approved.metadata["snapshot_migration"]
    reloaded = {task.task_id: task for task in store.list_tasks()}
    assert reloaded["legacy-approved"].metadata["snapshot_migration"] == first_migration
    assert len(reloaded["legacy-approved"].decision_history) == 1


@pytest.mark.unit
def test_supervisor_ui_state_migrates_legacy_autonomous_chain_snapshot(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._autonomous_chain_store.storage_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": [
                    {
                        "task_id": "legacy-ui-task",
                        "trace_id": "legacy-ui-trace",
                        "title": "Legacy UI task",
                        "status": "failed",
                        "execution_request": {
                            "request_id": "legacy-ui-request",
                            "task_id": "legacy-ui-task",
                            "trace_id": "legacy-ui-trace",
                            "kind": "general_self_evolution",
                            "status": "approved_for_execution",
                            "git_lineage": {},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with TestClient(supervisor.app) as client:
        response = client.get("/ui/state")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    migrated = supervisor._autonomous_chain_store.get_task("legacy-ui-task")
    assert migrated is not None
    assert migrated.status == "failed"
    assert migrated.execution_request is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_observation_input_projects_soft_signal_snapshot(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "activity": {
                "active_sessions": 2,
                "counts": {"error_count": 1},
                "recent_metadata": {"autonomous_chain_execute": {"task_id": "task-1"}},
            },
            "user_chain_signal": {
                "is_quiet": False,
                "quiet_after_seconds": 900,
            },
        }
    )

    result = await supervisor.get_runtime_observation_input()

    assert result["status"] == "ok"
    assert result["gateway_address"] == supervisor.config.execution.gateway_address
    observation_input = result["observation_input"]
    assert observation_input["snapshot_source"] == "live"
    assert observation_input["activity"]["active_sessions"] == 2
    assert observation_input["activity"]["counts"]["error_count"] == 1
    assert observation_input["activity"]["recent_metadata"]["autonomous_chain_execute"]["task_id"] == "task-1"
    assert observation_input["user_chain_signal"]["scope"] == "soft_signal_only"
    assert observation_input["user_chain_signal"]["active_sessions"] == 2
    assert observation_input["user_chain_signal"]["is_quiet"] is False
    assert observation_input["user_chain_signal"]["quiet_after_seconds"] == 900


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_view_aggregates_autonomous_activity_governance_and_gateway(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "追踪自主学习证据链路",
            "task_family": "self_learning",
            "source": "self_learning",
            "metadata": {
                "trace_id": "trace-runtime-1",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    trace_id = planned["tasks"][0]["trace_id"]
    decided = await supervisor.decide_autonomous_chain_task(
        task_id,
        {
            "decision": "approve",
            "reason": "Trace test approval.",
            "decision_id": "decision-runtime-1",
        },
    )
    supervisor._record_supervisor_ui_activity(
        "trace_marker",
        scene="learning",
        summary="来自监督者活动的轨迹标记。",
        metadata={
            "trace_id": trace_id,
            "task_id": task_id,
            "governance_task_type": "self_learning",
            "task_family": "self_learning",
            "decision_id": "decision-runtime-1",
        },
    )

    async def fake_gateway_activity_log(trace_id=None, limit=200):
        assert trace_id == planned["tasks"][0]["trace_id"]
        assert limit == 200
        return {
            "status": "ok",
            "events": [
                {
                    "activity_id": "gateway-activity-1",
                    "activity_kind": "self_learning",
                    "recorded_at": "2026-05-25T12:05:00",
                    "source_service": "self-learning",
                    "session_id": None,
                    "metadata": {
                        "trace_id": trace_id,
                        "task_id": task_id,
                        "governance_task_type": "self_learning",
                        "task_family": "self_learning",
                        "decision_id": "decision-runtime-1",
                        "task_identity": {
                            "title": "追踪自主学习证据链路",
                            "display_label": "自主学习",
                            "summary": "追踪自主学习证据链路 (自主学习)",
                        },
                    },
                }
            ],
        }

    supervisor._fetch_gateway_activity_log = fake_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.get_runtime_trace(trace_id)

    assert decided["status"] == "approved"
    assert result["status"] == "ok"
    assert result["found"] is True
    assert result["summary"]["trace_id"] == trace_id
    assert result["summary"]["task_ids"] == [task_id]
    assert result["summary"]["decision_ids"] == ["decision-runtime-1"]
    assert result["summary"]["governance_task_types"] == ["self_learning"]
    assert "自主学习" in result["summary"]["governance_labels"]
    assert "链路存储" in result["summary"]["source_labels"]
    assert result["summary"]["task_families"] == ["self_learning"]
    assert result["sources"]["autonomous_chain_store"] >= 2
    assert result["sources"]["supervisor_activity"] >= 1
    assert result["sources"]["mem_governor_history"] >= 1
    assert result["sources"]["gateway_activity_log"] == 1
    event_sources = {event["source"] for event in result["timeline"]}
    assert {
        "autonomous_chain_store",
        "supervisor_activity",
        "mem_governor_history",
        "gateway_activity_log",
    }.issubset(event_sources)
    gateway_event = next(
        event for event in result["timeline"]
        if event.get("source") == "gateway_activity_log"
    )
    assert gateway_event["event_label"] == "自主学习回报"
    assert gateway_event["summary"] == "网关记下了 「追踪自主学习证据链路 (自主学习)」 的自主学习回报。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_list_summarizes_known_traces_without_gateway_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    first = await supervisor.plan_autonomous_chain_task({"title": "First trace"})
    second = await supervisor.plan_autonomous_chain_task({"title": "Second trace"})

    async def unavailable_gateway_activity_log(trace_id=None, limit=200):
        raise RuntimeError("gateway unavailable")

    supervisor._fetch_gateway_activity_log = unavailable_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.list_runtime_traces(limit=10)
    trace_ids = {trace["trace_id"] for trace in result["traces"]}

    assert result["status"] == "ok"
    assert first["tasks"][0]["trace_id"] in trace_ids
    assert second["tasks"][0]["trace_id"] in trace_ids
    assert result["sources"]["autonomous_chain_store"] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_includes_writeback_and_cancelled_chain_records(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def empty_gateway_activity_log(trace_id=None, limit=200):
        return {"status": "ok", "events": []}

    supervisor._fetch_gateway_activity_log = empty_gateway_activity_log  # type: ignore[method-assign]

    completed = await supervisor.plan_autonomous_chain_task(
        {"title": "已完成轨迹记录", "trace_id": "trace-runtime-projection-1"}
    )
    cancelled = await supervisor.plan_autonomous_chain_task(
        {"title": "已取消轨迹记录", "trace_id": "trace-runtime-projection-2"}
    )

    completed_id = completed["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        completed_id,
        status="approved",
        actor="test",
        reason="已批准进入自主交接",
    )
    supervisor._autonomous_chain_store.update_status(
        completed_id,
        status="running",
        actor="test",
        reason="自主交接进行中",
    )
    supervisor._autonomous_chain_store.update_status(
        completed_id,
        status="completed",
        actor="test",
        reason="writeback finished",
    )
    cancelled_id = cancelled["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        cancelled_id,
        status="cancelled",
        actor="test",
        reason="cancelled during governance review",
    )

    completed_trace = await supervisor.get_runtime_trace(completed["tasks"][0]["trace_id"])
    cancelled_trace = await supervisor.get_runtime_trace(cancelled["tasks"][0]["trace_id"])

    assert completed_trace["found"] is True
    assert cancelled_trace["found"] is True
    assert completed_trace["summary"]["task_ids"] == [completed_id]
    assert cancelled_trace["summary"]["task_ids"] == [cancelled_id]
    assert completed_trace["sources"]["autonomous_chain_store"] >= 2
    assert cancelled_trace["sources"]["autonomous_chain_store"] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_normalizes_execution_request_drive_input_evidence(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def empty_gateway_activity_log(trace_id=None, limit=200):
        return {"status": "ok", "events": []}

    supervisor._fetch_gateway_activity_log = empty_gateway_activity_log  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "带旧证据字段的自主交接请求",
            "trace_id": "trace-runtime-execution-request-1",
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    trace_id = planned["tasks"][0]["trace_id"]
    execution_request = AutonomousChainExecutionRequest.model_validate(
        {
            "task_id": task_id,
            "trace_id": trace_id,
            "task_type": "self_evolution",
            "decision_id": "decision-trace-execution-1",
            "kind": "general_self_evolution",
            "target_slot_id": "slot-B",
            "git_lineage": {
                "source_commit": "aaa111",
                "candidate_commit": "bbb222",
                "rollback_commit": "aaa111",
                "changed_files": ["agent/runtime.py"],
            },
            "drive_input_evidence": {
                "user_chain_signal": {
                    "scope": "soft_signal_only",
                    "active_sessions": 5,
                }
            },
        }
    )

    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        actor="test",
        reason="seed execution request trace payload",
        execution_request=execution_request,
    )

    result = await supervisor.get_runtime_trace(trace_id)

    execution_event = next(
        event for event in result["timeline"]
        if event.get("event_type") == "execution_request"
    )
    payload = execution_event["payload"]
    assert payload["drive_input_evidence"]["user_chain_signal"]["active_sessions"] == 5
    assert "activity_guard_evidence" not in payload


@pytest.mark.unit
def test_supervisor_builds_body_slot_cards_with_upgrade_tree_focus(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    registry = supervisor._body_registry.load_registry()
    shell_slot = str(registry.shell_slot or "")
    active_slot = str(registry.active_slot or "")
    assert shell_slot
    assert active_slot

    shell_meta = supervisor._body_registry.load_slot_meta(shell_slot)
    shell_worktree = Path(shell_meta.worktree_path)
    (shell_worktree / "agent").mkdir(parents=True, exist_ok=True)
    (shell_worktree / "systems").mkdir(parents=True, exist_ok=True)
    (shell_worktree / "tools").mkdir(parents=True, exist_ok=True)
    (shell_worktree / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    (shell_worktree / "config.yaml").write_text("name: shell\n", encoding="utf-8")

    slot_metas = {
        slot_id: supervisor._body_registry.load_slot_meta(slot_id).model_dump(mode="json")
        for slot_id in registry.slot_ids
    }
    cards = supervisor._build_ui_body_slot_cards(
        registry=registry,
        slot_metas=slot_metas,
        chain_history_projection=[
            {
                "task_id": "body-1",
                "title": "Refine shell structure",
                "execution_kind": "body_improvement",
                "status": "running",
                "execution_request": {
                    "target_slot_id": shell_slot,
                    "editable_dirs": ["systems", "agent"],
                },
                "changed_files": ["systems/supervisor/ui_runtime.py"],
            }
        ],
    )

    shell_card = next(card for card in cards if card["slot_id"] == shell_slot)
    active_card = next(card for card in cards if card["slot_id"] == active_slot)

    assert shell_card["role_label"] == "培养替身"
    assert shell_card["upgrade_active"] is True
    assert "API-A 正在改" in shell_card["focus_summary"]
    assert any(node["key"] == "systems" and node["upgrade_active"] for node in shell_card["tree_nodes"])
    assert any(node["key"] == "agent" and node["upgrade_active"] for node in shell_card["tree_nodes"])
    assert any(
        node["key"] == "systems/supervisor/ui_runtime.py"
        and node["label"] == "ui_runtime.py"
        and node["upgrade_dot"] is True
        and node["upgrade_status"] == "running"
        and node["upgrade_task_id"] == "body-1"
        for node in shell_card["tree_nodes"]
    )
    assert any(node["key"] == "run_agent.py" for node in shell_card["tree_nodes"])
    assert shell_card["upgrade_signals"][0]["source_label"] == "API-A 正在改"
    assert active_card["upgrade_active"] is False


@pytest.mark.unit
def test_supervisor_builds_body_slot_cards_with_api_b_scheduled_upgrade_focus(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    registry = supervisor._body_registry.load_registry()
    shell_slot = str(registry.shell_slot or "")
    assert shell_slot

    shell_meta = supervisor._body_registry.load_slot_meta(shell_slot)
    shell_worktree = Path(shell_meta.worktree_path)
    (shell_worktree / "systems").mkdir(parents=True, exist_ok=True)
    (shell_worktree / "prompts").mkdir(parents=True, exist_ok=True)

    slot_metas = {
        slot_id: supervisor._body_registry.load_slot_meta(slot_id).model_dump(mode="json")
        for slot_id in registry.slot_ids
    }
    cards = supervisor._build_ui_body_slot_cards(
        registry=registry,
        slot_metas=slot_metas,
        chain_history_projection=[
            {
                "task_id": "body-2",
                "title": "Prepare shell prompt cleanup",
                "execution_kind": "body_improvement",
                "status": "approved",
                "execution_request": {
                    "target_slot_id": shell_slot,
                    "editable_dirs": ["systems", "prompts"],
                },
                "metadata": {
                    "changed_files": ["prompts/body_upgrade.md"],
                },
            }
        ],
    )

    shell_card = next(card for card in cards if card["slot_id"] == shell_slot)

    assert shell_card["upgrade_active"] is True
    assert "API-B 已转交" in shell_card["focus_summary"]
    assert any(node["key"] == "systems" and node["upgrade_active"] for node in shell_card["tree_nodes"])
    assert any(node["key"] == "prompts" and node["upgrade_active"] for node in shell_card["tree_nodes"])
    assert any(
        node["key"] == "prompts/body_upgrade.md"
        and node["label"] == "body_upgrade.md"
        and node["upgrade_dot"] is True
        and node["upgrade_status"] == "approved"
        and node["upgrade_task_id"] == "body-2"
        for node in shell_card["tree_nodes"]
    )
    assert shell_card["upgrade_signals"][0]["source_label"] == "API-B 已转交"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_timeline_exposes_recent_unified_trace_records(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "时间线驱动的界面观察",
            "trace_id": "trace-timeline-1",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    trace_id = planned["tasks"][0]["trace_id"]

    async def fake_gateway_activity_log(trace_id=None, limit=200):
        assert trace_id is None
        return {
            "status": "ok",
            "events": [
                {
                    "activity_id": "gateway-timeline-1",
                    "activity_kind": "self_learning",
                    "recorded_at": "2026-05-25T12:05:00",
                    "metadata": {
                        "trace_id": trace_id or "trace-timeline-1",
                        "task_id": task_id,
                        "governance_task_type": "self_learning",
                        "task_family": "self_learning",
                        "task_identity": {
                            "title": "时间线驱动的界面观察",
                            "display_label": "自主学习",
                            "summary": "时间线驱动的界面观察 (自主学习)",
                        },
                    },
                },
                {
                    "activity_id": "gateway-user-request-1",
                    "activity_kind": "user_request",
                    "recorded_at": "2026-05-25T12:06:00",
                    "session_id": "user-chat-session",
                    "metadata": {
                        "trace_id": "trace-timeline-1",
                        "request_id": "user-request-1",
                        "prompt_preview": "USER_CHAT_SECRET_SHOULD_NOT_RENDER",
                    },
                },
                {
                    "activity_id": "gateway-user-chat-scene-1",
                    "activity_kind": "agent_scene",
                    "recorded_at": "2026-05-25T12:07:00",
                    "session_id": "user-chat-session",
                    "metadata": {
                        "trace_id": "trace-timeline-1",
                        "agent_role": "user_chat",
                        "scene": "executing",
                        "subagent_focus_preview": "USER_CHAT_SUBAGENT_SHOULD_NOT_RENDER",
                    },
                },
            ],
        }

    supervisor._fetch_gateway_activity_log = fake_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.get_runtime_timeline(limit=10)

    assert result["status"] == "ok"
    assert result["count"] >= 3
    sources = {event["source"] for event in result["timeline"]}
    assert {
        "autonomous_chain_store",
        "supervisor_activity",
        "mem_governor_history",
        "gateway_activity_log",
    }.issubset(sources)
    assert {event["trace_id"] for event in result["timeline"]} == {trace_id}
    assert {event["task_id"] for event in result["timeline"] if event.get("task_id")} == {task_id}
    rendered = json.dumps(result["timeline"], ensure_ascii=False)
    assert "USER_CHAT_SECRET_SHOULD_NOT_RENDER" not in rendered
    assert "USER_CHAT_SUBAGENT_SHOULD_NOT_RENDER" not in rendered
    gateway_events = [
        event for event in result["timeline"]
        if event.get("source") == "gateway_activity_log"
    ]
    assert [event["event_type"] for event in gateway_events] == ["self_learning"]
    assert gateway_events[0]["source_label"] == "网关回报"
    assert gateway_events[0]["summary"] == "网关记下了 「时间线驱动的界面观察 (自主学习)」 的自主学习回报。"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runtime_trace_fallback_summaries_use_human_labels(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    assert supervisor._trace_human_summary_fallback(  # type: ignore[attr-defined]
        event_type="tasks reviewed",
        scope_label="监督者活动",
    ) == "监督者活动：API-B 复核记录"
    assert supervisor._trace_human_summary_fallback(  # type: ignore[attr-defined]
        event_type="supervisor_activity",
        scope_label="治理记录",
    ) == "治理记录：监督者活动"


@pytest.mark.unit
def test_supervisor_can_disable_built_in_room_ui(tmp_path):
    config = _make_supervisor_config(tmp_path).model_copy(update={"ui_enabled": False})
    supervisor = Supervisor(config)
    route_paths = {route.path for route in supervisor.app.routes}

    assert "/ui" not in route_paths
    assert "/ui/state" not in route_paths
    assert "/ui/events" not in route_paths


@pytest.mark.unit
def test_supervisor_room_ui_event_frame_uses_sse_state_event(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    frame = supervisor._format_supervisor_ui_event(
        "state",
        {
            "status": "ok",
            "scene": "planning",
            "title": "西子正在思考",
        }
    )

    assert frame.startswith("event: state\n")
    assert '"status":"ok"' in frame
    assert '"scene":"planning"' in frame
    assert frame.endswith("\n\n")


@pytest.mark.unit
def test_supervisor_room_ui_records_bounded_activity_timeline(tmp_path):
    config = _make_supervisor_config(tmp_path).model_copy(
        update={"ui_activity_buffer_size": 2}
    )
    supervisor = Supervisor(config)

    supervisor._record_supervisor_ui_activity("first", summary="第一条事件")
    supervisor._record_supervisor_ui_activity("second", summary="第二条事件")
    supervisor._record_supervisor_ui_activity("third", summary="第三条事件")

    timeline = supervisor._recent_supervisor_ui_activity(limit=10)
    assert [event["event_type"] for event in timeline] == ["third", "second"]
    assert timeline[0]["summary"] == "第三条事件"
    persisted = supervisor._supervisor_ui_activity_path.read_text(encoding="utf-8")
    assert "third" in persisted
    assert "first" not in persisted


@pytest.mark.unit
def test_supervisor_room_ui_restores_activity_timeline_from_runtime_store(tmp_path):
    config = _make_supervisor_config(tmp_path).model_copy(
        update={"ui_activity_buffer_size": 3}
    )
    first = Supervisor(config)
    first._record_supervisor_ui_activity("remembered", summary="已持久化事件")

    second = Supervisor(config)
    timeline = second._recent_supervisor_ui_activity(limit=10)

    assert timeline[0]["event_type"] == "remembered"
    assert timeline[0]["summary"] == "已持久化事件"
    assert second._supervisor_ui_activity_path == first._supervisor_ui_activity_path


@pytest.mark.unit
def test_supervisor_room_ui_activity_is_mirrored_to_governance_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    event = supervisor._record_supervisor_ui_activity(
        "task_decided",
        scene="execution",
        summary="裁决已镜像到治理历史",
        metadata={
            "trace_id": "trace-ui-1",
            "task_id": "task-ui-1",
            "task_type": "self_learning_followup",
            "governance_task_type": "self_learning",
            "task_family": "self_learning",
            "decision_id": "decision-ui-1",
        },
    )

    history = supervisor._governor.list_history(limit=5)
    record = history[-1]
    assert record["kind"] == "supervisor_activity"
    assert record["request"]["event_id"] == event["event_id"]
    assert record["request"]["event_type"] == "task_decided"
    assert record["request"]["trace_id"] == "trace-ui-1"
    assert record["request"]["governance_task_type"] == "self_learning"
    assert record["evolution_lineage"]["decision_id"] == "decision-ui-1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_read_does_not_mirror_observation_to_governance_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    before = len(supervisor._governor.list_history(limit=100))

    state = await supervisor.get_supervisor_ui_state()

    after = supervisor._governor.list_history(limit=100)
    assert state["status"] == "ok"
    assert len(after) == before


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_maps_memory_task_to_memory_scene(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    await supervisor.plan_autonomous_chain_task(
        {
            "title": "Run memory continuity sweep",
            "execution_kind": "memory_maintenance",
        }
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "maintenance"
    judgement = _observation_section(state["autonomous_observation"], "api_b_judgement")
    assert judgement["items"][0]["title"] == "Run memory continuity sweep"
    assert "tasks" not in state
    assert "整理记忆" in state["title"]
    assert "tasks_planned" in [event["event_type"] for event in state["timeline"]]
    assert "supervisor_activity" in [event["source"] for event in state["timeline"]]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_read_does_not_create_timeline_events(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.get_runtime_timeline = AsyncMock(return_value={"timeline": []})  # type: ignore[method-assign]
    supervisor.evaluate_drive_input = AsyncMock(
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 2, "counts": {}},
            "thresholds": {"user_idle_seconds": 600},
            "user_chain_signal": {
                "scope": "soft_signal_only",
                "active_sessions": 2,
                "is_quiet": False,
                "quiet_after_seconds": 600,
            },
            "decisions": {
                "eligible_for_planning": True,
                "eligible_for_execution": False,
            },
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
    )  # type: ignore[method-assign]

    state = await supervisor.get_supervisor_ui_state()

    candidate_section = _observation_section(state["autonomous_observation"], "api_b_candidates")
    assert candidate_section["items"] == []
    assert state["timeline"] == []
    assert "in_execution_window" not in state
    assert "active_executions" not in state
    assert "drive_candidates" not in state
    assert "drive_available" not in state
    assert "autonomous_chain_gate" not in state
    assert "active_sessions" not in state
    assert "activity_guards" not in state
    assert "metrics" not in state
    runtime = state["autonomous_observation"]["runtime"]
    assert runtime["user_chain_signal"]["active_sessions"] == 2
    assert runtime["user_chain_signal"]["is_quiet"] is False
    assert runtime["snapshot_source"] == "live"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_falls_back_to_fast_default_snapshots_when_live_probes_fail(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_drive_input = AsyncMock(side_effect=RuntimeError("gateway down"))  # type: ignore[method-assign]
    supervisor._fetch_tier1_stats = AsyncMock(side_effect=RuntimeError("memory down"))  # type: ignore[method-assign]
    supervisor.get_runtime_timeline = AsyncMock(side_effect=RuntimeError("timeline down"))  # type: ignore[method-assign]

    state = await supervisor.get_supervisor_ui_state()

    runtime = state["autonomous_observation"]["runtime"]
    assert runtime["user_chain_signal"]["active_sessions"] == 0
    assert runtime["snapshot_source"] == "default"
    assert state["tier1_stats"]["memory_unavailable"] is True
    assert state["tier1_stats"]["snapshot_source"] == "default"
    assert state["timeline"] == []


@pytest.mark.unit
def test_supervisor_room_labels_active_sessions_as_user_chain_idle_signal():
    ui_source = Path("systems/supervisor/ui_runtime.py").read_text(encoding="utf-8")

    assert "API-B 判断输入" in ui_source
    assert "label:'活跃会话'" not in ui_source


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_judgement_preview_for_shadow_review(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "items": [
                {"title": "Duplicate learning branch"},
                {"title": "Canonical learning branch"},
            ]
        }
    )
    tasks_by_title = {task["title"]: task["task_id"] for task in planned["tasks"]}

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, drive_input):
        assert drive_input["user_chain_signal"]["is_quiet"] is True
        return {
            tasks_by_title["Duplicate learning branch"]: {
                "action": "merge",
                "reason": "Duplicate branch should merge into the canonical one.",
                "followup_suggestion": {
                    "action": "merge",
                    "reason": "Duplicate branch should merge into the canonical one.",
                    "merge_into": tasks_by_title["Canonical learning branch"],
                },
            }
        }

    monkeypatch.setattr(supervisor, "_review_task_governance_with_supervisor", fake_lm_review)

    await supervisor.review_autonomous_chain_tasks(
        {
            "drive_input": _runtime_drive_input_payload(),
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    duplicate = _find_autonomous_observation_task(
        state,
        title="Duplicate learning branch",
    )
    assert "lm_review_shadow" not in duplicate["judgement_preview"]
    assert all(
        "lm_review_shadow" not in dict(entry.get("context") or {})
        for entry in duplicate.get("decision_history", [])
        if isinstance(entry, dict)
    )
    preview = duplicate["judgement_preview"]["followup_suggestion"]
    assert preview["action"] == "merge"
    assert preview["merge_into"] == tasks_by_title["Canonical learning branch"]
    assert preview["merge_into_title"] == "Canonical learning branch"
    assert "监督者保留建议" in preview["summary"]
    assert "Canonical learning branch" in duplicate["judgement_preview"]["summary"]
    assert "governance_preview" not in duplicate
    assert state["autonomous_observation"]["metrics"]["observation"]["followup_signals"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_applied_priority_updates(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Underweighted architecture follow-up",
            "priority": "low",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_autonomous_chain_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, drive_input):
        assert drive_input["user_chain_signal"]["is_quiet"] is True
        return {
            task_id: {
                "action": "reprioritize",
                "priority": "high",
                "reason": "This follow-up now blocks higher-value evolution work.",
            }
        }

    monkeypatch.setattr(supervisor, "_review_task_governance_with_supervisor", fake_lm_review)

    await supervisor.review_autonomous_chain_tasks(
        {
            "drive_input": _runtime_drive_input_payload(),
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    task = _find_autonomous_observation_task(
        state,
        task_id=task_id,
    )
    assert task["priority"] == "high"
    assert "lm_review_priority" not in task["judgement_preview"]
    assert all(
        "lm_review_priority" not in dict(entry.get("context") or {})
        for entry in task.get("decision_history", [])
        if isinstance(entry, dict)
    )
    assert task["judgement_preview"]["priority_adjustment"]["priority"] == "high"
    assert task["judgement_preview"]["priority_adjustment"]["priority_label"] == "高"
    assert "监督者已重排优先级" in task["judgement_preview"]["summary"]
    assert "governance_preview" not in task
    assert state["autonomous_observation"]["metrics"]["observation"]["priority_change_signals"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_task_identity_for_body_improvement(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "根据学习结果改进 shell 替身",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
                "execution_request": {
                    "kind": "body_improvement",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    state = await supervisor.get_supervisor_ui_state()
    task = _find_autonomous_observation_task(
        state,
        task_id=task_id,
    )

    assert task["task_identity"]["task_family"] == "body_upgrade"
    assert task["task_identity"]["execution_kind"] == "body_improvement"
    assert task["task_identity"]["requested_kind"] == "body_improvement"
    assert task["task_identity"]["display_kind"] == "body_improvement"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_uses_autonomous_observation_model(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": True},
                "body_upgrade": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
        }
    )

    supervisor_task_1 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Supervisor first task",
            "task_family": "memory_maintenance",
            "metadata": {"task_family": "memory_maintenance"},
        }
    )
    supervisor_task_2 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Supervisor second task",
            "task_family": "general_self_evolution",
            "metadata": {"task_family": "general_self_evolution"},
        }
    )
    agent_task_1 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "第一个自主学习链路项",
            "task_type": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    agent_task_2 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Agent second creative task",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )

    await supervisor.decide_autonomous_chain_task(
        supervisor_task_1["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "first supervisor task"},
    )
    await supervisor.decide_autonomous_chain_task(
        agent_task_1["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "first agent task"},
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]
    loop_stage_keys = [item["stage_key"] for item in observation["loop"]["stage_cards"]]
    group_keys = [group["key"] for group in observation["chain"]["segments"]]
    api_b_judgement = _observation_section(observation, "api_b_judgement")
    api_a_handoff = _observation_section(observation, "api_a_handoff")

    assert "queue_layout" not in state
    assert "panels" not in state
    assert observation["read_model_version"] == 13
    assert "observed_tasks" not in observation
    assert "candidates" not in observation
    assert observation["mode"]["scope"] == "api_b_autonomous_chain_only"
    assert observation["loop"]["stage_cards"][0]["stage_key"] == "api_b_judgement"
    assert observation["loop"]["stage_cards"][1]["stage_key"] == "api_a_execution"
    assert observation["loop"]["recent_writebacks"] == []
    assert "stages" not in observation["loop"]
    assert observation["board"]["headline"] == "API-B 主视角自主闭环总览"
    assert "watch_groups" not in observation["board"]
    assert "protocol_notes" not in observation["board"]
    assert "boundary_note" not in observation["board"]
    assert observation["loop"]["boundary"] == (
        "自主链路闭环只展示 API-B 判断、API-A 自主执行、Mem 写回回流和 API-B 再读取；"
        "用户链路只作让路软感知，不展示聊天内容。"
    )
    assert "metric_cards" not in observation["board"]
    assert loop_stage_keys == [
        "api_b_judgement",
        "api_a_execution",
        "mem_writeback",
        "api_b_reread",
    ]
    assert group_keys == ["api_b_candidates", "api_b_judgement", "api_a_handoff", "mem_recent"]
    assert "queue" not in observation
    assert observation["chain"]["headline"] == "自主闭环分段观察"
    assert "presentation" not in observation
    assert observation["board"]["primary_focus"]["title"] == "Supervisor first task"
    assert observation["board"]["primary_focus"]["status"] == "当前在途"
    assert observation["board"]["primary_focus"]["observation_role"] == "api_b_judgement"
    assert observation["board"]["primary_focus"]["stage_key"] == "api_b_judgement"
    assert observation["board"]["primary_focus"]["source_label"] == "API-B"
    assert "stage_owner" not in observation["board"]["primary_focus"]
    assert observation["board"]["hero_summary"] == observation["board"]["summary"]
    assert "hero_pills" not in observation["board"]
    assert not any(
        note.get("key") == "governance_waiting"
        for note in observation["board"]["observation_notes"]
    )
    assert not any("待认领" in str(note.get("title") or "") for note in observation["board"]["observation_notes"])
    assert not any(note.get("key") == "api_b_scope" for note in observation["board"]["observation_notes"])
    assert not any(note.get("key") == "recent_activity" for note in observation["board"]["observation_notes"])
    assert not any(note.get("key") == "ready_boundary" for note in observation["board"]["observation_notes"])
    assert not any(note.get("key") == "user_chain_signal" for note in observation["board"]["observation_notes"])
    assert not any(note.get("key") == "protocol_contract" for note in observation["board"]["observation_notes"])
    assert "current_cards" not in observation["board"]
    assert _observation_loop_stage(observation, "api_a_execution")["status"] == "ready"
    assert _observation_loop_stage(observation, "api_a_execution")["focus_task"]["title"] == "第一个自主学习链路项"
    assert [group["key"] for group in observation["chain"]["segments"]] == group_keys
    assert api_b_judgement["source_label"] == "API-B"
    assert "owner" not in api_b_judgement
    assert api_b_judgement["stage_label"] == "判断在途"
    assert api_b_judgement["segment_kind"] == "api_b_judgement"
    assert api_b_judgement["decor_class"] == "supervisor"
    assert "display_decor" not in api_b_judgement
    assert "display_copy" not in api_b_judgement
    assert api_a_handoff["decor_class"] == "agent"
    assert api_a_handoff["source_label"] == "API-A"
    assert api_b_judgement["item_label"] == "判断项"
    assert api_a_handoff["item_label"] == "待接手项"
    assert api_b_judgement["event_label"] == "动作"
    assert api_a_handoff["trace_label"] == "回合"
    assert api_b_judgement["projection_scope"] == "chain_segment_projection"
    assert api_b_judgement["payload_count"] == 3
    assert api_b_judgement["event_count"] >= 1
    assert api_b_judgement["trace_count"] >= 1
    assert api_b_judgement["segment_status"] in {"active", "ready"}
    assert api_b_judgement["segment_status_label"] in {"当前有流动", "已有观测"}
    assert api_b_judgement["focus_item"]["observation_role"] == "api_b_judgement"
    assert api_b_judgement["latest_item"]["title"] == "Supervisor first task"
    assert api_b_judgement["latest_summary"]
    assert api_b_judgement["drawer_summary"].startswith("API-B")
    assert "当前可见判断项" in api_b_judgement["drawer_counts_summary"]
    assert "没有可见判断项" in api_b_judgement["drawer_empty_items_text"]
    assert api_b_judgement["drawer_recent_events_label"] == "最近动作"
    assert api_b_judgement["drawer_recent_traces_label"] == "最近回合"
    assert "判断项" in api_b_judgement["footer_text"]
    assert isinstance(api_b_judgement["recent_events"], list)
    assert api_b_judgement["recent_event_count"] >= 1
    assert isinstance(api_b_judgement["recent_traces"], list)
    assert api_b_judgement["recent_traces"][0]["trace_id"] == supervisor_task_1["tasks"][0]["trace_id"]
    assert api_b_judgement["recent_traces"][0]["detail"]["record_count"] >= 1
    assert isinstance(
        api_b_judgement["recent_traces"][0]["detail"]["source_counts"],
        dict,
    )
    assert isinstance(
        api_b_judgement["recent_traces"][0]["detail"]["timeline_preview"],
        list,
    )
    assert isinstance(
        api_b_judgement["recent_traces"][0]["detail"]["timeline_events"],
        list,
    )
    assert api_b_judgement["latest_trace_detail"]["trace_id"] == supervisor_task_1["tasks"][0]["trace_id"]
    assert "api_b" not in observation
    assert "api_a" not in observation
    assert "mem" not in observation
    assert "reread" not in observation
    assert _observation_loop_stage(observation, "api_b_judgement")["status"] == "active"
    assert _observation_loop_stage(observation, "api_a_execution")["status"] == "ready"
    assert _observation_loop_stage(observation, "mem_writeback")["status"] == "idle"
    assert _observation_loop_stage(observation, "api_b_judgement")["observation_role"] == "api_b_judgement"
    assert _observation_loop_stage(observation, "api_a_execution")["lane"] == "agent"
    assert [card["observation_role"] for card in observation["loop"]["stage_cards"]] == loop_stage_keys
    assert [card["observation_stage_label"] for card in observation["loop"]["stage_cards"]] == [
        "API-B 判断阶段",
        "API-A 接手 / 执行观测阶段",
        "Mem 写回阶段",
        "API-B 再读取阶段",
    ]
    assert [entry["key"] for entry in observation["loop"]["rail_entries"]] == loop_stage_keys
    assert observation["loop"]["rail_entries"][0]["source_label"] == "API-B"
    assert [card["stage_key"] for card in observation["loop"]["stage_cards"]] == loop_stage_keys
    assert observation["loop"]["stage_cards"][1]["source_label"] == "API-A"
    assert observation["loop"]["stage_cards"][0]["focus_task"]["title"] == "Supervisor first task"
    assert observation["loop"]["stage_cards"][1]["focus_task"]["title"] == "第一个自主学习链路项"
    assert observation["loop"]["stage_cards"][1]["chain_reason"]
    assert observation["loop"]["stage_cards"][1]["activity_text"]
    assert "owner" not in observation["loop"]["stage_cards"][0]
    assert all("stage_owner" not in card for card in observation["loop"]["stage_cards"])
    assert [card["lane"] for card in observation["loop"]["stage_cards"]] == [
        "supervisor",
        "agent",
        "mem",
        "supervisor",
    ]
    assert all("state" in entry and entry["state"] for entry in observation["loop"]["rail_entries"])
    assert all("note" in entry for entry in observation["loop"]["rail_entries"])
    assert all(isinstance(entry.get("focus"), bool) for entry in observation["loop"]["rail_entries"])
    assert _observation_loop_stage(observation, "api_b_judgement")["transition_hint"] == "判断通过后交给 API-A 接手。"
    assert _observation_loop_stage(observation, "api_b_judgement")["card_subtitle"].startswith("API-B 判断阶段")
    assert _observation_loop_stage(observation, "api_b_judgement")["focus_task"]["title"] == "Supervisor first task"
    assert _observation_loop_stage(observation, "api_b_judgement")["focus_task"]["display_status"] == "已转交"
    assert _observation_loop_stage(observation, "api_a_execution")["focus_task"]["title"] == "第一个自主学习链路项"
    assert _observation_loop_stage(observation, "api_a_execution")["focus_task"]["display_status"] == "已转交"
    assert [item["title"] for item in api_b_judgement["items"]] == [
        "Supervisor first task",
        "Supervisor second task",
        "Agent second creative task",
    ]
    assert "第一个自主学习链路项" not in [
        item["title"] for item in api_b_judgement["items"]
    ]
    assert [item["display_status"] for item in api_b_judgement["items"]] == ["已转交", "待判断", "待判断"]
    assert [item["lane"] for item in api_b_judgement["items"]] == ["supervisor", "supervisor", "supervisor"]
    assert api_b_judgement["items"][0]["observation_card_subtitle"]
    assert api_b_judgement["items"][0]["identity_hint"]
    assert "judgement_hint" in api_b_judgement["items"][0]
    assert "governance_hint" not in api_b_judgement["items"][0]
    assert api_a_handoff["items"][0]["observation_card_subtitle"]
    assert [item["title"] for item in api_a_handoff["items"]] == ["第一个自主学习链路项"]
    assert [item["display_status"] for item in api_a_handoff["items"]] == ["已转交"]
    assert [item["lane"] for item in api_a_handoff["items"]] == ["agent"]
    assert observation["metrics"]["slot_overview"] == "slot-A / slot-B"
    assert observation["metrics"]["chain_projection"]["api_b_judgement"] == 3
    assert observation["metrics"]["chain_projection"]["api_a_running"] == 0
    assert observation["metrics"]["chain_projection"]["api_a_handoff"] == 1
    assert observation["metrics"]["chain_projection"]["writeback_history"] == 0
    assert observation["runtime"]["snapshot_source"] == "live"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_keeps_running_api_a_task_out_of_ready_segment(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "运行中的自主学习链路项",
            "task_type": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        reason="API-B handed off for API-A claim",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="running",
        reason="claimed by API-A executor",
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]
    api_a_handoff = _observation_section(observation, "api_a_handoff")
    api_a_execution = _observation_loop_stage(observation, "api_a_execution")
    notes = list(observation["board"].get("observation_notes") or [])

    assert api_a_handoff["items"] == []
    assert api_a_handoff["payload_count"] == 0
    assert api_a_execution["status"] == "active"
    assert api_a_execution["focus_task"]["title"] == "运行中的自主学习链路项"
    assert observation["counts"]["api_a_running"] == 1
    assert observation["runtime"]["api_a_running_count"] == 1
    assert any(
        note.get("title") == "API-A 执行中"
        and "写回后会回到这里" in str(note.get("text") or "")
        for note in notes
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_maps_running_api_a_task_to_handoff_scene(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "正在执行的自主学习链路项",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        reason="API-B handed off for API-A claim",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="running",
        reason="claimed by API-A executor",
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "handoff"
    assert "自主交接中" in state["title"]
    assert "已交给 API-A 自主执行面处理" in state["summary"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_observed_candidates_deduplicate_tasks_by_key(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }
    )
    supervisor._record_supervisor_ui_activity(
        "endogenous_drive_evaluated",
        scene="drive",
        summary="已缓存内生驱动候选。",
        metadata={
            "candidates": [
                {
                    "title": "重复链路候选",
                    "stable_key": "candidate-dup",
                    "value_tags": ["continuity"],
                    "utility": 0.91,
                    "metadata": {
                        "endogenous_drive_key": "candidate-dup",
                        "scheduled_for": "2026-06-28T01:00:00",
                    },
                },
                {
                    "title": "唯一链路候选",
                    "stable_key": "candidate-unique",
                    "value_tags": ["creativity"],
                    "utility": 0.88,
                    "metadata": {
                        "endogenous_drive_key": "candidate-unique",
                        "scheduled_for": "2026-06-28T02:00:00",
                    },
                },
            ]
        },
    )

    await supervisor.plan_autonomous_chain_task(
        {
            "title": "已被观察到的治理任务",
            "metadata": {
                "endogenous_drive_key": "candidate-dup",
                "scheduled_for": "2026-06-28T01:00:00",
            },
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]

    judgement = _observation_section(observation, "api_b_judgement")
    candidates = _observation_section(observation, "api_b_candidates")

    assert judgement["items"][0]["title"] == "已被观察到的治理任务"
    assert [item["title"] for item in candidates["items"]] == ["唯一链路候选"]
    assert candidates["items"][0]["display_status"] == "候选形成"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_does_not_show_completed_drive_candidate_residue(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
        }
    )
    drive_key = "creativity:self_learning:cognitive_review:memory"
    supervisor._record_supervisor_ui_activity(
        "endogenous_drive_evaluated",
        scene="planning",
        summary="旧候选快照。",
        metadata={
            "candidates": [
                {
                    "title": "已完成但仍在快照中的候选",
                    "stable_key": drive_key,
                    "metadata": {"endogenous_drive_key": drive_key},
                }
            ]
        },
    )

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "已完成但仍在快照中的候选",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "endogenous_drive_key": drive_key,
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        reason="API-B handed off to API-A",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="running",
        reason="claimed by API-A",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="completed",
        reason="writeback completed",
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]

    candidates = _observation_section(observation, "api_b_candidates")
    writebacks = _observation_section(observation, "mem_recent")

    assert candidates["items"] == []
    assert writebacks["items"][0]["task_id"] == task_id
    assert writebacks["items"][0]["status"] == "completed"


def test_latest_drive_candidate_snapshot_stops_at_newer_idle_event(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._record_supervisor_ui_activity(
        "endogenous_drive_evaluated",
        scene="planning",
        summary="旧候选快照。",
        metadata={"candidates": [{"title": "旧候选"}]},
    )
    supervisor._record_supervisor_ui_activity(
        "endogenous_drive_idle",
        scene="idle",
        summary="本轮没有候选。",
    )

    assert supervisor._latest_drive_candidate_snapshot() == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_recent_mem_writebacks_in_autonomous_loop(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "已完成的自主学习写回",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_result": {
                    "summary": "Summarized learning result for Mem writeback.",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        reason="Approved autonomous learning writeback.",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="running",
        reason="Autonomous learning writeback running.",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="completed",
        reason="已完成的自主学习写回。",
    )

    state = await supervisor.get_supervisor_ui_state()
    writeback = state["autonomous_observation"]["loop"]["recent_writebacks"][0]
    mem_recent = state["autonomous_observation"]["chain"]["segments"][3]["items"][0]
    mem_stage = _observation_loop_stage(state["autonomous_observation"], "mem_writeback")

    assert writeback["title"] == "已完成的自主学习写回"
    assert writeback["lane"] == "agent"
    assert writeback["status"] == "completed"
    assert mem_stage["focus_task"]["title"] == "已完成的自主学习写回"
    assert mem_stage["status"] == "ready"
    assert mem_recent["lane"] == "mem"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_ui_state_projects_cognition_judgement_and_uncertainty_for_web_room(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidates": []}
    )
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }
    )
    supervisor._persist_endogenous_cognition_state(
        {
            "perception": {
                "system_posture": "truth_guarded",
                "user_mode": "quiet",
                "api_b_judgement_count": 2,
                "api_a_handoff_count": 1,
                "api_a_running_count": 0,
                "active_sessions": 0,
                "recent_errors": 1,
                "learning_quality": 61,
                "correction_signals": 2,
                "idle_seconds": {"user": 120, "memory": 15},
            },
            "world_model": {
                "governance_load_state": "strained",
                "memory_pressure": 0.22,
                "truthfulness_pressure": 0.71,
                "learning_momentum": 0.33,
                "self_confidence": 0.44,
            },
            "needs": [
                {
                    "need_type": "truthfulness_repair",
                    "severity": 0.83,
                    "urgency": 0.8,
                    "confidence": 0.66,
                    "rationale": "Recent corrections suggest unresolved truthfulness debt.",
                }
            ],
            "intents": [
                {
                    "intent_type": "protect_truthfulness",
                    "priority": 0.86,
                    "output_channel": "governance_review",
                    "target_horizon": "next_cycle",
                    "rationale": "Protect truthfulness before expanding output.",
                }
            ],
            "signals": [
                {
                    "signal_type": "truthfulness_alert",
                    "priority": 0.72,
                    "message": "Truthfulness alerts have been rising.",
                }
            ],
            "adaptive_policy": {
                "learning_expansion_bias": 0.12,
                "truthfulness_bias": 0.77,
                "memory_continuity_bias": 0.15,
                "governance_hygiene_bias": 0.54,
                "body_growth_bias": 0.08,
                "observation_bias": 0.63,
                "candidate_throttle": 0.4,
                "candidate_budget": 2,
                "exploratory_learning_quota": 0,
                "body_growth_quota": 0,
                "preferred_focus": "truthfulness",
            },
            "judgement_core": {
                "primary_need": {"need_type": "truthfulness_repair"},
                "primary_intent": {"intent_type": "protect_truthfulness"},
            },
            "governance": {
                "preferred_focus": "truthfulness",
                "dominant_constraint": "api_b_judgement_blockage",
            },
            "proposal_cognition": {
                "assessment_trace": {
                    "available": True,
                    "dominant_constraint": "api_b_judgement_blockage",
                    "current_judgement": "在 grounding 修复前，复核应保持主导",
                    "why_not_improvement_now": "在直接进行身体改进前，应优先处理 truthfulness 治理。",
                    "why_not_improvement_now_count": 1,
                    "self_iteration_target": "truthfulness",
                    "self_iteration_hypothesis": "先修补 truthfulness 信号，再推进身体工作。",
                },
                "meta_cognition_profile": {
                    "current_judgement": "",
                    "dominant_constraint": "",
                    "self_iteration_focus": {
                        "domain": "truthfulness",
                        "hypothesis": "先修补 truthfulness 信号，再推进身体工作。",
                    },
                },
            },
            "uncertainty_ledger": {
                "active_count": 1,
                "highest_risk_domain": "truthfulness",
                "entries": [
                    {
                        "domain": "truthfulness",
                        "risk": 0.72,
                        "confidence": 0.64,
                        "why_uncertain": "Corrections are visible but still need targeted review.",
                        "observation_target": "truthfulness",
                        "recommended_probe": "review recent uncertain answers and correction signals",
                    }
                ],
            },
            "observation_program": {
                "highest_priority_target": "truthfulness",
                "entries": [
                    {
                        "target": "truthfulness",
                        "recommended_probe": "review recent uncertain answers and correction signals",
                        "recommended_next_step": "collect_observation",
                        "persistence_state": "stalled",
                    }
                ],
            },
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    cognition = state["cognition"]
    judgement = cognition["judgement"]
    uncertainty = cognition["uncertainty"]
    top_item = uncertainty["top_items"][0]

    assert judgement["focus_label"] == "真实性"
    assert judgement["dominant_constraint_label"] == "API-B 判断阻塞"
    assert judgement["primary_need_label"] == "修补真实性风险"
    assert judgement["primary_intent_label"] == "保护真实性"
    assert judgement["observation_target_label"] == "真实性侧"
    assert judgement["why_not_direct_improvement"][0] == "先处理真实性风险，再考虑直接替身改进"
    assert "真实性" in judgement["summary"]
    assert judgement["api_a_handoff_count"] == 1
    assert judgement["api_a_running_count"] == 0
    assert judgement["api_a_lane_summary"] == "API-B 已转交 1 个链路项，等待 API-A 接手。"
    assert cognition["perception"]["api_a_handoff_count"] == 1
    assert cognition["perception"]["api_a_running_count"] == 0
    assert uncertainty["highest_risk_label"] == "真实性侧"
    assert uncertainty["summary"] == "当前最需要补证据的是真实性侧。"
    assert top_item["domain_label"] == "真实性侧"
    assert top_item["risk_label"] == "72%"
    assert top_item["confidence_label"] == "64%"
    assert top_item["recommended_probe_label"] == "复核近期不确定回答与修正信号"
    assert top_item["recommended_next_step_label"] == "补观察证据"
    assert top_item["persistence_label"] == "长期未化解"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_ui_state_projects_recent_autonomous_activity_for_web_room(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidates": []}
    )
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {
                "active_sessions": 0,
                "counts": {},
                "last_autonomous_chain_execute_at": "2026-07-06T10:05:00",
                "recent_metadata": {
                    "autonomous_chain_execute": {
                        "source_service": "executor",
                        "task_type": "self_evolution",
                        "task_type_label": "自主改进",
                        "task_family": "body_switch",
                        "task_family_label": "身体切换",
                        "execution_kind": "body_switch",
                        "execution_kind_label": "身体切换",
                        "task_identity": {
                            "display_label": "身体切换",
                            "summary": "替身切换验收 (身体切换)",
                        },
                    }
                },
            },
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]
    recent = observation["board"]["recent_activity"]

    assert recent["kind"] == "autonomous_chain_execute"
    assert recent["phase_label"] == "执行回报"
    assert recent["title"] == "替身切换验收 (身体切换)"
    assert recent["summary"] == "API-A 子执行面 已向 API-B 回报 身体切换 的执行进展。"
    assert recent["source_label"] == "API-A 子执行面"
    assert recent["tone"] == "accent"
    assert observation["board"]["hero_summary"] == observation["board"]["summary"]
    assert "hero_pills" not in observation["board"]
    assert not any(
        note.get("key") == "recent_activity"
        for note in observation["board"]["observation_notes"]
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_keeps_supervisor_idle_when_only_agent_task_is_waiting(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidates": []}
    )
    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
        }
    )

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Agent waiting creative task",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    await supervisor.decide_autonomous_chain_task(
        planned["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "creative task ready"},
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "idle"
    assert "api_b" not in state["autonomous_observation"]
    assert _observation_loop_stage(state["autonomous_observation"], "api_b_judgement")["focus_task"] is None
    assert _observation_loop_stage(state["autonomous_observation"], "api_a_execution")["focus_task"]["title"] == "Agent waiting creative task"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_delegates_memory_compression_to_maintenance_adapter(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    expected = {
        "status": "compressed",
        "execution_route_hint": build_execution_route_hint("memory.compress"),
    }
    supervisor._execution_facade.memory_maintenance.trigger_memory_compression = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await _trigger_memory_compression(supervisor, {"namespace": "default"})

    assert result == expected
    assert result["execution_route_hint"]["preferred_entrypoint"]["gateway_path"] == "/api/executor/memory/compress"
    supervisor._execution_facade.memory_maintenance.trigger_memory_compression.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_compression_runtime_does_not_route_through_execution_facade_helper(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0, "handed_off": []})  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(  # type: ignore[method-assign]
        side_effect=asyncio.CancelledError()
    )
    original_memory_maintenance = supervisor._execution_facade.memory_maintenance
    facade_memory_maintenance = SimpleNamespace(
        trigger_memory_compression=AsyncMock(
            side_effect=AssertionError(
                "periodic compression should use the canonical maintenance executor directly"
            )
        )
    )
    supervisor._execution_facade.memory_maintenance = facade_memory_maintenance

    await supervisor._start_periodic_tasks()

    # Compression is now owned by the Memory Service (architecture baseline §3.4).
    # The supervisor no longer runs a compression loop — verify it's gone.
    # Compression task was removed from supervisor (baseline §3.4)
    assert not hasattr(supervisor, '_compression_task'), (
        "Supervisor should not have a _compression_task attribute "
        "(compression is now owned by Memory Service per baseline §3.4)"
    )
    supervisor._execution_facade.memory_maintenance = original_memory_maintenance

    assert supervisor._service_runtime.autonomous_chain_gate_active is False
    assert supervisor._autonomous_chain_review_task is None
    assert supervisor._endogenous_drive_task is None
    await supervisor._stop_periodic_tasks()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_runtime_does_not_start_autonomous_chain(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0})  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]
    await supervisor._start_periodic_tasks()

    assert supervisor._service_runtime.autonomous_chain_gate_active is False
    assert supervisor._service_runtime.stellar_mode is StellarMode.DAILY_COMPANION
    assert supervisor._companion_observation_task is not None
    assert supervisor._autonomous_chain_review_task is None
    assert supervisor._endogenous_drive_task is None
    await supervisor._stop_periodic_tasks()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_autonomous_chain_deactivate_stops_enabled_runtime(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0})  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    await supervisor._start_periodic_tasks()
    companion_task = supervisor._companion_observation_task
    await supervisor._start_autonomous_chain_gate()

    assert companion_task is not None and companion_task.cancelled()
    assert supervisor._service_runtime.stellar_mode is StellarMode.AUTO_EVOLUTION
    assert supervisor._companion_observation_task is None
    packet = supervisor._service_runtime.auto_evidence_packet
    assert packet["mode"] == "auto_evolution"
    assert packet["source_domains"] == ["evolution"]
    assert packet["frozen"] is True
    assert "live_user_activity" in packet["excluded_signals"]

    stopped = await supervisor.deactivate_autonomous_chain_gate({})
    assert stopped["autonomous_chain_gate_active"] is False
    assert stopped["mode"] == "daily_companion"
    assert stopped["companion_loop_running"] is True
    assert "autonomous_chain_runtime_mode" not in stopped
    assert supervisor._autonomous_chain_review_task is None
    assert supervisor._endogenous_drive_task is None
    assert supervisor._service_runtime.auto_evidence_packet == {}
    await supervisor._stop_periodic_tasks()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_daily_companion_cycle_defaults_to_silence_without_intent(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.get_runtime_observation_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "ok",
            "observation_input": {
                "activity": {"active_sessions": 1},
                "user_chain_signal": {"is_quiet": False},
            },
        }
    )

    snapshot = await supervisor._run_daily_companion_observation_cycle()

    assert snapshot["mode"] == "daily_companion"
    assert snapshot["source"] == "voidcube_internal_events"
    assert snapshot["intent_state"] == "unknown"
    assert snapshot["disposition"] == "silent"
    assert snapshot["reason"] == "insufficient_user_intent_evidence"
    assert supervisor._service_runtime.latest_companion_observation == snapshot


@pytest.mark.asyncio
@pytest.mark.unit
async def test_daily_companion_calls_api_b_only_for_changed_complete_evidence(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.get_runtime_observation_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "ok",
            "observation_input": {
                "activity": {
                    "active_sessions": 1,
                    "counts": {"error_count": 1},
                    "recent_metadata": {
                        "user_request": {
                            "text": "修复记忆隔离问题",
                            "request_id": "request-1",
                        },
                        "agent_work": {
                            "summary": "正在修改无关的 UI 动画",
                            "trace_id": "trace-1",
                        },
                    },
                }
            },
        }
    )
    supervisor._recall_companion_context = AsyncMock(return_value="memory context")  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "inferred_goal": "修复记忆隔离问题",
            "goal_confidence": 0.95,
            "deviation_summary": "API-A 当前工作偏离用户目标",
            "deviation_confidence": 0.9,
            "help_value": 0.85,
            "interruption_cost": 0.2,
            "disposition": "remind",
            "reason": "目标与当前活动不一致",
            "reminder_text": "当前工作似乎偏离了记忆隔离目标。",
            "evidence_refs": ["gateway:user_request:request-1", "gateway:agent_work:trace-1"],
        }
    )

    first = await supervisor._run_daily_companion_observation_cycle()
    second = await supervisor._run_daily_companion_observation_cycle()

    assert first["intent_state"] == "understood"
    assert first["disposition"] == "remind"
    assert first["judgement"]["reminder_text"]
    assert second["disposition"] == "silent"
    assert second["reason"] == "internal_activity_unchanged"
    supervisor._call_companion_model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_daily_companion_rejects_low_confidence_reminder(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    normalized = supervisor._normalize_companion_judgement(
        {
            "inferred_goal": "可能的目标",
            "goal_confidence": 0.4,
            "deviation_confidence": 0.9,
            "help_value": 0.9,
            "interruption_cost": 0.1,
            "disposition": "remind",
            "reminder_text": "不应发出的提醒",
            "evidence_refs": ["gateway:user_request:request-1"],
        },
        {"evidence_refs": ["gateway:user_request:request-1"]},
    )

    assert normalized["intent_state"] == "uncertain"
    assert normalized["disposition"] == "silent"
    assert normalized["judgement"]["reminder_text"] == ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_proactive_reminder_delivers_only_after_policy_gate_and_records_audit(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._voice_manager.status = Mock(return_value={"enabled": True})
    supervisor._voice_manager.speak_text = AsyncMock(
        return_value={"status": "complete", "reply_text": "请检查当前任务。"}
    )
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    observation = {
        "evidence_key": "evidence-reminder-1",
        "reason": "goal_deviation_supported",
        "evidence": {"evidence_refs": ["gateway:user_request:r1"]},
        "judgement": {
            "reminder_text": "请检查当前任务。",
            "evidence_refs": ["gateway:agent_work:a1"],
        },
    }

    supervisor._queue_proactive_reminder(observation, now=now)
    delivered = await supervisor._deliver_pending_proactive_reminder(now=now)

    assert delivered["status"] == "delivered"
    supervisor._voice_manager.speak_text.assert_awaited_once_with(
        "请检查当前任务。",
        reason="proactive_companion_reminder",
    )
    assert supervisor._service_runtime.pending_proactive_reminder == {}
    assert supervisor._service_runtime.last_proactive_reminder_evidence_key == "evidence-reminder-1"
    supervisor._touch_gateway_activity.assert_awaited_once()
    assert supervisor._touch_gateway_activity.await_args.args[0] == "companion_proactive_reminder"

    supervisor._queue_proactive_reminder(observation, now=now + timedelta(minutes=1))
    suppressed = await supervisor._deliver_pending_proactive_reminder(
        now=now + timedelta(minutes=1)
    )
    assert suppressed["reason"] == "proactive_reminder_cooldown"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_proactive_reminder_waits_for_voice_and_respects_do_not_disturb(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={
                    "companion_proactive_dnd_start": "22:00",
                    "companion_proactive_dnd_end": "08:00",
                }
            )
        }
    )
    supervisor._voice_manager.status = Mock(return_value={"enabled": False})
    supervisor._voice_manager.speak_text = AsyncMock()
    observation = {
        "evidence_key": "evidence-reminder-2",
        "evidence": {},
        "judgement": {"reminder_text": "提醒内容", "evidence_refs": ["ref-2"]},
    }
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    dnd_time = datetime(2026, 5, 25, 23, 0, tzinfo=local_tz)
    supervisor._queue_proactive_reminder(observation, now=dnd_time)

    suppressed = await supervisor._deliver_pending_proactive_reminder(now=dnd_time)
    assert suppressed["reason"] == "do_not_disturb_window"
    supervisor._voice_manager.status.return_value = {"enabled": False}
    waiting = await supervisor._deliver_pending_proactive_reminder(
        now=datetime(2026, 5, 26, 9, 0, tzinfo=local_tz)
    )
    assert waiting["reason"] == "voice_output_disabled"
    supervisor._voice_manager.speak_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_companion_text_message_reuses_daily_mode_and_companion_memory(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._recall_companion_context = AsyncMock(return_value="API-A memory")  # type: ignore[method-assign]
    supervisor._call_companion_model = AsyncMock(  # type: ignore[method-assign]
        return_value={"reply_text": "我看到了当前任务上下文。", "reason": "direct_user_request"}
    )
    supervisor._persist_companion_turn_pair = AsyncMock(return_value=True)  # type: ignore[method-assign]

    result = await supervisor.handle_companion_message(
        text="星子，我现在在做什么？",
        session_id="voice-session-1",
    )

    assert result["status"] == "ok"
    assert result["disposition"] == "respond_to_user"
    assert result["memory_persisted"] is True
    supervisor._persist_companion_turn_pair.assert_awaited_once_with(
        session_id="voice-session-1",
        user_text="星子，我现在在做什么？",
        assistant_text="我看到了当前任务上下文。",
    )

    supervisor._service_runtime.stellar_mode = StellarMode.AUTO_EVOLUTION
    unavailable = await supervisor.handle_companion_message(text="还在吗？")
    assert unavailable["status"] == "unavailable"
    assert unavailable["reason"] == "stellar_auto_evolution_active"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stellar_mode_status_route_exposes_canonical_default(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    payload = await supervisor.get_stellar_mode_status()

    assert payload["mode"] == "daily_companion"
    assert payload["autonomous_chain_gate_active"] is False
    await supervisor._stop_periodic_tasks()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_autonomous_chain_deactivate_closes_running_tasks(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Execution interrupted by gate deactivation",
            "task_type": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    await supervisor.decide_autonomous_chain_task(
        task_id,
        {"decision": "approved", "reason": "ready"},
    )
    await supervisor.decide_autonomous_chain_task(
        task_id,
        {
            "decision": "running",
            "actor": "cli_agent",
            "session_id": "gate-stop-owner",
            "reason": "claimed",
        },
    )

    await supervisor.deactivate_autonomous_chain_gate({})

    task = supervisor._autonomous_chain_store.get_task(task_id)
    assert task.status == "failed"
    assert task.decision_history[-1].context == {
        "failure_kind": "interrupted_by_gate_deactivation"
    }
    recovered = AutonomousChainStore(tmp_path / "recovered-after-stop.json")
    recovered.recover_from_governance_events(
        supervisor._governor.governance_repository.list_events()
    )
    assert recovered.get_task(task_id).status == "failed"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_autonomous_chain_review_cycle_hands_off_approved_formal_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._body_upgrade_executor.execute_body_upgrade = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "upgrade_awaiting_user_consent"}
    )

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "自动交接的正式身体切换",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "source_commit": "aaa111",
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["agent/stream_handler.py"],
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
            "task_family_decisions": {
                "body_switch": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                }
            },
            "governance_task_type_decisions": {
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                }
            },
        }
    )
    supervisor._review_task_governance_with_supervisor = AsyncMock(return_value={})  # type: ignore[method-assign]

    cycle = await supervisor._run_autonomous_chain_review_cycle()

    task_snapshot = await supervisor.get_autonomous_chain_task(task_id)
    assert cycle["reviewed"] == 1
    assert cycle["handed_off"] == [{"task_id": task_id, "status": "autonomous_chain_execution_executed"}]
    assert task_snapshot["status"] == "awaiting_user_consent"
    assert task_snapshot["metadata"]["execution_result"]["status"] == "autonomous_chain_execution_executed"
    supervisor._body_upgrade_executor.execute_body_upgrade.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_execution_handoff_unknown_executor_status_retries_instead_of_completing(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    class UnknownStatusFacade:
        async def execute_autonomous_chain_request(self, _payload):
            return {"status": "accepted"}

    supervisor._execution_facade = UnknownStatusFacade()

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Do not complete on unknown executor status",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "source_commit": "aaa111",
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["agent/stream_handler.py"],
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    supervisor.evaluate_drive_input = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
            "task_family_decisions": {
                "body_switch": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                }
            },
            "governance_task_type_decisions": {
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                }
            },
        }
    )
    supervisor._review_task_governance_with_supervisor = AsyncMock(return_value={})  # type: ignore[method-assign]

    cycle = await supervisor._run_autonomous_chain_review_cycle()
    task_snapshot = await supervisor.get_autonomous_chain_task(task_id)

    assert cycle["handed_off"] == [{"task_id": task_id, "status": "accepted"}]
    assert task_snapshot["status"] == "approved"
    assert task_snapshot["metadata"]["execution_failed"] is True
    assert task_snapshot["metadata"]["execution_failure_count"] == 1
    assert task_snapshot["metadata"]["execution_result"]["status"] == "accepted"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_autonomous_chain_review_runtime_invokes_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"autonomous_chain_review_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    await supervisor._start_autonomous_chain_gate()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._autonomous_chain_review_task

    supervisor._run_autonomous_chain_review_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Drive loop was also started by the autonomous-chain gate.
    supervisor._endogenous_drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_endogenous_drive_runtime_invokes_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0, "handed_off": []})  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"endogenous_drive_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    await supervisor._start_autonomous_chain_gate()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task

    supervisor._run_endogenous_drive_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Review loop was also started by the autonomous-chain gate.
    supervisor._autonomous_chain_review_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._autonomous_chain_review_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_start_autonomous_chain_gate_renotifies_gateway_when_already_active(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._service_runtime.autonomous_chain_gate_active = True
    supervisor._notify_gateway_autonomous_chain_gate = AsyncMock()  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock()  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock()  # type: ignore[method-assign]

    await supervisor._start_autonomous_chain_gate()

    supervisor._notify_gateway_autonomous_chain_gate.assert_awaited_once_with(active=True)  # type: ignore[attr-defined]
    assert supervisor._autonomous_chain_review_task is None
    assert supervisor._endogenous_drive_task is None


@pytest.mark.unit
def test_supervisor_fastapi_lifespan_starts_and_stops_periodic_runtime(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.register_with_gateway = AsyncMock(return_value="service-1")  # type: ignore[method-assign]
    supervisor._start_periodic_tasks = AsyncMock()  # type: ignore[method-assign]
    supervisor._stop_periodic_tasks = AsyncMock()  # type: ignore[method-assign]

    with TestClient(supervisor.app) as client:
        response = client.get("/")
        assert response.status_code == 200

    supervisor.register_with_gateway.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._start_periodic_tasks.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._stop_periodic_tasks.assert_awaited_once_with()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_autonomous_chain_review_loop_survives_iteration_exception(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("transient review failure"), asyncio.CancelledError()]
    )
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"autonomous_chain_review_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    await supervisor._start_autonomous_chain_gate()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._autonomous_chain_review_task

    assert supervisor._run_autonomous_chain_review_cycle.await_count == 2  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Drive loop was also started by the autonomous-chain gate.
    supervisor._endogenous_drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_internal_body_upgrade_pipeline_does_not_route_through_facade_execution_helpers(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        execute_body_upgrade=supervisor._body_upgrade_executor.execute_body_upgrade,
        run_body_probe=AsyncMock(
            side_effect=AssertionError("execution facade run_body_probe should not be used internally")
        ),
        start_managed_agent=AsyncMock(
            side_effect=AssertionError("execution facade start_managed_agent should not be used internally")
        ),
    )
    try:
        result = await _execute_body_upgrade(supervisor, {"body_version": "v2"})
    finally:
        supervisor._execution_facade = original_facade

    assert result["status"] == "upgrade_awaiting_user_consent"
    assert result["probe_execution"]["report"]["overall_passed"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_accepts_self_learning_conclusion_submission_into_backlog(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    learning = SelfLearningConclusionStore(tmp_path / "self-learning")

    topic = learning.create_topic(
        title="网关活动支撑的活动护栏",
        reason="需要一个由学习证据支撑的正式自主链路提案。",
        tags=["gateway", "idle"],
    )
    session = learning.plan_session(topic=topic, planned_minutes=20, trigger="idle")
    experiment = learning.record_experiment(
        topic=topic,
        session=session,
        hypothesis="网关活动事实应参与空闲判断门控。",
        method="对比只看时钟的判断与网关活动标记。",
        observations=["网关标记更贴近真实的用户打断模式。"],
        outcome="passed",
        compared_against=["clock-only"],
    )
    conclusion = learning.submit_conclusion(
        topic=topic,
        session=session,
        experiments=[experiment],
        comparisons=["gateway-facts > clock-only"],
        summary="把基于网关的空闲判断提升为 API-B 判断在途任务。",
        verified=True,
        recommendations=[
            LearningRecommendation(
                recommendation_type="propose_evolution_task",
                title="采用基于网关的空闲判断",
                summary="创建 API-B 判断在途任务，而不是直接改动运行时。",
                evidence={"priority_reason": "reduces false activity-guard approvals"},
            )
        ],
    )

    submission = learning.build_supervisor_payload(conclusion)
    assert "task_type" not in submission["proposals"][0]
    result = await supervisor.submit_self_learning_conclusion(submission)

    assert result["status"] == "accepted"
    assert result["count"] == 1
    assert result["tasks"][0]["title"] == "采用基于网关的空闲判断"
    assert result["tasks"][0]["task_type"] == "self_evolution"
    assert result["tasks"][0]["governance_task_type"] == "self_evolution"
    assert result["tasks"][0]["task_family"] == "general_self_evolution"
    assert result["tasks"][0]["execution_kind"] == "general_self_evolution"
    assert result["tasks"][0]["metadata"]["conclusion_id"] == conclusion.conclusion_id
    supervisor._touch_gateway_activity.assert_awaited_once_with(  # type: ignore[attr-defined]
        "self_learning",
        metadata={
            "action": "self_learning_submission",
            "count": 1,
            "conclusion_id": conclusion.conclusion_id,
        },
    )


@pytest.mark.unit
def test_supervisor_display_and_trace_labels_leave_unknown_status_unchanged(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    assert supervisor._observation_display_status({"status": "orphaned"}) == "orphaned"
    assert supervisor._trace_status_label("orphaned") == "orphaned"
    card = supervisor._build_observation_card(
        {"title": "未知状态链路项", "status": "orphaned"},
        lane="supervisor",
    )
    assert card is not None
    assert card["status"] == "orphaned"
    assert card["display_status"] == "orphaned"
    assert supervisor._observation_display_status({"status": "completed"}) == "已完成"
    assert supervisor._trace_status_label("completed") == "已写回"






