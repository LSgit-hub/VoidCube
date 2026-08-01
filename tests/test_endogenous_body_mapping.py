from datetime import datetime, timezone

from systems.supervisor.endogenous_body_mapping import (
    build_body_structure_mapping,
    canonical_body_editable_roots,
    path_within_body_editable_roots,
)


def _policy(**overrides):
    policy = {
        "body_improvement_editable_dirs": ["agent/", "tools/", "systems/"],
        "body_improvement_forbidden_patterns": ["systems/**", "**/credential*"],
        "body_improvement_max_files": 3,
    }
    policy.update(overrides)
    return policy


def test_canonical_roots_and_path_boundary_reject_forbidden_mother_system_nodes():
    roots = canonical_body_editable_roots(_policy())

    assert roots == ["agent/", "tools/"]
    assert path_within_body_editable_roots(
        "agent/stream_handler.py", roots, ["**/credential*"]
    ) is True
    assert path_within_body_editable_roots(
        "systems/supervisor/planning_runtime.py", roots, []
    ) is False
    assert path_within_body_editable_roots(
        "agent/credential_pool.py", roots, ["**/credential*"]
    ) is False


def test_mapping_projects_explicit_and_keyword_learning_evidence():
    completed_at = "2026-07-30T00:00:00+00:00"
    projection = build_body_structure_mapping(
        completed_learning_tasks=[
            {
                "task_id": "learn-stream",
                "title": "Stream display finding",
                "conclusion": "Improve agent/stream_handler.py after validation.",
                "completed_at": completed_at,
                "quality_score": 1.0,
            },
            {
                "task_id": "learn-memory",
                "title": "Memory access finding",
                "conclusion": "The memory recall path needs clearer evidence.",
                "completed_at": completed_at,
                "quality_score": 0.8,
            },
        ],
        shell_slot_id="slot-B",
        shell_worktree="F:/tmp/slot-B/worktree",
        policy=_policy(body_improvement_editable_dirs=["agent/", "tools/"]),
        learning_quality_score=88.0,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert projection["available"] is True
    assert projection["mapping_source"] == (
        "learning_evidence_structure_projection_v1"
    )
    assert projection["target_paths"] == [
        "agent/stream_handler.py",
        "agent/memory_manager.py",
        "agent/memory_provider.py",
    ]
    assert projection["learning_refs"][0]["mem_id"] == "learn-stream"


def test_mapping_rejects_learning_without_safe_structure_targets():
    projection = build_body_structure_mapping(
        completed_learning_tasks=[
            {
                "task_id": "learn-abstract",
                "title": "Abstract observation",
                "conclusion": "No concrete subsystem target was validated.",
                "completed_at": "2026-07-30T00:00:00+00:00",
                "quality_score": 1.0,
            }
        ],
        shell_slot_id="slot-B",
        shell_worktree="F:/tmp/slot-B/worktree",
        policy=_policy(body_improvement_editable_dirs=["agent/", "tools/"]),
        learning_quality_score=90.0,
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert projection == {
        "available": False,
        "reason": "learning_evidence_has_no_safe_structure_mapping",
        "learning_quality_score": 90.0,
        "editable_roots": ["agent/", "tools/"],
    }
