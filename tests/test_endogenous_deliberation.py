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


def test_deliberation_projects_foundation_tasks_as_shadow_only_intents():
    report = build_deliberation_report(
        drive_input={
            "activity": {"mode": "autonomous", "system_posture": "stable"},
            "evolution_foundation": {
                "mode": "shadow_read_only",
                "shadow_tasks": [
                    {
                        "task_kind": "fill_self_cognition",
                        "title": "补充代码自我认知快照",
                        "rationale": "snapshot missing",
                        "execution_allowed": False,
                        "evidence_refs": ["self_cognition:no_snapshot"],
                    },
                    {
                        "task_kind": "fill_research_knowledge",
                        "title": "补充外部知识 artifact",
                        "rationale": "knowledge missing",
                        "execution_allowed": False,
                        "evidence_refs": ["research_knowledge:no_artifact"],
                    },
                    {
                        "task_kind": "run_evolution_evaluation",
                        "title": "执行一次受控 BenchmarkPack 对比实验",
                        "rationale": "evaluation missing",
                        "execution_allowed": False,
                        "evidence_refs": ["evaluation:no_experiment_result"],
                    },
                ],
            },
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }
    )

    shadow_needs = {
        need.need_type
        for need in report.needs
        if need.need_type in {
            "complete_self_cognition",
            "complete_research_knowledge",
            "run_evolution_evaluation",
        }
    }
    shadow_intents = [
        intent
        for intent in report.intents
        if intent.output_channel == "shadow_task"
    ]
    shadow_signals = [
        signal
        for signal in report.signals
        if signal.signal_type == "foundation_shadow_task_signal"
    ]

    assert shadow_needs == {
        "complete_self_cognition",
        "complete_research_knowledge",
        "run_evolution_evaluation",
    }
    assert len(shadow_intents) == 3
    assert all(intent.candidate_kind is None for intent in shadow_intents)
    assert len(shadow_signals) == 3
    assert all(signal.payload["execution_allowed"] is False for signal in shadow_signals)
