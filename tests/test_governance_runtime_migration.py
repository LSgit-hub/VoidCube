from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from memai.governance import (
    GOVERNANCE_MEMORY_DOMAIN,
    GovernanceDecision,
    GovernanceEvent,
    GovernanceEventType,
)
from memai.governance_repository import GovernanceEventRepository
from systems.governance_runtime_migration import (
    GovernanceEventMigrationConflict,
    consolidate_governance_event_logs,
)
from systems.supervisor.config_models import (
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from systems.supervisor.supervisor import Supervisor


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _event(event_id: str, *, minute: int, reason: str | None = None) -> GovernanceEvent:
    event = GovernanceEvent.create(
        event_type=GovernanceEventType.EXECUTION_OUTCOME,
        source_actor="test",
        decision=GovernanceDecision.COMPLETED,
        reason=reason or event_id,
    )
    event.id = event_id
    event.created_at = datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc)
    return event


def _write_events(path: Path, *events: GovernanceEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )


def test_consolidation_merges_primary_retry_and_duplicate_events(
    tmp_path: Path,
) -> None:
    root_source = tmp_path / "project" / "mem_governance.jsonl"
    nested_source = tmp_path / "canonical" / "self-learning" / "mem_governance.jsonl"
    target = tmp_path / "canonical" / "mem_governance.jsonl"
    first = _event("gov_first", minute=1)
    second = _event("gov_second", minute=2)
    third = _event("gov_third", minute=3)
    fourth = _event("gov_fourth", minute=4)
    _write_events(target, second)
    _write_events(target.with_suffix(".retry.jsonl"), fourth)
    _write_events(root_source, first, third)
    _write_events(root_source.with_suffix(".retry.jsonl"), fourth)
    _write_events(nested_source, third)

    result = consolidate_governance_event_logs(
        sources=(root_source, nested_source),
        target=target,
    )

    assert result.status == "migrated"
    assert result.source_events == 4
    assert result.target_events == 2
    assert result.merged_events == 4
    assert result.duplicates_removed == 2
    assert [event.id for event in GovernanceEventRepository(target).list_events()] == [
        "gov_first",
        "gov_second",
        "gov_third",
        "gov_fourth",
    ]
    assert not root_source.exists()
    assert not root_source.with_suffix(".retry.jsonl").exists()
    assert not nested_source.exists()
    assert not target.with_suffix(".retry.jsonl").exists()


def test_consolidation_refuses_conflicting_payload_for_same_event_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project" / "mem_governance.jsonl"
    target = tmp_path / "canonical" / "mem_governance.jsonl"
    _write_events(source, _event("gov_same", minute=1, reason="source"))
    _write_events(target, _event("gov_same", minute=1, reason="target"))
    original_target = target.read_bytes()

    with pytest.raises(GovernanceEventMigrationConflict, match="gov_same"):
        consolidate_governance_event_logs(sources=(source,), target=target)

    assert source.exists()
    assert target.read_bytes() == original_target
    assert list(target.parent.glob(".mem_governance.jsonl.migrating-*")) == []


def test_invalid_event_is_not_published_or_deleted(tmp_path: Path) -> None:
    source = tmp_path / "project" / "mem_governance.jsonl"
    target = tmp_path / "canonical" / "mem_governance.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid governance event"):
        consolidate_governance_event_logs(sources=(source,), target=target)

    assert source.exists()
    assert not target.exists()


def test_target_retry_is_recovered_without_legacy_source(tmp_path: Path) -> None:
    source = tmp_path / "project" / "mem_governance.jsonl"
    target = tmp_path / "canonical" / "mem_governance.jsonl"
    retry = target.with_suffix(".retry.jsonl")
    _write_events(retry, _event("gov_retry", minute=1))

    result = consolidate_governance_event_logs(
        sources=(source,),
        target=target,
    )

    assert result.status == "recovered_retry"
    assert [event.id for event in GovernanceEventRepository(target).list_events()] == [
        "gov_retry"
    ]
    assert not retry.exists()


def test_existing_target_without_domain_is_normalized_in_place(tmp_path: Path) -> None:
    target = tmp_path / "canonical" / "mem_governance.jsonl"
    payload = _event("gov_legacy_domain", minute=1).to_dict()
    payload.pop("memory_domain")
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = consolidate_governance_event_logs(sources=(), target=target)

    assert result.status == "normalized"
    row = json.loads(target.read_text(encoding="utf-8").strip())
    assert row["memory_domain"] == GOVERNANCE_MEMORY_DOMAIN
    assert GovernanceEventRepository(target).list_events()[0].memory_domain == "evolution"


def test_governance_event_rejects_non_evolution_domain() -> None:
    payload = _event("gov_wrong_domain", minute=1).to_dict()
    payload["memory_domain"] = "companion"

    with pytest.raises(ValueError, match="evolution memory domain"):
        GovernanceEvent.from_dict(payload)


def test_default_supervisor_consolidates_root_and_supervisor_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    supervisor_legacy = project / ".soul-runtime" / "mem_governance.jsonl"
    root_legacy = project / "mem_governance.jsonl"
    _write_events(supervisor_legacy, _event("gov_supervisor", minute=1))
    _write_events(root_legacy, _event("gov_executor", minute=2))
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(project)),
        service_runtime=SupervisorServiceRuntimeConfig(
            governor_llm_advisory_enabled=False,
            endogenous_drive_lm_task_generation_enabled=False,
        ),
        ui_enabled=False,
    )

    supervisor = Supervisor(config)
    target = home / "runtime" / "supervisor" / "mem_governance.jsonl"

    assert supervisor._governor.governance_repository.path == target
    assert [event.id for event in GovernanceEventRepository(target).list_events()] == [
        "gov_supervisor",
        "gov_executor",
    ]
    assert not root_legacy.exists()
    assert not (project / ".soul-runtime").exists()


def test_custom_supervisor_root_never_scans_project_governance_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    custom = tmp_path / "custom-supervisor"
    root_legacy = project / "mem_governance.jsonl"
    _write_events(root_legacy, _event("gov_legacy", minute=1))
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(project)),
        soul_store_path=str(custom),
        service_runtime=SupervisorServiceRuntimeConfig(
            governor_llm_advisory_enabled=False,
            endogenous_drive_lm_task_generation_enabled=False,
        ),
        ui_enabled=False,
    )

    supervisor = Supervisor(config)

    assert supervisor._governor.governance_repository.path == (
        custom / "mem_governance.jsonl"
    )
    assert root_legacy.exists()
    assert not (home / "runtime" / "supervisor").exists()
