import json

from voidcube.systems.supervisor.endogenous_shell_profile import build_shell_body_profile


def test_shell_body_profile_reports_missing_worktree():
    result = build_shell_body_profile({"slot_id": "slot-a"})

    assert result == {
        "slot_id": "slot-a",
        "worktree_path": "",
        "body_version": None,
        "generation": None,
        "materialized_from": None,
        "candidate_branch": None,
        "candidate_commit": None,
        "profile_status": "missing_worktree",
    }


def test_shell_body_profile_reports_worktree_missing_on_disk(tmp_path):
    missing = tmp_path / "body" / "slot-a"

    result = build_shell_body_profile(
        {
            "slot_id": "slot-a",
            "worktree_path": str(missing),
            "body_version": 3,
        }
    )

    assert result["profile_status"] == "worktree_missing_on_disk"
    assert result["body_version"] == 3
    assert "confidence_score" not in result


def test_shell_body_profile_projects_worktree_and_origin_manifest(tmp_path):
    worktree = tmp_path / "body" / "slot-a"
    worktree.mkdir(parents=True)
    for name in ("src", "tests"):
        (worktree / name).mkdir()
    agent_runner = worktree / "src" / "voidcube" / "runtime" / "agent" / "runner.py"
    agent_runner.parent.mkdir(parents=True)
    agent_runner.write_text("", encoding="utf-8")
    (worktree / "pyproject.toml").write_text("", encoding="utf-8")
    (worktree / "README.md").write_text("", encoding="utf-8")
    (worktree.parent / "worktree-origin.json").write_text(
        json.dumps(
            {
                "source": "origin",
                "source_root": "/repo",
                "source_branch": "main",
                "source_commit": "abc123",
                "candidate_branch": "candidate/slot-a",
                "candidate_commit": "def456",
                "materialized_at": "2026-08-02T00:00:00+00:00",
                "ignored": "field",
            }
        ),
        encoding="utf-8",
    )

    result = build_shell_body_profile(
        {
            "slot_id": " slot-a ",
            "worktree_path": str(worktree),
            "body_version": 3,
            "generation": 8,
            "materialized_from": "seed",
            "candidate_branch": "candidate/slot-a",
            "candidate_commit": "def456",
        }
    )

    assert result["profile_status"] == "ready"
    assert result["slot_id"] == "slot-a"
    assert result["present_roots"] == ["src", "tests"]
    assert result["has_agent_runner"] is True
    assert result["has_project_config"] is True
    assert result["top_level_entries"] == [
        "README.md",
        "pyproject.toml",
        "src",
        "tests",
    ]
    assert result["origin_manifest"] == {
        "source": "origin",
        "source_root": "/repo",
        "source_branch": "main",
        "source_commit": "abc123",
        "candidate_branch": "candidate/slot-a",
        "candidate_commit": "def456",
        "materialized_at": "2026-08-02T00:00:00+00:00",
    }


def test_shell_body_profile_adds_quality_projection_for_ready_worktree(tmp_path):
    worktree = tmp_path / "body"
    worktree.mkdir()

    result = build_shell_body_profile({"worktree_path": str(worktree)})

    assert result["profile_status"] == "ready"
    assert result["confidence_score"] == 0.625
    assert result["source_reliability"] == 0.9
    assert result["supports"] == ["self_structure", "body_state"]
    assert result["contradicts"] == []
