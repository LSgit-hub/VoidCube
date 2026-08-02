from collections import deque
import json

from systems.supervisor.ui_activity_adapters import (
    SupervisorUIActivityContext,
    clear_supervisor_ui_activity,
    latest_drive_candidate_snapshot,
    load_supervisor_ui_activity,
    record_supervisor_ui_activity,
)


def test_activity_owner_records_scene_guard_persists_and_loads(tmp_path):
    path = tmp_path / "activity.json"
    events = deque(maxlen=4)
    history = []
    context = SupervisorUIActivityContext(
        activity_path=path,
        events=events,
        legal_scenes={"planning", "idle"},
        record_history=history.append,
    )

    event = record_supervisor_ui_activity(
        context=context,
        event_type="test_event",
        scene="execution",
        metadata={"key": "value"},
    )

    assert event["scene"] == "planning"
    assert history == [event]
    assert json.loads(path.read_text(encoding="utf-8"))["events"] == [event]
    assert load_supervisor_ui_activity(path=path, max_events=4) == [event]


def test_activity_owner_clears_storage_and_projects_latest_drive_snapshot(tmp_path):
    path = tmp_path / "activity.json"
    events = deque(
        [
            {
                "event_type": "endogenous_drive_planned",
                "metadata": {"tasks": [{"task_id": "task-1"}]},
            }
        ],
        maxlen=4,
    )

    assert latest_drive_candidate_snapshot(events=events) == [{"task_id": "task-1"}]
    clear_supervisor_ui_activity(path=path, events=events)
    assert list(events) == []
    assert json.loads(path.read_text(encoding="utf-8"))["events"] == []
