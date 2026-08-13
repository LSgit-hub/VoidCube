from __future__ import annotations

from agent.subagent_display import SubagentDisplayManager, SubagentStatus
from VoidCube_app.contracts.execution import ExecutionState


def test_resolve_task_ref_accepts_task_id_and_one_based_index():
    manager = SubagentDisplayManager()
    task = manager.create_task("delegate-100", "Inspect the parser", task_index=0)

    assert manager.resolve_task_ref("delegate-100") is task
    assert manager.resolve_task_ref("1") is task
    assert manager.resolve_task_ref("99") is None


def test_render_tasks_command_separates_foreground_and_background_sections():
    manager = SubagentDisplayManager()
    fg = manager.create_task("delegate-fg", "Foreground task", task_index=0)
    bg = manager.create_task("delegate-bg", "Background task", task_index=1)
    fg.status = SubagentStatus.THINKING
    bg.status = SubagentStatus.TOOL_CALL
    manager.send_to_background("delegate-bg")

    panel = manager.render_tasks_command()

    assert "前台任务" in panel
    assert "后台任务" in panel
    assert "delegate-fg" in panel
    assert "delegate-bg" in panel
    assert "API-A 会在多步骤工作中自动管理子代理" in panel
    assert "调试操作" in panel


def test_get_active_count_excludes_background_tasks():
    manager = SubagentDisplayManager()
    fg = manager.create_task("delegate-fg", "Foreground task", task_index=0)
    bg = manager.create_task("delegate-bg", "Background task", task_index=1)
    fg.status = SubagentStatus.THINKING
    bg.status = SubagentStatus.TOOL_CALL
    manager.send_to_background("delegate-bg")

    assert manager.get_active_count() == 1


def test_background_foreground_notifications_use_advanced_debug_wording():
    rendered: list[str] = []
    manager = SubagentDisplayManager()
    manager.print_fn = lambda *args, **kwargs: rendered.append(str(args[0] if args else ""))

    task = manager.create_task("delegate-fg", "Foreground task", task_index=0)
    task.status = SubagentStatus.THINKING

    assert manager.send_to_background("delegate-fg") is True
    assert any("已应用调试操作" in line for line in rendered)
    assert any("使用 /tasks 查看" in line for line in rendered)

    rendered.clear()
    assert manager.bring_to_foreground("delegate-fg") is True
    assert any("已应用调试操作" in line for line in rendered)


def test_progress_updates_are_event_driven_and_do_not_render_snapshots():
    rendered: list[str] = []
    manager = SubagentDisplayManager()
    manager.print_fn = lambda *args, **kwargs: rendered.append(str(args[0] if args else ""))
    manager.create_task("delegate-1", "Inspect display", task_index=0)

    manager.on_api_call("delegate-1", 2)
    manager.on_thinking("delegate-1", "checking", iteration=2)
    manager.on_tool_start("delegate-1", "read_file", iteration=2)

    assert rendered == []

    manager.on_tool_complete("delegate-1", "read_file")

    assert len(rendered) == 1
    assert "read_file" in rendered[0]
    assert manager.get_task("delegate-1").iteration == 2


def test_duplicate_or_orphaned_tool_terminals_do_not_repeat_output():
    rendered: list[str] = []
    manager = SubagentDisplayManager()
    manager.print_fn = lambda *args, **kwargs: rendered.append(str(args[0]))
    manager.create_task("delegate-1", "Inspect display", task_index=0)

    manager.on_tool_complete(
        "delegate-1",
        "read_file",
        state=ExecutionState.FAILED,
    )
    manager.on_tool_start("delegate-1", "read_file")
    manager.on_tool_complete(
        "delegate-1",
        "read_file",
        state=ExecutionState.FAILED,
    )
    manager.on_tool_complete(
        "delegate-1",
        "read_file",
        state=ExecutionState.FAILED,
    )

    assert len(rendered) == 1
    assert "失败" in rendered[0]


def test_start_and_cancel_use_real_lifecycle_duration():
    rendered: list[str] = []
    manager = SubagentDisplayManager()
    manager.print_fn = lambda *args, **kwargs: rendered.append(str(args[0]))
    task = manager.create_task("delegate-1", "Inspect display", task_index=0)

    manager.on_start(task.task_id)
    manager.on_complete(
        task.task_id,
        error="interrupted",
        state=ExecutionState.CANCELLED,
    )

    assert task.started_at > 0
    assert task.duration_seconds >= 0
    assert task.status is SubagentStatus.CANCELLED
    assert sum("子代理已取消" in line for line in rendered) == 1
