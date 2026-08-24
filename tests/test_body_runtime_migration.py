from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess

import pytest

from voidcube.infrastructure.config.system import load_config_from_env
from voidcube.systems.body_registry import (
    BodyLaunchTarget,
    BodyRegistry,
    BodyRegistryManager,
    BodySlotMeta,
)
from voidcube.systems.body_runtime_migration import (
    BodyRuntimeMigrationConflict,
    IncompleteLegacyBodyRuntime,
    migrate_body_runtime,
)
from voidcube.systems.supervisor.config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from voidcube.systems.supervisor.supervisor import Supervisor


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
    )


def _initialize_git_repo(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    _run_git(project, "init", "-q")
    _run_git(project, "config", "user.email", "body-migration@example.com")
    _run_git(project, "config", "user.name", "Body Migration Test")
    (project / "README.md").write_text("# body migration\n", encoding="utf-8")
    _run_git(project, "add", "README.md")
    _run_git(project, "commit", "-qm", "initial")


def _create_legacy_body_bundle(
    project: Path,
    *,
    linked_worktrees: bool = False,
) -> None:
    project.mkdir(parents=True, exist_ok=True)
    slots_root = project / ".body-slots"
    now = datetime.utcnow()

    if linked_worktrees:
        _initialize_git_repo(project)

    for index, slot_id in enumerate(("slot-A", "slot-B")):
        slot_root = slots_root / slot_id
        worktree = slot_root / "worktree"
        runtime = slot_root / "runtime"
        logs = slot_root / "logs"
        if linked_worktrees:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            _run_git(
                project,
                "worktree",
                "add",
                "--detach",
                "-q",
                str(worktree),
                "HEAD",
            )
        else:
            worktree.mkdir(parents=True, exist_ok=True)
            (worktree / "run_agent.py").write_text(
                f"print('{slot_id}')\n",
                encoding="utf-8",
            )
        runtime.mkdir(parents=True)
        logs.mkdir(parents=True)

        state = "active" if index == 0 else "shell"
        meta = BodySlotMeta(
            slot_id=slot_id,
            body_state=state,
            worktree_path=str(worktree.resolve()),
            runtime_path=str(runtime.resolve()),
            logs_path=str(logs.resolve()),
            lease="active" if state == "active" else None,
            materialized_from=(
                str(project.resolve()) if index == 0 else "slot:slot-A"
            ),
            last_materialized_at=now,
            runtime_bootstrapped_at=now,
        )
        _write_json(slot_root / "meta.json", meta)
        _write_json(
            slot_root / "worktree-origin.json",
            {
                "slot_id": slot_id,
                "worktree_path": str(worktree.resolve()),
                "source": str(project.resolve()) if index == 0 else "slot:slot-A",
                "source_root": str(
                    project.resolve()
                    if index == 0
                    else (slots_root / "slot-A" / "worktree").resolve()
                ),
                "materialized_at": now.isoformat(),
                "materialization_mode": (
                    "git_worktree" if linked_worktrees else "directory_copy"
                ),
            },
        )
        _write_json(
            runtime / "slot-runtime.json",
            {
                "slot_id": slot_id,
                "runtime_path": str(runtime.resolve()),
                "logs_path": str(logs.resolve()),
                "bootstrapped_at": now.isoformat(),
            },
        )

    registry = BodyRegistry(
        active_slot="slot-A",
        shell_slot="slot-B",
    )
    _write_json(project / ".body-registry.json", registry)
    _write_json(
        project / ".body-active.json",
        BodyLaunchTarget(
            slot_id="slot-A",
            body_state="active",
            worktree_path=str((slots_root / "slot-A" / "worktree").resolve()),
            runtime_path=str((slots_root / "slot-A" / "runtime").resolve()),
            logs_path=str((slots_root / "slot-A" / "logs").resolve()),
            body_version="bootstrap",
            generation=0,
            materialized_from=str(project.resolve()),
        ),
    )


def _supervisor_config(
    project: Path,
    *,
    body_state_root: Path | None = None,
) -> SupervisorConfig:
    body_runtime = (
        SupervisorBodyRuntimeConfig(state_root=str(body_state_root))
        if body_state_root is not None
        else SupervisorBodyRuntimeConfig()
    )
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(project)),
        service_runtime=SupervisorServiceRuntimeConfig(
            governor_llm_advisory_enabled=False,
            endogenous_drive_lm_task_generation_enabled=False,
        ),
        body_runtime=body_runtime,
        ui_enabled=False,
    )


def test_migration_rewrites_paths_and_supports_switch_and_rollback(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    target = tmp_path / "home" / "runtime" / "body"
    _create_legacy_body_bundle(project)

    result = migrate_body_runtime(source_root=project, target_root=target)

    assert result.status == "migrated"
    assert result.files_verified == 10
    assert result.linked_worktrees_repaired == 0
    assert not (project / ".body-slots").exists()
    assert not (project / ".body-registry.json").exists()
    assert not (project / ".body-active.json").exists()

    manager = BodyRegistryManager(project, state_root=target)
    report = manager.inspect_layout()
    assert report["healthy"] is True
    assert manager.registry_path == target / "registry.json"
    assert manager.active_body_pointer_path() == target / "active.json"
    for slot_id in ("slot-A", "slot-B"):
        meta = manager.load_slot_meta(slot_id)
        expected = target / "slots" / slot_id
        assert Path(meta.worktree_path) == expected / "worktree"
        assert Path(meta.runtime_path) == expected / "runtime"
        assert Path(meta.logs_path) == expected / "logs"

    manager.mark_candidate("slot-B", body_version="v2")
    manager.start_probe("slot-B")
    manager.await_user_consent("slot-B", request_payload={})
    switched = manager.activate_slot("slot-B", reason="migration_switch_test")
    assert switched.active_slot == "slot-B"
    assert switched.retired_slot == "slot-A"

    rolled_back = manager.activate_slot("slot-A", reason="migration_rollback_test")
    assert rolled_back.active_slot == "slot-A"
    assert rolled_back.retired_slot == "slot-B"
    assert manager.load_active_body_pointer().slot_id == "slot-A"

    second = migrate_body_runtime(source_root=project, target_root=target)
    assert second.status == "target_exists"


def test_migration_refuses_canonical_and_legacy_conflict(tmp_path: Path) -> None:
    project = tmp_path / "project"
    target = tmp_path / "home" / "runtime" / "body"
    _create_legacy_body_bundle(project)
    target.mkdir(parents=True)

    with pytest.raises(BodyRuntimeMigrationConflict, match="Both canonical"):
        migrate_body_runtime(source_root=project, target_root=target)

    assert (project / ".body-registry.json").exists()
    assert target.exists()


def test_migration_refuses_incomplete_legacy_bundle(tmp_path: Path) -> None:
    project = tmp_path / "project"
    target = tmp_path / "home" / "runtime" / "body"
    project.mkdir()
    _write_json(project / ".body-registry.json", BodyRegistry())

    with pytest.raises(IncompleteLegacyBodyRuntime, match="incomplete"):
        migrate_body_runtime(source_root=project, target_root=target)

    assert (project / ".body-registry.json").exists()
    assert not target.exists()


def test_corrupt_body_state_is_not_published_or_deleted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    target = tmp_path / "home" / "runtime" / "body"
    _create_legacy_body_bundle(project)
    (project / ".body-registry.json").write_text("not-json", encoding="utf-8")

    with pytest.raises((json.JSONDecodeError, ValueError)):
        migrate_body_runtime(source_root=project, target_root=target)

    assert (project / ".body-slots").exists()
    assert (project / ".body-registry.json").exists()
    assert not target.exists()
    assert list(target.parent.glob(".body.migrating-*")) == []


def test_migration_repairs_linked_git_worktree_registration(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    target = tmp_path / "home" / "runtime" / "body"
    _create_legacy_body_bundle(project, linked_worktrees=True)

    result = migrate_body_runtime(source_root=project, target_root=target)

    assert result.linked_worktrees_repaired == 2
    listing = _run_git(project, "worktree", "list", "--porcelain").stdout.replace(
        "\\", "/"
    )
    for slot_id in ("slot-A", "slot-B"):
        worktree = (target / "slots" / slot_id / "worktree").resolve()
        assert f"worktree {worktree.as_posix()}" in listing
        assert _run_git(worktree, "rev-parse", "--show-toplevel").stdout.strip().replace(
            "\\", "/"
        ) == worktree.as_posix()
    assert ".body-slots" not in listing


def test_default_supervisor_migrates_body_before_registry_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    _create_legacy_body_bundle(project)
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    supervisor = Supervisor(_supervisor_config(project))
    target = home / "runtime" / "body"

    assert supervisor._body_registry.state_root == target.resolve()
    assert supervisor._body_registry.inspect_layout()["healthy"] is True
    assert not (project / ".body-slots").exists()
    assert (target / "registry.json").is_file()
    assert (target / "active.json").is_file()


def test_explicit_body_state_root_never_migrates_project_legacy_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    custom = tmp_path / "custom-body"
    _create_legacy_body_bundle(project)
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    supervisor = Supervisor(
        _supervisor_config(project, body_state_root=custom)
    )

    assert supervisor._body_registry.state_root == custom.resolve()
    assert supervisor._body_registry.inspect_layout()["healthy"] is True
    assert (project / ".body-slots").exists()
    assert (project / ".body-registry.json").exists()
    assert not (home / "runtime" / "body").exists()


def test_body_state_environment_override_wins_over_canonical_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    custom = tmp_path / "custom-body"
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.setenv("BODY_STATE_ROOT", str(custom))

    config = load_config_from_env()

    assert config.supervisor.body_runtime.state_root == str(custom)
