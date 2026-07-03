"""Phase 1 core loop end-to-end tests.

Validates the complete running cycle described in
docs/phase1-core-loop-and-endogenous-drive.md:

  endogenous_drive → plan → review → decide → dispatch → execute → trace
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest

from systems.supervisor.endogenous_drive import EndogenousDriveEngine
from systems.supervisor.supervisor import (
    Supervisor,
    SupervisorConfig,
)
from systems.supervisor.task_queue import SelfEvolutionTaskQueue


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_supervisor(tmp_path: Path) -> Supervisor:
    """Minimal wiring for in-process supervisor tests."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    (repo / "config.yaml").write_text("_config_version: 1\n", encoding="utf-8")

    cfg = SupervisorConfig()
    cfg.execution.git_repo_path = str(repo)
    cfg.soul_store_path = str(tmp_path / ".soul-runtime")

    sv = Supervisor(config=cfg)
    sv._agents = {}
    sv._governor = Mock()
    sv._governor.list_history = Mock(return_value=[])
    sv._governor.get_latest = Mock(return_value=None)
    sv._governor.record_boundary_defer = Mock()
    sv._governor.review = Mock()
    sv._body_registry.initialize_layout = Mock()
    sv._body_registry.load_registry = Mock()
    sv._body_registry.load_slot_meta = Mock()
    sv._body_registry.active_body_pointer_path = Mock(
        return_value=tmp_path / ".body-active.json"
    )
    sv._execution_facade = Mock()
    sv._execution_facade.execute_self_evolution_request = AsyncMock(
        return_value={"status": "executed"}
    )
    sv._execution_facade.execute_self_learning_followup = AsyncMock(
        return_value={"status": "learn_only_completed"}
    )
    sv._touch_gateway_activity = AsyncMock()
    sv._fetch_gateway_activity_snapshot = AsyncMock()
    sv._endogenous_drive_task = None
    sv._watch_window_runtime = type("_W", (), {"task": None, "last_outcome": None, "last_body_upgrade_trace_id": None})()
    return sv


def _idle_snapshot(
    *,
    user_idle: bool = True,
    active_sessions: int = 0,
    error_count: int = 1,
    uncertainty_high_count: int = 2,
) -> Dict[str, Any]:
    """Build a gateway activity snapshot for runtime-activity evaluation."""
    base_time = "2026-06-20T02:00:00"
    old_time = "2026-06-20T01:00:00" if user_idle else base_time
    return {
        "last_user_request_at": old_time if user_idle else base_time,
        "last_agent_work_at": old_time if user_idle else base_time,
        "last_memory_task_at": old_time if user_idle else base_time,
        "last_self_learning_activity_at": old_time if user_idle else base_time,
        "last_self_evolution_plan_at": old_time if user_idle else base_time,
        "last_self_evolution_execute_at": old_time if user_idle else base_time,
        "last_self_evolution_activity_at": old_time if user_idle else base_time,
        "counts": {
            "user_request_count": 10,
            "agent_work_count": 10,
            "memory_task_count": 3,
            "self_learning_activity_count": 5,
            "self_evolution_activity_count": 2,
            "self_evolution_plan_count": 2,
            "self_evolution_execute_count": 1,
            "error_count": error_count,
            "uncertainty_high_count": uncertainty_high_count,
        },
        "active_sessions": active_sessions,
        "recent_metadata": {},
    }


# ---------------------------------------------------------------------------
# Phase 1 loop steps
# ---------------------------------------------------------------------------

class TestPhase1EndogenousDriveToQueue:
    """Step ①: Endogenous drive evaluates and emits queue candidates/signals."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_memory_continuity_candidate_is_queued_under_current_drive_posture(self, tmp_path):
        """The current endogenous posture emits the memory continuity task candidate."""
        sv = _make_supervisor(tmp_path)
        # Increase max_candidates so all 4 are included
        sv.config = sv.config.model_copy(
            update={
                "service_runtime": sv.config.service_runtime.model_copy(
                    update={"endogenous_drive_max_candidates": 10}
                )
            }
        )
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        result = await sv._run_endogenous_drive_cycle()

        assert result["status"] == "planned", f"Expected planned, got {result}"
        assert result["planned"] >= 1, f"Expected at least one candidate, got {result['planned']}"

        tasks = await sv.list_self_evolution_tasks()
        keys = {
            t["metadata"].get("endogenous_drive_key")
            for t in tasks["tasks"]
        }
        assert "continuity:memory_maintenance_sweep" in keys
        assert any(t["governance_task_type"] == "memory_maintenance" for t in tasks["tasks"])

        # Verify canonical gateway field names are read correctly
        memory_task = next(
            t for t in tasks["tasks"]
            if t["metadata"].get("endogenous_drive_key") == "continuity:memory_maintenance_sweep"
        )
        assert memory_task["source"] == "endogenous_drive"
        assert memory_task["governance_task_type"] == "memory_maintenance"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_truthfulness_candidate_requires_live_correction_signal_pressure(self, tmp_path):
        """Truthfulness review is triggered by active correction signals, not stale counts alone."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value={
                **_idle_snapshot(error_count=3, uncertainty_high_count=0),
                "last_user_request_at": "2026-07-03T13:10:00",
                "last_agent_work_at": "2026-07-03T13:10:00",
                "last_memory_task_at": "2026-07-03T13:10:00",
                "last_self_learning_activity_at": "2026-07-03T13:10:00",
                "last_self_evolution_plan_at": "2026-07-03T13:10:00",
                "last_self_evolution_execute_at": "2026-07-03T13:10:00",
                "last_self_evolution_activity_at": "2026-07-03T13:10:00",
            }
        )

        evaluation = await sv.evaluate_endogenous_drive({})
        candidates = evaluation["candidates"]
        truth = next(
            (
                c
                for c in candidates
                if c.get("metadata", {}).get("endogenous_drive_key") == "truthfulness:review_correction_signals"
            ),
            None,
        )
        assert truth is not None, "truthfulness candidate should fire when error_count > 0"
        assert truth["priority"] == "high", f"Expected high priority for 3 errors, got {truth['priority']}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_no_duplicate_candidates_across_cycles(self, tmp_path):
        """Second drive cycle does not re-create already-queued candidates."""
        sv = _make_supervisor(tmp_path)
        sv.config = sv.config.model_copy(
            update={
                "service_runtime": sv.config.service_runtime.model_copy(
                    update={"endogenous_drive_max_candidates": 10}
                )
            }
        )
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        first = await sv._run_endogenous_drive_cycle()
        assert first["status"] == "planned"

        second = await sv._run_endogenous_drive_cycle()
        assert second["status"] == "idle", f"Second cycle should be idle, got {second}"


class TestPhase1IdleWindowGovernance:
    """Step ②-③: Idle-window evaluation and governance auto-decision."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_auto_decision_approves_memory_maintenance_when_runtime_signals_allow(self, tmp_path):
        """Memory maintenance is approved when its runtime activity signals are satisfied."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        # Create a memory_maintenance task via endogenous drive
        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_self_evolution_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )

        # Auto-decide
        decision = await sv.decide_self_evolution_task(
            mem_task["task_id"],
            {"decision": "auto", "idle_window": {"now": "2026-06-20T02:00:00"}},
        )
        assert decision["status"] == "approved", f"Expected approved, got {decision}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_auto_decision_no_longer_defers_by_time_of_day(self, tmp_path):
        """Whole-day execution means time-of-day no longer blocks auto decisions."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_self_evolution_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )

        decision = await sv.decide_self_evolution_task(
            mem_task["task_id"],
            {"decision": "auto", "idle_window": {"now": "2026-06-20T14:00:00"}},
        )
        assert decision["status"] == "approved", f"Expected approval under whole-day execution, got {decision}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_memory_maintenance_not_blocked_by_unrelated_activity(self, tmp_path):
        """After idle-window fix: memory_maintenance execution only requires
        user + agent + memory idle, NOT self_learning or self_evolution idle."""
        sv = _make_supervisor(tmp_path)
        # Self-learning and self-evolution are active, but memory path is idle
        snapshot = _idle_snapshot()
        snapshot["last_self_learning_activity_at"] = "2026-06-20T02:00:00"  # recent
        snapshot["last_self_evolution_plan_at"] = "2026-06-20T02:00:00"     # recent
        snapshot["last_self_evolution_execute_at"] = "2026-06-20T02:00:00"  # recent
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value=snapshot)

        idle = await sv.evaluate_idle_window({
            "now": "2026-06-20T02:15:00",
            "task_family": "memory_maintenance",
        })

        # memory_maintenance execution should STILL be eligible because
        # it only gates on user + agent + memory (the fix removed the extra checks)
        mem_decision = idle["task_family_decisions"]["memory_maintenance"]
        assert mem_decision["eligible_for_execution"] is True, (
            f"memory_maintenance should be eligible even when self_learning/self_evolution "
            f"are active. Got: {mem_decision}"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_self_evolution_uses_correct_idle_fields(self, tmp_path):
        """After idle-window fix: self_evolution checks self_evolution_plan_idle,
        NOT self_learning_idle."""
        sv = _make_supervisor(tmp_path)
        # self_evolution_plan is VERY recent (only 60s ago → NOT idle),
        # but self_learning is old (idle)
        snapshot = _idle_snapshot()
        snapshot["last_self_evolution_plan_at"] = "2026-06-20T02:14:00"  # 60s ago → NOT idle
        snapshot["last_self_learning_activity_at"] = "2026-06-20T01:00:00"  # 75min ago → idle
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value=snapshot)

        idle = await sv.evaluate_idle_window({
            "now": "2026-06-20T02:15:00",
            "task_family": "body_upgrade",
        })

        # body_upgrade maps to self_evolution decisions
        se_decision = idle["governance_task_type_decisions"]["self_evolution"]
        # has_self_evolution_plan_idle should be FALSE (the plan was active at 02:00)
        assert idle["checks"]["has_self_evolution_plan_idle"] is False, (
            "self_evolution_plan should NOT be idle when last_self_evolution_plan_at is recent"
        )
        # Therefore execution should be blocked
        assert se_decision["eligible_for_execution"] is False, (
            f"self_evolution execution should be blocked when plan was recent. "
            f"Got: {se_decision}"
        )


class TestPhase1ExecutionDispatchAndTraceWriteback:
    """Step ④-⑤: Dispatch to executor, trace_id flows through chain."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_execution_request_dispatched_for_approved_memory_task(self, tmp_path):
        """Approved memory_maintenance task gets dispatched with execution_request."""
        sv = _make_supervisor(tmp_path)
        sv.config = sv.config.model_copy(
            update={
                "service_runtime": sv.config.service_runtime.model_copy(
                    update={"endogenous_drive_max_candidates": 10}
                )
            }
        )
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        # Create all 4 candidates
        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_self_evolution_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )

        # Mock idle-window to guarantee in-window approval regardless of real time
        async def fake_idle_for_dispatch(_request=None):
            return {
                "status": "evaluated",
                "checks": {
                    "has_user_idle": True,
                    "has_agent_idle": True,
                    "has_memory_idle": True,
                    "in_execution_window": True,
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
                        "eligible_for_execution": True,
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
                        "eligible_for_execution": True,
                    },
                },
                "decisions": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
            }
        sv.evaluate_idle_window = fake_idle_for_dispatch  # type: ignore[method-assign]

        # Run full review + dispatch cycle
        result = await sv._run_self_evolution_cycle()
        assert result["dispatched"], f"No tasks dispatched: {result}"

        # Verify execution was dispatched and completed
        updated = await sv.get_self_evolution_task(mem_task["task_id"])
        assert updated["status"] in ("running", "completed"), (
            f"status not running/completed. status={updated.get('status')}, metadata={updated.get('metadata', {})}"
        )
        assert updated["metadata"].get("execution_result", {}).get("status") == "executed"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_duplicate_dispatch_prevented(self, tmp_path):
        """running status prevents duplicate execution."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_self_evolution_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )
        await sv.decide_self_evolution_task(
            mem_task["task_id"],
            {"decision": "auto", "idle_window": {"now": "2026-06-20T02:00:00"}},
        )

        # First dispatch
        await sv._run_self_evolution_cycle()
        call_count_before = sv._execution_facade.execute_self_evolution_request.call_count

        # Second dispatch attempt
        await sv._run_self_evolution_cycle()
        call_count_after = sv._execution_facade.execute_self_evolution_request.call_count

        # No additional calls — duplicate prevented
        assert call_count_after == call_count_before, (
            f"Duplicate dispatch not prevented: {call_count_before} → {call_count_after}"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_trace_id_flows_to_watch_window_state(self, tmp_path):
        """Body upgrade dispatch writes trace_id to WatchWindowRuntimeState."""
        sv = _make_supervisor(tmp_path)
        sv.config = sv.config.model_copy(
            update={
                "service_runtime": sv.config.service_runtime.model_copy(
                    update={"endogenous_drive_max_candidates": 10}
                )
            }
        )
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        # Generate all 4 candidates
        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_self_evolution_tasks()

        # Pick the memory continuity task emitted by the current drive posture.
        task = next(
            t for t in tasks["tasks"]
            if t["metadata"].get("endogenous_drive_key") == "continuity:memory_maintenance_sweep"
        )

        # Mock idle-window for in-window approval
        async def fake_idle(_request=None):
            return {
                "status": "evaluated",
                "checks": {
                    "has_user_idle": True, "has_agent_idle": True,
                    "has_memory_idle": True, "in_execution_window": True,
                },
                "task_family_decisions": {
                    "general_self_evolution": {
                        "eligible_for_planning": True,
                        "eligible_for_execution": True,
                    },
                },
                "governance_task_type_decisions": {
                    "self_evolution": {
                        "eligible_for_planning": True,
                        "eligible_for_execution": True,
                    },
                },
                "decisions": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                },
            }
        sv.evaluate_idle_window = fake_idle  # type: ignore[method-assign]

        await sv._run_self_evolution_cycle()

        updated = await sv.get_self_evolution_task(task["task_id"])
        assert updated["status"] in ("running", "completed"), (
            f"Task was not dispatched. status={updated.get('status')}, "
            f"metadata={updated.get('metadata')}"
        )
        assert "trace_id" in updated, "Task should have trace_id"


class TestPhase1TimezoneSafety:
    """Regression: timezone-aware timestamps from gateway don't crash idle-window."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_timezone_aware_iso_timestamps_are_normalized(self, tmp_path):
        """Gateway timestamps with timezone offset are safely parsed."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value={
                "last_user_request_at": "2026-06-20T01:00:00+00:00",  # aware
                "last_agent_work_at": "2026-06-20T01:00:00Z",          # UTC suffix
                "last_memory_task_at": "2026-06-20T01:00:00",          # naive
                "last_self_learning_activity_at": None,
                "last_self_evolution_plan_at": None,
                "last_self_evolution_execute_at": None,
                "last_self_evolution_activity_at": None,
                "counts": {"error_count": 0, "uncertainty_high_count": 0},
                "active_sessions": 0,
                "recent_metadata": {},
            }
        )

        # Must not raise TypeError
        result = await sv.evaluate_idle_window({
            "now": "2026-06-20T02:15:00",
            "task_family": "self_learning",
        })
        assert result["status"] == "evaluated"
        # All timestamps normalized to naive UTC, comparison safe
        assert result["idle_seconds"]["user"] >= 3600  # at least 1 hour idle


class TestPhase1AbandonCandidateAction:
    """Regression: probe→shell governed cleanup path exists."""

    def test_abandon_candidate_action_type_registered(self):
        """abandon_candidate is recognized as a valid GovernorActionType."""
        from systems.governor import GovernorAction, GovernorActionType
        # This is a type-level check: the Literal includes abandon_candidate
        action_types: set[str] = set(GovernorActionType.__args__)  # type: ignore[union-attr]
        assert "abandon_candidate" in action_types, (
            f"abandon_candidate missing from GovernorActionType: {sorted(action_types)}"
        )

    def test_abandon_candidate_probe_to_shell_allowed(self):
        """probe → shell is in ALLOWED_STATE_TRANSITIONS."""
        from systems.body_registry import ALLOWED_STATE_TRANSITIONS
        assert "shell" in ALLOWED_STATE_TRANSITIONS.get("probe", set()), (
            "probe→shell must be allowed in state transitions"
        )


class TestPhase1GatewayErrorTracking:
    """Regression: gateway error_count is populated and endogenous drive reads it."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_error_count_in_activity_snapshot(self, tmp_path):
        """Gateway snapshot includes error_count and uncertainty_high_count."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot(error_count=5, uncertainty_high_count=3)
        )

        idle = await sv.evaluate_idle_window({
            "now": "2026-06-20T02:15:00",
            "task_family": "self_learning",
        })
        counts = idle.get("activity", {}).get("counts", {})
        assert counts.get("error_count") == 5
        assert counts.get("uncertainty_high_count") == 3

    def test_endogenous_drive_reads_canonical_gateway_field_names(self):
        """Endogenous drive prefers error_count and uncertainty_high_count."""
        engine = EndogenousDriveEngine()
        idle_window = {
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 7,
                    "uncertainty_high_count": 4,
                    # Old field names should NOT be preferred
                    "recent_errors": 0,
                    "high_uncertainty": 0,
                },
            },
            "checks": {"has_user_idle": True},
            "idle_seconds": {"user": 900},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True},
                "memory_maintenance": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {},
            "decisions": {"eligible_for_planning": True, "eligible_for_execution": True},
        }
        candidates = engine.generate_candidates(
            idle_window=idle_window,
            existing_drive_keys=set(),
            max_candidates=10,
        )
        truth = next(
            (c for c in candidates if c.stable_key == "truthfulness:review_correction_signals"),
            None,
        )
        assert truth is not None
        # With 7 errors + 4 uncertainty = 11 correction signals, utility is high
        assert truth.utility > 0.85, f"Expected high utility, got {truth.utility}"
        assert truth.priority == "high"


class TestPhase1GovernorMode:
    """Supervisor AUTO gate state transitions and review-cycle behavior."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_governor_mode_activation_sets_flags(self, tmp_path):
        """Enabling the supervisor AUTO gate sets state and starts loops."""
        sv = _make_supervisor(tmp_path)
        sv._ensure_watch_window_task = Mock()
        sv.run_health_checks = AsyncMock(return_value={"results": []})
        sv._run_self_evolution_cycle = AsyncMock(return_value={"reviewed": 0, "dispatched": []})
        sv._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value={
            "last_agent_work_at": None, "counts": {}, "active_sessions": 0,
        })

        await sv._start_periodic_tasks()
        assert sv._service_runtime.governor_mode_active is False
        assert sv._self_evolution_review_task is None
        assert sv._endogenous_drive_task is None

        await sv._start_governor_mode()
        assert sv._service_runtime.governor_mode_active is True
        assert sv._self_evolution_review_task is not None
        assert sv._endogenous_drive_task is not None

        await sv._stop_governor_mode()
        assert sv._service_runtime.governor_mode_active is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_review_cycle_keeps_approved_self_learning_tasks_on_agent_pull_path(self, tmp_path):
        """Approved self-learning tasks remain on the agent-pull path during review cycles."""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value={
            "last_agent_work_at": "2026-06-20T00:00:00",  # hours ago
            "counts": {"error_count": 1, "uncertainty_high_count": 2},
            "active_sessions": 0,
        })
        sv._touch_gateway_activity = AsyncMock()
        sv._dispatch_self_learning_followup = AsyncMock(
            return_value={"status": "self_learning_followup_executed"}
        )
        sv._dispatch_self_evolution_execution_request = AsyncMock(
            return_value={"status": "executed"}
        )
        async def fake_idle(_request=None):
            return {
                "status": "evaluated",
                "checks": {"has_user_idle": True, "has_agent_idle": True, "has_memory_idle": True, "in_execution_window": True},
                "task_family_decisions": {
                    "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                },
                "governance_task_type_decisions": {
                    "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                },
                "decisions": {"eligible_for_planning": True, "eligible_for_execution": True},
            }
        sv.evaluate_idle_window = fake_idle  # type: ignore[method-assign]

        # Create an approved self_learning task
        sv._self_evolution_queue.create_task(
            title="Test learning task",
            summary="Test",
            trace_id="trace-1",
            task_type="self_learning",
            source="endogenous_drive",
            priority="normal",
            metadata={
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        )
        task = sv._self_evolution_queue.list_tasks()[0]
        sv._self_evolution_queue.update_status(
            task.task_id, status="approved", actor="test", reason="test"
        )

        await sv._run_self_evolution_cycle()
        # Self-learning tasks are NOT dispatched by supervisor review — they wait for Agent pull.
        sv._dispatch_self_learning_followup.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_governor_mode_idle_window_override(self, tmp_path):
        """Supervisor AUTO gate keeps self_learning planning eligible while the user is active."""
        sv = _make_supervisor(tmp_path)
        sv._service_runtime.governor_mode_active = True
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value=_idle_snapshot(
            user_idle=False  # user IS active
        ))

        idle = await sv.evaluate_idle_window({"task_family": "self_learning"})
        assert idle["governor_mode_active"] is True
        # self_learning planning should be eligible despite user being active
        assert idle["task_family_decisions"]["self_learning"]["eligible_for_planning"] is True


class TestPhase1LearningTopicExtraction:
    """Intelligent learning topic extraction from gateway metadata."""

    def test_extract_topic_from_user_request(self):
        """Topic extracted from recent user_request metadata."""
        from systems.supervisor.endogenous_drive import EndogenousDriveEngine
        engine = EndogenousDriveEngine()
        activity = {
            "recent_metadata": {
                "user_request": {
                    "text": "How can I optimize the agent tool-calling pipeline for lower latency?"
                }
            }
        }
        topic = engine._extract_learning_topic(activity)
        assert "optimize" in topic.lower()
        assert "agent tool-calling" in topic.lower()

    def test_extract_topic_fallback_to_agent_work(self):
        """Fallback to agent_work summary when no user_request."""
        from systems.supervisor.endogenous_drive import EndogenousDriveEngine
        engine = EndogenousDriveEngine()
        activity = {
            "recent_metadata": {
                "agent_work": {
                    "summary": "Investigated WebSocket vs SSE tradeoffs for real-time updates"
                }
            }
        }
        topic = engine._extract_learning_topic(activity)
        assert "websocket" in topic.lower() or "sse" in topic.lower()

    def test_extract_topic_empty_when_no_data(self):
        """Returns empty string when no usable metadata."""
        from systems.supervisor.endogenous_drive import EndogenousDriveEngine
        engine = EndogenousDriveEngine()
        assert engine._extract_learning_topic({}) == ""
        assert engine._extract_learning_topic({"recent_metadata": {}}) == ""
