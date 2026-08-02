from systems.supervisor.endogenous_deliberation import build_deliberation_report
from systems.supervisor.endogenous_drive_context import get_shell_slot_meta


def test_shell_slot_meta_normalization_returns_only_mapping_inputs():
    assert get_shell_slot_meta({"shell_slot": {"slot_id": "slot-A"}}) == {
        "slot_id": "slot-A"
    }
    assert get_shell_slot_meta({"shell_slot": None}) == {}


def test_deliberation_owner_assembles_projection_pipeline_from_explicit_input():
    report = build_deliberation_report(
        drive_input={
            "activity": {
                "mode": "user_chain_quiet",
                "system_posture": "stable",
                "counts": {"error_count": 1},
            },
            "correction_signals": 2,
            "shell_slot": {"slot_id": "slot-A", "worktree_path": ""},
            "endogenous_drive_policy": {"candidate_budget": 3},
            "drive_history": {"outcomes": []},
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }
    )

    assert report.perception.correction_signals == 2
    assert report.perception.shell_slot_id == "slot-A"
    assert report.world_model.system_posture == "stable"
    assert report.to_dict()["signals"]
