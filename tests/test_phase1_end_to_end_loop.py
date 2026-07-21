"""Phase 1 自主闭环端到端测试。

覆盖当前自主链路的完整运行回路：

  endogenous_drive -> plan -> review -> decide -> handoff -> execute -> trace
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
from systems.supervisor.autonomous_chain_store import AutonomousChainStore


pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_supervisor(tmp_path: Path) -> Supervisor:
    """为进程内 Supervisor 测试准备最小装配。"""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    (repo / "config.yaml").write_text("_config_version: 1\n", encoding="utf-8")

    cfg = SupervisorConfig()
    cfg.execution.git_repo_path = str(repo)
    cfg.soul_store_path = str(tmp_path / ".soul-runtime")
    cfg.service_runtime.endogenous_drive_lm_task_generation_enabled = False

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
    sv._execution_facade.execute_autonomous_chain_request = AsyncMock(
        return_value={"status": "executed"}
    )
    sv._execution_facade.execute_self_learning_followup = AsyncMock(
        return_value={"status": "learn_only_completed"}
    )
    sv._review_task_governance_with_supervisor = AsyncMock(return_value={})
    # These consumers have dedicated coverage in the autonomous-chain store
    # suite. Keep them neutral here so dynamic regulation cannot rewrite the
    # handoff target while this file is testing the Phase 1 execution path.
    empty_consumption = {"count": 0, "consumed": []}
    sv._consume_endogenous_governance_review_events = Mock(
        return_value=dict(empty_consumption)
    )
    sv._consume_endogenous_alignment_events = Mock(
        return_value=dict(empty_consumption)
    )
    sv._consume_endogenous_truthfulness_alerts = Mock(
        return_value=dict(empty_consumption)
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
    """构造一份用于运行时活动门控判断的 gateway 快照。"""
    base_time = "2026-06-20T02:00:00"
    old_time = "2026-06-20T01:00:00" if user_idle else base_time
    return {
        "last_user_request_at": old_time if user_idle else base_time,
        "last_agent_work_at": old_time if user_idle else base_time,
        "last_memory_task_at": old_time if user_idle else base_time,
        "last_self_learning_activity_at": old_time if user_idle else base_time,
        "last_autonomous_chain_plan_at": old_time if user_idle else base_time,
        "last_autonomous_chain_execute_at": old_time if user_idle else base_time,
        "last_autonomous_chain_activity_at": old_time if user_idle else base_time,
        "counts": {
            "user_request_count": 10,
            "agent_work_count": 10,
            "memory_task_count": 3,
            "self_learning_activity_count": 5,
            "autonomous_chain_activity_count": 2,
            "autonomous_chain_plan_count": 2,
            "autonomous_chain_execute_count": 1,
            "error_count": error_count,
            "uncertainty_high_count": uncertainty_high_count,
        },
        "active_sessions": active_sessions,
        "recent_metadata": {},
    }


async def _drive_input_from_runtime_probe(
    supervisor: Supervisor,
    *,
    now: str,
    task_family: str,
) -> Dict[str, Any]:
    runtime_probe_snapshot = await supervisor.evaluate_drive_input(
        {
            "now": now,
            "task_family": task_family,
        }
    )
    return supervisor._project_drive_input_snapshot(runtime_probe_snapshot)


# ---------------------------------------------------------------------------
# Phase 1 loop steps
# ---------------------------------------------------------------------------

class TestPhase1EndogenousDriveToBacklog:
    """步骤 ①：内生驱动评估后形成治理在途候选与信号。"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_memory_continuity_candidate_is_planned_under_current_drive_posture(self, tmp_path):
        """当前内生姿态会形成记忆连续性候选。"""
        sv = _make_supervisor(tmp_path)
        # 拉高上限，确保 4 个候选都能进入本轮结果。
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

        tasks = await sv.list_autonomous_chain_tasks()
        keys = {
            t["metadata"].get("endogenous_drive_key")
            for t in tasks["tasks"]
        }
        assert "continuity:memory_maintenance_sweep" in keys
        assert any(t["governance_task_type"] == "memory_maintenance" for t in tasks["tasks"])

        # 确认读取的仍是 gateway 规范字段名。
        memory_task = next(
            t for t in tasks["tasks"]
            if t["metadata"].get("endogenous_drive_key") == "continuity:memory_maintenance_sweep"
        )
        assert memory_task["source"] == "endogenous_drive"
        assert memory_task["governance_task_type"] == "memory_maintenance"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_truthfulness_candidate_requires_live_correction_signal_pressure(self, tmp_path):
        """真实性复核要由仍在生效的修正压力触发，而不是只看陈旧计数。"""
        sv = _make_supervisor(tmp_path)
        live_signal_at = datetime.now().isoformat()
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value={
                **_idle_snapshot(error_count=3, uncertainty_high_count=0),
                "last_user_request_at": live_signal_at,
                "last_agent_work_at": live_signal_at,
                "last_memory_task_at": live_signal_at,
                "last_self_learning_activity_at": live_signal_at,
                "last_autonomous_chain_plan_at": live_signal_at,
                "last_autonomous_chain_execute_at": live_signal_at,
                "last_autonomous_chain_activity_at": live_signal_at,
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
        assert truth is not None, "当 error_count > 0 时应形成真实性候选"
        assert truth["priority"] == "high", f"3 个错误应给出高优先级，实际是 {truth['priority']}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_no_duplicate_candidates_across_cycles(self, tmp_path):
        """第二轮内生驱动不会重复生成已经进入治理在途的候选。"""
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
        assert second["status"] == "idle", f"第二轮应保持 idle，实际得到 {second}"


class TestPhase1IdleWindowGovernance:
    """步骤 ②-③：活动门控评估与治理自动裁决。"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_auto_decision_approves_memory_maintenance_when_runtime_signals_allow(self, tmp_path):
        """运行时信号满足时，记忆维护可以自动获批。"""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        # 先通过内生驱动生成一条记忆维护链路项。
        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_autonomous_chain_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )

        # 再执行自动裁决。
        drive_input = await _drive_input_from_runtime_probe(
            sv,
            now="2026-06-20T02:00:00",
            task_family="memory_maintenance",
        )
        decision = await sv.decide_autonomous_chain_task(
            mem_task["task_id"],
            {"decision": "auto", "drive_input": drive_input},
        )
        assert decision["status"] == "approved", f"Expected approved, got {decision}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_auto_decision_no_longer_defers_by_time_of_day(self, tmp_path):
        """全天候执行意味着不再因为时段不同而推迟自动裁决。"""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_autonomous_chain_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )

        drive_input = await _drive_input_from_runtime_probe(
            sv,
            now="2026-06-20T14:00:00",
            task_family="memory_maintenance",
        )
        decision = await sv.decide_autonomous_chain_task(
            mem_task["task_id"],
            {"decision": "auto", "drive_input": drive_input},
        )
        assert decision["status"] == "approved", f"全天候执行下应允许批准，实际得到 {decision}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_memory_maintenance_not_blocked_by_unrelated_activity(self, tmp_path):
        """修正后记忆维护只要求 user/agent/memory 安静，不受其它自主活动影响。"""
        sv = _make_supervisor(tmp_path)
        # 自主学习和自主改进仍在活动，但记忆链路本身是安静的。
        snapshot = _idle_snapshot()
        snapshot["last_self_learning_activity_at"] = "2026-06-20T02:00:00"  # recent
        snapshot["last_autonomous_chain_plan_at"] = "2026-06-20T02:00:00"     # recent
        snapshot["last_autonomous_chain_execute_at"] = "2026-06-20T02:00:00"  # recent
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value=snapshot)

        idle = await sv.evaluate_drive_input({
            "now": "2026-06-20T02:15:00",
            "task_family": "memory_maintenance",
        })

        # 记忆维护仍应可执行，因为这里只看 user + agent + memory 三条信号。
        mem_decision = idle["task_family_decisions"]["memory_maintenance"]
        assert mem_decision["eligible_for_execution"] is True, (
            f"即便 self_learning/self_evolution 活跃，memory_maintenance 也应可执行。实际: {mem_decision}"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_self_evolution_uses_correct_idle_fields(self, tmp_path):
        """修正后 self_evolution 应检查 autonomous_chain_plan，而不是借用 self_learning 空闲位。"""
        sv = _make_supervisor(tmp_path)
        # autonomous_chain_plan 很新，因此不空闲；self_learning 很旧，因此空闲。
        snapshot = _idle_snapshot()
        snapshot["last_autonomous_chain_plan_at"] = "2026-06-20T02:14:00"  # 60s ago → NOT idle
        snapshot["last_self_learning_activity_at"] = "2026-06-20T01:00:00"  # 75min ago → idle
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value=snapshot)

        idle = await sv.evaluate_drive_input({
            "now": "2026-06-20T02:15:00",
            "task_family": "body_upgrade",
        })

        # body_upgrade 会映射到 self_evolution 判断。
        se_decision = idle["governance_task_type_decisions"]["self_evolution"]
        # has_autonomous_chain_plan_idle 应为 False。
        assert idle["checks"]["has_autonomous_chain_plan_idle"] is False, (
            "last_autonomous_chain_plan_at 很新时，autonomous_chain_plan 不应被判为空闲"
        )
        # 因此执行应被拦住。
        assert se_decision["eligible_for_execution"] is False, (
            f"plan 最近活跃时，self_evolution 应被拦住。实际: {se_decision}"
        )


class TestPhase1ExecutionDispatchAndTraceWriteback:
    """步骤 ④-⑤：交接给执行面，并让 trace_id 贯穿链路。"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_execution_request_handed_off_for_approved_memory_task(self, tmp_path):
        """获批的记忆维护链路项会生成 execution_request 并被交接。"""
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

        # 先生成 4 个候选。
        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_autonomous_chain_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )

        # 伪造活动门控，确保本轮一定允许交接。
        async def fake_idle_for_handoff(_request=None):
            return {
                "status": "evaluated",
                "checks": {
                    "has_api_a_execution_idle": True,
                    "has_memory_idle": True,
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
        sv.evaluate_drive_input = fake_idle_for_handoff  # type: ignore[method-assign]

        # 跑完整复核与交接闭环。
        result = await sv._run_autonomous_chain_review_cycle()
        updated = await sv.get_autonomous_chain_task(mem_task["task_id"])
        assert result["handed_off"], (
            f"No tasks handed off: result={result}, task={updated}"
        )

        # 确认执行已经被交接并留下结果。
        assert updated["status"] in ("running", "completed"), (
            f"status not running/completed. status={updated.get('status')}, metadata={updated.get('metadata', {})}"
        )
        assert updated["metadata"].get("execution_result", {}).get("status") == "executed"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_duplicate_handoff_prevented(self, tmp_path):
        """进入 running 后不应再次重复交接。"""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot()
        )

        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_autonomous_chain_tasks()
        mem_task = next(
            t for t in tasks["tasks"]
            if t["governance_task_type"] == "memory_maintenance"
        )
        drive_input = await _drive_input_from_runtime_probe(
            sv,
            now="2026-06-20T02:00:00",
            task_family="memory_maintenance",
        )
        await sv.decide_autonomous_chain_task(
            mem_task["task_id"],
            {"decision": "auto", "drive_input": drive_input},
        )

        # 第一次交接。
        await sv._run_autonomous_chain_review_cycle()
        call_count_before = sv._execution_facade.execute_autonomous_chain_request.call_count

        # 第二次尝试交接。
        await sv._run_autonomous_chain_review_cycle()
        call_count_after = sv._execution_facade.execute_autonomous_chain_request.call_count

        # 不应新增调用，说明重复交接已被拦住。
        assert call_count_after == call_count_before, (
            f"Duplicate handoff not prevented: {call_count_before} → {call_count_after}"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_trace_id_flows_to_watch_window_state(self, tmp_path):
        """链路交接会把 trace_id 写入 WatchWindowRuntimeState。"""
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

        # 先生成 4 个候选。
        await sv._run_endogenous_drive_cycle()
        tasks = await sv.list_autonomous_chain_tasks()

        # 取出当前姿态下形成的记忆连续性链路项。
        task = next(
            t for t in tasks["tasks"]
            if t["metadata"].get("endogenous_drive_key") == "continuity:memory_maintenance_sweep"
        )

        # 伪造活动门控，确保当前窗口允许通过。
        async def fake_idle(_request=None):
            return {
                "status": "evaluated",
                "checks": {
                    "has_api_a_execution_idle": True,
                    "has_memory_idle": True,
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
        sv.evaluate_drive_input = fake_idle  # type: ignore[method-assign]

        await sv._run_autonomous_chain_review_cycle()

        updated = await sv.get_autonomous_chain_task(task["task_id"])
        assert updated["status"] in ("running", "completed"), (
            f"Task was not handed off. status={updated.get('status')}, "
            f"metadata={updated.get('metadata')}"
        )
        assert "trace_id" in updated, "Task should have trace_id"


class TestPhase1TimezoneSafety:
    """回归：gateway 的带时区时间戳不会让活动门控崩掉。"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_timezone_aware_iso_timestamps_are_normalized(self, tmp_path):
        """带时区偏移的 gateway 时间戳可以被安全解析。"""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value={
                "last_user_request_at": "2026-06-20T01:00:00+00:00",  # aware
                "last_agent_work_at": "2026-06-20T01:00:00Z",          # UTC suffix
                "last_memory_task_at": "2026-06-20T01:00:00",          # naive
                "last_self_learning_activity_at": None,
                "last_autonomous_chain_plan_at": None,
                "last_autonomous_chain_execute_at": None,
                "last_autonomous_chain_activity_at": None,
                "counts": {"error_count": 0, "uncertainty_high_count": 0},
                "active_sessions": 0,
                "recent_metadata": {},
            }
        )

        # 不应抛出 TypeError。
        result = await sv.evaluate_drive_input({
            "now": "2026-06-20T02:15:00",
            "task_family": "self_learning",
        })
        assert result["status"] == "evaluated"
        # 所有时间都应被收口到可安全比较的形式。
        assert result["idle_seconds"]["user"] >= 3600  # 至少空闲 1 小时


class TestPhase1AbandonCandidateAction:
    """回归：probe -> shell 的受治理清理路径仍然存在。"""

    def test_abandon_candidate_action_type_registered(self):
        """abandon_candidate 仍是有效的 GovernorActionType。"""
        from systems.governor import GovernorAction, GovernorActionType
        # 这里只做类型层检查：Literal 里必须仍包含 abandon_candidate。
        action_types: set[str] = set(GovernorActionType.__args__)  # type: ignore[union-attr]
        assert "abandon_candidate" in action_types, (
            f"abandon_candidate missing from GovernorActionType: {sorted(action_types)}"
        )

    def test_abandon_candidate_probe_to_shell_allowed(self):
        """probe -> shell 仍在 ALLOWED_STATE_TRANSITIONS 中。"""
        from systems.body_registry import ALLOWED_STATE_TRANSITIONS
        assert "shell" in ALLOWED_STATE_TRANSITIONS.get("probe", set()), (
            "probe→shell must be allowed in state transitions"
        )


class TestPhase1GatewayErrorTracking:
    """回归：gateway 会带出 error_count，且内生驱动会读取它。"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_error_count_in_activity_snapshot(self, tmp_path):
        """gateway 快照里应包含 error_count 与 uncertainty_high_count。"""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(
            return_value=_idle_snapshot(error_count=5, uncertainty_high_count=3)
        )

        idle = await sv.evaluate_drive_input({
            "now": "2026-06-20T02:15:00",
            "task_family": "self_learning",
        })
        counts = idle.get("activity", {}).get("counts", {})
        assert counts.get("error_count") == 5
        assert counts.get("uncertainty_high_count") == 3

    def test_endogenous_drive_reads_canonical_gateway_field_names(self):
        """内生驱动优先读取 error_count 与 uncertainty_high_count。"""
        engine = EndogenousDriveEngine()
        drive_input = {
            "activity": {
                "active_sessions": 0,
                "counts": {
                    "error_count": 7,
                    "uncertainty_high_count": 4,
                    # 旧字段名不应再优先。
                    "recent_errors": 0,
                    "high_uncertainty": 0,
                },
            },
            "checks": {},
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
            drive_input=drive_input,
            existing_drive_keys=set(),
            max_candidates=10,
        )
        truth = next(
            (c for c in candidates if c.stable_key == "truthfulness:review_correction_signals"),
            None,
        )
        assert truth is not None
        # 7 个错误 + 4 个高不确定性，应形成较高 utility。
        assert truth.utility > 0.85, f"应得到较高 utility，实际是 {truth.utility}"
        assert truth.priority == "high"


class TestPhase1GovernorMode:
    """监督者自主链路门控的状态流转与复核周期行为。"""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_autonomous_chain_gate_activation_sets_flags(self, tmp_path):
        """监督者启动保持门控关闭，显式激活后才运行自主链路。"""
        sv = _make_supervisor(tmp_path)
        sv._ensure_watch_window_task = Mock()
        sv.run_health_checks = AsyncMock(return_value={"results": []})
        sv._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0, "handed_off": []})
        sv._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value={
            "last_agent_work_at": None, "counts": {}, "active_sessions": 0,
        })

        await sv._start_periodic_tasks()
        assert sv._service_runtime.autonomous_chain_gate_active is False
        assert sv._autonomous_chain_review_task is None
        assert sv._endogenous_drive_task is None

        await sv._start_autonomous_chain_gate()
        assert sv._service_runtime.autonomous_chain_gate_active is True
        assert sv._autonomous_chain_review_task is not None
        assert sv._endogenous_drive_task is not None

        await sv._stop_autonomous_chain_gate()
        assert sv._service_runtime.autonomous_chain_gate_active is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_review_cycle_keeps_approved_self_learning_tasks_on_agent_pull_path(self, tmp_path):
        """获批的自主学习链路项在复核周期里仍停留在 Agent pull 路径。"""
        sv = _make_supervisor(tmp_path)
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value={
            "last_agent_work_at": "2026-06-20T00:00:00",  # hours ago
            "counts": {"error_count": 1, "uncertainty_high_count": 2},
            "active_sessions": 0,
        })
        sv._touch_gateway_activity = AsyncMock()
        sv._handoff_autonomous_chain_execution_request = AsyncMock(
            return_value={"status": "executed"}
        )
        async def fake_idle(_request=None):
            return {
                "status": "evaluated",
                "checks": {
                    "has_api_a_execution_idle": True,
                    "has_memory_idle": True,
                },
                "task_family_decisions": {
                    "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                },
                "governance_task_type_decisions": {
                    "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                },
                "decisions": {"eligible_for_planning": True, "eligible_for_execution": True},
            }
        sv.evaluate_drive_input = fake_idle  # type: ignore[method-assign]

        # 手工造一条已获批的 self_learning 链路项。
        sv._autonomous_chain_store.create_task(
            title="测试学习任务",
            summary="测试",
            trace_id="trace-1",
            task_type="self_learning",
            source="endogenous_drive",
            priority="normal",
            metadata={
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        )
        task = sv._autonomous_chain_store.list_tasks()[0]
        sv._autonomous_chain_store.update_status(
            task.task_id, status="approved", actor="test", reason="测试"
        )

        await sv._run_autonomous_chain_review_cycle()
        # self-learning 不由 supervisor 主动交接，而是等待 Agent 自己来 pull。
        sv._handoff_autonomous_chain_execution_request.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_autonomous_chain_gate_activity_guard_override(self, tmp_path):
        """自主链路门控开启后，即使用户活跃也不阻断 self_learning 的规划资格。"""
        sv = _make_supervisor(tmp_path)
        sv._service_runtime.autonomous_chain_gate_active = True
        sv._fetch_gateway_activity_snapshot = AsyncMock(return_value=_idle_snapshot(
            user_idle=False  # 用户此刻仍在活跃
        ))

        idle = await sv.evaluate_drive_input({"task_family": "self_learning"})
        assert idle["autonomous_chain_gate_active"] is True
        # 即便用户活跃，self_learning 规划资格也应保留。
        assert idle["task_family_decisions"]["self_learning"]["eligible_for_planning"] is True


class TestPhase1LearningTopicExtraction:
    """从 gateway 元数据中提取学习主题。"""

    def test_extract_topic_from_user_request(self):
        """可以从最近 user_request 元数据里提取主题。"""
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
        """当没有 user_request 时，会回退到 agent_work 摘要。"""
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
        """没有可用元数据时返回空字符串。"""
        from systems.supervisor.endogenous_drive import EndogenousDriveEngine
        engine = EndogenousDriveEngine()
        assert engine._extract_learning_topic({}) == ""
        assert engine._extract_learning_topic({"recent_metadata": {}}) == ""





