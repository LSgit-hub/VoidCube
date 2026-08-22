from pathlib import Path


HTML = Path("src/voidcube/systems/supervisor/web/supervisor.html").read_text(
    encoding="utf-8"
)


def test_scene_entries_have_explicit_domain_boundaries():
    assert 'data-scene-entry="memory-flow"' in HTML
    assert 'data-scene-entry="employee-runs"' in HTML
    assert 'data-scene-entry="autonomous-tasks"' in HTML
    assert 'data-scene-entry="api-a-schedules"' in HTML
    assert 'data-drill="outboxes"' not in HTML


def test_scene_entry_labels_are_accessible_and_point_to_expected_drawers():
    assert 'data-scene-entry="memory-flow" aria-label="Mem 记忆传输与写回"' in HTML
    assert 'data-scene-entry="employee-runs" aria-label="员工代理执行详情"' in HTML
    assert 'data-scene-entry="autonomous-tasks" aria-label="星子自主任务安排"' in HTML
    assert 'data-scene-entry="api-a-schedules" title="查看 API-A 定时任务"' in HTML
    assert "target === 'memory-flow'" in HTML
    assert "openDrawer('employee_runs')" in HTML
    assert "openDrawer('autonomous_tasks')" in HTML
    assert "openPanel('schedules')" in HTML


def test_autonomous_task_drawer_explicitly_excludes_schedules():
    start = HTML.index("function renderAutonomousTasksDrawer")
    end = HTML.index("function renderIdentityDrawer", start)
    drawer_source = HTML[start:end]
    assert "/scheduled-tasks" not in drawer_source
    assert "board.autonomous_tasks" in drawer_source
