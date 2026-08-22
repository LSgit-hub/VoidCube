from voidcube.systems.supervisor.ui_autonomous_projection import (
    project_autonomous_observation,
)


def test_autonomous_board_exposes_separate_task_run_and_writeback_collections():
    observation = project_autonomous_observation([], drive_candidates=[])

    board = observation["board"]
    assert board["autonomous_tasks"] == []
    assert board["employee_runs"] == []
    assert board["mem_writeback"] == []
    assert observation["read_model_version"] == 14
