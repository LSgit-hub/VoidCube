from __future__ import annotations

import pytest

from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.tool_events import ToolEvent, ToolEventKind


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_started_event_copies_arguments_and_preserves_identity() -> None:
    arguments = {"path": "before.txt"}

    event = ToolEvent.started(
        call_id="call-1",
        name="write_file",
        arguments=arguments,
        preview="write before.txt",
    )
    arguments["path"] = "after.txt"

    assert event.kind is ToolEventKind.STARTED
    assert event.call_id == "call-1"
    assert event.name == "write_file"
    assert event.arguments == {"path": "before.txt"}
    assert event.preview == "write before.txt"
    with pytest.raises(TypeError):
        event.arguments["path"] = "mutated.txt"


def test_completed_event_normalizes_duration_and_error_state() -> None:
    event = ToolEvent.completed(
        call_id="call-2",
        name="shell",
        arguments={"command": "exit 1"},
        result="failed",
        duration=-1,
        is_error=True,
    )

    assert event.kind is ToolEventKind.COMPLETED
    assert event.duration == 0
    assert event.is_error is True
    assert event.result == "failed"


def test_completed_event_carries_structured_artifacts() -> None:
    artifact = Artifact(
        kind="image",
        uri="C:/tmp/screenshot.png",
        mime_type="image/png",
    )

    event = ToolEvent.completed(
        call_id="call-3",
        name="browser_vision",
        arguments={},
        result='{"success": true}',
        duration=1.0,
        is_error=False,
        artifacts=(artifact,),
    )

    assert event.artifacts == (artifact,)


def test_reasoning_and_subagent_progress_are_distinct_events() -> None:
    reasoning = ToolEvent.reasoning("checked repository")
    progress = ToolEvent.subagent_progress("read_file, search_files")

    assert reasoning.kind is ToolEventKind.REASONING
    assert reasoning.text == "checked repository"
    assert progress.kind is ToolEventKind.SUBAGENT_PROGRESS
    assert progress.text == "read_file, search_files"
