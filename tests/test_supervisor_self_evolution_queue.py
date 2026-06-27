from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import AsyncMock
from fastapi import HTTPException

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.supervisor.supervisor import Supervisor, SupervisorConfig, SupervisorExecutionConfig


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
    )


def _make_supervisor(tmp_path: Path) -> Supervisor:
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
    assert any(task["governance_task_type"] == "self_learning" for task in queued["tasks"])
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
async def test_endogenous_drive_fallback_learning_targets_shell_codebase_without_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

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
                "counts": {},
                "recent_metadata": {},
            },
            "shell_slot": {
                "slot_id": "slot-B",
                "worktree_path": str((tmp_path / ".body-slots" / "slot-B" / "worktree").resolve()),
            },
            "completed_learning_tasks": [],
            "task_family_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "general_self_evolution": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
                "self_learning": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
                "self_evolution": {
                    "eligible_for_planning": False,
                    "eligible_for_execution": False,
                },
            },
        }

    supervisor.evaluate_idle_window = fake_idle_window  # type: ignore[method-assign]

    result = await supervisor._run_endogenous_drive_cycle()

    assert result["status"] == "planned"
    learning_task = next(
        task for task in result["tasks"]
        if task["governance_task_type"] == "self_learning"
    )
    assert learning_task["title"] == "Understand the current shell body codebase"
    assert learning_task["constraints"]["execution_policy"] == "learn_shell_baseline"
    assert learning_task["constraints"]["baseline_slot_id"] == "slot-B"
    assert learning_task["metadata"]["learning_branch"] == "codebase_baseline"
    assert learning_task["metadata"]["self_learning_mode"] == "shell_codebase_baseline"
    assert learning_task["evidence"]["learning_branch"] == "codebase_baseline"
    assert "slot-B" in learning_task["summary"]


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
async def test_batch_review_can_reapprove_deferred_tasks_on_later_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Revisit idle learning thread"},
                {"title": "Revisit stale improvement evidence"},
            ]
        }
    )

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    first = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )
    assert all(task["status"] == "deferred" for task in first["tasks"])

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    second = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    assert second["status"] == "reviewed"
    assert second["count"] == 2
    assert all(task["status"] == "approved" for task in second["tasks"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_endogenous_drive_still_plans_learning_candidates_with_active_sessions(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    async def fake_idle_window(request: dict | None = None):
        del request
        return {
            "checks": {"in_execution_window": True},
            "idle_seconds": {"user": 1000, "agent": 1000, "memory": 1000},
            "activity": {
                "counts": {},
                "active_sessions": 2,
                "recent_metadata": {
                    "user_request": {"topic": "AUTO foreground execution diagnostics"}
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

    result = await supervisor._run_endogenous_drive_cycle()
    queued = await supervisor.list_self_evolution_tasks()
    keys = {
        task["metadata"]["endogenous_drive_key"]: task for task in queued["tasks"]
    }

    assert result["status"] == "planned"
    assert any(task["governance_task_type"] == "self_learning" for task in queued["tasks"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_accepts_lm_queue_governance_override(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Learn unresolved architecture issue"},
                {"title": "Weak duplicate follow-up", "priority": "low"},
            ]
        }
    )
    tasks_by_title = {task["title"]: task["task_id"] for task in planned["tasks"]}

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        assert len(tasks) == 2
        assert idle_window["checks"]["has_user_idle"] is True
        return {
            tasks_by_title["Weak duplicate follow-up"]: {
                "action": "cancel",
                "reason": "Duplicate and low-evidence task should be cleared from the queue.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    tasks = {task["title"]: task for task in result["tasks"]}
    assert tasks["Learn unresolved architecture issue"]["status"] == "approved"
    assert tasks["Weak duplicate follow-up"]["status"] == "cancelled"
    lm_context = tasks["Weak duplicate follow-up"]["decision_history"][-1]["context"]["lm_queue_review"]
    assert lm_context["action"] == "cancelled"
    assert "Duplicate and low-evidence task" in lm_context["reason"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_governor_mode_preserves_agent_pull_task_approval_when_lm_suggests_defer(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor._service_runtime.governor_mode_active = True
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Explore one unresolved learning thread",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        del idle_window
        assert len(tasks) == 1
        return {
            task_id: {
                "action": "defer",
                "reason": "Conservative queue governance would wait for more evidence.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    assert result["tasks"][0]["status"] == "approved"
    latest_context = result["tasks"][0]["decision_history"][-1]["context"]
    assert latest_context["lm_queue_review"]["action"] == "deferred"
    assert latest_context["lm_queue_shadow"]["preserved_status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_preserves_agent_pull_task_approval_without_governor_mode(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Explore one unresolved learning thread",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        del idle_window
        assert len(tasks) == 1
        return {
            task_id: {
                "action": "defer",
                "reason": "Conservative queue governance would wait for more evidence.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    assert result["tasks"][0]["status"] == "approved"
    latest_context = result["tasks"][0]["decision_history"][-1]["context"]
    assert latest_context["lm_queue_review"]["action"] == "deferred"
    assert latest_context["lm_queue_shadow"]["preserved_status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_auto_approves_body_improvement_agent_pull_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = result["tasks"][0]
    assert task["task_id"] == task_id
    assert task["status"] == "approved"
    assert task["execution_kind"] == "body_improvement"
    assert task["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_defers_body_improvement_until_self_learning_finishes(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    await supervisor.plan_self_evolution_task(
        {
            "title": "Understand current shell body baseline",
            "task_family": "self_learning",
            "source": "self_learning",
        }
    )
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def busy_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:12:00",
            "last_agent_work_at": "2026-05-25T00:12:00",
            "last_memory_task_at": None,
            "last_self_evolution_activity_at": None,
            "counts": {},
            "active_sessions": 1,
        }

    supervisor._fetch_gateway_activity_snapshot = busy_snapshot  # type: ignore[method-assign]

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T00:15:00"},
        }
    )

    task = next(item for item in result["tasks"] if item["task_id"] == task_id)
    assert task["status"] == "deferred"
    assert task["execution_kind"] == "body_improvement"
    assert "self-learning tasks awaiting completion" in task["decision_reason"]
    assert task["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gateway_activity_snapshot_retries_after_transient_failure(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)

    calls = {"count": 0}

    class _FakeResponse:
        status = 200

        async def json(self):
            return {"last_user_request_at": None, "counts": {}, "active_sessions": 0}

        async def __aenter__(self):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("transient gateway timeout")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            del url
            return _FakeResponse()

    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)

    snapshot = await supervisor._fetch_gateway_activity_snapshot()

    assert snapshot["active_sessions"] == 0
    assert calls["count"] == 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_records_shadow_governance_actions_without_mutating_state(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Duplicate learning branch", "priority": "normal"},
                {"title": "Canonical learning branch", "priority": "high"},
            ]
        }
    )
    tasks_by_title = {task["title"]: task["task_id"] for task in planned["tasks"]}

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        assert len(tasks) == 2
        return {
            tasks_by_title["Duplicate learning branch"]: {
                "action": "merge",
                "reason": "This task duplicates the stronger canonical branch.",
                "shadow": {
                    "action": "merge",
                    "reason": "This task duplicates the stronger canonical branch.",
                    "merge_into": tasks_by_title["Canonical learning branch"],
                },
            },
            tasks_by_title["Canonical learning branch"]: {
                "action": "reprioritize",
                "reason": "Promote the canonical branch ahead of duplicates.",
                "shadow": {
                    "action": "reprioritize",
                    "reason": "Promote the canonical branch ahead of duplicates.",
                    "priority": "high",
                },
            },
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    tasks = {task["title"]: task for task in result["tasks"]}
    assert tasks["Duplicate learning branch"]["status"] == "approved"
    assert tasks["Canonical learning branch"]["status"] == "approved"
    duplicate_shadow = tasks["Duplicate learning branch"]["decision_history"][-1]["context"]["lm_queue_shadow"]
    canonical_shadow = tasks["Canonical learning branch"]["decision_history"][-1]["context"]["lm_queue_shadow"]
    assert duplicate_shadow["action"] == "merge"
    assert duplicate_shadow["merge_into"] == tasks_by_title["Canonical learning branch"]
    assert canonical_shadow["action"] == "reprioritize"
    assert canonical_shadow["priority"] == "high"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_batch_review_can_apply_lm_reprioritize_to_real_task_priority(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Underweighted architecture follow-up",
            "priority": "low",
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    async def idle_snapshot():
        return {
            "last_user_request_at": "2026-05-25T00:00:00",
            "last_agent_work_at": "2026-05-25T00:00:00",
            "last_memory_task_at": "2026-05-25T00:00:00",
            "last_self_learning_activity_at": "2026-05-25T00:00:00",
            "last_self_evolution_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, idle_window):
        return {
            task_id: {
                "action": "reprioritize",
                "priority": "high",
                "reason": "This follow-up now blocks higher-value evolution work.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    result = await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    task = result["tasks"][0]
    assert task["priority"] == "high"
    priority_context = task["decision_history"][-1]["context"]["lm_queue_priority"]
    assert priority_context["priority"] == "high"
    assert "blocks higher-value evolution work" in priority_context["reason"]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="Boundary violation defer removed — body_upgrade/body_switch no longer driven by task queue")
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
@pytest.mark.skip(reason="Boundary defer removed — body_upgrade/body_switch no longer driven by task queue")
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
    assert execution_request["kind"] == "general_self_evolution"
    assert execution_request["target_slot_id"] == "slot-B"
    assert execution_request["git_lineage"]["candidate_commit"] == "bbb222"
    assert execution_request["git_lineage"]["rollback_commit"] == "aaa111"
    assert execution_request["probe_report_ref"] == "probe-reports/slot-B/latest.json"
    assert execution_request["governor_decision"]["actor"] == "mem_supervisor"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.skip(reason="evolution_boundary removed — body switching validation no longer in task queue path")
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
@pytest.mark.skip(reason="Body boundary validation removed — body_upgrade not driven by task queue")
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
@pytest.mark.skip(reason="Body validation removed — SelfEvolutionExecutionRequest no longer validates git_lineage")
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
@pytest.mark.skip(reason="Body validation removed — SelfEvolutionExecutionRequest no longer validates changed_files")
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
    assert isinstance(idle_window["checks"]["in_execution_window"], bool)
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


@pytest.mark.asyncio
@pytest.mark.unit
async def test_list_self_evolution_tasks_can_filter_body_improvement_agent_tasks(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body after learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )

    listed = await supervisor.list_self_evolution_tasks(
        status="approved",
        execution_kind="body_improvement",
    )

    assert listed["count"] == 0

    task_id = supervisor._self_evolution_queue.list_tasks()[0].task_id
    await supervisor.decide_self_evolution_task(
        task_id,
        {"decision": "approve", "actor": "supervisor", "reason": "ready for agent pull"},
    )

    listed = await supervisor.list_self_evolution_tasks(
        status="approved",
        execution_kind="body_improvement",
    )

    assert listed["count"] == 1
    assert listed["tasks"][0]["execution_kind"] == "body_improvement"
    assert listed["tasks"][0]["execution_request"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_self_evolution_cycle_recovers_orphaned_agent_pull_running_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Recover abandoned learning task",
            "summary": "Ensure orphaned AUTO tasks return to approved",
            "task_type": "self_learning",
            "source": "endogenous_drive",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    supervisor._self_evolution_queue.update_status(
        task_id,
        status="approved",
        actor="test",
        reason="ready",
    )
    supervisor._self_evolution_queue.update_status(
        task_id,
        status="running",
        actor="cli_agent",
        reason="Agent pulled task for execution in AUTO mode.",
        context={"session_id": "stale-cli-session"},
    )
    supervisor._self_evolution_queue.update_metadata(
        task_id,
        metadata={
            "owner_session_id": "stale-cli-session",
            "execution_source": "cli_agent_pull",
            "execution_started_at": "2026-06-26T14:00:00+00:00",
        },
    )

    async def fake_active_executor():
        return {"session_id": "fresh-cli-session", "scene": "idle"}

    async def fake_review(request=None):
        return {"count": 0, "tasks": [], "decision": "approved", "reviewed_statuses": [], "idle_window": {}}

    supervisor._fetch_gateway_active_cli_executor = fake_active_executor  # type: ignore[method-assign]
    supervisor.review_self_evolution_tasks = fake_review  # type: ignore[method-assign]

    result = await supervisor._run_self_evolution_cycle()
    updated = await supervisor.get_self_evolution_task(task_id)

    assert result["recovered_orphaned"] == 1
    assert updated["status"] == "approved"
    assert updated["metadata"]["recovered_from_orphaned_running"] is True
    assert updated["decision_history"][-1]["context"]["previous_owner_session_id"] == "stale-cli-session"
    assert updated["decision_history"][-1]["context"]["active_cli_session_id"] == "fresh-cli-session"
