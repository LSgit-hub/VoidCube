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
            rollback_body_improvement=AsyncMock(
                return_value={"status": "body_improvement_rollback_verified"}
            ),
        ),
        body_upgrade=SimpleNamespace(
            execute_body_upgrade=AsyncMock(return_value={"status": "upgrade_awaiting_user_consent"}),
            confirm_body_switch=AsyncMock(return_value={"status": "body_switch_activated"}),
        ),
        memory_maintenance=SimpleNamespace(
            trigger_memory_compression=AsyncMock(return_value={"status": "compressed"}),
        ),
    )
    facade = VoidCubeExecutionFacade(
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
    assert "/body/switch/consent" in payload["routes"]["body_upgrade"]
    assert "/autonomous-chain/execute" in payload["routes"]["autonomous_chain_execution"]
    assert "/body/watch-window/status" in payload["routes"]["body_lifecycle"]
    assert "self_learning" not in payload["routes"]
    assert "compatibility_notes" not in payload


@pytest.mark.unit
def test_execution_service_accepts_gateway_executor_prefix_routes():
    service, adapters = _make_service()
    client = TestClient(service.app)

    upgrade = client.post("/executor/body/upgrade/execute", json={"slot_id": "slot-B"})

    assert upgrade.status_code == 200
    assert upgrade.json()["status"] == "upgrade_awaiting_user_consent"
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
    consent = client.post("/executor/body/switch/consent", json={"slot_id": "slot-B", "approved": True})
    rollback = client.post(
        "/executor/body/slots/slot-B/improvement/rollback",
        json={"regression_detected": True},
    )

    assert prepare.json()["status"] == "slot_prepared"
    assert candidate.json()["status"] == "candidate_marked"
    assert probe_report.json()["status"] == "probe_report_recorded"
    assert probe_run.json()["status"] == "probe_executed"
    assert upgrade.json()["status"] == "upgrade_awaiting_user_consent"
    assert consent.json()["status"] == "body_switch_activated"
    assert rollback.json()["status"] == "body_improvement_rollback_verified"
    adapters.body_lifecycle.prepare_body_slot.assert_awaited_once_with("slot-B", {"clear_existing": False})
    adapters.body_lifecycle.mark_body_candidate.assert_awaited_once_with("slot-B", {"body_version": "v2"})
    adapters.body_upgrade.execute_body_upgrade.assert_awaited_once_with({"slot_id": "slot-B"})
    adapters.body_upgrade.confirm_body_switch.assert_awaited_once_with({"slot_id": "slot-B", "approved": True})
    adapters.body_lifecycle.rollback_body_improvement.assert_awaited_once_with(
        "slot-B",
        {"regression_detected": True},
    )


@pytest.mark.unit
def test_execution_service_accepts_only_autonomous_chain_execution_handoff():
    service, adapters = _make_service()
    client = TestClient(service.app)

    response = client.post(
        "/executor/autonomous-chain/execute",
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
    assert payload["status"] == "autonomous_chain_execution_executed"
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
