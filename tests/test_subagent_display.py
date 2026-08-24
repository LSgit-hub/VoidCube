from __future__ import annotations

from voidcube.runtime.agent.subagent_display import SubagentDisplayManager, SubagentStatus
from voidcube.domain.contracts.execution import ExecutionState


def test_resolve_task_ref_accepts_task_id_and_one_based_index():
    manager = SubagentDisplayManager()
    task = manager.create_task("delegate-100", "Inspect the parser", task_index=0)

    assert manager.resolve_task_ref("delegate-100") is task
    assert manager.resolve_task_ref("1") is task
    assert manager.resolve_task_ref("99") is None


def test_render_tasks_command_is_compact_and_omits_tool_log_details():
    manager = SubagentDisplayManager()
    fg = manager.create_task("delegate-fg", "Foreground task", task_index=0)
    bg = manager.create_task("delegate-bg", "Background task", task_index=1)
    fg.status = SubagentStatus.THINKING
    bg.status = SubagentStatus.TOOL_CALL
    manager.send_to_background("delegate-bg")

    panel = manager.render_tasks_command()

    assert "子代理" in panel
    assert "Foreground task" in panel
    assert "Background task" in panel
    assert "后台" in panel
    assert "调试操作" not in panel


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
    assert any("转入后台" in line for line in rendered)

    rendered.clear()
    assert manager.bring_to_foreground("delegate-fg") is True
    assert any("恢复到前台" in line for line in rendered)


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

    assert rendered == []
    assert manager.get_task("delegate-1").iteration == 2


def test_render_task_detail_shows_recent_tools_only_on_demand():
    manager = SubagentDisplayManager()
    task = manager.create_task("delegate-1", "Inspect display", task_index=0)
    manager.on_start(task.task_id)
    manager.on_thinking(task.task_id, "checking component boundaries")
    manager.on_tool_start(task.task_id, "read_file", "agent/subagent_display.py")
    manager.on_tool_complete(task.task_id, "read_file")

    detail = manager.render_task_detail("1")

    assert detail is not None
    assert "Inspect display" in detail
    assert "delegate-1" in detail
    assert "最近工具" in detail
    assert "read_file" in detail
    assert "agent/subagent_display.py" in detail
    assert manager.render_task_detail("missing") is None


def test_render_task_log_preserves_ordered_lifecycle_events():
    manager = SubagentDisplayManager()
    task = manager.create_task("delegate-log", "Inspect event log", task_index=0)
    manager.on_start(task.task_id)
    manager.on_thinking(task.task_id, "checking event order")
    manager.on_tool_start(task.task_id, "read_file", "README.md")
    manager.on_tool_complete(task.task_id, "read_file")
    manager.on_complete(task.task_id, summary="done")

    log = manager.render_task_log("1")

    assert log is not None
    assert "事件记录" in log
    assert log.index("已创建") < log.index("已启动") < log.index("进展")
    assert log.index("◆ read_file") < log.index("✓ read_file") < log.index("完成")
    assert manager.render_task_log("missing") is None


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
