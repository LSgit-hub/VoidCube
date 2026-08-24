from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voidcube.systems.body_registry import (
    BodyRegistryManager,
    BodyWorkspaceRecoveryRequired,
)


def _await_user_consent(manager: BodyRegistryManager, slot_id: str = "slot-B"):
    return manager.await_user_consent(slot_id, request_payload={"watch_window_seconds": 120})


@pytest.mark.unit
def test_source_and_state_roots_are_independent(tmp_path):
    source_root = tmp_path / "source"
    state_root = tmp_path / "state"
    source_root.mkdir()
    (source_root / "run_agent.py").write_text("print('source')\n", encoding="utf-8")

    manager = BodyRegistryManager(source_root, state_root=state_root)
    manager.initialize_layout()

    active = manager.load_slot_meta("slot-A")
    assert manager.source_root == source_root.resolve()
    assert manager.state_root == state_root.resolve()
    assert manager.registry_path == state_root / "registry.json"
    assert manager.active_body_pointer_path() == state_root / "active.json"
    assert manager.slots_root == state_root / "slots"
    assert (Path(active.worktree_path) / "run_agent.py").is_file()
    assert not (source_root / ".body-registry.json").exists()
    assert not (source_root / ".body-active.json").exists()
    assert not (source_root / ".body-slots").exists()


@pytest.mark.unit
def test_initialize_layout_bootstraps_dual_slots(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    registry = manager.initialize_layout()

    assert registry.active_slot == "slot-A"
    assert registry.shell_slot == "slot-B"
    assert manager.registry_path.exists()
    assert manager.slot_meta_path("slot-A").exists()
    assert manager.slot_meta_path("slot-B").exists()

    slot_a = manager.load_slot_meta("slot-A")
    slot_b = manager.load_slot_meta("slot-B")
    assert slot_a.body_state == "active"
    assert slot_a.lease == "active"
    assert slot_a.last_materialized_at is not None
    assert manager.slot_worktree_manifest_path("slot-A").exists()
    assert slot_b.body_state == "shell"
    assert (manager.slot_root("slot-A") / "runtime").exists()
    assert (manager.slot_root("slot-B") / "worktree").exists()
    assert manager.load_active_body_pointer().body_state == "active"


@pytest.mark.unit
def test_initialize_layout_repairs_unmaterialized_active_slot(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('active')\n", encoding="utf-8")
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    active_meta = manager.load_slot_meta("slot-A")
    active_worktree = Path(active_meta.worktree_path)
    (active_worktree / "run_agent.py").unlink()
    manager.slot_worktree_manifest_path("slot-A").unlink()
    active_meta.body_state = "shell"
    active_meta.lease = None
    active_meta.last_materialized_at = None
    manager.save_slot_meta(active_meta)

    manager.initialize_layout()

    repaired = manager.load_slot_meta("slot-A")
    pointer = manager.load_active_body_pointer()
    assert repaired.body_state == "active"
    assert repaired.lease == "active"
    assert repaired.last_materialized_at is not None
    assert (Path(repaired.worktree_path) / "run_agent.py").exists()
    assert manager.slot_worktree_manifest_path("slot-A").exists()
    assert pointer.body_state == "active"
    assert pointer.worktree_path == repaired.worktree_path


@pytest.mark.unit
def test_initialize_layout_refuses_to_clear_dirty_invalid_git_worktree(tmp_path):
    source_root = tmp_path / "source"
    state_root = tmp_path / "state"
    source_root.mkdir()
    (source_root / "agent.py").write_text("VERSION = 'stable'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=source_root, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "voidcube@example.test"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "VoidCube Test"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(["git", "add", "agent.py"], cwd=source_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "stable body"],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )

    manager = BodyRegistryManager(source_root, state_root=state_root)
    manager.initialize_layout()
    active = manager.load_slot_meta("slot-A")
    worktree = Path(active.worktree_path)
    (worktree / "agent.py").write_text("VERSION = 'evolving'\n", encoding="utf-8")
    manager.slot_worktree_manifest_path("slot-A").unlink()
    active.last_materialized_at = None
    manager.save_slot_meta(active)

    with pytest.raises(BodyWorkspaceRecoveryRequired, match="preserve the changes"):
        manager.initialize_layout()

    assert (worktree / "agent.py").read_text(encoding="utf-8") == "VERSION = 'evolving'\n"


@pytest.mark.unit
def test_initialize_layout_refuses_to_replace_clean_evolved_git_commit(tmp_path):
    source_root = tmp_path / "source"
    state_root = tmp_path / "state"
    source_root.mkdir()
    (source_root / "agent.py").write_text("VERSION = 'stable'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=source_root, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "voidcube@example.test"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "VoidCube Test"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(["git", "add", "agent.py"], cwd=source_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "stable body"],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )

    manager = BodyRegistryManager(source_root, state_root=state_root)
    manager.initialize_layout()
    shell = manager.load_slot_meta("slot-B")
    worktree = Path(shell.worktree_path)
    (worktree / "agent.py").write_text("VERSION = 'evolved'\n", encoding="utf-8")
    subprocess.run(["git", "add", "agent.py"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "commit", "-m", "evolved body"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    evolved_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manager.slot_worktree_manifest_path("slot-B").unlink()
    shell.last_materialized_at = None
    manager.save_slot_meta(shell)

    with pytest.raises(BodyWorkspaceRecoveryRequired, match="preserve the evolved commit"):
        manager.initialize_layout()

    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == evolved_commit
    assert (worktree / "agent.py").read_text(encoding="utf-8") == "VERSION = 'evolved'\n"


@pytest.mark.unit
def test_default_materialization_rejects_missing_active_baseline(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    active_meta = manager.load_slot_meta("slot-A")
    manager.slot_worktree_manifest_path("slot-A").unlink()
    active_meta.last_materialized_at = None
    manager.save_slot_meta(active_meta)

    with pytest.raises(ValueError, match="Active body slot slot-A has no materialized baseline"):
        manager.prepare_slot_workspace("slot-B")


@pytest.mark.unit
def test_initialize_layout_preserves_in_progress_probe_state(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    probe = manager.start_probe("slot-B")
    materialized_at = probe.last_materialized_at

    registry = manager.initialize_layout()
    preserved = manager.load_slot_meta("slot-B")

    assert registry.shell_slot is None
    assert preserved.body_state == "probe"
    assert preserved.lease == "probe"
    assert preserved.last_materialized_at == materialized_at


@pytest.mark.unit
def test_inspect_layout_reports_healthy_initialized_registry(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()

    report = manager.inspect_layout()

    assert report["healthy"] is True
    assert report["violations"] == []
    assert report["slots"]["slot-A"]["role"] == "active"
    assert report["slots"]["slot-A"]["materialized"] is True
    assert report["slots"]["slot-B"]["role"] == "shell"
    assert report["active_pointer"]["healthy"] is True


@pytest.mark.unit
def test_inspect_layout_reports_dirty_git_worktree_without_reverting_changes(tmp_path):
    source_root = tmp_path / "source"
    state_root = tmp_path / "state"
    source_root.mkdir()
    (source_root / "agent.py").write_text("VERSION = 'stable'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=source_root, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "voidcube@example.test"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "VoidCube Test"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(["git", "add", "agent.py"], cwd=source_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "stable body"],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )

    manager = BodyRegistryManager(source_root, state_root=state_root)
    manager.initialize_layout()
    worktree = Path(manager.load_slot_meta("slot-A").worktree_path)
    (worktree / "agent.py").write_text("VERSION = 'evolving'\n", encoding="utf-8")

    report = manager.inspect_layout()
    slot_report = report["slots"]["slot-A"]
    codes = {item["code"] for item in report["violations"]}

    assert report["healthy"] is False
    assert slot_report["healthy"] is False
    assert slot_report["git"]["mode"] == "git_worktree"
    assert slot_report["git"]["clean"] is False
    assert "slot_worktree_dirty" in codes
    assert (worktree / "agent.py").read_text(encoding="utf-8") == "VERSION = 'evolving'\n"


@pytest.mark.unit
def test_inspect_layout_reports_manifest_and_pointer_corruption(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.slot_worktree_manifest_path("slot-A").unlink()
    pointer = json.loads(manager.active_body_pointer_path().read_text(encoding="utf-8"))
    pointer["body_state"] = "shell"
    manager.active_body_pointer_path().write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = manager.inspect_layout()
    codes = {item["code"] for item in report["violations"]}

    assert report["healthy"] is False
    assert report["slots"]["slot-A"]["healthy"] is False
    assert report["active_pointer"]["healthy"] is False
    assert "slot_not_materialized" in codes
    assert "active_pointer_mismatch" in codes


@pytest.mark.unit
def test_probe_to_active_switch_retires_previous_active(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()

    manager.mark_candidate("slot-B", body_version="v2")
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    registry = manager.activate_slot("slot-B", watch_window_seconds=120)

    slot_a = manager.load_slot_meta("slot-A")
    slot_b = manager.load_slot_meta("slot-B")
    assert registry.active_slot == "slot-B"
    assert registry.retired_slot == "slot-A"
    assert registry.watch_window.status == "active"
    assert slot_a.body_state == "retired"
    assert slot_b.body_state == "active"
    assert slot_b.body_version == "v2"
    assert slot_b.lease == "active"
    assert slot_b.active_ref == "body/slot-B"
    assert registry.last_switch_result["active_ref"] == "body/slot-B"


@pytest.mark.unit
def test_activate_slot_records_active_ref_and_commit(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()

    manager.mark_candidate(
        "slot-B",
        body_version="v2",
        candidate_branch="evolution/task-1",
        candidate_commit="bbb222",
        active_ref="stable/v2",
        rollback_ref="body/slot-A",
        rollback_commit="aaa111",
    )
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    registry = manager.activate_slot("slot-B")
    slot_b = manager.load_slot_meta("slot-B")
    pointer = manager.load_active_body_pointer()

    assert slot_b.active_ref == "stable/v2"
    assert slot_b.active_commit == "bbb222"
    assert pointer.active_ref == "stable/v2"
    assert pointer.active_commit == "bbb222"
    assert pointer.candidate_branch == "evolution/task-1"
    assert registry.last_switch_result["active_ref"] == "stable/v2"
    assert registry.last_switch_result["active_commit"] == "bbb222"
    assert registry.last_switch_result["rollback_ref"] == "body/slot-A"


@pytest.mark.unit
def test_body_switch_does_not_bind_or_validate_mem(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    manager = BodyRegistryManager(
        source_root,
        state_root=tmp_path / "state",
    )
    manager.initialize_layout()

    manager.mark_candidate("slot-B", body_version="v2")
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    registry = manager.activate_slot("slot-B")
    assert registry.active_slot == "slot-B"
    assert "mem_editable_binding" not in registry.last_switch_result


@pytest.mark.unit
def test_body_switch_accepts_candidate_without_a_body_mem_package(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "run_agent.py").write_text("print('ok')\n", encoding="utf-8")
    manager = BodyRegistryManager(
        source_root,
        state_root=tmp_path / "state",
    )
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    registry = manager.activate_slot("slot-B")
    assert registry.active_slot == "slot-B"


@pytest.mark.unit
def test_candidate_slot_records_git_lineage_metadata(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()

    meta = manager.mark_candidate(
        "slot-B",
        body_version="v2",
        source_commit="aaa111",
        candidate_commit="bbb222",
        rollback_commit="aaa111",
        diff_summary="Improve isolated runtime.",
        changed_files=["systems/body_registry.py", "systems/probe.py"],
    )

    assert meta.source_commit == "aaa111"
    assert meta.candidate_commit == "bbb222"
    assert meta.rollback_commit == "aaa111"
    assert meta.build_from_commit == "bbb222"
    assert meta.diff_summary == "Improve isolated runtime."
    assert meta.changed_files == ["systems/body_registry.py", "systems/probe.py"]


@pytest.mark.unit
def test_candidate_slot_auto_records_git_head_when_lineage_is_not_provided(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "voidcube@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "VoidCube Test"], cwd=tmp_path, check=True)
    (tmp_path / "run_agent.py").write_text("print('git lineage')\n", encoding="utf-8")
    subprocess.run(["git", "add", "run_agent.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "seed lineage"], cwd=tmp_path, check=True, capture_output=True, text=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    prepared = manager.prepare_slot_workspace("slot-B")
    meta = manager.mark_candidate("slot-B")
    manifest = json.loads(manager.slot_worktree_manifest_path("slot-B").read_text(encoding="utf-8"))

    assert prepared.source_commit == head
    assert prepared.source_branch == branch
    assert prepared.candidate_commit == head
    assert prepared.candidate_branch == branch
    assert meta.source_commit == head
    assert meta.source_branch == branch
    assert meta.candidate_commit == head
    assert meta.candidate_branch == branch
    assert meta.rollback_commit == head
    assert meta.rollback_ref == branch
    assert manifest["source_branch"] == branch
    assert manifest["source_commit"] == head
    assert manifest["candidate_branch"] == branch
    assert manifest["candidate_commit"] == head
    assert manifest["materialization_mode"] == "git_worktree"
    assert subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=prepared.worktree_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == str(Path(prepared.worktree_path).resolve()).replace("\\", "/")
    assert not (Path(prepared.worktree_path) / ".body-origin.json").exists()


@pytest.mark.unit
def test_candidate_slot_auto_records_changed_files_from_git_diff(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "voidcube@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "VoidCube Test"], cwd=tmp_path, check=True)
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "stream_handler.py").write_text(
        "VERSION = 'stable'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "agent/stream_handler.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "stable body"], cwd=tmp_path, check=True, capture_output=True, text=True)
    stable_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (tmp_path / "agent" / "stream_handler.py").write_text(
        "VERSION = 'candidate'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "agent/stream_handler.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "candidate body"], cwd=tmp_path, check=True, capture_output=True, text=True)
    candidate_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    meta = manager.mark_candidate(
        "slot-B",
        source_commit=stable_commit,
        candidate_commit=candidate_commit,
        rollback_commit=stable_commit,
    )

    assert meta.changed_files == ["agent/stream_handler.py"]


@pytest.mark.unit
def test_recycle_retired_slot_returns_it_to_shell(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    (tmp_path / "run_agent.py").write_text("print('stable shell')\n", encoding="utf-8")
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    manager.activate_slot("slot-B")

    registry = manager.recycle_retired_slot("slot-A", source_path=tmp_path)
    slot_a = manager.load_slot_meta("slot-A")

    assert slot_a.body_state == "shell"
    assert slot_a.lease is None
    assert registry.shell_slot == "slot-A"
    assert registry.retired_slot is None


@pytest.mark.unit
def test_recycle_retired_slot_can_sync_from_stable_slot(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    (tmp_path / "run_agent.py").write_text("print('root stable')\n", encoding="utf-8")
    manager.prepare_slot_workspace("slot-A", source_path=tmp_path)
    manager.prepare_slot_workspace("slot-B", source_slot_id="slot-A")
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    manager.activate_slot("slot-B")

    (Path(manager.load_slot_meta("slot-B").worktree_path) / "stable.marker").write_text(
        "new stable version\n",
        encoding="utf-8",
    )
    slot_a = manager.load_slot_meta("slot-A")
    slot_a.health_score = 88.0
    slot_a.health_history = [{"reason": "retired_body"}]
    slot_a.improvement_count = 4
    slot_a.last_probe_result = {"overall_passed": True}
    slot_a.changed_files = ["stale.py"]
    manager.save_slot_meta(slot_a)

    registry = manager.recycle_retired_slot("slot-A", source_slot_id="slot-B")
    slot_a = manager.load_slot_meta("slot-A")

    assert slot_a.body_state == "shell"
    assert slot_a.materialized_from == "slot:slot-B"
    assert (Path(slot_a.worktree_path) / "stable.marker").exists()
    assert slot_a.health_score == 0.0
    assert slot_a.health_history == []
    assert slot_a.improvement_count == 0
    assert slot_a.last_probe_result is None
    assert slot_a.changed_files == []
    assert registry.shell_slot == "slot-A"


@pytest.mark.unit
def test_illegal_transition_is_rejected(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()

    with pytest.raises(ValueError, match="Illegal body state transition"):
        manager.transition_slot("slot-B", "active")


@pytest.mark.unit
def test_prepare_slot_workspace_copies_repo_template_and_bootstraps_runtime(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    for runtime_dir_name in ("cache", "logs", "sessions", "state"):
        runtime_dir = tmp_path / runtime_dir_name
        runtime_dir.mkdir()
        (runtime_dir / "stale.json").write_text("{}\n", encoding="utf-8")

    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    meta = manager.prepare_slot_workspace("slot-B")

    worktree_root = Path(meta.worktree_path)
    runtime_manifest = manager.slot_runtime_manifest_path("slot-B")
    worktree_manifest = manager.slot_worktree_manifest_path("slot-B")

    assert meta.materialized_from == "slot:slot-A"
    assert meta.last_materialized_at is not None
    assert meta.runtime_bootstrapped_at is not None
    assert (worktree_root / "run_agent.py").exists()
    assert (worktree_root / "config.yaml").exists()
    assert (worktree_root / "tools" / "__init__.py").exists()
    assert not (worktree_root / ".body-active.json").exists()
    assert not (worktree_root / ".body-slots").exists()
    for runtime_dir_name in ("cache", "logs", "sessions", "state"):
        assert not (worktree_root / runtime_dir_name).exists()
    assert runtime_manifest.exists()
    assert worktree_manifest.exists()
    manifest = json.loads(worktree_manifest.read_text(encoding="utf-8"))
    assert manifest["slot_id"] == "slot-B"
    assert manifest["materialization_mode"] == "directory_copy"


@pytest.mark.unit
def test_prepare_slot_workspace_resets_stale_non_active_baseline_metadata(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('stable')\n", encoding="utf-8")
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    meta = manager.load_slot_meta("slot-B")
    meta.body_version = "stale-version"
    meta.build_from_commit = "stale-build"
    meta.source_branch = "stale-source"
    meta.source_commit = "stale-source-commit"
    meta.candidate_branch = "stale-candidate"
    meta.candidate_commit = "stale-candidate-commit"
    meta.active_ref = "stale-active-ref"
    meta.active_commit = "stale-active-commit"
    meta.rollback_ref = "stale-rollback-ref"
    meta.rollback_commit = "stale-rollback-commit"
    meta.diff_summary = "stale diff"
    meta.changed_files = ["stale.py"]
    meta.last_probe_result = {"overall_passed": False}
    meta.health_score = 75.0
    meta.health_history = [{"reason": "stale"}]
    meta.improvement_count = 3
    meta.last_improvement_at = "2026-01-01T00:00:00+00:00"
    meta.current_healthy_commit = "stale-healthy"
    meta.previous_healthy_commit = "stale-previous"
    meta.decay_applied_at = "2026-01-02T00:00:00+00:00"
    meta.rollback_in_progress = {"source_commit": "stale"}
    meta.last_improvement_rollback = {"target_commit": "stale"}
    manager.save_slot_meta(meta)

    prepared = manager.prepare_slot_workspace("slot-B", source_path=tmp_path)

    assert prepared.body_version == "unknown"
    assert prepared.build_from_commit is None
    assert prepared.source_branch is None
    assert prepared.source_commit is None
    assert prepared.candidate_branch is None
    assert prepared.candidate_commit is None
    assert prepared.active_ref is None
    assert prepared.active_commit is None
    assert prepared.rollback_ref is None
    assert prepared.rollback_commit is None
    assert prepared.diff_summary == ""
    assert prepared.changed_files == []
    assert prepared.last_probe_result is None
    assert prepared.health_score == 0.0
    assert prepared.health_history == []
    assert prepared.improvement_count == 0
    assert prepared.last_improvement_at is None
    assert prepared.current_healthy_commit is None
    assert prepared.previous_healthy_commit is None
    assert prepared.decay_applied_at is None
    assert prepared.rollback_in_progress is None
    assert prepared.last_improvement_rollback is None


@pytest.mark.unit
def test_abandon_candidate_restores_clean_shell_baseline(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('stable')\n", encoding="utf-8")
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.prepare_slot_workspace("slot-B", source_path=tmp_path)
    worktree = Path(manager.load_slot_meta("slot-B").worktree_path)
    (worktree / "run_agent.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    (worktree / "failed-candidate.txt").write_text("discard me\n", encoding="utf-8")
    manager.mark_candidate(
        "slot-B",
        body_version="broken-version",
        diff_summary="failed candidate",
        changed_files=["run_agent.py"],
    )
    manager.start_probe("slot-B")
    manager.write_probe_report("slot-B", {"overall_passed": False, "checks": []})
    meta = manager.load_slot_meta("slot-B")
    meta.health_score = 42.0
    meta.improvement_count = 2
    manager.save_slot_meta(meta)

    restored = manager.abandon_candidate("slot-B")

    assert restored.body_state == "shell"
    assert restored.body_version == manager.load_slot_meta("slot-A").body_version
    assert restored.diff_summary == ""
    assert restored.changed_files == []
    assert restored.last_probe_result is None
    assert restored.health_score == 0.0
    assert restored.improvement_count == 0
    assert (worktree / "run_agent.py").read_text(encoding="utf-8") == "print('stable')\n"
    assert not (worktree / "failed-candidate.txt").exists()
    assert manager.load_registry().shell_slot == "slot-B"


@pytest.mark.unit
def test_prepare_slot_workspace_can_clone_from_active_slot_worktree(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('repo root')\n", encoding="utf-8")

    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.prepare_slot_workspace("slot-A", source_path=tmp_path)
    active_worktree = Path(manager.load_slot_meta("slot-A").worktree_path)
    (active_worktree / "evolution.patch").write_text("candidate body data\n", encoding="utf-8")

    meta = manager.prepare_slot_workspace("slot-B", source_slot_id="slot-A")
    shell_worktree = Path(meta.worktree_path)

    assert meta.materialized_from == "slot:slot-A"
    assert (shell_worktree / "evolution.patch").exists()


@pytest.mark.unit
def test_active_body_pointer_tracks_current_active_slot(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.prepare_slot_workspace("slot-A", source_path=tmp_path)
    pointer = manager.load_active_body_pointer()

    assert pointer.slot_id == "slot-A"
    assert pointer.body_state == "active"
    assert Path(pointer.worktree_path).name == "worktree"

    manager.prepare_slot_workspace("slot-B", source_slot_id="slot-A")
    manager.mark_candidate("slot-B", body_version="v2")
    manager.start_probe("slot-B")
    _await_user_consent(manager)
    manager.activate_slot("slot-B")
    pointer = manager.load_active_body_pointer()

    assert pointer.slot_id == "slot-B"
    assert pointer.body_version == "v2"


@pytest.mark.unit
def test_restore_previous_healthy_commit_requires_clean_isolated_worktree(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "voidcube@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "VoidCube Test"], cwd=tmp_path, check=True)
    (tmp_path / "agent.py").write_text("VERSION = 'stable'\n", encoding="utf-8")
    subprocess.run(["git", "add", "agent.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "stable"], cwd=tmp_path, check=True, capture_output=True, text=True)
    stable_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    meta = manager.prepare_slot_workspace("slot-B")
    worktree = Path(meta.worktree_path)
    (worktree / "agent.py").write_text("VERSION = 'broken'\n", encoding="utf-8")
    subprocess.run(["git", "add", "agent.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "breaking improvement"], cwd=worktree, check=True, capture_output=True, text=True)
    broken_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    meta = manager.load_slot_meta("slot-B")
    meta.current_healthy_commit = broken_commit
    meta.previous_healthy_commit = stable_commit
    meta.candidate_commit = broken_commit
    meta.health_score = 80.0
    manager.save_slot_meta(meta)

    (worktree / "untracked.txt").write_text("must not be discarded\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        manager.restore_previous_healthy_commit(
            "slot-B",
            expected_current_commit=broken_commit,
        )
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == broken_commit

    (worktree / "untracked.txt").unlink()
    restored = manager.restore_previous_healthy_commit(
        "slot-B",
        expected_current_commit=broken_commit,
        request_id="rollback-1",
    )
    assert restored.body_state == "probe"
    assert restored.lease == "rollback_probe"
    assert restored.rollback_in_progress["source_commit"] == broken_commit
    assert restored.rollback_in_progress["target_commit"] == stable_commit
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == stable_commit

    finalized = manager.finalize_previous_healthy_commit_restore(
        "slot-B",
        probe_report={"overall_passed": True},
    )
    assert finalized.body_state == "shell"
    assert finalized.current_healthy_commit == stable_commit
    assert finalized.previous_healthy_commit is None
    assert finalized.health_score == pytest.approx(56.0)
    assert finalized.last_improvement_rollback["probe_passed"] is True


@pytest.mark.unit
def test_activate_slot_records_stable_window_settings(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    _await_user_consent(manager)

    registry = manager.activate_slot(
        "slot-B",
        watch_window_seconds=120,
        stable_window_days=3,
        stable_health_checks=5,
    )

    assert registry.watch_window.stable_window_days == 3
    assert registry.watch_window.stable_health_checks == 5
    assert registry.last_switch_result["stable_window_days"] == 3
    assert registry.last_switch_result["stable_health_checks"] == 5


@pytest.mark.unit
def test_activate_slot_can_record_runtime_task_profile(tmp_path):
    manager = BodyRegistryManager(tmp_path, state_root=tmp_path)
    manager.initialize_layout()
    manager.mark_candidate("slot-B")
    manager.start_probe("slot-B")
    _await_user_consent(manager)

    registry = manager.activate_slot(
        "slot-B",
        runtime_task_profile={
            "task_type": "self_evolution",
            "governance_task_type": "self_evolution",
            "task_family": "body_switch",
            "execution_kind": "body_switch",
        },
    )

    assert registry.last_switch_result["runtime_task_profile"]["task_family"] == "body_switch"
    assert registry.last_switch_result["governance_task_type"] == "self_evolution"
    assert registry.last_switch_result["execution_kind"] == "body_switch"
