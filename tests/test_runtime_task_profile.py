from voidcube.domain.tasks.runtime_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_family,
    resolve_broad_task_type,
)


def test_body_improvement_uses_body_upgrade_execution_family():
    assert normalize_runtime_task_family("body_improvement") == "body_upgrade"

    profile = derive_runtime_task_profile(
        task_type="self_evolution",
        execution_kind="body_improvement",
        default_task_family="general_self_evolution",
    )

    assert profile == {
        "governance_task_type": "self_evolution",
        "task_family": "body_upgrade",
        "execution_kind": "body_upgrade",
    }


def test_body_switch_keeps_distinct_runtime_family():
    assert normalize_runtime_task_family("body_switch") == "body_switch"

    profile = derive_runtime_task_profile(
        task_type="self_evolution",
        execution_kind="body_switch",
        default_task_family="general_self_evolution",
    )

    assert profile == {
        "governance_task_type": "self_evolution",
        "task_family": "body_switch",
        "execution_kind": "body_switch",
    }


def test_body_improvement_and_switch_remain_self_evolution_broad_tasks():
    assert (
        resolve_broad_task_type(
            governance_task_type="self_evolution",
            execution_kind="body_improvement",
        )
        == "self_evolution"
    )
    assert (
        resolve_broad_task_type(
            governance_task_type="self_evolution",
            execution_kind="body_switch",
        )
        == "self_evolution"
    )
