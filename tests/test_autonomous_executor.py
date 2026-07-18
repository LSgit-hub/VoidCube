from __future__ import annotations

from VoidCube_cli.autonomous_executor import (
    AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX,
    AUTONOMOUS_LEARNING_TASK_PREFIX,
    autonomous_task_run_id_for_message,
    bind_autonomous_execution_start,
    build_autonomous_task_prompt,
)


def test_learning_prompt_uses_autonomous_prefix_and_binds_run_id():
    task = {
        "title": "Learn backlog boundaries",
        "summary": "Inspect lane separation",
        "metadata": {"learning_branch": "exploratory"},
    }

    prompt = build_autonomous_task_prompt(task, "self_learning")
    run_id = bind_autonomous_execution_start(task, prompt)

    assert prompt.startswith(f"{AUTONOMOUS_LEARNING_TASK_PREFIX} Learn backlog boundaries")
    assert "Learning branch: exploratory" in prompt
    assert task["_autonomous_task_run_id"] == run_id
    assert autonomous_task_run_id_for_message(task, prompt) == run_id
    assert autonomous_task_run_id_for_message(task, "ordinary user turn") == ""


def test_body_improvement_prompt_captures_body_context():
    task = {
        "title": "Improve shell memory display",
        "summary": "Apply approved body improvement",
        "constraints": {
            "worktree_path": "F:/worktree/slot-B",
            "target_slot_id": "slot-B",
            "target_paths": ["agent/memory_manager.py"],
            "editable_dirs": ["agent/", "tools/"],
            "forbidden_patterns": ["systems/**"],
            "max_files_changed": 3,
        },
        "evidence": {
            "learning_refs": [
                {
                    "mem_id": "learning-memory-1",
                    "title": "Verified memory display finding",
                }
            ]
        },
    }

    prompt = build_autonomous_task_prompt(
        task,
        "body_improvement",
        git_head_commit=lambda worktree_path: f"head-for-{worktree_path}",
    )

    assert prompt.startswith(f"{AUTONOMOUS_BODY_IMPROVEMENT_TASK_PREFIX} Improve shell memory display")
    assert "Worktree path: F:/worktree/slot-B" in prompt
    assert "Editable dirs: agent/, tools/" in prompt
    assert "Approved target paths: agent/memory_manager.py" in prompt
    assert "learning-memory-1: Verified memory display finding" in prompt
    assert "Keep the change within the approved target paths" in prompt
    assert task["_baseline_head"] == "head-for-F:/worktree/slot-B"
    assert task["_improvement_worktree"] == "F:/worktree/slot-B"
    assert task["_improvement_slot_id"] == "slot-B"
