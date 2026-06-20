from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.supervisor.supervisor import Supervisor, SupervisorConfig, SupervisorExecutionConfig


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
    )


def _make_supervisor(tmp_path: Path) -> Supervisor:
    (tmp_path / "systems").mkdir()
    (tmp_path / "systems" / "agent").mkdir()
    (tmp_path / "systems" / "agent" / "run_agent_instance.py").write_text(
        "print('slot launch')\n",
        encoding="utf-8",
    )
    return Supervisor(_make_supervisor_config(tmp_path))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planning_self_evolution_task_creates_planned_queue_entry(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    result = await supervisor.plan_self_evolution_task(
        {
            "title": "Evaluate memory compaction heuristics",
            "summary": "Review whether current memory compression thresholds should be adjusted.",
        }
    )

    assert result["status"] == "planned"
    assert result["count"] == 1
    task = result["tasks"][0]
    assert task["status"] == "planned"
    assert task["title"] == "Evaluate memory compaction heuristics"
    assert task["governance_task_type"] == "self_evolution"
    assert task["task_family"] == "general_self_evolution"
    assert task["execution_kind"] == "general_self_evolution"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_cycle_generates_value_backed_tasks_without_duplicate_queue_spam(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    supervisor.config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"endogenous_drive_max_candidates": 10}
            )
        }
    )

    async def fake_idle_window(_request=None):
        return {
            "checks": {
                "has_user_idle": True,
                "has_agent_idle": True,
                "has_memory_idle": True,
                "in_execution_window": True,
            },
            "idle_seconds": {
                "user": 900,
                "agent": 900,
                "memory": 900,
            },
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "recent_errors": 1,
                    "high_uncertainty": 2,
                },
            },
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    first = await supervisor._run_endogenous_drive_cycle()
    second = await supervisor._run_endogenous_drive_cycle()
    queued = await supervisor.list_self_evolution_tasks()
    timeline = supervisor._recent_supervisor_ui_activity(limit=10)

    assert first["status"] == "planned"
    assert first["planned"] == 4
    assert second["status"] == "idle"
    assert queued["count"] == 4
    tasks_by_key = {
        task["metadata"]["endogenous_drive_key"]: task for task in queued["tasks"]
    }
    assert "continuity:memory_maintenance_sweep" in tasks_by_key
    assert "truthfulness:review_correction_signals" in tasks_by_key
    assert "creativity:idle_learning_thread" in tasks_by_key
    memory_task = tasks_by_key["continuity:memory_maintenance_sweep"]
    assert memory_task["source"] == "endogenous_drive"
    assert memory_task["governance_task_type"] == "memory_maintenance"
    assert memory_task["task_family"] == "memory_maintenance"
    assert memory_task["execution_kind"] == "memory_maintenance"
    assert memory_task["evidence"]["endogenous_drive"]["core_values"] == ["continuity"]
    event_types = [event["event_type"] for event in timeline]
    assert "endogenous_drive_evaluated" in event_types
    assert "endogenous_drive_planned" in event_types
    assert "endogenous_drive_idle" in event_types


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_decision_approves_task_when_idle_window_allows_execution(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task({"title": "Review body upgrade proposal"})
    task_id = planned["tasks"][0]["task_id"]

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "auto",
            "idle_window": {"now": "2026-05-25T00:15:00"},
        },
    )

    assert result["status"] == "approved"
    assert result["task"]["status"] == "approved"
    assert result["task"]["task_family"] == "general_self_evolution"
    assert result["task"]["decision_history"][-1]["status"] == "approved"
    assert result["task"]["decision_history"][-1]["governance_task_type"] == "self_evolution"
    assert result["task"]["decision_history"][-1]["task_family"] == "general_self_evolution"
    assert result["task"]["decision_history"][-1]["execution_kind"] == "general_self_evolution"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_defers_tasks_when_idle_window_is_not_ready(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Evaluate tool scheduler backpressure"},
                {"title": "Assess body probe retry policy"},
            ]
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    assert result["status"] == "reviewed"
    assert result["decision"] == "deferred"
    assert result["count"] == 2
    assert all(task["status"] == "deferred" for task in result["tasks"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_defers_body_task_with_boundary_violations(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "title": "Reject boundary-violating body candidate during review",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": [
                        "agent/stream_handler.py",
                        "systems/body_registry.py",
                    ],
                },
            },
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = result["tasks"][0]
    assert result["decision"] == "approved"
    assert task["status"] == "deferred"
    assert task["execution_request"] is None
    assert "systems/body_registry.py" in task["decision_reason"]
    assert task["decision_history"][-1]["context"]["evolution_boundary"]["ok"] is False
    assert task["evolution_boundary"]["violations"] == ["systems/body_registry.py"]
    history = supervisor._governor.list_history(limit=10)
    boundary_record = [record for record in history if record["kind"] == "boundary_defer"][-1]
    assert boundary_record["kind"] == "boundary_defer"
    assert boundary_record["request"]["body_id"] == "slot-B"
    assert boundary_record["response"]["decision"] == "defer"
    assert boundary_record["response"]["violations"] == ["systems/body_registry.py"]
    assert boundary_record["evolution_lineage"]["candidate_commit"] == "bbb222"
    assert any(record["kind"] == "supervisor_activity" for record in history)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_boundary_defer_does_not_depend_on_mem_write_success(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "title": "Boundary defer should survive governance log failure",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["systems/body_registry.py"],
                },
            },
        }
    )

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    def failing_record_boundary_defer(**_kwargs):
        raise RuntimeError("mem bridge unavailable")

    supervisor._governor.record_boundary_defer = failing_record_boundary_defer  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = result["tasks"][0]
    assert task["status"] == "deferred"
    assert task["execution_request"] is None
    assert task["evolution_boundary"]["violations"] == ["systems/body_registry.py"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cancelled_task_is_terminal_for_supervisor_decisioning(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task({"title": "Prepare guarded runtime experiment"})
    task_id = planned["tasks"][0]["task_id"]

    cancel_result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "cancel",
            "reason": "Operator cancelled this task family for the current cycle.",
        },
    )
    assert cancel_result["status"] == "cancelled"

    follow_up = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "approve",
        },
    )
    assert follow_up["status"] == "unchanged"
    assert follow_up["task"]["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_self_evolution_approval_builds_formal_execution_request(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Promote cultivated body slot",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "source_commit": "aaa111",
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "diff_summary": "Improve body runtime isolation.",
                    "changed_files": ["agent/stream_handler.py"],
                },
            },
            "constraints": {
                "rollback_plan": {
                    "strategy": "restore_retired_slot",
                    "retired_slot": "slot-A",
                }
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "approve",
            "actor": "mem_supervisor",
            "reason": "Probe and lineage evidence are sufficient for formal handoff.",
        },
    )

    execution_request = result["task"]["execution_request"]
    assert result["status"] == "approved"
    assert result["task"]["governance_task_type"] == "self_evolution"
    assert result["task"]["task_family"] == "body_switch"
    assert result["task"]["execution_kind"] == "body_switch"
    assert execution_request["status"] == "approved_for_execution"
    assert execution_request["task_type"] == "self_evolution"
    assert execution_request["trace_id"] == result["task"]["trace_id"]
    assert execution_request["decision_id"] == result["task"]["decision_history"][-1]["decision_id"]
    assert execution_request["kind"] == "body_switch"
    assert execution_request["target_slot_id"] == "slot-B"
    assert execution_request["git_lineage"]["candidate_commit"] == "bbb222"
    assert execution_request["git_lineage"]["rollback_commit"] == "aaa111"
    assert execution_request["probe_report_ref"] == "probe-reports/slot-B/latest.json"
    assert execution_request["governor_decision"]["actor"] == "mem_supervisor"
    boundary = execution_request["governor_decision"]["evolution_boundary"]
    assert boundary["ok"] is True
    assert boundary["allowed_files"] == ["agent/stream_handler.py"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_self_evolution_task_api_exposes_boundary_summary_before_approval(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Review candidate boundary before approval",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": [
                        "agent/stream_handler.py",
                        "systems/body_registry.py",
                    ],
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    listed = await supervisor.list_self_evolution_tasks()
    fetched = await supervisor.get_self_evolution_task(task_id)

    planned_boundary = planned["tasks"][0]["evolution_boundary"]
    listed_boundary = listed["tasks"][0]["evolution_boundary"]
    fetched_boundary = fetched["evolution_boundary"]

    assert planned["tasks"][0]["governance_task_type"] == "self_evolution"
    assert planned["tasks"][0]["task_family"] == "body_switch"
    assert planned["tasks"][0]["execution_kind"] == "body_switch"
    assert listed["tasks"][0]["task_family"] == "body_switch"
    assert fetched["task_family"] == "body_switch"
    assert planned_boundary["ok"] is False
    assert planned_boundary["allowed_files"] == ["agent/stream_handler.py"]
    assert planned_boundary["forbidden_files"] == ["systems/body_registry.py"]
    assert listed_boundary == planned_boundary
    assert fetched_boundary == planned_boundary


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_self_evolution_approval_rejects_mother_system_changes(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Reject mixed mother-system body proposal",
            "metadata": {
                "execution_kind": "body_upgrade",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "source_commit": "aaa111",
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "diff_summary": "Accidentally mixed body runtime and registry changes.",
                    "changed_files": [
                        "agent/stream_handler.py",
                        "systems/body_registry.py",
                    ],
                },
            },
            "constraints": {
                "rollback_plan": {
                    "strategy": "restore_retired_slot",
                    "retired_slot": "slot-A",
                }
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    with pytest.raises(Exception) as exc_info:
        await supervisor.decide_self_evolution_task(
            task_id,
            {
                "decision": "approve",
                "actor": "mem_supervisor",
            },
        )

    assert "outside the child-agent boundary" in str(exc_info.value)
    assert "systems/body_registry.py" in str(exc_info.value)
    queued = await supervisor.get_self_evolution_task(task_id)
    assert queued["status"] == "planned"
    assert queued["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_self_evolution_approval_requires_git_lineage_and_rollback(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Unsafe body switch proposal",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    with pytest.raises(Exception) as exc_info:
        await supervisor.decide_self_evolution_task(
            task_id,
            {
                "decision": "approve",
                "actor": "mem_supervisor",
            },
        )

    assert "git_lineage.rollback_commit" in str(exc_info.value)
    queued = await supervisor.get_self_evolution_task(task_id)
    assert queued["status"] == "planned"
    assert queued["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_self_evolution_approval_requires_changed_files(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Unsafe body switch without auditable diff",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    with pytest.raises(Exception) as exc_info:
        await supervisor.decide_self_evolution_task(
            task_id,
            {
                "decision": "approve",
                "actor": "mem_supervisor",
            },
        )

    assert "git_lineage.changed_files" in str(exc_info.value)
    queued = await supervisor.get_self_evolution_task(task_id)
    assert queued["status"] == "planned"
    assert queued["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_self_learning_followup_auto_approval_does_not_build_execution_request(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Review newly collected learning evidence",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T11:40:00",
            "last_agent_work_at": "2026-05-25T11:40:00",
            "last_memory_task_at": "2026-05-25T11:40:00",
            "last_self_learning_activity_at": "2026-05-25T11:40:00",
            "last_self_evolution_activity_at": "2026-05-25T11:40:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "auto",
            "idle_window": {
                "now": "2026-05-25T12:00:00",
            },
        },
    )

    assert result["status"] == "approved"
    assert result["task"]["status"] == "approved"
    assert result["task"]["governance_task_type"] == "self_learning"
    assert result["task"]["task_family"] == "self_learning"
    assert result["task"]["execution_kind"] is None
    assert result["task"]["execution_request"] is None
    decision = result["task"]["decision_history"][-1]
    assert decision["trace_id"] == result["task"]["trace_id"]
    assert decision["task_type"] == "self_learning_followup"
    assert decision["governance_task_type"] == "self_learning"
    assert decision["task_family"] == "self_learning"
    assert decision["execution_kind"] is None
    assert decision["decision_id"]
    idle_window = decision["context"]["idle_window"]
    assert idle_window["governance_task_type"] == "self_learning"
    assert idle_window["task_family"] == "self_learning"
    assert idle_window["checks"]["in_execution_window"] is False
    assert idle_window["decisions"]["eligible_for_execution"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_memory_maintenance_auto_decision_defers_when_memory_activity_is_recent(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Run memory compression sweep",
            "execution_kind": "memory_maintenance",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def fake_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:14:30",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = fake_snapshot  # type: ignore[method-assign]

    result = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "auto",
            "idle_window": {
                "now": "2026-05-25T00:15:00",
            },
        },
    )

    assert result["status"] == "deferred"
    assert result["task"]["status"] == "deferred"
    assert result["task"]["governance_task_type"] == "memory_maintenance"
    assert result["task"]["task_family"] == "memory_maintenance"
    assert result["task"]["execution_kind"] == "memory_maintenance"
    assert result["task"]["execution_request"] is None
    assert result["task"]["decision_history"][-1]["governance_task_type"] == "memory_maintenance"
    assert result["task"]["decision_history"][-1]["task_family"] == "memory_maintenance"
    assert result["task"]["decision_history"][-1]["execution_kind"] == "memory_maintenance"
    assert "memory maintenance requires the execution window plus idle user, runtime, memory" in result["task"]["decision_reason"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_and_body_switch_keep_distinct_task_family_metadata(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    result = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {
                    "title": "Prepare body upgrade handoff",
                    "execution_kind": "body_upgrade",
                },
                {
                    "title": "Prepare body switch handoff",
                    "execution_kind": "body_switch",
                },
            ]
        }
    )

    tasks_by_title = {task["title"]: task for task in result["tasks"]}
    upgrade_task = tasks_by_title["Prepare body upgrade handoff"]
    switch_task = tasks_by_title["Prepare body switch handoff"]

    assert upgrade_task["governance_task_type"] == "self_evolution"
    assert upgrade_task["task_family"] == "body_upgrade"
    assert upgrade_task["execution_kind"] == "body_upgrade"
    assert switch_task["governance_task_type"] == "self_evolution"
    assert switch_task["task_family"] == "body_switch"
    assert switch_task["execution_kind"] == "body_switch"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runtime_profile_prefers_canonical_task_fields_over_broad_task_type(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Canonical body switch should survive serialization",
            "task_type": "self_evolution",
            "task_family": "body_switch",
            "execution_kind": "body_switch",
            "metadata": {
                "task_family": "body_switch",
                "execution_kind": "body_switch",
            },
        }
    )

    task = planned["tasks"][0]
    listed = await supervisor.list_self_evolution_tasks()
    fetched = await supervisor.get_self_evolution_task(task["task_id"])

    assert task["task_type"] == "self_evolution"
    assert task["governance_task_type"] == "self_evolution"
    assert task["task_family"] == "body_switch"
    assert task["execution_kind"] == "body_switch"
    assert listed["tasks"][0]["task_family"] == "body_switch"
    assert listed["tasks"][0]["execution_kind"] == "body_switch"
    assert fetched["task_family"] == "body_switch"
    assert fetched["execution_kind"] == "body_switch"
