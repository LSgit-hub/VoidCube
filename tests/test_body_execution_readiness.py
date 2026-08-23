import json
import subprocess

from voidcube.systems.supervisor.body_execution_readiness import (
    inspect_body_execution_readiness,
)
from voidcube.systems.supervisor.endogenous_body_projection import (
    build_body_improvement_projection,
)


def _write_manifest(worktree, slot_id, mode="directory_copy"):
    manifest = worktree.parent / "worktree-origin.json"
    manifest.write_text(
        json.dumps(
            {
                "slot_id": slot_id,
                "worktree_path": str(worktree.resolve()),
                "materialization_mode": mode,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_directory_copy_requires_code_files_and_consistent_manifest(tmp_path):
    worktree = tmp_path / "slot-B" / "worktree"
    worktree.mkdir(parents=True)
    manifest = _write_manifest(worktree, "slot-B")

    missing = inspect_body_execution_readiness(
        slot_id="slot-B",
        worktree_path=str(worktree),
        manifest_path=manifest,
    )
    assert missing["ready"] is False
    assert missing["reason"] == "executable_code_missing"

    (worktree / "runner.py").write_text("print('ok')\n", encoding="utf-8")
    ready = inspect_body_execution_readiness(
        slot_id="slot-B",
        worktree_path=str(worktree),
        manifest_path=manifest,
    )
    assert ready["ready"] is True
    assert ready["materialization_mode"] == "directory_copy"
    assert ready["checks"]["manifest_consistent"] is True


def test_git_worktree_requires_resolvable_head(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "runner.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "runner.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=VoidCube Tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    worktree = tmp_path / "slots" / "slot-A" / "worktree"
    worktree.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "--detach", str(worktree), "HEAD"],
        check=True,
    )
    manifest = _write_manifest(worktree, "slot-A", mode="git_worktree")

    result = inspect_body_execution_readiness(
        slot_id="slot-A",
        worktree_path=str(worktree),
        manifest_path=manifest,
    )
    assert result["ready"] is True
    assert result["checks"]["git_worktree"] is True
    assert result["checks"]["head_resolvable"] is True
    assert result["head_commit"]


def test_endogenous_projection_blocks_empty_body_baseline(tmp_path):
    worktree = tmp_path / "slot-B" / "worktree"
    worktree.mkdir(parents=True)
    _write_manifest(worktree, "slot-B")

    result = build_body_improvement_projection(
        drive_context={
            "policy": {},
            "completed_learning_tasks": [
                {"task_id": "learn-1", "quality_score": 1.0}
            ],
            "api_b_judgement_tasks": [],
        },
        shell_slot_meta={
            "slot_id": "slot-B",
            "body_state": "shell",
            "worktree_path": str(worktree),
            "body_readiness": inspect_body_execution_readiness(
                slot_id="slot-B",
                worktree_path=str(worktree),
            ),
        },
    )

    assert result["available"] is False
    assert result["reason"] == "body_baseline_unavailable"
    assert result["body_readiness"]["reason"] == "executable_code_missing"
