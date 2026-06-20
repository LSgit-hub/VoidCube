from __future__ import annotations

from memai import (
    GovernanceBoundaryReport,
    GovernanceDecision,
    GovernanceEvent,
    GovernanceEventType,
    GovernanceFailureSignature,
    GovernanceFailureType,
    GovernanceGitLineage,
    GovernanceRiskLevel,
)


def test_governance_event_serializes_boundary_defer() -> None:
    event = GovernanceEvent.create(
        event_type=GovernanceEventType.BOUNDARY_DEFER,
        source_actor="supervisor",
        decision=GovernanceDecision.DEFER,
        reason="Candidate changed files outside the child-agent boundary.",
        task_id="task-1",
        body_id="slot-B",
        risk_level=GovernanceRiskLevel.MEDIUM,
        confidence=0.92,
        git_lineage=GovernanceGitLineage(
            candidate_commit="bbb222",
            rollback_commit="aaa111",
            changed_files=["agent/stream_handler.py", "systems/body_registry.py"],
        ),
        evolution_boundary=GovernanceBoundaryReport(
            ok=False,
            changed_files=["agent/stream_handler.py", "systems/body_registry.py"],
            allowed_files=["agent/stream_handler.py"],
            forbidden_files=["systems/body_registry.py"],
            violations=["systems/body_registry.py"],
        ),
        failure_signature=GovernanceFailureSignature(
            failure_type=GovernanceFailureType.BOUNDARY_VIOLATION,
            primary_paths=["systems/body_registry.py"],
            risk_flags=["mother_system_path_in_body_candidate"],
            similarity_keys=[
                "boundary_violation:systems/body_registry.py",
                "body_candidate:mixed_agent_and_mother_paths",
            ],
        ),
    )

    payload = event.to_dict()
    restored = GovernanceEvent.from_dict(payload)

    assert payload["type"] == "governance_event"
    assert payload["event_type"] == "boundary_defer"
    assert payload["decision"] == "defer"
    assert payload["git_lineage"]["candidate_commit"] == "bbb222"
    assert payload["evolution_boundary"]["violations"] == ["systems/body_registry.py"]
    assert restored.event_type == GovernanceEventType.BOUNDARY_DEFER
    assert restored.failure_signature is not None
    assert restored.failure_signature.failure_type == GovernanceFailureType.BOUNDARY_VIOLATION


def test_governance_event_serializes_execution_outcome() -> None:
    event = GovernanceEvent.create(
        event_type=GovernanceEventType.EXECUTION_OUTCOME,
        source_actor="executor",
        decision=GovernanceDecision.COMPLETED,
        reason="Formal body self-evolution handoff completed.",
        task_id="task-2",
        body_id="slot-B",
        risk_level=GovernanceRiskLevel.LOW,
        confidence=1.0,
        git_lineage=GovernanceGitLineage(
            candidate_commit="ccc333",
            rollback_commit="aaa111",
            changed_files=["agent/stream_handler.py"],
        ),
        execution_result={
            "status": "formal_self_evolution_executed",
            "active_slot": "slot-B",
        },
    )

    payload = event.to_dict()
    restored = GovernanceEvent.from_dict(payload)

    assert payload["event_type"] == "execution_outcome"
    assert payload["decision"] == "completed"
    assert payload["execution_result"]["active_slot"] == "slot-B"
    assert restored.execution_result is not None
    assert restored.execution_result["status"] == "formal_self_evolution_executed"


def test_governance_event_serializes_watch_window_rollback() -> None:
    event = GovernanceEvent.create(
        event_type=GovernanceEventType.WATCH_WINDOW_ROLLBACK,
        source_actor="supervisor_watch_window",
        decision=GovernanceDecision.ROLLBACK_REQUIRED,
        reason="Watch window failed for the new active body.",
        body_id="slot-B",
        risk_level=GovernanceRiskLevel.HIGH,
        confidence=0.88,
        git_lineage=GovernanceGitLineage(
            active_ref="body/slot-B",
            rollback_ref="body/slot-A",
            rollback_commit="aaa111",
        ),
        failure_signature=GovernanceFailureSignature(
            failure_type=GovernanceFailureType.WATCH_WINDOW_FAILURE,
            risk_flags=["active_body_unhealthy"],
            similarity_keys=["watch_window_failure:active_body_unhealthy"],
        ),
        evidence_refs=["watch-window/slot-B/latest.json"],
    )

    payload = event.to_dict()
    restored = GovernanceEvent.from_dict(payload)

    assert payload["event_type"] == "watch_window_rollback"
    assert payload["decision"] == "rollback_required"
    assert payload["risk_level"] == "high"
    assert payload["evidence_refs"] == ["watch-window/slot-B/latest.json"]
    assert restored.git_lineage.rollback_ref == "body/slot-A"
