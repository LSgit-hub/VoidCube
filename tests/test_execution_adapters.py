from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.body_registry import BodyRegistryManager
from systems.execution.adapters import (
    BodyUpgradeExecutionAdapter,
    BodyLifecycleExecutionAdapter,
    GovernorReviewExecutionAdapter,
    WatchWindowExecutionAdapter,
)
from systems.execution.facade import VoidCubeExecutionFacade
from systems.governor import GovernorRequest
from systems.governor import GovernorDecisionEngine
from systems.lifecycle import BodyLifecycleExecutor
from systems.probe import ProbeExecutor, ProbeRunner
from systems.supervisor.supervisor import AgentInstance


def _attach_route_hint(payload: dict, interface_id: str) -> dict:
    result = dict(payload)
    result["execution_route_hint"] = {"interface_id": interface_id}
    return result


def _make_watch_window_state(*, task=None, last_outcome=None, last_body_upgrade_trace_id=None) -> SimpleNamespace:
    return SimpleNamespace(task=task, last_outcome=last_outcome, last_body_upgrade_trace_id=last_body_upgrade_trace_id)


def _make_governor_request_executor(result=None) -> SimpleNamespace:
    return SimpleNamespace(execute_governor_request=Mock(return_value=result or {"status": "executed"}))


def _seed_body_repo(tmp_path: Path, *, probe_ready: bool) -> None:
    (tmp_path / "systems").mkdir(exist_ok=True)
    (tmp_path / "systems" / "agent").mkdir(exist_ok=True)
    (tmp_path / "systems" / "agent" / "run_agent_instance.py").write_text(
        "print('slot launch')\n",
        encoding="utf-8",
    )
    (tmp_path / ".soul-runtime").mkdir(exist_ok=True)

    if not probe_ready:
        return

    (tmp_path / "run_agent.py").write_text("print('agent entrypoint')\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(exist_ok=True)
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "model_tools.py").write_text("# probe smoke\n", encoding="utf-8")


def _make_body_upgrade_runtime(tmp_path: Path) -> SimpleNamespace:
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    lifecycle = BodyLifecycleExecutor(manager)
    body_lifecycle = BodyLifecycleExecutionAdapter(
        config=SimpleNamespace(git_repo_path=str(tmp_path)),
        body_registry=manager,
        lifecycle=lifecycle,
        probe_runner=ProbeRunner(),
        probe_executor=ProbeExecutor(),
        governor_storage_root=str(tmp_path / ".soul-runtime"),
        attach_execution_route_hint=_attach_route_hint,
    )
    engine = GovernorDecisionEngine()

    def review(governor_request, *, slot_meta=None):
        return engine.evaluate(governor_request, slot_meta=slot_meta)

    governor = SimpleNamespace(
        review=review,
        record_execution_outcome=Mock(),
    )
    governor_review = GovernorReviewExecutionAdapter(
        body_registry=manager,
        governor=governor,
        lifecycle=lifecycle,
        watch_window_runtime_sync=SimpleNamespace(
            sync_runtime_after_governor_response=Mock(
                return_value={"status": "no_watch_window_runtime_change"},
            )
        ),
    )
    recorded_requests: list[Any] = []
    governor_request_executor = SimpleNamespace(
        execute_governor_request=Mock(
            side_effect=lambda governor_request: (
                recorded_requests.append(governor_request),
                governor_review.execute_governor_request(governor_request),
            )[1]
        )
    )

    adapter = BodyUpgradeExecutionAdapter(
        config=SimpleNamespace(probe_watch_window_seconds=300),
        body_registry=manager,
        run_body_probe=body_lifecycle.run_body_probe,
        attach_execution_route_hint=_attach_route_hint,
        agents={},
        governor_request_executor=governor_request_executor,
    )
    return SimpleNamespace(
        adapter=adapter,
        manager=manager,
        body_lifecycle=body_lifecycle,
        governor_review=governor_review,
        governor_request_executor=governor_request_executor,
        recorded_requests=recorded_requests,
    )


def _make_body_lifecycle_runtime(tmp_path: Path) -> SimpleNamespace:
    manager = BodyRegistryManager(tmp_path)
    manager.initialize_layout()
    lifecycle = BodyLifecycleExecutor(manager)
    adapter = BodyLifecycleExecutionAdapter(
        config=SimpleNamespace(git_repo_path=str(tmp_path)),
        body_registry=manager,
        lifecycle=lifecycle,
        probe_runner=ProbeRunner(),
        probe_executor=ProbeExecutor(),
        governor_storage_root=str(tmp_path / ".soul-runtime"),
        attach_execution_route_hint=_attach_route_hint,
    )
    return SimpleNamespace(
        adapter=adapter,
        manager=manager,
        lifecycle=lifecycle,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_execution_facade_delegates_to_current_adapters():
    body_lifecycle = SimpleNamespace(
        get_body_registry=Mock(return_value={"registry": {"active_slot": "slot-A"}}),
        get_active_body_target=Mock(return_value={"slot_id": "slot-A"}),
        list_body_slots=Mock(return_value={"slots": {"slot-A": {}}}),
        get_body_slot=Mock(return_value={"slot_id": "slot-A"}),
        prepare_body_slot=AsyncMock(return_value={"status": "slot_prepared"}),
        mark_body_candidate=AsyncMock(return_value={"status": "candidate_marked"}),
        record_body_probe_report=AsyncMock(return_value={"status": "probe_report_recorded"}),
        run_body_probe=AsyncMock(return_value={"status": "probe_executed"}),
    )
    watch_window = SimpleNamespace(
        reconcile_watch_window_outcome=AsyncMock(return_value={"action": "retired_slot_recycled"}),
        get_watch_window_status=Mock(return_value={"status": "watch_status"}),
        ensure_watch_window_task=Mock(return_value="task-handle"),
        sync_runtime_after_governor_response=Mock(return_value={"status": "watch_window_runtime_ensured"}),
        build_watch_window_evidence=Mock(return_value={"healthy": True}),
        poll_watch_window=AsyncMock(return_value={"should_evaluate": False}),
        run_watch_window_loop=AsyncMock(return_value=None),
        evaluate_watch_window=AsyncMock(return_value={"status": "watch_window_evaluated"}),
    )
    body_upgrade = SimpleNamespace(
        execute_body_upgrade=AsyncMock(return_value={"status": "upgrade_executed"}),
    )
    memory_maintenance = SimpleNamespace(
        trigger_memory_compression=AsyncMock(return_value={"status": "compressed"}),
    )
    facade = VoidCubeExecutionFacade(
        watch_window=watch_window,
        body_lifecycle=body_lifecycle,
        body_upgrade=body_upgrade,
        memory_maintenance=memory_maintenance,
    )

    assert facade.get_watch_window_status() == {"status": "watch_status"}
    assert await facade.evaluate_watch_window({"healthy_override": True}) == {"status": "watch_window_evaluated"}
    assert facade.get_body_registry() == {"registry": {"active_slot": "slot-A"}}
    assert facade.get_active_body_target() == {"slot_id": "slot-A"}
    assert facade.list_body_slots() == {"slots": {"slot-A": {}}}
    assert facade.get_body_slot("slot-A") == {"slot_id": "slot-A"}
    assert await facade.prepare_body_slot("slot-B", {}) == {"status": "slot_prepared"}
    assert await facade.mark_body_candidate("slot-B", {}) == {"status": "candidate_marked"}
    assert await facade.execute_body_upgrade({}) == {"status": "upgrade_executed"}
    formal_result = await facade.execute_self_evolution_request(
        {
            "task_id": "task-1",
            "kind": "general_self_evolution",
            "source_actor": "mem_supervisor",
            "target_slot_id": "slot-B",
            "git_lineage": {
                "candidate_commit": "bbb222",
                "rollback_commit": "aaa111",
                "changed_files": ["agent/stream_handler.py"],
            },
        }
    )
    assert await facade.record_body_probe_report({"slot_id": "slot-B"}) == {"status": "probe_report_recorded"}
    assert await facade.run_body_probe({"slot_id": "slot-B"}) == {"status": "probe_executed"}
    assert await facade.trigger_memory_compression({}) == {"status": "compressed"}

    watch_window.get_watch_window_status.assert_called_once_with()
    watch_window.evaluate_watch_window.assert_awaited_once_with({"healthy_override": True})
    body_lifecycle.get_body_slot.assert_called_once_with("slot-A")
    body_lifecycle.mark_body_candidate.assert_awaited_once_with("slot-B", {})
    assert body_upgrade.execute_body_upgrade.await_count == 2
    body_upgrade.execute_body_upgrade.assert_any_await({})
    body_upgrade.execute_body_upgrade.assert_any_await(
        {
            "slot_id": "slot-B",
            "execution_request": formal_result["execution_request"],
        }
    )
    assert formal_result["status"] == "formal_self_evolution_executed"
    memory_maintenance.trigger_memory_compression.assert_awaited_once_with({})




@pytest.mark.unit
def test_governor_review_execution_adapter_coordinates_review_and_runtime_followup():
    slot_meta = SimpleNamespace(last_probe_result={"overall_passed": True, "summary": "probe ok"})
    registry = SimpleNamespace(model_dump=lambda mode="json": {"active_slot": "slot-B", "retired_slot": "slot-A"})
    governor_response = SimpleNamespace(
        decision="approve_with_watch",
        model_dump=lambda mode="json": {"decision": "approve_with_watch"},
    )
    execution_report = SimpleNamespace(model_dump=lambda mode="json": {"status": "applied"})
    body_registry = SimpleNamespace(
        load_slot_meta=Mock(return_value=slot_meta),
        load_registry=Mock(return_value=registry),
    )
    governor = SimpleNamespace(
        review=Mock(return_value=governor_response),
        record_execution_outcome=Mock(),
    )
    lifecycle = SimpleNamespace(apply_governor_response=Mock(return_value=execution_report))
    sync_runtime_after_governor_response = Mock(
        return_value={"status": "watch_window_runtime_ensured"},
    )
    adapter = GovernorReviewExecutionAdapter(
        body_registry=body_registry,
        governor=governor,
        lifecycle=lifecycle,
        watch_window_runtime_sync=SimpleNamespace(
            sync_runtime_after_governor_response=sync_runtime_after_governor_response
        ),
    )

    result = adapter.execute_governor_request(
        GovernorRequest.model_validate(
            {
                "request_id": "switch-1",
                "event_type": "switch_request",
                "body_id": "slot-B",
                "source_actor": "gateway",
                "summary": "Promote candidate after probe pass",
                "evidence": {},
                "constraints": {"watch_window_seconds": 120},
            }
        )
    )

    assert result["governor_response"]["decision"] == "approve_with_watch"
    assert result["execution_report"]["status"] == "applied"
    assert result["runtime_followup"] == {"status": "watch_window_runtime_ensured"}
    governor.review.assert_called_once()
    reviewed_request = governor.review.call_args.args[0]
    assert reviewed_request.evidence["probe_report"]["overall_passed"] is True
    assert reviewed_request.evidence["probe_passed"] is True
    sync_runtime_after_governor_response.assert_called_once_with(governor_response)
    governor.record_execution_outcome.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_lifecycle_adapter_marks_candidate_through_registry(tmp_path: Path):
    (tmp_path / "run_agent.py").write_text("print('agent')\n", encoding="utf-8")
    runtime = _make_body_lifecycle_runtime(tmp_path)

    result = await runtime.adapter.mark_body_candidate("slot-B", {"body_version": "v2"})

    slot = runtime.manager.load_slot_meta("slot-B")
    assert result["status"] == "candidate_marked"
    assert result["slot"]["body_state"] == "candidate"
    assert result["slot"]["body_version"] == "v2"
    assert result["prepared_slot"]["slot_id"] == "slot-B"
    assert result["execution_route_hint"]["interface_id"] == "body.candidate"
    assert slot.body_state == "candidate"
    assert slot.body_version == "v2"


@pytest.mark.unit
def test_body_lifecycle_adapter_exposes_body_registry_snapshot(tmp_path: Path):
    runtime = _make_body_lifecycle_runtime(tmp_path)

    result = runtime.adapter.get_body_registry()

    assert result["registry"]["active_slot"] == "slot-A"
    assert result["registry"]["shell_slot"] == "slot-B"
    assert result["slots"]["slot-A"]["body_state"] == "active"
    assert result["slots"]["slot-B"]["body_state"] == "shell"


@pytest.mark.unit
def test_body_lifecycle_adapter_exposes_active_target_and_slot_views(tmp_path: Path):
    runtime = _make_body_lifecycle_runtime(tmp_path)

    active_target = runtime.adapter.get_active_body_target()
    slots = runtime.adapter.list_body_slots()
    slot = runtime.adapter.get_body_slot("slot-A")

    assert active_target["slot_id"] == "slot-A"
    assert "slot-A" in slots["slots"]
    assert "slot-B" in slots["slots"]
    assert slot["slot_id"] == "slot-A"
    assert slot["body_state"] == "active"


@pytest.mark.unit
def test_body_lifecycle_adapter_rejects_unknown_slot_lookup(tmp_path: Path):
    runtime = _make_body_lifecycle_runtime(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        runtime.adapter.get_body_slot("slot-missing")

    assert exc_info.value.status_code == 400
    assert "Unknown slot_id 'slot-missing'" in str(exc_info.value.detail)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_lifecycle_adapter_prepares_slot_workspace_and_bootstraps_runtime(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_lifecycle_runtime(tmp_path)

    result = await runtime.adapter.prepare_body_slot("slot-B")

    slot = runtime.manager.load_slot_meta("slot-B")
    worktree_root = Path(slot.worktree_path)
    runtime_root = Path(slot.runtime_path)
    assert result["status"] == "slot_prepared"
    assert result["slot"]["slot_id"] == "slot-B"
    assert result["execution_route_hint"]["interface_id"] == "body.prepare"
    assert slot.materialized_from == "repo_root"
    assert (worktree_root / "run_agent.py").exists()
    assert (worktree_root / "config.yaml").exists()
    assert (runtime_root / "slot-runtime.json").exists()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_lifecycle_adapter_records_probe_report_and_persists_it(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_lifecycle_runtime(tmp_path)

    await runtime.adapter.mark_body_candidate(
        "slot-B",
        {
            "body_version": "v2",
            "source_commit": "aaa111",
            "candidate_commit": "bbb222",
            "rollback_commit": "aaa111",
            "changed_files": ["systems/probe.py"],
        },
    )
    runtime.manager.start_probe("slot-B")

    result = await runtime.adapter.record_body_probe_report(
        {
            "slot_id": "slot-B",
            "checks": [
                {"name": "startup_ok", "passed": True},
                {"name": "config_load_ok", "passed": True},
                {"name": "memory_path_ok", "passed": True},
                {"name": "tool_smoke_ok", "passed": True},
                {"name": "task_replay_ok", "passed": True},
            ],
            "summary": "Probe report recorded through execution adapter.",
        }
    )

    slot = runtime.manager.load_slot_meta("slot-B")
    assert result["status"] == "probe_report_recorded"
    assert result["result"]["status"] == "applied"
    assert result["report"]["overall_passed"] is True
    assert result["execution_route_hint"]["interface_id"] == "body.probe.report"
    assert slot.last_probe_result is not None
    assert slot.last_probe_result["overall_passed"] is True
    assert slot.last_probe_result["candidate_commit"] == "bbb222"
    assert slot.last_probe_result["changed_files"] == ["systems/probe.py"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_lifecycle_adapter_runs_probe_and_persists_report(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_lifecycle_runtime(tmp_path)

    await runtime.adapter.mark_body_candidate("slot-B", {"body_version": "v2"})
    runtime.manager.start_probe("slot-B")

    result = await runtime.adapter.run_body_probe({"slot_id": "slot-B"})

    slot = runtime.manager.load_slot_meta("slot-B")
    assert result["status"] == "probe_executed"
    assert result["report"]["overall_passed"] is True
    assert result["persistence"]["status"] == "applied"
    assert result["execution_route_hint"]["interface_id"] == "body.probe.run"
    assert slot.last_probe_result is not None
    assert slot.last_probe_result["overall_passed"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_execution_adapter_halts_when_probe_fails(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=False)
    runtime = _make_body_upgrade_runtime(tmp_path)

    result = await runtime.adapter.execute_body_upgrade({"body_version": "v2"})

    registry = runtime.manager.load_registry()
    slot_b = runtime.manager.load_slot_meta("slot-B")
    assert result["status"] == "upgrade_halted"
    assert result["stage"] == "probe_execution"
    assert result["probe_review"]["governor_response"]["decision"] == "approve"
    assert result["probe_execution"]["report"]["overall_passed"] is False
    assert result["execution_route_hint"]["interface_id"] == "body.upgrade.execute"
    assert registry.active_slot == "slot-A"
    assert slot_b.body_state == "probe"
    assert len(runtime.recorded_requests) == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_execution_adapter_persists_formal_git_lineage(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_upgrade_runtime(tmp_path)

    result = await runtime.adapter.execute_body_upgrade(
        {
            "body_version": "v2",
            "execution_request": {
                "git_lineage": {
                    "source_branch": "main",
                    "source_commit": "aaa111",
                    "candidate_branch": "evolution/task-1",
                    "candidate_commit": "bbb222",
                    "active_ref": "stable/v2",
                    "rollback_ref": "body/slot-A",
                    "rollback_commit": "aaa111",
                    "diff_summary": "Formal lineage handoff.",
                    "changed_files": ["systems/execution/adapters.py"],
                }
            },
        }
    )

    registry = runtime.manager.load_registry()
    slot_b = runtime.manager.load_slot_meta("slot-B")
    pointer = runtime.manager.load_active_body_pointer()

    assert result["status"] == "upgrade_executed"
    assert result["execution_route_hint"]["interface_id"] == "body.upgrade.execute"
    assert slot_b.source_commit == "aaa111"
    assert slot_b.source_branch == "main"
    assert slot_b.candidate_commit == "bbb222"
    assert slot_b.candidate_branch == "evolution/task-1"
    assert slot_b.active_ref == "stable/v2"
    assert slot_b.active_commit == "bbb222"
    assert slot_b.rollback_commit == "aaa111"
    assert slot_b.last_probe_result["candidate_commit"] == "bbb222"
    assert slot_b.last_probe_result["changed_files"] == ["systems/execution/adapters.py"]
    assert registry.last_switch_result["active_ref"] == "stable/v2"
    assert registry.last_switch_result["active_commit"] == "bbb222"
    assert pointer.slot_id == "slot-B"
    assert pointer.active_ref == "stable/v2"
    assert pointer.active_commit == "bbb222"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_execution_adapter_propagates_trace_and_decision_ids_into_governor_requests(
    tmp_path: Path,
):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_upgrade_runtime(tmp_path)

    result = await runtime.adapter.execute_body_upgrade(
        {
            "body_version": "v2",
            "execution_request": {
                "trace_id": "trace-formal-1",
                "task_type": "self_evolution",
                "decision_id": "decision-formal-1",
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["systems/execution/adapters.py"],
                },
            },
        }
    )

    assert result["status"] == "upgrade_executed"
    assert len(runtime.recorded_requests) == 2
    first_request = runtime.recorded_requests[0]
    second_request = runtime.recorded_requests[1]
    assert first_request.trace_id == "trace-formal-1"
    assert first_request.task_type == "self_evolution"
    assert first_request.evidence["runtime_task_profile"] == {
        "task_type": "self_evolution",
        "governance_task_type": "self_evolution",
        "task_family": "general_self_evolution",
        "execution_kind": "general_self_evolution",
    }
    assert first_request.decision_id == "decision-formal-1"
    assert second_request.trace_id == "trace-formal-1"
    assert second_request.task_type == "self_evolution"
    assert second_request.evidence["runtime_task_profile"] == {
        "task_type": "self_evolution",
        "governance_task_type": "self_evolution",
        "task_family": "general_self_evolution",
        "execution_kind": "general_self_evolution",
    }
    assert second_request.decision_id == "decision-formal-1"
    assert result["switch_review"]["execution_report"]["runtime_task_profile"] == {
        "task_type": "self_evolution",
        "governance_task_type": "self_evolution",
        "task_family": "general_self_evolution",
        "execution_kind": "general_self_evolution",
    }
    assert runtime.manager.load_registry().last_switch_result["task_family"] == "general_self_evolution"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_then_watch_window_pass_recycles_retired_slot_end_to_end(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_upgrade_runtime(tmp_path)
    old_agent = AgentInstance(
        instance_id="old-active",
        name="agent-slot-A-old",
        pid=1701,
        port=9701,
        status="running",
        healthy=True,
        slot_id="slot-A",
    )
    new_agent = AgentInstance(
        instance_id="new-active",
        name="agent-slot-B-new",
        pid=1702,
        port=9702,
        status="running",
        healthy=True,
        slot_id="slot-B",
    )
    agents = {"old-active": old_agent, "new-active": new_agent}
    stopped_instances: list[str] = []

    async def stop_agent(instance_id: str) -> dict:
        stopped_instances.append(instance_id)
        agent = agents[instance_id]
        agent.status = "stopped"
        agent.pid = None
        agent.healthy = False
        return {"status": "stopped", "instance_id": instance_id}

    upgrade = await runtime.adapter.execute_body_upgrade(
        {
            "body_version": "v2",
            "execution_request": {
                "trace_id": "trace-phase1-pass",
                "decision_id": "decision-phase1-pass",
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["systems/execution/adapters.py"],
                },
            },
        }
    )
    watch = WatchWindowExecutionAdapter(
        body_registry=runtime.manager,
        agents=agents,
        stop_agent=stop_agent,
        run_health_checks=AsyncMock(return_value={"results": []}),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=runtime.governor_request_executor,
    )

    result = await watch.evaluate_watch_window({"healthy_override": True})

    registry = runtime.manager.load_registry()
    slot_a = runtime.manager.load_slot_meta("slot-A")
    slot_b = runtime.manager.load_slot_meta("slot-B")
    pointer = runtime.manager.load_active_body_pointer()
    assert upgrade["status"] == "upgrade_executed"
    assert upgrade["previous_active_slot"] == "slot-A"
    assert upgrade["retired_slot"] == "slot-A"
    assert result["status"] == "watch_window_evaluated"
    assert result["governor_response"]["decision"] == "approve"
    assert result["execution_report"]["action_results"][0]["action_type"] == "recycle_retired_slot"
    assert result["execution_report"]["action_results"][0]["status"] == "applied"
    assert result["execution_followup"] == {
        "action": "retired_slot_recycled",
        "slot_id": "slot-A",
        "stopped_instance_ids": ["old-active"],
    }
    assert registry.active_slot == "slot-B"
    assert registry.shell_slot == "slot-A"
    assert registry.retired_slot is None
    assert registry.watch_window.status == "completed"
    assert slot_a.body_state == "shell"
    assert slot_b.body_state == "active"
    assert pointer.slot_id == "slot-B"
    assert stopped_instances == ["old-active"]
    assert old_agent.status == "stopped"
    assert new_agent.status == "running"
    assert len(runtime.recorded_requests) == 3
    assert [request.event_type for request in runtime.recorded_requests] == [
        "health_review_request",
        "switch_request",
        "post_switch_review",
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_upgrade_then_watch_window_failure_rolls_back_to_retired_slot_end_to_end(tmp_path: Path):
    _seed_body_repo(tmp_path, probe_ready=True)
    runtime = _make_body_upgrade_runtime(tmp_path)
    restored_agent = AgentInstance(
        instance_id="restored-old",
        name="agent-slot-A-old",
        pid=1801,
        port=9801,
        status="running",
        healthy=True,
        slot_id="slot-A",
    )
    failed_agent = AgentInstance(
        instance_id="failed-new",
        name="agent-slot-B-new",
        pid=1802,
        port=9802,
        status="running",
        healthy=False,
        slot_id="slot-B",
    )
    agents = {"restored-old": restored_agent, "failed-new": failed_agent}
    stopped_instances: list[str] = []

    async def stop_agent(instance_id: str) -> dict:
        stopped_instances.append(instance_id)
        agent = agents[instance_id]
        agent.status = "stopped"
        agent.pid = None
        agent.healthy = False
        return {"status": "stopped", "instance_id": instance_id}

    upgrade = await runtime.adapter.execute_body_upgrade(
        {
            "body_version": "v2",
            "execution_request": {
                "trace_id": "trace-phase1-rollback",
                "decision_id": "decision-phase1-rollback",
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["systems/execution/adapters.py"],
                },
            },
        }
    )
    watch = WatchWindowExecutionAdapter(
        body_registry=runtime.manager,
        agents=agents,
        stop_agent=stop_agent,
        run_health_checks=AsyncMock(return_value={"results": []}),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=runtime.governor_request_executor,
    )

    result = await watch.evaluate_watch_window({"healthy_override": False})

    registry = runtime.manager.load_registry()
    slot_a = runtime.manager.load_slot_meta("slot-A")
    slot_b = runtime.manager.load_slot_meta("slot-B")
    pointer = runtime.manager.load_active_body_pointer()
    assert upgrade["status"] == "upgrade_executed"
    assert result["status"] == "watch_window_evaluated"
    assert result["governor_response"]["decision"] == "rollback_required"
    assert result["execution_report"]["action_results"][0]["action_type"] == "restore_retired_slot"
    assert result["execution_report"]["action_results"][0]["status"] == "applied"
    assert result["execution_followup"] == {
        "action": "failed_slot_drained",
        "slot_id": "slot-B",
        "restored_slot_id": "slot-A",
        "restored_instance_id": "restored-old",
        "stopped_instance_ids": ["failed-new"],
    }
    assert registry.active_slot == "slot-A"
    assert registry.retired_slot == "slot-B"
    assert registry.watch_window.status == "active"
    assert slot_a.body_state == "active"
    assert slot_b.body_state == "retired"
    assert pointer.slot_id == "slot-A"
    assert stopped_instances == ["failed-new"]
    assert failed_agent.status == "stopped"
    assert restored_agent.status == "running"
    assert len(runtime.recorded_requests) == 3
    assert [request.event_type for request in runtime.recorded_requests] == [
        "health_review_request",
        "switch_request",
        "rollback_request",
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_stops_retired_slot_agents():
    old_agent = AgentInstance(
        instance_id="old-active",
        name="agent-slot-A-old",
        pid=1001,
        port=9001,
        status="running",
        healthy=True,
        slot_id="slot-A",
    )
    new_agent = AgentInstance(
        instance_id="new-active",
        name="agent-slot-B-new",
        pid=1002,
        port=9002,
        status="running",
        healthy=True,
        slot_id="slot-B",
    )
    stop_agent = AsyncMock(side_effect=lambda instance_id: {"status": "stopped", "instance_id": instance_id})
    body_registry = SimpleNamespace(load_registry=Mock(return_value=SimpleNamespace()))
    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"old-active": old_agent, "new-active": new_agent},
        stop_agent=stop_agent,
        run_health_checks=AsyncMock(),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=_make_governor_request_executor(),
    )

    result = await adapter.reconcile_watch_window_outcome(
        result={"governor_response": {"decision": "approve"}},
        previous_retired_slot="slot-A",
    )

    assert result == {
        "action": "retired_slot_recycled",
        "slot_id": "slot-A",
        "stopped_instance_ids": ["old-active"],
    }
    stop_agent.assert_awaited_once_with("old-active")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_stops_failed_slot_agents_after_rollback():
    restored_agent = AgentInstance(
        instance_id="restored-old",
        name="agent-slot-A-old",
        pid=1101,
        port=9101,
        status="running",
        healthy=True,
        slot_id="slot-A",
    )
    failed_agent = AgentInstance(
        instance_id="failed-new",
        name="agent-slot-B-new",
        pid=1102,
        port=9102,
        status="running",
        healthy=False,
        slot_id="slot-B",
    )
    stop_agent = AsyncMock(side_effect=lambda instance_id: {"status": "stopped", "instance_id": instance_id})
    body_registry = SimpleNamespace(
        load_registry=Mock(return_value=SimpleNamespace(active_slot="slot-A"))
    )
    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"restored-old": restored_agent, "failed-new": failed_agent},
        stop_agent=stop_agent,
        run_health_checks=AsyncMock(),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=_make_governor_request_executor(),
    )

    result = await adapter.reconcile_watch_window_outcome(
        result={
            "governor_response": {"decision": "rollback_required"},
            "request": {"body_id": "slot-B"},
        },
        previous_retired_slot="slot-A",
    )

    assert result == {
        "action": "failed_slot_drained",
        "slot_id": "slot-B",
        "restored_slot_id": "slot-A",
        "restored_instance_id": "restored-old",
        "stopped_instance_ids": ["failed-new"],
    }
    stop_agent.assert_awaited_once_with("failed-new")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_polls_for_expired_window(tmp_path: Path):
    watch_window = SimpleNamespace(
        status="active",
        expires_at=datetime.utcnow(),
        model_dump=lambda mode="json": {"status": "active"},
    )
    registry = SimpleNamespace(
        active_slot="slot-B",
        retired_slot="slot-A",
        watch_window=watch_window,
    )
    body_registry = SimpleNamespace(load_registry=Mock(return_value=registry))
    agent = AgentInstance(
        instance_id="new-active",
        name="agent-slot-B-new",
        pid=1202,
        port=9202,
        status="running",
        healthy=True,
        slot_id="slot-B",
    )
    run_health_checks = AsyncMock(return_value={"results": []})
    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"new-active": agent},
        stop_agent=AsyncMock(),
        run_health_checks=run_health_checks,
        runtime_state=_make_watch_window_state(),
        governor_request_executor=_make_governor_request_executor(),
    )

    result = await adapter.poll_watch_window()

    assert result == {
        "should_evaluate": True,
        "request": {
            "healthy_override": True,
            "metrics": {"reason": "automatic_watch_window_expired_cleanly"},
        },
    }
    run_health_checks.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_builds_runtime_status_snapshot():
    watch_window = SimpleNamespace(
        status="active",
        expires_at=None,
        model_dump=lambda mode="json": {"status": "active"},
    )
    registry = SimpleNamespace(
        active_slot="slot-B",
        retired_slot="slot-A",
        watch_window=watch_window,
    )
    body_registry = SimpleNamespace(load_registry=Mock(return_value=registry))
    agent = AgentInstance(
        instance_id="new-active",
        name="agent-slot-B-new",
        pid=1302,
        port=9302,
        status="running",
        healthy=True,
        slot_id="slot-B",
    )
    runtime_state = _make_watch_window_state(last_outcome={"status": "watch_window_evaluated"})
    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"new-active": agent},
        stop_agent=AsyncMock(),
        run_health_checks=AsyncMock(),
        runtime_state=runtime_state,
        governor_request_executor=_make_governor_request_executor(),
    )

    status = adapter.get_watch_window_status()
    evidence = adapter.build_watch_window_evidence(metrics={"reason": "manual-check"})

    assert status["watch_window"]["status"] == "active"
    assert status["task_running"] is False
    assert evidence["healthy"] is True
    assert evidence["observation"]["active_slot"] == "slot-B"
    assert evidence["observation"]["metrics"]["reason"] == "manual-check"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_evaluates_and_records_outcome():
    watch_window = SimpleNamespace(
        status="active",
        expires_at=None,
        model_dump=lambda mode="json": {"status": "active"},
    )
    registry = SimpleNamespace(
        active_slot="slot-B",
        retired_slot="slot-A",
        watch_window=watch_window,
    )
    body_registry = SimpleNamespace(load_registry=Mock(return_value=registry))
    agent = AgentInstance(
        instance_id="new-active",
        name="agent-slot-B-new",
        pid=1402,
        port=9402,
        status="running",
        healthy=True,
        slot_id="slot-B",
    )
    governor_request_executor = _make_governor_request_executor(
        {
            "request": {"body_id": "slot-A"},
            "governor_response": {"decision": "approve"},
            "registry": {"active_slot": "slot-B", "retired_slot": None},
        }
    )
    runtime_state = _make_watch_window_state()
    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"new-active": agent},
        stop_agent=AsyncMock(return_value={"status": "stopped"}),
        run_health_checks=AsyncMock(),
        runtime_state=runtime_state,
        governor_request_executor=governor_request_executor,
    )

    result = await adapter.evaluate_watch_window({"healthy_override": True})

    assert result["status"] == "watch_window_evaluated"
    assert result["governor_response"]["decision"] == "approve"
    assert result["execution_followup"]["action"] == "retired_slot_recycled"
    governor_request_executor.execute_governor_request.assert_called_once()
    assert adapter._state.last_outcome is result  # S-02/03: state self-owned


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_evaluate_success_reuses_reconcile_cleanup():
    watch_window = SimpleNamespace(
        status="active",
        expires_at=None,
        model_dump=lambda mode="json": {"status": "active"},
    )
    registry = SimpleNamespace(
        active_slot="slot-B",
        retired_slot="slot-A",
        watch_window=watch_window,
    )
    body_registry = SimpleNamespace(load_registry=Mock(return_value=registry))
    old_agent = AgentInstance(
        instance_id="old-active",
        name="agent-slot-A-old",
        pid=1501,
        port=9501,
        status="running",
        healthy=True,
        slot_id="slot-A",
    )
    new_agent = AgentInstance(
        instance_id="new-active",
        name="agent-slot-B-new",
        pid=1502,
        port=9502,
        status="running",
        healthy=True,
        slot_id="slot-B",
    )
    stopped_instances: list[str] = []

    async def stop_agent(instance_id: str) -> dict:
        stopped_instances.append(instance_id)
        agent = {"old-active": old_agent, "new-active": new_agent}[instance_id]
        agent.status = "stopped"
        agent.pid = None
        agent.healthy = False
        return {"status": "stopped", "instance_id": instance_id}

    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"old-active": old_agent, "new-active": new_agent},
        stop_agent=stop_agent,
        run_health_checks=AsyncMock(),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=_make_governor_request_executor(
            {
                "request": {"body_id": "slot-A"},
                "governor_response": {"decision": "approve"},
                "registry": {"active_slot": "slot-B", "retired_slot": None},
            }
        ),
    )

    result = await adapter.evaluate_watch_window({"healthy_override": True})

    assert result["execution_followup"] == {
        "action": "retired_slot_recycled",
        "slot_id": "slot-A",
        "stopped_instance_ids": ["old-active"],
    }
    assert stopped_instances == ["old-active"]
    assert old_agent.status == "stopped"
    assert new_agent.status == "running"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_evaluate_rollback_reuses_reconcile_cleanup():
    watch_window = SimpleNamespace(
        status="active",
        expires_at=None,
        model_dump=lambda mode="json": {"status": "active"},
    )
    registry = SimpleNamespace(
        active_slot="slot-B",
        retired_slot="slot-A",
        watch_window=watch_window,
    )
    body_registry = SimpleNamespace(load_registry=Mock(return_value=registry))
    restored_agent = AgentInstance(
        instance_id="restored-old",
        name="agent-slot-A-old",
        pid=1601,
        port=9601,
        status="running",
        healthy=True,
        slot_id="slot-A",
    )
    failed_agent = AgentInstance(
        instance_id="failed-new",
        name="agent-slot-B-new",
        pid=1602,
        port=9602,
        status="running",
        healthy=False,
        slot_id="slot-B",
    )
    stopped_instances: list[str] = []

    async def stop_agent(instance_id: str) -> dict:
        stopped_instances.append(instance_id)
        agent = {"restored-old": restored_agent, "failed-new": failed_agent}[instance_id]
        agent.status = "stopped"
        agent.pid = None
        agent.healthy = False
        return {"status": "stopped", "instance_id": instance_id}

    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={"restored-old": restored_agent, "failed-new": failed_agent},
        stop_agent=stop_agent,
        run_health_checks=AsyncMock(),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=_make_governor_request_executor(
            {
                "request": {"body_id": "slot-B"},
                "governor_response": {"decision": "rollback_required"},
                "registry": {"active_slot": "slot-A", "retired_slot": "slot-B"},
            }
        ),
    )

    result = await adapter.evaluate_watch_window({"healthy_override": False})

    assert result["execution_followup"] == {
        "action": "failed_slot_drained",
        "slot_id": "slot-B",
        "restored_slot_id": "slot-A",
        "restored_instance_id": "restored-old",
        "stopped_instance_ids": ["failed-new"],
    }
    assert stopped_instances == ["failed-new"]
    assert failed_agent.status == "stopped"
    assert restored_agent.status == "running"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_execution_adapter_owns_task_lifecycle():
    watch_window = SimpleNamespace(
        status="active",
        expires_at=None,
        model_dump=lambda mode="json": {"status": "active"},
    )
    registry = SimpleNamespace(
        active_slot="slot-B",
        retired_slot="slot-A",
        watch_window=watch_window,
    )
    body_registry = SimpleNamespace(load_registry=Mock(return_value=registry))
    runtime_state = _make_watch_window_state()

    adapter = WatchWindowExecutionAdapter(
        body_registry=body_registry,
        agents={},
        stop_agent=AsyncMock(),
        run_health_checks=AsyncMock(),
        runtime_state=runtime_state,
        governor_request_executor=_make_governor_request_executor(),
        poll_interval_seconds=0.01,
    )

    task = adapter.ensure_watch_window_task()
    assert task is not None
    # State now owned by adapter (S-02/03)
    assert adapter._state.task is not None
    assert adapter.ensure_watch_window_task() is task

    await asyncio.sleep(0.03)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime_state.task is None


@pytest.mark.unit
def test_watch_window_execution_adapter_syncs_runtime_after_watch_approval():
    existing_task = Mock()
    existing_task.done.return_value = False
    runtime_state = _make_watch_window_state(task=existing_task)

    adapter = WatchWindowExecutionAdapter(
        body_registry=SimpleNamespace(load_registry=Mock()),
        agents={},
        stop_agent=AsyncMock(),
        run_health_checks=AsyncMock(),
        governor_request_executor=_make_governor_request_executor(),
    )
    # S-02/03: populate adapter's self-owned state
    adapter._state.task = existing_task

    result = adapter.sync_runtime_after_governor_response(SimpleNamespace(decision="approve_with_watch"))

    assert result["status"] == "watch_window_runtime_ensured"
    assert result["decision"] == "approve_with_watch"
    assert result["task_running"] is True
    assert result["task_created"] is False
    assert adapter._state.task is existing_task


@pytest.mark.unit
def test_watch_window_execution_adapter_ignores_non_watch_governor_decisions():
    adapter = WatchWindowExecutionAdapter(
        body_registry=SimpleNamespace(load_registry=Mock()),
        agents={},
        stop_agent=AsyncMock(),
        run_health_checks=AsyncMock(),
        runtime_state=_make_watch_window_state(),
        governor_request_executor=_make_governor_request_executor(),
    )

    result = adapter.sync_runtime_after_governor_response(SimpleNamespace(decision="approve"))

    assert result == {
        "status": "no_watch_window_runtime_change",
        "decision": "approve",
    }
