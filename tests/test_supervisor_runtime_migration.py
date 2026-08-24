from __future__ import annotations

import json
from pathlib import Path

import pytest

from voidcube.infrastructure.config.system import load_config_from_env
from voidcube.systems.supervisor.config_models import (
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from voidcube.systems.supervisor.runtime_migration import (
    SupervisorRuntimeMigrationConflict,
    migrate_supervisor_runtime,
)
from voidcube.systems.supervisor.supervisor import Supervisor


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _write_valid_runtime(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "state.json").write_text(
        json.dumps({"status": "preserved"}),
        encoding="utf-8",
    )
    (root / "events.jsonl").write_text(
        '{"event_id":"one"}\n{"event_id":"two"}\n',
        encoding="utf-8",
    )
    nested = root / "self-learning" / "sessions"
    nested.mkdir(parents=True)
    (nested / "record.bin").write_bytes(b"runtime-data")


def _supervisor_config(project: Path, **updates: object) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(project)),
        service_runtime=SupervisorServiceRuntimeConfig(
            governor_llm_advisory_enabled=False,
            endogenous_drive_lm_task_generation_enabled=False,
        ),
        ui_enabled=False,
        **updates,
    )


def test_directory_migration_verifies_structured_files_and_removes_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project" / ".soul-runtime"
    target = tmp_path / "home" / "runtime" / "supervisor"
    _write_valid_runtime(source)

    result = migrate_supervisor_runtime(source=source, target=target)

    assert result.status == "migrated"
    assert result.files_verified == 3
    assert source.exists() is False
    assert json.loads((target / "state.json").read_text(encoding="utf-8")) == {
        "status": "preserved"
    }
    assert (target / "self-learning" / "sessions" / "record.bin").read_bytes() == (
        b"runtime-data"
    )

    second = migrate_supervisor_runtime(source=source, target=target)
    assert second.status == "target_exists"


def test_directory_migration_refuses_when_both_roots_exist(tmp_path: Path) -> None:
    source = tmp_path / "project" / ".soul-runtime"
    target = tmp_path / "home" / "runtime" / "supervisor"
    _write_valid_runtime(source)
    _write_valid_runtime(target)

    with pytest.raises(SupervisorRuntimeMigrationConflict, match="Both canonical"):
        migrate_supervisor_runtime(source=source, target=target)

    assert source.exists()
    assert target.exists()


def test_invalid_structured_file_is_not_published_or_deleted(tmp_path: Path) -> None:
    source = tmp_path / "project" / ".soul-runtime"
    target = tmp_path / "home" / "runtime" / "supervisor"
    source.mkdir(parents=True)
    (source / "broken.json").write_text("not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        migrate_supervisor_runtime(source=source, target=target)

    assert source.exists()
    assert target.exists() is False
    assert list(target.parent.glob(".supervisor.migrating-*")) == []


def test_default_supervisor_migrates_before_runtime_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    source = project / ".soul-runtime"
    source.mkdir(parents=True)
    (source / "migration-marker.txt").write_text("preserved", encoding="utf-8")
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    supervisor = Supervisor(_supervisor_config(project))
    target = home / "runtime" / "supervisor"

    assert supervisor._runtime_root == target
    assert source.exists() is False
    assert (target / "migration-marker.txt").read_text(encoding="utf-8") == "preserved"
    assert supervisor._autonomous_chain_store.storage_path == (
        target / "autonomous_chain_store.json"
    ).resolve()


def test_explicit_supervisor_root_never_scans_or_migrates_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    source = project / ".soul-runtime"
    custom = tmp_path / "custom" / "supervisor-state"
    source.mkdir(parents=True)
    (source / "migration-marker.txt").write_text("legacy", encoding="utf-8")
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    supervisor = Supervisor(
        _supervisor_config(project, soul_store_path=str(custom))
    )

    assert supervisor._runtime_root == custom
    assert custom.exists()
    assert source.exists()
    assert (source / "migration-marker.txt").read_text(encoding="utf-8") == "legacy"
    assert (home / "runtime" / "supervisor").exists() is False


def test_supervisor_environment_override_wins_over_canonical_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    custom = tmp_path / "custom" / "supervisor"
    chain_store = tmp_path / "custom" / "chain.json"
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.setenv("SUPERVISOR_SOUL_STORE_PATH", str(custom))
    monkeypatch.setenv("SUPERVISOR_AUTONOMOUS_CHAIN_STORE_PATH", str(chain_store))

    config = load_config_from_env()

    assert config.supervisor.soul_store_path == str(custom)
    assert config.supervisor.autonomous_chain_store_path == str(chain_store)
