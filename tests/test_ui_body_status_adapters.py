from types import SimpleNamespace

from voidcube.systems.supervisor.ui_body_status_adapters import (
    SupervisorUIBodyStatusContext,
    load_body_status,
)
from voidcube.systems.supervisor.ui_body_projection import project_body_slot_cards


def test_body_status_owner_loads_slot_metadata_and_bounded_directory_entries(
    tmp_path,
    monkeypatch,
):
    worktree = tmp_path / "slot-A"
    worktree.mkdir()
    for name in ("z-last", "a-first", "middle"):
        (worktree / name).write_text(name, encoding="utf-8")

    captured = {}
    monkeypatch.setattr(
        "voidcube.systems.supervisor.ui_body_status_adapters.project_body_slot_cards",
        lambda **kwargs: captured.update(kwargs) or [{"slot_id": "slot-A"}],
    )
    context = SupervisorUIBodyStatusContext(
        inspect_layout=lambda: {
            "registry": {
                "active_slot": "slot-A",
                "slot_ids": ["slot-A"],
                "last_switch_result": {"status": "stable"},
            },
            "violations": [],
        },
        load_slot_meta=lambda slot_id: SimpleNamespace(
            model_dump=lambda mode="json": {
                "slot_id": slot_id,
                "worktree_path": str(worktree),
            }
        ),
    )

    status = load_body_status(
        context=context,
        chain_history_projection=[{"task_id": "task-1"}],
    )

    assert status["active_slot"] == "slot-A"
    assert status["slot_cards"] == [{"slot_id": "slot-A"}]
    assert captured["chain_history_projection"] == [{"task_id": "task-1"}]
    assert captured["top_level_entries_by_slot"]["slot-A"] == [
        "a-first",
        "middle",
        "z-last",
    ]


def test_body_slot_projection_separates_structure_runtime_code_and_improvement():
    cards = project_body_slot_cards(
        registry={
            "active_slot": "slot-A",
            "shell_slot": "slot-B",
            "slot_ids": ["slot-A", "slot-B"],
        },
        slot_metas={
            "slot-A": {
                "slot_id": "slot-A",
                "body_state": "active",
                "body_version": "v1",
                "health_score": 82,
                "worktree_path": "C:/body/slot-A/worktree",
                "body_readiness": {
                    "ready": True,
                    "head_commit": "abc123",
                    "checks": {"worktree_exists": True},
                },
            },
            "slot-B": {
                "slot_id": "slot-B",
                "body_state": "shell",
                "worktree_path": "C:/body/slot-B/worktree",
                "body_readiness": {
                    "ready": False,
                    "reason": "executable_code_missing",
                    "checks": {"worktree_exists": True},
                },
            },
        },
        chain_history_projection=[],
        integrity_report={
            "slots": {
                "slot-A": {
                    "healthy": True,
                    "materialized": True,
                    "head_change_audit": [
                        {
                            "event_type": "body_head_changed",
                            "operation": "materialize_candidate_commit",
                            "before_commit": "a" * 40,
                            "after_commit": "b" * 40,
                            "reason": "evaluated_candidate_materialized",
                            "occurred_at": "2026-08-24T00:00:00+00:00",
                        }
                    ],
                },
                "slot-B": {"healthy": False, "materialized": False},
            },
            "violations": [],
        },
    )

    active, shell = cards
    assert active["structure_health"]["label"] == "结构正常"
    assert active["runtime_health"]["label"] == "当前运行"
    assert active["code_health"]["label"] == "代码就绪"
    assert active["improvement_progress"]["label"] == "暂无改进"
    assert active["health_score"] == 82.0
    assert active["head_change"]["label"] == "候选物化"
    assert active["head_change"]["before_commit"] == "a" * 40
    assert active["head_change"]["after_commit"] == "b" * 40
    assert shell["structure_health"]["label"] == "结构异常"
    assert shell["code_health"]["label"] == "代码未就绪"
    assert shell["improvement_progress"]["label"] == "等待培养"
