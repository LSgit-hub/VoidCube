from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from voidcube.systems.body_registry import BodyRegistryManager
from voidcube.systems.supervisor.evolution_candidate_generation_request_service import (
    EvolutionCandidateGenerationRequestService,
)


pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


class _Scheduler:
    def __init__(self) -> None:
        self.requests = []

    def register(self, request):
        self.requests.append(request)
        return {"state": "pending"}


def _manager(tmp_path: Path) -> BodyRegistryManager:
    source = tmp_path / "source"
    source.mkdir()
    entrypoint = source / "src" / "voidcube" / "runtime" / "agent" / "runner.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("print('body')\n", encoding="utf-8")
    _git("init", cwd=source)
    _git("config", "user.name", "VoidCube Test", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-m", "baseline", cwd=source)
    manager = BodyRegistryManager(source, state_root=tmp_path / "state")
    manager.initialize_layout()
    return manager


def _evaluation(manager: BodyRegistryManager) -> dict:
    shell = manager.load_slot_meta("slot-B")
    return {
        "drive_input": {
            "candidate_generation": {
                "candidate_generation_ready": True,
                "mapping_key": "mapping-stream",
                "mapping_source": "learning-evidence-v1",
                "target_slot_id": "slot-B",
                "worktree_path": shell.worktree_path,
                "objective": "Improve stream behavior.",
                "improvement_hypothesis": "Focused stream changes improve correctness.",
                "target_paths": ["src/voidcube/runtime/agent/runner.py"],
                "forbidden_patterns": ["**/credential*"],
                "max_files_changed": 1,
                "learning_refs": [
                    {
                        "mem_id": "learning-stream",
                        "timestamp": NOW.isoformat(),
                        "relevance": 0.9,
                        "title": "Stream evidence",
                        "target_paths": ["src/voidcube/runtime/agent/runner.py"],
                    }
                ],
            }
        }
    }


def test_ready_projection_is_registered_idempotently(tmp_path: Path):
    manager = _manager(tmp_path)
    scheduler = _Scheduler()
    service = EvolutionCandidateGenerationRequestService(
        body_registry=manager,
        scheduler=scheduler,
        test_commands=("python -m py_compile src/voidcube/runtime/agent/runner.py",),
    )
    evaluation = _evaluation(manager)

    first = service.register_from_evaluation(evaluation)
    second = service.register_from_evaluation(evaluation)

    assert first["status"] == "registered"
    assert second["status"] == "registered"
    assert first["request_id"] == second["request_id"]
    assert len(scheduler.requests) == 2
    assert scheduler.requests[0] == scheduler.requests[1]


def test_dirty_shell_is_rejected_without_registration(tmp_path: Path):
    manager = _manager(tmp_path)
    scheduler = _Scheduler()
    service = EvolutionCandidateGenerationRequestService(
        body_registry=manager,
        scheduler=scheduler,
    )
    shell = manager.load_slot_meta("slot-B")
    Path(shell.worktree_path, "untracked.txt").write_text("dirty\n", encoding="utf-8")

    result = service.register_from_evaluation(_evaluation(manager))

    assert result["status"] == "rejected"
    assert scheduler.requests == []
