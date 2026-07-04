from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.body_registry import BodyRegistryManager
from systems.governor import GovernorDecisionEngine, GovernorRequest
from systems.lifecycle import BodyLifecycleExecutor


@pytest.mark.unit
def test_lifecycle_applies_probe_lease_action(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    slot = manager.mark_candidate("slot-B")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="probe-1",
        event_type="health_review_request",
        body_id="slot-B",
        source_actor="active_body",
        summary="Build complete",
        evidence={"build_complete": True},
        constraints={"target_transition": "candidate_to_probe"},
    )
    response = engine.evaluate(request, slot_meta=slot)

    executor = BodyLifecycleExecutor(manager)
    report = executor.apply_governor_response(response)
    slot_meta = manager.load_slot_meta("slot-B")

    assert report.action_results[0].status == "applied"
    assert slot_meta.body_state == "probe"
    assert slot_meta.lease == "probe"


@pytest.mark.unit
def test_lifecycle_applies_activate_slot_action(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    slot = manager.start_probe("slot-B")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="switch-1",
        event_type="switch_request",
        body_id="slot-B",
        source_actor="gateway",
        summary="Promote candidate body",
        evidence={
            "probe_passed": True,
            "runtime_task_profile": {
                "task_type": "self_evolution",
                "governance_task_type": "self_evolution",
                "task_family": "body_switch",
                "execution_kind": "body_switch",
            },
        },
        constraints={"watch_window_seconds": 90},
    )
    response = engine.evaluate(request, slot_meta=slot)

    executor = BodyLifecycleExecutor(manager)
    report = executor.apply_governor_response(response)
    registry = manager.load_registry()

    assert report.action_results[0].status == "applied"
    assert report.runtime_task_profile == {
        "task_type": "self_evolution",
        "governance_task_type": "self_evolution",
        "task_family": "body_switch",
        "execution_kind": "body_switch",
    }
    assert report.writeback_events[0]["payload"]["runtime_task_profile"]["task_family"] == "body_switch"
    assert report.action_results[0].details["task_family"] == "body_switch"
    assert registry.active_slot == "slot-B"
    assert registry.retired_slot == "slot-A"
    assert registry.last_switch_result["task_family"] == "body_switch"
    assert registry.last_switch_result["execution_kind"] == "body_switch"
    assert manager.load_slot_meta("slot-A").body_state == "retired"


@pytest.mark.unit
def test_lifecycle_applies_rollback_restore_action(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    manager.activate_slot("slot-B")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="rollback-1",
        event_type="rollback_request",
        body_id="slot-B",
        source_actor="gateway",
        summary="New body failed",
        evidence={"rollback_signal": True},
        constraints={"retired_slot": "slot-A"},
    )
    response = engine.evaluate(request)

    executor = BodyLifecycleExecutor(manager)
    report = executor.apply_governor_response(response)
    registry = manager.load_registry()

    assert report.action_results[0].status == "applied"
    assert registry.active_slot == "slot-A"
    assert manager.load_slot_meta("slot-A").body_state == "active"
    assert manager.load_slot_meta("slot-B").body_state == "retired"


@pytest.mark.unit
def test_lifecycle_applies_recycle_action_after_post_switch_review(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    manager.activate_slot("slot-B")
    retired = manager.load_slot_meta("slot-A")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="post-1",
        event_type="post_switch_review",
        body_id="slot-A",
        source_actor="governor",
        summary="Watch window stable",
        evidence={"watch_window_passed": True},
    )
    response = engine.evaluate(request, slot_meta=retired)

    executor = BodyLifecycleExecutor(manager)
    report = executor.apply_governor_response(response)
    slot_meta = manager.load_slot_meta("slot-A")

    assert report.action_results[0].status == "applied"
    assert slot_meta.body_state == "shell"


@pytest.mark.unit
def test_record_evolution_event_is_noop_but_successful(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="evo-1",
        event_type="body_upgrade_request",
        body_id="slot-B",
        source_actor="active_body",
        summary="Plan to improve the shell body",
    )
    response = engine.evaluate(request)

    executor = BodyLifecycleExecutor(manager)
    report = executor.apply_governor_response(response)

    assert report.action_results[0].status == "noop"
