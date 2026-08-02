from systems.supervisor.endogenous_body_projection import (
    build_body_improvement_projection,
)


def _policy(**overrides):
    policy = {
        "body_improvement_editable_dirs": ["agent/", "tools/"],
        "body_improvement_forbidden_patterns": ["**/credential*"],
        "body_improvement_max_files": 3,
    }
    policy.update(overrides)
    return policy


def test_body_improvement_projection_preserves_eligibility_rejection():
    result = build_body_improvement_projection(
        drive_context={"policy": {}, "completed_learning_tasks": []},
        shell_slot_meta={"slot_id": "slot-B", "worktree_path": "body/slot-B"},
    )

    assert result == {
        "available": False,
        "reason": "learning_evidence_unavailable",
    }


def test_body_improvement_projection_composes_learning_mapping():
    result = build_body_improvement_projection(
        drive_context={
            "policy": _policy(),
            "completed_learning_tasks": [
                {
                    "task_id": "learn-stream",
                    "title": "Stream display finding",
                    "conclusion": "Improve agent/stream_handler.py after validation.",
                    "completed_at": "2026-07-30T00:00:00+00:00",
                    "quality_score": 1.0,
                }
            ],
            "api_b_judgement_tasks": [],
        },
        shell_slot_meta={"slot_id": "slot-B", "worktree_path": "F:/tmp/slot-B/worktree"},
    )

    assert result["available"] is True
    assert result["mapping_key"]
    assert result["target_paths"] == ["agent/stream_handler.py"]
    assert result["learning_refs"][0]["mem_id"] == "learn-stream"
