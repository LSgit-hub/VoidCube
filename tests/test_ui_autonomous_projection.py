from voidcube.systems.supervisor.ui_autonomous_projection import (
    project_autonomous_observation,
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
