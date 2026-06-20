from __future__ import annotations

from memai import (
    GovernanceBoundaryReport,
    GovernanceDecision,
    GovernanceEvent,
    GovernanceEventQuery,
    GovernanceEventRepository,
    GovernanceEventType,
    GovernanceFailureSampleQuery,
    GovernanceFailureSignature,
    GovernanceFailureType,
    GovernanceGitLineage,
)


def test_repository_appends_and_lists_events(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    event = _boundary_defer_event("task-1", "slot-B")

    repository.append(event)

    events = repository.list_events()
    assert len(events) == 1
    assert events[0].id == event.id
    assert events[0].event_type == GovernanceEventType.BOUNDARY_DEFER


def test_repository_append_is_idempotent_by_event_id(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    event = _boundary_defer_event("task-1", "slot-B")

    repository.append(event)
    repository.append(event)

    assert len(repository.list_events()) == 1


def test_repository_queries_governance_dimensions(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    boundary_event = _boundary_defer_event("task-1", "slot-B")
    execution_event = GovernanceEvent.create(
        event_type=GovernanceEventType.EXECUTION_OUTCOME,
        source_actor="executor",
        decision=GovernanceDecision.COMPLETED,
        reason="Execution completed.",
        task_id="task-2",
        body_id="slot-C",
        git_lineage=GovernanceGitLineage(
            candidate_commit="ccc333",
            rollback_commit="bbb222",
            changed_files=["agent/stream_handler.py"],
        ),
    )
    repository.append(boundary_event)
    repository.append(execution_event)

    assert repository.query(GovernanceEventQuery(event_type="boundary_defer")) == [
        boundary_event
    ]
    assert repository.query(GovernanceEventQuery(decision=GovernanceDecision.COMPLETED)) == [
        execution_event
    ]
    assert repository.query(GovernanceEventQuery(task_id="task-1")) == [boundary_event]
    assert repository.query(GovernanceEventQuery(body_id="slot-C")) == [execution_event]
    assert repository.query(GovernanceEventQuery(candidate_commit="bbb222")) == [
        boundary_event
    ]
    assert repository.query(GovernanceEventQuery(rollback_commit="bbb222")) == [
        execution_event
    ]
    assert repository.query(GovernanceEventQuery(changed_file="systems/body_registry.py")) == [
        boundary_event
    ]
    assert repository.query(GovernanceEventQuery(violation="systems/body_registry.py")) == [
        boundary_event
    ]
    assert repository.query(
        GovernanceEventQuery(failure_type=GovernanceFailureType.BOUNDARY_VIOLATION)
    ) == [boundary_event]
    assert repository.query(
        GovernanceEventQuery(
            similarity_key="boundary_violation:systems/body_registry.py"
        )
    ) == [boundary_event]


def test_repository_query_limit_returns_latest_matches(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    first = _boundary_defer_event("task-1", "slot-B")
    second = _boundary_defer_event("task-2", "slot-B")
    repository.append(first)
    repository.append(second)

    assert repository.query(GovernanceEventQuery(body_id="slot-B", limit=1)) == [second]
    assert repository.list_events(limit=1) == [second]


def test_repository_returns_ranked_failure_samples(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    boundary_event = _boundary_defer_event("task-1", "slot-B")
    probe_event = GovernanceEvent.create(
        event_type=GovernanceEventType.PROBE_FAILURE,
        source_actor="supervisor",
        decision=GovernanceDecision.DEFER,
        reason="Probe failed for active body candidate.",
        task_id="task-2",
        body_id="slot-C",
        git_lineage=GovernanceGitLineage(
            candidate_commit="ccc333",
            rollback_commit="aaa111",
            changed_files=["agent/runtime.py"],
        ),
        failure_signature=GovernanceFailureSignature(
            failure_type=GovernanceFailureType.PROBE_FAILURE,
            primary_paths=["agent/runtime.py"],
            probe_checks=["smoke"],
            risk_flags=["probe_smoke_failed"],
            similarity_keys=["probe_failure:smoke"],
        ),
    )
    repository.append(boundary_event)
    repository.append(probe_event)

    samples = repository.query_failure_samples(
        GovernanceFailureSampleQuery(
            changed_files=["systems/body_registry.py", "agent/runtime.py"],
            similarity_keys=["boundary_violation:systems/body_registry.py"],
        )
    )

    assert [sample.event for sample in samples] == [boundary_event, probe_event]
    assert samples[0].score > samples[1].score
    assert samples[0].matched_files == ["systems/body_registry.py"]
    assert samples[0].matched_similarity_keys == [
        "boundary_violation:systems/body_registry.py"
    ]
    assert samples[0].risk_flags == ["mother_system_path_in_body_candidate"]


def test_repository_filters_failure_samples_by_failure_type(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    repository.append(_boundary_defer_event("task-1", "slot-B"))
    repository.append(
        GovernanceEvent.create(
            event_type=GovernanceEventType.PROBE_FAILURE,
            source_actor="supervisor",
            decision=GovernanceDecision.DEFER,
            reason="Probe failed.",
            git_lineage=GovernanceGitLineage(changed_files=["systems/body_registry.py"]),
            failure_signature=GovernanceFailureSignature(
                failure_type=GovernanceFailureType.PROBE_FAILURE,
                primary_paths=["systems/body_registry.py"],
                risk_flags=["probe_failed"],
                similarity_keys=["probe_failure:body_registry"],
            ),
        )
    )

    samples = repository.query_failure_samples(
        GovernanceFailureSampleQuery(
            changed_files=["systems/body_registry.py"],
            failure_type=GovernanceFailureType.BOUNDARY_VIOLATION,
        )
    )

    assert len(samples) == 1
    assert samples[0].event.event_type == GovernanceEventType.BOUNDARY_DEFER


def test_repository_summarizes_governance_context_for_supervisor(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")
    event = _boundary_defer_event("task-1", "slot-B")
    repository.append(event)

    summary = repository.summarize_governance_context(
        GovernanceFailureSampleQuery(
            changed_files=["systems/body_registry.py"],
            similarity_keys=["boundary_violation:systems/body_registry.py"],
        )
    )

    assert summary.relevant_event_ids == [event.id]
    assert summary.recommendation == GovernanceDecision.DEFER
    assert summary.confidence == 0.86
    assert summary.risk_flags == ["mother_system_path_in_body_candidate"]
    assert "Found 1 similar governance failure sample" in summary.summary
    assert "systems/body_registry.py" in summary.summary


def test_repository_summarizes_empty_governance_context(tmp_path) -> None:
    repository = GovernanceEventRepository(tmp_path / "governance-events.jsonl")

    summary = repository.summarize_governance_context(
        GovernanceFailureSampleQuery(changed_files=["agent/runtime.py"])
    )

    assert summary.relevant_event_ids == []
    assert summary.recommendation == GovernanceDecision.RECORD_ONLY
    assert summary.confidence == 0.35
    assert summary.summary == "No similar governance failure samples were found."


def _boundary_defer_event(task_id: str, body_id: str) -> GovernanceEvent:
    return GovernanceEvent.create(
        event_type=GovernanceEventType.BOUNDARY_DEFER,
        source_actor="supervisor",
        decision=GovernanceDecision.DEFER,
        reason="Candidate changed files outside the child-agent boundary.",
        task_id=task_id,
        body_id=body_id,
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
            similarity_keys=["boundary_violation:systems/body_registry.py"],
        ),
    )
