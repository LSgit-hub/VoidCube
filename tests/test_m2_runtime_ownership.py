from __future__ import annotations

from pathlib import Path

import pytest

from memai.repository.governance import GovernanceEventRepository
from memai.application.memory_service import MemoryService
from voidcube.systems.supervisor.config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from voidcube.systems.supervisor.supervisor import Supervisor
from voidcube.infrastructure.runtime.layout import RuntimeLayout
from voidcube.infrastructure.runtime.layout import get_runtime_layout


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_fresh_runtime_and_restart_never_write_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    (project / "run_agent.py").write_text("print('source')\n", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.delenv("SUPERVISOR_SOUL_STORE_PATH", raising=False)
    monkeypatch.delenv("BODY_STATE_ROOT", raising=False)

    MemoryService()
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(project)),
        service_runtime=SupervisorServiceRuntimeConfig(
            governor_llm_advisory_enabled=False,
            endogenous_drive_lm_task_generation_enabled=False,
        ),
        ui_enabled=False,
    )
    supervisor = Supervisor(config)
    supervisor._governor.record_supervisor_activity(
        event={
            "event_id": "fresh-runtime-event",
            "event_type": "runtime_test",
            "summary": "canonical governance write",
            "metadata": {"source_actor": "test"},
        }
    )

    layout = get_runtime_layout()
    assert layout.memory_db.is_file()
    assert layout.supervisor_governance_log.is_file()
    assert layout.body_registry.is_file()
    assert layout.body_active_pointer.is_file()
    assert GovernanceEventRepository(layout.supervisor_governance_log).list_events()

    # Repeated startup must reuse canonical state without recreating old paths.
    MemoryService()
    restarted = Supervisor(config.model_copy(deep=True))
    assert restarted._body_registry.inspect_layout()["healthy"] is True

    forbidden = (
        project / "memory.db",
        project / ".soul-runtime",
        project / ".body-slots",
        project / ".body-registry.json",
        project / ".body-active.json",
        project / "mem_governance.jsonl",
    )
    assert all(not path.exists() for path in forbidden)


def test_removed_body_path_configuration_fields_stay_absent() -> None:
    fields = SupervisorBodyRuntimeConfig.model_fields

    assert "slots_dir_name" not in fields
    assert "registry_file_name" not in fields


def test_legacy_runtime_literals_are_confined_to_migration_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    scan_roots = (
        root / "systems",
        root / "plugins",
        root / "Mem" / "src",
        root / "voidcube.interfaces.cli",
        root / "voidcube",
    )
    allowed = {
        (root / "voidcube" / "runtime_paths.py").resolve(),
        (root / "systems" / "body_registry.py").resolve(),
    }
    markers = (
        '"./memory.db"',
        '".soul-runtime"',
        '".body-slots"',
        '".body-registry.json"',
        '".body-active.json"',
        "BODY_SLOTS_DIR_NAME",
        "BODY_REGISTRY_FILE_NAME",
    )
    violations: list[str] = []
    for scan_root in scan_roots:
        for path in scan_root.rglob("*.py"):
            if path.resolve() in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in markers:
                if marker in text:
                    violations.append(f"{path.relative_to(root)}: {marker}")

    assert violations == []
