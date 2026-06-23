from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.execution.facade import VoidCubeExecutionFacade
from systems.execution.service import VoidCubeExecutionService


def _make_service() -> tuple[VoidCubeExecutionService, SimpleNamespace]:
    adapters = SimpleNamespace(
        agent_lifecycle=SimpleNamespace(
            start_managed_agent=AsyncMock(return_value={"status": "started", "instance_id": "agent-1"}),
            stop_agent=AsyncMock(return_value={"status": "stopped"}),
            activate_body=AsyncMock(return_value={"status": "activated"}),
        ),
        watch_window=SimpleNamespace(
            reconcile_watch_window_outcome=AsyncMock(return_value={"action": "noop"}),
            get_watch_window_status=Mock(
                return_value={
                    "watch_window": {"status": "active"},
                    "task_running": False,
                    "last_outcome": None,
                }
            ),
            evaluate_watch_window=AsyncMock(return_value={"status": "watch_window_evaluated"}),
        ),
        body_lifecycle=SimpleNamespace(
            get_body_registry=Mock(return_value={"registry": {"active_slot": "slot-A"}}),
            get_active_body_target=Mock(return_value={"slot_id": "slot-A"}),
            list_body_slots=Mock(return_value={"slots": {"slot-A": {}}}),
            get_body_slot=Mock(return_value={"slot_id": "slot-A"}),
            prepare_body_slot=AsyncMock(return_value={"status": "slot_prepared"}),
            mark_body_candidate=AsyncMock(return_value={"status": "candidate_marked"}),
            record_body_probe_report=AsyncMock(return_value={"status": "probe_report_recorded"}),
            run_body_probe=AsyncMock(return_value={"status": "probe_executed"}),
        ),
        body_upgrade=SimpleNamespace(
            execute_body_upgrade=AsyncMock(return_value={"status": "upgrade_executed"}),
        ),
        memory_maintenance=SimpleNamespace(
            trigger_memory_compression=AsyncMock(return_value={"status": "compressed"}),
        ),
    )
    facade = VoidCubeExecutionFacade(
        agent_lifecycle=adapters.agent_lifecycle,
        watch_window=adapters.watch_window,
        body_lifecycle=adapters.body_lifecycle,
        body_upgrade=adapters.body_upgrade,
        memory_maintenance=adapters.memory_maintenance,
    )
    service = VoidCubeExecutionService(facade)
    return service, adapters


@pytest.mark.unit
def test_execution_service_health_describes_execution_only_boundary():
    service, _ = _make_service()
    client = TestClient(service.app)

    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "executor"
    assert payload["boundary"] == "execution_only"
    assert payload["decision_policy"] == "external_governor_required"
    assert payload["preferred_gateway_prefix"] == "/api/executor"
    assert payload["direct_executor_prefix"] == "/executor"
    assert payload["executor_access_policy"]["failure_mode"] == "executor_required"
    assert "/body/upgrade/execute" in payload["routes"]["body_upgrade"]
    assert "/self-evolution/execute" in payload["routes"]["formal_self_evolution"]
    assert "/self-learning/execute" in payload["routes"]["self_learning"]
    assert "/body/watch-window/status" in payload["routes"]["body_lifecycle"]
    assert "legacy_compatibility" not in payload["routes"]


@pytest.mark.unit
def test_execution_service_delegates_agent_lifecycle_routes():
    service, adapters = _make_service()
    client = TestClient(service.app)

    start = client.post("/executor/agents/start", json={})
    stop = client.delete("/executor/agents/agent-1")
    activate = client.post("/executor/body/activate", json={"slot_id": "slot-B"})

    assert start.status_code == 200
    assert start.json()["status"] == "started"
    assert stop.json()["status"] == "stopped"
    assert activate.json()["status"] == "activated"
    adapters.agent_lifecycle.start_managed_agent.assert_awaited_once_with({})
    adapters.agent_lifecycle.stop_agent.assert_awaited_once_with("agent-1")
    adapters.agent_lifecycle.activate_body.assert_awaited_once_with({"slot_id": "slot-B"})


@pytest.mark.unit
def test_execution_service_accepts_gateway_executor_prefix_routes():
    service, adapters = _make_service()
    client = TestClient(service.app)

    start = client.post("/executor/agents/start", json={})
    upgrade = client.post("/executor/body/upgrade/execute", json={"slot_id": "slot-B"})

    assert start.status_code == 200
    assert start.json()["status"] == "started"
    assert upgrade.status_code == 200
    assert upgrade.json()["status"] == "upgrade_executed"
    adapters.agent_lifecycle.start_managed_agent.assert_awaited_once_with({})
    adapters.body_upgrade.execute_body_upgrade.assert_awaited_once_with({"slot_id": "slot-B"})


@pytest.mark.unit
def test_execution_service_accepts_gateway_executor_prefix_query_routes():
    service, adapters = _make_service()
    client = TestClient(service.app)

    registry = client.get("/executor/body/registry")
    active_target = client.get("/executor/body/active-target")
    slots = client.get("/executor/body/slots")
    slot = client.get("/executor/body/slots/slot-A")
    watch_window = client.get("/executor/body/watch-window/status")
    watch_window_eval = client.post("/executor/body/watch-window/evaluate", json={"healthy_override": True})

    assert registry.status_code == 200
    assert registry.json()["registry"]["active_slot"] == "slot-A"
    assert active_target.json()["slot_id"] == "slot-A"
    assert "slot-A" in slots.json()["slots"]
    assert slot.json()["slot_id"] == "slot-A"
    assert watch_window.json()["watch_window"]["status"] == "active"
    assert watch_window_eval.json()["status"] == "watch_window_evaluated"
    adapters.body_lifecycle.get_body_registry.assert_called_once_with()
    adapters.body_lifecycle.get_active_body_target.assert_called_once_with()
    adapters.body_lifecycle.list_body_slots.assert_called_once_with()
    adapters.body_lifecycle.get_body_slot.assert_called_once_with("slot-A")
    adapters.watch_window.get_watch_window_status.assert_called_once_with()
    adapters.watch_window.evaluate_watch_window.assert_awaited_once_with({"healthy_override": True})


@pytest.mark.unit
def test_execution_service_delegates_body_lifecycle_and_upgrade_routes():
    service, adapters = _make_service()
    client = TestClient(service.app)

    prepare = client.post("/executor/body/slots/slot-B/prepare", json={"clear_existing": False})
    candidate = client.post("/executor/body/slots/slot-B/candidate", json={"body_version": "v2"})
    probe_report = client.post("/executor/body/probe/report", json={"slot_id": "slot-B", "checks": []})
    probe_run = client.post("/executor/body/probe/run", json={"slot_id": "slot-B"})
    upgrade = client.post("/executor/body/upgrade/execute", json={"slot_id": "slot-B"})

    assert prepare.json()["status"] == "slot_prepared"
    assert candidate.json()["status"] == "candidate_marked"
    assert probe_report.json()["status"] == "probe_report_recorded"
    assert probe_run.json()["status"] == "probe_executed"
    assert upgrade.json()["status"] == "upgrade_executed"
    adapters.body_lifecycle.prepare_body_slot.assert_awaited_once_with("slot-B", {"clear_existing": False})
    adapters.body_lifecycle.mark_body_candidate.assert_awaited_once_with("slot-B", {"body_version": "v2"})
    adapters.body_upgrade.execute_body_upgrade.assert_awaited_once_with({"slot_id": "slot-B"})


@pytest.mark.unit
def test_execution_service_accepts_only_formal_self_evolution_handoff_for_execution():
    service, adapters = _make_service()
    client = TestClient(service.app)

    response = client.post(
        "/executor/self-evolution/execute",
        json={
            "task_id": "task-1",
            "trace_id": "trace-http-1",
            "task_type": "self_evolution",
            "decision_id": "decision-http-1",
            "kind": "general_self_evolution",
            "source_actor": "mem_supervisor",
            "target_slot_id": "slot-B",
            "git_lineage": {
                "candidate_commit": "bbb222",
                "rollback_commit": "aaa111",
                "changed_files": ["agent/stream_handler.py"],
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "formal_self_evolution_executed"
    assert payload["execution_metadata"]["trace_id"] == "trace-http-1"
    assert payload["execution_metadata"]["governance_task_type"] == "self_evolution"
    assert payload["execution_metadata"]["task_family"] == "general_self_evolution"
    assert payload["execution_metadata"]["execution_kind"] == "general_self_evolution"
    assert payload["execution_metadata"]["decision_id"] == "decision-http-1"
    assert payload["execution_metadata"]["task_id"] == "task-1"
    assert payload["execution_request"]["status"] == "approved_for_execution"
    assert payload["execution_request"]["trace_id"] == "trace-http-1"
    adapters.body_upgrade.execute_body_upgrade.assert_awaited_once()


@pytest.mark.unit
def test_execution_service_delegates_maintenance_route():
    service, adapters = _make_service()
    client = TestClient(service.app)

    memory = client.post("/executor/memory/compress", json={"namespace": "default"})

    assert memory.json()["status"] == "compressed"
    adapters.memory_maintenance.trigger_memory_compression.assert_awaited_once_with({"namespace": "default"})
    missing_legacy = client.post("/upgrade/legacy", json={"branch": "main"})
    assert missing_legacy.status_code == 404


