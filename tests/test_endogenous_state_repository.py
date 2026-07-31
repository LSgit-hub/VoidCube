from __future__ import annotations

import json
from pathlib import Path

from systems.supervisor.endogenous_state_repository import EndogenousStateRepository
from systems.supervisor.supervisor import Supervisor, SupervisorConfig, SupervisorExecutionConfig


def test_endogenous_state_repository_uses_one_explicit_runtime_root(tmp_path: Path):
    repository = EndogenousStateRepository(tmp_path / "runtime")

    assert repository.root == (tmp_path / "runtime").resolve()
    assert repository.paths.drive_history == repository.root / "endogenous_drive_history.json"
    assert repository.paths.governance_events == repository.root / "endogenous_governance_events.json"
    assert repository.paths.cognition_state == repository.root / "endogenous_cognition_state.json"
    assert repository.paths.self_regulation == repository.root / "endogenous_self_regulation.json"


def test_endogenous_state_repository_writes_and_reads_json_objects(tmp_path: Path):
    repository = EndogenousStateRepository(tmp_path / "runtime")
    payload = {"version": 1, "state": {"status": "ready"}}

    repository.write_object(repository.paths.cognition_state, payload)

    assert repository.read_object(repository.paths.cognition_state) == payload
    assert json.loads(repository.paths.cognition_state.read_text(encoding="utf-8")) == payload


def test_endogenous_state_repository_rejects_missing_invalid_and_non_object_json(tmp_path: Path):
    repository = EndogenousStateRepository(tmp_path / "runtime")
    path = repository.paths.governance_events

    assert repository.read_object(path) is None

    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert repository.read_object(path) is None

    path.write_text("[]", encoding="utf-8")
    assert repository.read_object(path) is None


def test_supervisor_runtime_uses_the_assembled_endogenous_state_repository(tmp_path: Path):
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
        soul_store_path=str(tmp_path / "runtime"),
    )

    supervisor = Supervisor(config)

    assert supervisor._endogenous_state_repository.root == (tmp_path / "runtime").resolve()
