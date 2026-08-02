from types import SimpleNamespace

from systems.supervisor.ui_body_status_adapters import (
    SupervisorUIBodyStatusContext,
    load_body_status,
)


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
        "systems.supervisor.ui_body_status_adapters.project_body_slot_cards",
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
