from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.body_registry import BodyRegistryManager
from systems.governor import GovernorDecisionEngine, GovernorRequest


@pytest.mark.unit
def test_candidate_to_probe_health_review_approves_when_build_ready(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    slot = manager.mark_candidate("slot-B", body_version="v-next")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="req-1",
        event_type="health_review_request",
        body_id="slot-B",
        source_actor="active_body",
        summary="Candidate build completed",
        evidence={"build_complete": True},
        constraints={"target_transition": "candidate_to_probe"},
    )

    response = engine.evaluate(request, slot_meta=slot)
    assert response.decision == "approve"
    assert response.required_actions[0].action_type == "issue_probe_lease"
    assert response.required_actions[0].slot_id == "slot-B"


@pytest.mark.unit
def test_probe_to_active_health_review_requires_passing_probe(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    slot = manager.start_probe("slot-B")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="req-2",
        event_type="health_review_request",
        body_id="slot-B",
        source_actor="probe_runner",
        summary="Probe completed",
        evidence={"probe_report": {"overall_passed": True}},
        constraints={"target_transition": "probe_to_active", "watch_window_seconds": 180},
    )

    response = engine.evaluate(request, slot_meta=slot)
    assert response.decision == "approve_with_watch"
    assert response.watch_window_hint == 180
    assert response.required_actions[0].action_type == "activate_slot"


@pytest.mark.unit
def test_switch_request_rejects_failed_probe(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    slot = manager.start_probe("slot-B")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="req-3",
        event_type="switch_request",
        body_id="slot-B",
        source_actor="gateway",
        summary="Switch candidate into service",
        evidence={"probe_report": {"overall_status": "failed"}},
    )

    response = engine.evaluate(request, slot_meta=slot)
    assert response.decision == "reject"


@pytest.mark.unit
def test_rollback_request_emits_restore_action():
    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="req-4",
        event_type="rollback_request",
        body_id="slot-B",
        source_actor="gateway",
        summary="New active body failed health checks",
        evidence={"rollback_signal": True, "active_body_healthy": False},
        constraints={"retired_slot": "slot-A"},
    )

    response = engine.evaluate(request)
    assert response.decision == "rollback_required"
    assert response.required_actions[0].action_type == "restore_retired_slot"
    assert response.required_actions[0].slot_id == "slot-A"


@pytest.mark.unit
def test_post_switch_review_recycles_retired_slot(tmp_path):
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    manager.activate_slot("slot-B")
    retired = manager.load_slot_meta("slot-A")

    engine = GovernorDecisionEngine()
    request = GovernorRequest(
        request_id="req-5",
        event_type="post_switch_review",
        body_id="slot-A",
        source_actor="governor",
        summary="Watch window completed",
        evidence={"watch_window_passed": True},
    )

    response = engine.evaluate(request, slot_meta=retired)
    assert response.decision == "approve"
    assert response.required_actions[0].action_type == "recycle_retired_slot"
