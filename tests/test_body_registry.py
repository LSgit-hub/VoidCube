from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.body_registry import BodyRegistryManager


def _await_user_consent(manager: BodyRegistryManager, slot_id: str = "slot-B"):
    return manager.await_user_consent(slot_id, request_payload={"watch_window_seconds": 120})


@pytest.mark.unit
def test_initialize_layout_bootstraps_dual_slots(tmp_path):
    manager = BodyRegistryManager(tmp_path)
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
    assert slot_b.body_state == "shell"
    assert (manager.slot_root("slot-A") / "runtime").exists()
    assert (manager.slot_root("slot-B") / "worktree").exists()


@pytest.mark.unit
def test_probe_to_active_switch_retires_previous_active(tmp_path):
    manager = BodyRegistryManager(tmp_path)
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
    manager = BodyRegistryManager(tmp_path)
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
def test_candidate_slot_records_git_lineage_metadata(tmp_path):
    manager = BodyRegistryManager(tmp_path)
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

    manager = BodyRegistryManager(tmp_path)
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

    manager = BodyRegistryManager(tmp_path)
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
    manager = BodyRegistryManager(tmp_path)
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
    manager = BodyRegistryManager(tmp_path)
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

    registry = manager.recycle_retired_slot("slot-A", source_slot_id="slot-B")
    slot_a = manager.load_slot_meta("slot-A")

    assert slot_a.body_state == "shell"
    assert slot_a.materialized_from == "slot:slot-B"
    assert (Path(slot_a.worktree_path) / "stable.marker").exists()
    assert registry.shell_slot == "slot-A"


@pytest.mark.unit
def test_illegal_transition_is_rejected(tmp_path):
    manager = BodyRegistryManager(tmp_path)
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

    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    meta = manager.prepare_slot_workspace("slot-B")

    worktree_root = Path(meta.worktree_path)
    runtime_manifest = manager.slot_runtime_manifest_path("slot-B")
    worktree_manifest = manager.slot_worktree_manifest_path("slot-B")

    assert meta.materialized_from == "repo_root"
    assert meta.last_materialized_at is not None
    assert meta.runtime_bootstrapped_at is not None
    assert (worktree_root / "run_agent.py").exists()
    assert (worktree_root / "config.yaml").exists()
    assert (worktree_root / "tools" / "__init__.py").exists()
    assert not (worktree_root / ".body-slots").exists()
    assert runtime_manifest.exists()
    assert worktree_manifest.exists()
    manifest = json.loads(worktree_manifest.read_text(encoding="utf-8"))
    assert manifest["slot_id"] == "slot-B"


@pytest.mark.unit
def test_prepare_slot_workspace_can_clone_from_active_slot_worktree(tmp_path):
    (tmp_path / "run_agent.py").write_text("print('repo root')\n", encoding="utf-8")

    manager = BodyRegistryManager(tmp_path)
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
    manager = BodyRegistryManager(tmp_path)
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
def test_activate_slot_records_stable_window_settings(tmp_path):
    manager = BodyRegistryManager(tmp_path)
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
    manager = BodyRegistryManager(tmp_path)
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
