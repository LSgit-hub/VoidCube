from types import SimpleNamespace

import pytest

from voidcube.systems.supervisor.ui_state_orchestration import (
    SupervisorUIStateContext,
    build_supervisor_ui_state,
    normalize_ui_cognition_snapshot,
)


def test_ui_state_owner_bounds_cognition_panel_payloads():
    cognition = normalize_ui_cognition_snapshot(
        {
            "perception": {"system_posture": "stable"},
            "world_model": {"memory_pressure": 0.4},
            "needs": [{"need_type": str(index)} for index in range(12)],
            "intents": [{"intent_type": str(index)} for index in range(8)],
            "signals": [{"signal_type": str(index)} for index in range(7)],
        }
    )

    assert cognition["perception"]["system_posture"] == "stable"
    assert cognition["world_model"]["memory_pressure"] == 0.4
    assert len(cognition["needs"]) == 8
    assert len(cognition["intents"]) == 6
    assert len(cognition["signals"]) == 5


@pytest.mark.asyncio
async def test_ui_state_owner_assembles_explicit_runtime_snapshot(monkeypatch):
    import voidcube.systems.supervisor.ui_state_orchestration as owner

    monkeypatch.setattr(
        owner,
        "load_ui_memory_token_usage",
        lambda: {"total_tokens": 12, "last_request_usage_percent": 1},
    )

    async def load_observation_input_snapshot():
        return (
            {
                "activity": {"counts": {}},
                "user_chain_signal": {"scope": "soft_signal_only"},
                "snapshot_source": "cached",
            },
            True,
        )

    async def load_memory_stats():
        return {"memory_active": False}

    async def load_timeline(*, limit):
        assert limit == 12
        return [{"trace_id": "trace-1"}]

    async def attach_trace_details(observation):
        return observation

    context = SupervisorUIStateContext(
        runtime_config=SimpleNamespace(
            endogenous_drive_lm_task_generation_enabled=True
        ),
        list_chain_projection_tasks=lambda: [],
        serialize_chain_task=lambda task: dict(task),
        latest_drive_candidates=lambda: [],
        load_observation_input_snapshot=load_observation_input_snapshot,
        load_memory_stats=load_memory_stats,
        load_observation_timeline=load_timeline,
        load_body_status=lambda chain: {"active_slot": "slot-A"},
        attach_trace_details=attach_trace_details,
        load_cognition_state=lambda: {
            "state": {
                "proposal_cognition": {
                    "lm_trace": {"status": "completed", "proposal_count": 1}
                }
            }
        },
        stellar_mode_status=lambda: {"mode": "idle"},
        voice_status=lambda: {"active": False},
        current_media=lambda: None,
    )

    state = await build_supervisor_ui_state(context=context)

    assert state["status"] == "ok"
    assert state["lm_input"] == {
        "generation_enabled": True,
        "status": "completed",
        "proposal_count": 1,
    }
    assert state["tier1_stats"] == {"memory_active": False}
    assert state["body_status"] == {"active_slot": "slot-A"}
    assert state["timeline"] == [{"trace_id": "trace-1"}]
    assert state["media"] == {"current": None, "queue_length": 0}


@pytest.mark.asyncio
async def test_ui_state_enriches_auto_employee_cards_with_run_details(monkeypatch):
    import voidcube.systems.supervisor.ui_state_orchestration as owner

    monkeypatch.setattr(owner, "load_ui_memory_token_usage", lambda: {})

    async def load_observation_input_snapshot():
        return ({"activity": {"counts": {}}}, True)

    async def load_memory_stats():
        return {"memory_active": True}

    async def load_timeline(*, limit):
        return []

    async def attach_trace_details(observation):
        return observation

    context = SupervisorUIStateContext(
        runtime_config=SimpleNamespace(
            endogenous_drive_lm_task_generation_enabled=False
        ),
        list_chain_projection_tasks=lambda: [
            {
                "task_id": "auto-task-1",
                "title": "研究任务",
                "governance_task_type": "self_learning",
                "status": "running",
                "metadata": {},
            }
        ],
        serialize_chain_task=lambda task: dict(task),
        latest_drive_candidates=lambda: [],
        load_observation_input_snapshot=load_observation_input_snapshot,
        load_memory_stats=load_memory_stats,
        load_observation_timeline=load_timeline,
        load_body_status=lambda chain: {},
        attach_trace_details=attach_trace_details,
        load_cognition_state=lambda: {},
        stellar_mode_status=lambda: {"mode": "auto_evolution"},
        voice_status=lambda: {"active": False},
        current_media=lambda: None,
        load_employee_execution_context=lambda: {
            "items": [
                {
                    "autonomous_task_id": "auto-task-1",
                    "employee_task_id": "employee-task-1",
                    "execution_provider": "research-provider",
                    "execution_model": "research-model",
                    "result_summary": "已完成研究并提交回写",
                }
            ]
        },
    )

    state = await build_supervisor_ui_state(context=context)

    run = state["autonomous_observation"]["board"]["employee_runs"][0]
    assert run["employee_task_id"] == "employee-task-1"
    assert run["execution_provider"] == "research-provider"
    assert run["execution_model"] == "research-model"
    assert run["result_summary"] == "已完成研究并提交回写"
    assert run["writeback_status"] == "pending"
