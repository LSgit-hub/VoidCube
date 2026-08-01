from systems.supervisor.autonomous_chain_store import AutonomousChainTask
from systems.supervisor.task_profile_policy import TaskProfilePolicy


def test_task_profile_policy_preserves_body_improvement_alias() -> None:
    policy = TaskProfilePolicy()
    task = AutonomousChainTask(
        title="body candidate",
        task_type="self_evolution",
        metadata={"execution_kind": "body_improvement"},
    )

    assert policy.runtime_profile(task) == {
        "governance_task_type": "self_evolution",
        "task_family": "body_upgrade",
        "execution_kind": "body_upgrade",
    }
    assert policy.execution_kind(task) == "body_improvement"
    assert policy.requires_execution_request(task) is False


def test_task_profile_policy_derives_request_and_drive_profiles() -> None:
    policy = TaskProfilePolicy()

    assert policy.request_type(
        {"source": "self_learning", "metadata": {"task_family": "memory"}}
    ) == "memory_maintenance"
    assert policy.drive_input_profile({"task_family": "self_learning"}) == {
        "governance_task_type": "self_learning",
        "task_family": "self_learning",
        "execution_kind": None,
    }


def test_task_profile_policy_requires_execution_for_memory_maintenance() -> None:
    policy = TaskProfilePolicy()
    task = AutonomousChainTask(
        title="memory maintenance",
        task_type="memory_maintenance",
        metadata={"task_family": "memory_maintenance"},
    )

    assert policy.governance_type(task) == "memory_maintenance"
    assert policy.runtime_family(task) == "memory_maintenance"
    assert policy.execution_kind(task) == "memory_maintenance"
    assert policy.requires_execution_request(task) is True
