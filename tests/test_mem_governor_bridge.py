from __future__ import annotations

import sys
import json
import logging
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.memory.mem.governor_bridge import MemGovernorBridge
from systems.body_registry import BodyRegistry, BodySlotMeta
from systems.governor import GovernorRequest
from systems.lifecycle import LifecycleExecutionReport
from memai.governance_repository import GovernanceEventRepository


class _FailingGovernanceRepo:
    def append(self, event):
        raise RuntimeError("repo mirror failed")


@pytest.mark.unit
def test_mem_governor_bridge_records_review_and_latest(tmp_path):
    bridge = MemGovernorBridge(storage_root=tmp_path / "soul")
    slot_meta = BodySlotMeta(
        slot_id="slot-B",
        body_state="candidate",
        worktree_path="wt",
        runtime_path="rt",
        logs_path="logs",
    )
    request = GovernorRequest(
        request_id="review-1",
        trace_id="trace-1",
        task_type="self_evolution",
        decision_id="decision-1",
        event_type="health_review_request",
        body_id="slot-B",
        source_actor="active_body",
        summary="Candidate ready",
        evidence={
            "build_complete": True,
            "runtime_task_profile": {
                "governance_task_type": "self_evolution",
                "task_family": "body_switch",
                "execution_kind": "body_switch",
            },
        },
        constraints={"target_transition": "candidate_to_probe"},
    )

    response = bridge.review(request, slot_meta=slot_meta)
    history = bridge.list_history(limit=10)
    latest = bridge.get_latest()

    assert response.decision == "approve"
    assert len(history) == 1
    assert history[0]["kind"] == "review"
    assert history[0]["memory_domain"] == "evolution"
    assert latest is not None
    assert latest["request"]["request_id"] == "review-1"
    assert latest["request"]["trace_id"] == "trace-1"
    assert latest["request"]["task_type"] == "self_evolution"
    assert latest["request"]["governance_task_type"] == "self_evolution"
    assert latest["request"]["task_family"] == "body_switch"
    assert latest["request"]["execution_kind"] == "body_switch"
    assert latest["request"]["decision_id"] == "decision-1"
    assert latest["evolution_lineage"]["governance_task_type"] == "self_evolution"
    assert latest["evolution_lineage"]["task_family"] == "body_switch"
    assert latest["evolution_lineage"]["execution_kind"] == "body_switch"

    events = GovernanceEventRepository(tmp_path / "soul" / "mem_governance.jsonl").list_events()
    assert len(events) == 1
    assert events[0].memory_domain == "evolution"
    execution_result = events[0].execution_result or {}
    assert execution_result["title"] == "Candidate ready"
    assert execution_result["summary"] == "Candidate ready"
    assert execution_result["trace_id"] == "trace-1"
    assert execution_result["task_type"] == "self_evolution"
    assert execution_result["decision_id"] == "decision-1"
    assert execution_result["constraints"]["target_transition"] == "candidate_to_probe"
    assert execution_result["runtime_task_profile"] == {
        "governance_task_type": "self_evolution",
        "task_family": "body_switch",
        "execution_kind": "body_switch",
    }


@pytest.mark.unit
def test_mem_governor_bridge_rejects_when_repository_write_fails(tmp_path):
    bridge = MemGovernorBridge(
        storage_root=tmp_path / "soul",
        governance_repo=_FailingGovernanceRepo(),
    )
    request = GovernorRequest(
        request_id="review-repository-failure",
        trace_id="trace-repository-failure",
        task_type="self_evolution",
        event_type="health_review_request",
        body_id="slot-B",
        source_actor="active_body",
        summary="Candidate ready",
        evidence={"build_complete": True},
        constraints={"target_transition": "candidate_to_probe"},
    )

    with pytest.raises(RuntimeError, match="repo mirror failed"):
        bridge.review(request)

    latest = bridge.get_latest()
    assert latest is None
    assert bridge.list_history() == []


@pytest.mark.unit
def test_mem_governor_bridge_normalizes_legacy_history_domain(tmp_path):
    storage = tmp_path / "soul"
    storage.mkdir()
    history = storage / "governor_history.jsonl"
    latest = storage / "governor_latest.json"
    history.write_text(json.dumps({"record_id": "legacy", "kind": "review"}) + "\n", encoding="utf-8")
    latest.write_text(json.dumps({"record_id": "legacy", "kind": "review"}), encoding="utf-8")

    bridge = MemGovernorBridge(storage_root=storage)

    row = json.loads(history.read_text(encoding="utf-8").strip())
    assert row["memory_domain"] == "evolution"
    assert json.loads(latest.read_text(encoding="utf-8"))["memory_domain"] == "evolution"
    assert bridge.list_history()[0]["memory_domain"] == "evolution"
    assert bridge.get_latest()["memory_domain"] == "evolution"


@pytest.mark.unit
def test_mem_governor_bridge_records_execution_outcome(tmp_path):
    bridge = MemGovernorBridge(storage_root=tmp_path / "soul")
    request = GovernorRequest(
        request_id="switch-1",
        trace_id="trace-2",
        task_type="self_evolution",
        decision_id="decision-2",
        event_type="switch_request",
        body_id="slot-B",
        source_actor="gateway",
        summary="Switch candidate",
        evidence={
            "probe_passed": True,
            "runtime_task_profile": {
                "governance_task_type": "self_evolution",
                "task_family": "body_switch",
                "execution_kind": "body_switch",
            },
        },
    )
    response = bridge.review(request)
    registry = BodyRegistry(active_slot="slot-B", retired_slot="slot-A")
    execution_report = LifecycleExecutionReport(
        decision=response.decision,
        action_results=[],
        writeback_events=[],
    )

    bridge.record_execution_outcome(
        request=request,
        response=response,
        execution_report=execution_report,
        registry=registry,
    )

    history = bridge.list_history(limit=10)
    assert len(history) == 2
    assert history[-1]["kind"] == "execution_outcome"
    assert history[-1]["registry"]["active_slot"] == "slot-B"
    assert history[-1]["request"]["trace_id"] == "trace-2"
    assert history[-1]["request"]["task_type"] == "self_evolution"
    assert history[-1]["request"]["governance_task_type"] == "self_evolution"
    assert history[-1]["request"]["task_family"] == "body_switch"
    assert history[-1]["request"]["execution_kind"] == "body_switch"
    assert history[-1]["request"]["decision_id"] == "decision-2"
    assert history[-1]["evolution_lineage"]["trace_id"] == "trace-2"
    assert history[-1]["evolution_lineage"]["task_type"] == "self_evolution"
    assert history[-1]["evolution_lineage"]["governance_task_type"] == "self_evolution"
    assert history[-1]["evolution_lineage"]["task_family"] == "body_switch"
    assert history[-1]["evolution_lineage"]["execution_kind"] == "body_switch"
    assert history[-1]["evolution_lineage"]["decision_id"] == "decision-2"

    events = GovernanceEventRepository(tmp_path / "soul" / "mem_governance.jsonl").list_events()
    execution_event = events[-1]
    execution_result = execution_event.execution_result or {}
    assert execution_event.task_id == "switch-1"
    assert execution_result["title"] == "Switch candidate"
    assert execution_result["summary"] == "Switch candidate"
    assert execution_result["trace_id"] == "trace-2"
    assert execution_result["decision_id"] == "decision-2"
    assert execution_result["runtime_task_profile"] == {
        "governance_task_type": "self_evolution",
        "task_family": "body_switch",
        "execution_kind": "body_switch",
    }


@pytest.mark.unit
def test_mem_governor_bridge_records_evolution_lineage_summary(tmp_path):
    bridge = MemGovernorBridge(storage_root=tmp_path / "soul")
    slot_meta = BodySlotMeta(
        slot_id="slot-B",
        body_state="probe",
        worktree_path="wt",
        runtime_path="rt",
        logs_path="logs",
        source_branch="main",
        source_commit="aaa111",
        candidate_branch="evolution/task-1",
        candidate_commit="bbb222",
        active_ref="body/slot-B",
        rollback_ref="body/slot-A",
        rollback_commit="aaa111",
        diff_summary="Improve governed body switching.",
        changed_files=["agent/stream_handler.py"],
    )
    request = GovernorRequest(
        request_id="switch-2",
        event_type="switch_request",
        body_id="slot-B",
        source_actor="mem_supervisor",
        summary="Switch candidate",
        evidence={"probe_passed": True},
    )

    bridge.review(request, slot_meta=slot_meta)
    latest = bridge.get_latest()

    assert latest is not None
    lineage = latest["evolution_lineage"]
    assert lineage["body_id"] == "slot-B"
    assert lineage["source_branch"] == "main"
    assert lineage["candidate_branch"] == "evolution/task-1"
    assert lineage["candidate_commit"] == "bbb222"
    assert lineage["active_ref"] == "body/slot-B"
    assert lineage["rollback_ref"] == "body/slot-A"
    assert lineage["rollback_commit"] == "aaa111"
    assert lineage["diff_summary"] == "Improve governed body switching."
    assert lineage["changed_files"] == ["agent/stream_handler.py"]
    assert lineage["evolution_boundary"]["ok"] is True
    assert lineage["evolution_boundary"]["allowed_files"] == ["agent/stream_handler.py"]
    assert lineage["evolution_boundary"]["violations"] == []


@pytest.mark.unit
def test_mem_governor_bridge_records_boundary_violations_in_lineage(tmp_path):
    bridge = MemGovernorBridge(storage_root=tmp_path / "soul")
    slot_meta = BodySlotMeta(
        slot_id="slot-B",
        body_state="probe",
        worktree_path="wt",
        runtime_path="rt",
        logs_path="logs",
        source_commit="aaa111",
        candidate_commit="bbb222",
        rollback_commit="aaa111",
        changed_files=["agent/stream_handler.py", "systems/body_registry.py"],
    )
    request = GovernorRequest(
        request_id="switch-3",
        event_type="switch_request",
        body_id="slot-B",
        source_actor="mem_supervisor",
        summary="Switch candidate with mixed boundary changes",
        evidence={"probe_passed": True},
    )

    bridge.review(request, slot_meta=slot_meta)
    latest = bridge.get_latest()

    assert latest is not None
    boundary = latest["evolution_lineage"]["evolution_boundary"]
    assert boundary["ok"] is False
    assert boundary["allowed_files"] == ["agent/stream_handler.py"]
    assert boundary["forbidden_files"] == ["systems/body_registry.py"]
    assert boundary["violations"] == ["systems/body_registry.py"]


@pytest.mark.unit
def test_mem_governor_bridge_records_boundary_defer_event(tmp_path):
    bridge = MemGovernorBridge(storage_root=tmp_path / "soul")
    bridge.record_boundary_defer(
        task_id="task-1",
        trace_id="trace-3",
        task_type="self_evolution",
        governance_task_type="self_evolution",
        task_family="body_switch",
        execution_kind="body_switch",
        decision_id="decision-3",
        title="Reject mixed boundary candidate",
        body_id="slot-B",
        source_actor="supervisor",
        reason="Task deferred because body self-evolution changes cross the child-agent boundary.",
        git_lineage={
            "candidate_commit": "bbb222",
            "rollback_commit": "aaa111",
            "changed_files": ["agent/stream_handler.py", "systems/body_registry.py"],
        },
        evolution_boundary={
            "ok": False,
            "changed_files": ["agent/stream_handler.py", "systems/body_registry.py"],
            "allowed_files": ["agent/stream_handler.py"],
            "forbidden_files": ["systems/body_registry.py"],
            "unknown_files": [],
            "violations": ["systems/body_registry.py"],
        },
    )

    latest = bridge.get_latest()
    assert latest is not None
    assert latest["kind"] == "boundary_defer"
    assert latest["request"]["task_id"] == "task-1"
    assert latest["request"]["trace_id"] == "trace-3"
    assert latest["request"]["task_type"] == "self_evolution"
    assert latest["request"]["governance_task_type"] == "self_evolution"
    assert latest["request"]["task_family"] == "body_switch"
    assert latest["request"]["execution_kind"] == "body_switch"
    assert latest["request"]["decision_id"] == "decision-3"
    assert latest["response"]["decision"] == "defer"
    assert latest["response"]["violations"] == ["systems/body_registry.py"]
    assert latest["evolution_lineage"]["trace_id"] == "trace-3"
    assert latest["evolution_lineage"]["task_type"] == "self_evolution"
    assert latest["evolution_lineage"]["governance_task_type"] == "self_evolution"
    assert latest["evolution_lineage"]["task_family"] == "body_switch"
    assert latest["evolution_lineage"]["execution_kind"] == "body_switch"
    assert latest["evolution_lineage"]["decision_id"] == "decision-3"
    assert latest["evolution_lineage"]["candidate_commit"] == "bbb222"
    assert latest["evolution_lineage"]["evolution_boundary"]["ok"] is False

    events = GovernanceEventRepository(tmp_path / "soul" / "mem_governance.jsonl").list_events()
    execution_result = events[-1].execution_result or {}
    assert execution_result["title"] == "Reject mixed boundary candidate"
    assert execution_result["summary"] == (
        "Task deferred because body self-evolution changes cross the child-agent boundary."
    )
    assert execution_result["trace_id"] == "trace-3"
    assert execution_result["decision_id"] == "decision-3"
    assert execution_result["runtime_task_profile"] == {
        "governance_task_type": "self_evolution",
        "task_family": "body_switch",
        "execution_kind": "body_switch",
    }
