from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from systems.supervisor.endogenous_governance_state_persistence_service import (
    EndogenousGovernanceStatePersistenceService,
)
from systems.supervisor.endogenous_state_repository import EndogenousStateRepository


def _service(tmp_path: Path, *, enabled: bool = True):
    return EndogenousGovernanceStatePersistenceService(
        EndogenousStateRepository(tmp_path / "runtime"),
        endogenous_drive_enabled=lambda: enabled,
    )


def test_default_snapshots_are_owned_by_the_service(tmp_path: Path):
    service = _service(tmp_path, enabled=False)

    assert service.load_governance_events() == {
        "version": 1,
        "updated_at": None,
        "events": [],
    }
    assert service.load_cognition_state()["state"]["enabled"] is False
    assert service.load_self_regulation()["dynamic_candidate_throttle_boost"] == 0.0


def test_governance_event_persistence_deduplicates_unconsumed_events(tmp_path: Path):
    service = _service(tmp_path)
    event = {
        "event_id": "event-1",
        "event_type": "truthfulness_alert",
        "channel": "governance",
        "message": "repair truthfulness",
    }

    service.persist_governance_events({"events": [event, dict(event)]})

    snapshot = service.load_governance_events()
    assert len(snapshot["events"]) == 1
    assert snapshot["events"][0]["event_id"] == "event-1"
    assert snapshot["updated_at"]


def test_cognition_state_and_regulation_round_trip_through_service(tmp_path: Path):
    service = _service(tmp_path)
    cognition = {"status": "ready", "identity": {"role": "test"}}
    regulation = {
        "dynamic_candidate_throttle_boost": 0.2,
        "dynamic_observation_bias_boost": 0.1,
        "dynamic_truthfulness_bias_boost": 0.3,
        "dynamic_learning_expansion_suppression": 0.15,
        "last_reason": "recent correction",
    }

    service.persist_cognition_state(cognition)
    service.persist_self_regulation(regulation)

    assert service.load_cognition_state()["state"] == cognition
    loaded_regulation = service.load_self_regulation()
    assert loaded_regulation["dynamic_truthfulness_bias_boost"] == 0.3
    assert loaded_regulation["last_reason"] == "recent correction"


def test_self_regulation_decays_after_elapsed_time(tmp_path: Path):
    repository = EndogenousStateRepository(tmp_path / "runtime")
    service = EndogenousGovernanceStatePersistenceService(
        repository,
        endogenous_drive_enabled=lambda: True,
    )
    repository.write_object(
        repository.paths.self_regulation,
        {
            "updated_at": (
                datetime.now(timezone.utc) - timedelta(hours=3)
            ).isoformat(),
            "version": 1,
            "dynamic_candidate_throttle_boost": 0.2,
            "dynamic_observation_bias_boost": 0.1,
            "dynamic_truthfulness_bias_boost": 0.3,
            "dynamic_learning_expansion_suppression": 0.15,
        }
    )

    regulation = service.load_self_regulation()

    assert 0.0 < regulation["dynamic_candidate_throttle_boost"] < 0.2
    assert 0.0 < regulation["dynamic_truthfulness_bias_boost"] < 0.3
