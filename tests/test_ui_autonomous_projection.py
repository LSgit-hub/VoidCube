from voidcube.systems.supervisor.ui_autonomous_projection import (
    project_autonomous_observation,
)
from voidcube.systems.supervisor.ui_state_projection import (
    project_supervisor_scene_state,
)


def _scene_for_task(task):
    observation = project_autonomous_observation([task], drive_candidates=[])
    return project_supervisor_scene_state(
        autonomous_observation=observation,
        observation_input_available=True,
        mode="auto_evolution",
    )


def test_autonomous_board_exposes_separate_task_run_and_writeback_collections():
    observation = project_autonomous_observation([], drive_candidates=[])

    board = observation["board"]
    assert "autonomous_tasks" not in board
    assert board["employee_runs"] == []
    assert board["mem_writeback"] == []
    assert board["planning_tasks"] == []
    assert board["autonomous_history"] == []
    assert observation["read_model_version"] == 15


def test_completed_employee_run_exposes_writeback_contract_fields():
    observation = project_autonomous_observation(
        [
            {
                "task_id": "auto-task-1",
                "title": "研究任务",
                "governance_task_type": "self_learning",
                "status": "running",
                "metadata": {},
            }
        ],
        drive_candidates=[],
        history_tasks=[
            {
                "task_id": "auto-task-1",
                "title": "研究任务",
                "governance_task_type": "self_learning",
                "status": "completed",
                "metadata": {
                    "employee_execution_result": {"result_summary": "已完成"},
                    "employee_result_disposition": {
                        "status": "written_to_mem",
                        "returned_at": "2026-08-29T00:00:00+00:00",
                    },
                },
            }
        ],
    )

    run = observation["board"]["employee_runs"][0]
    writeback = observation["board"]["mem_writeback"][0]
    assert run["task_id"] == "auto-task-1"
    assert run["employee_result_status"] == "not_returned"
    assert run["result_returned_to_xingzi"] is False
    assert writeback["source_task_id"] == "auto-task-1"
    assert writeback["writeback_status"] == "completed"


def test_candidate_and_planning_use_the_writing_desk_location():
    observation = project_autonomous_observation(
        [],
        drive_candidates=[
            {
                "task_id": "candidate-1",
                "title": "内生候选",
                "metadata": {"utility": 0.8},
            }
        ],
    )

    scene = project_supervisor_scene_state(
        autonomous_observation=observation,
        observation_input_available=True,
        mode="auto_evolution",
    )

    assert scene["scene"] == "planning"
    assert scene["room_location"] == "writing_desk"
    assert scene["action"] == "write"
    assert scene["stage"] == "candidate"
    assert scene["task_id"] == "candidate-1"


def test_employee_dispatch_and_execution_use_the_computer_desk_location():
    ready = _scene_for_task(
        {
            "task_id": "ready-1",
            "title": "待接手任务",
            "governance_task_type": "self_learning",
            "status": "approved",
            "metadata": {"employee_assignment": {"employee_task_id": "run-1"}},
        }
    )
    running = _scene_for_task(
        {
            "task_id": "running-1",
            "title": "执行中任务",
            "governance_task_type": "self_learning",
            "status": "running",
            "metadata": {"employee_assignment": {"employee_task_id": "run-2"}},
        }
    )

    assert ready["scene"] == "handoff"
    assert ready["room_location"] == "computer_desk"
    assert ready["stage"] == "dispatched"
    assert running["scene"] == "handoff"
    assert running["room_location"] == "computer_desk"
    assert running["stage"] == "running"


def test_returned_employee_result_returns_to_the_writing_desk():
    scene = _scene_for_task(
        {
            "task_id": "returned-1",
            "title": "已回传任务",
            "governance_task_type": "self_learning",
            "status": "completed",
            "metadata": {
                "employee_assignment": {"employee_task_id": "run-3"},
                "employee_result_disposition": {
                    "status": "awaiting_mem_review",
                    "returned_at": "2026-08-29T00:00:00+00:00",
                },
            },
        }
    )

    assert scene["scene"] == "planning"
    assert scene["room_location"] == "writing_desk"
    assert scene["stage"] == "awaiting_mem_review"
    assert scene["task_id"] == "returned-1"
