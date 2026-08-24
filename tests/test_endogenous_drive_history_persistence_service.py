from __future__ import annotations

from pathlib import Path

from voidcube.systems.supervisor.endogenous_drive_history_persistence_service import (
    EndogenousDriveHistoryPersistenceService,
)
from voidcube.systems.supervisor.endogenous_state_repository import EndogenousStateRepository


def _service(tmp_path: Path) -> EndogenousDriveHistoryPersistenceService:
    return EndogenousDriveHistoryPersistenceService(
        EndogenousStateRepository(tmp_path / "runtime"),
        history_limit=2,
    )


def test_load_returns_normalized_empty_snapshot_when_history_is_absent(tmp_path: Path):
    service = _service(tmp_path)

    assert service.load() == {
        "version": 1,
        "updated_at": None,
        "judgements": [],
        "outcomes": [],
        "strategy_memory": {
            "focus_stats": {},
            "agenda_topic_stats": {},
        },
    }


def test_persist_normalizes_and_trims_history_through_repository(tmp_path: Path):
    service = _service(tmp_path)
    service.persist(
        {
            "judgements": [{"id": 1}, {"id": 2}, {"id": 3}],
            "outcomes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "strategy_memory": {"focus_stats": {"observe": {"judged": 1}}},
        }
    )

    snapshot = service.load()

    assert [item["id"] for item in snapshot["judgements"]] == [1, 2]
    assert [item["id"] for item in snapshot["outcomes"]] == ["a", "b"]
    assert snapshot["strategy_memory"]["focus_stats"]["observe"]["judged"] == 1
    assert snapshot["updated_at"]
