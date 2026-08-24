from types import SimpleNamespace

from voidcube.runtime.agent.subagent_display import SubagentStatus
from voidcube.interfaces.cli.subagent_observability_runtime import (
    CliSubagentObservabilityPorts,
    CliSubagentObservabilityRuntime,
)


def _runtime(foreground=(), background=()):
    manager = SimpleNamespace(
        list_tasks=lambda include_background=False: list(foreground),
        list_background_tasks=lambda: list(background),
    )
    return CliSubagentObservabilityRuntime(
        CliSubagentObservabilityPorts(display_managers=lambda: [manager])
    )


def test_snapshot_filters_terminal_tasks_and_prioritizes_foreground_focus():
    snapshot = _runtime(
        foreground=[
            SimpleNamespace(
                task_id="fg-2",
                task_index=2,
                status=SubagentStatus.RUNNING,
                current_tool="later",
            ),
            SimpleNamespace(
                task_id="fg-1",
                task_index=1,
                status=SubagentStatus.RUNNING,
                current_tool="read_file",
            ),
            SimpleNamespace(
                task_id="done",
                task_index=0,
                status=SubagentStatus.COMPLETED,
            ),
        ],
        background=[
            SimpleNamespace(
                task_id="bg-1",
                task_index=1,
                status=SubagentStatus.RUNNING,
                current_thinking="background work",
            )
        ],
    ).snapshot()

    assert snapshot["active"] is True
    assert snapshot["foreground_count"] == 2
    assert snapshot["background_count"] == 1
    assert snapshot["counts_label"] == "2+1"
    assert snapshot["focus_task_id"] == "fg-1"
    assert snapshot["focus_tool"] == "read_file"


def test_snapshot_compacts_preview_and_handles_empty_managers():
    runtime = _runtime(
        foreground=[
            SimpleNamespace(
                task_id="task-1",
                task_index=0,
                status=SubagentStatus.RUNNING,
                current_tool="",
                current_thinking="  a very long   thinking preview that needs trimming  ",
            )
        ]
    )

    snapshot = runtime.snapshot()
    assert snapshot["compact_preview"].endswith("...")
    assert len(snapshot["compact_preview"]) == 18
    assert CliSubagentObservabilityRuntime(
        CliSubagentObservabilityPorts(display_managers=lambda: [])
    ).snapshot()["active"] is False
