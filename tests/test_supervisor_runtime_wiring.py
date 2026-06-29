from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.execution import build_execution_route_hint
from systems.supervisor.supervisor import (
    Supervisor,
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from systems.self_learning import LearningRecommendation, SelfLearningService


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
    )


def _make_supervisor(tmp_path: Path) -> Supervisor:
    return Supervisor(_make_supervisor_config(tmp_path))


async def _trigger_memory_compression(supervisor: Supervisor, request: dict | None = None):
    return await supervisor._execution_facade.trigger_memory_compression(request)


async def _execute_body_upgrade(supervisor: Supervisor, request: dict | None = None):
    return await supervisor._execution_facade.execute_body_upgrade(request)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_health_exposes_runtime_state_without_deprecated_runtime_catalog(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    health = await supervisor.health_check()

    assert health["status"] == "healthy"
    assert health["service"] == "supervisor"
    assert health["body_runtime"]["active_slot"] == "slot-A"
    assert "transitional_interfaces" not in health


@pytest.mark.unit
def test_supervisor_wires_execution_facade_to_canonical_executors(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    assert supervisor._execution_facade.body_lifecycle is supervisor._body_lifecycle_executor
    assert supervisor._execution_facade.body_upgrade is supervisor._body_upgrade_executor
    assert supervisor._execution_facade.memory_maintenance is supervisor._memory_maintenance_executor


@pytest.mark.unit
def test_supervisor_exposes_segmented_runtime_config_views_and_uses_them_for_execution_wiring(tmp_path):
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(
            git_repo_path=str(tmp_path),
            gateway_address="http://gateway.segmented.local",
            memory_gateway_path="/memory-api/",
            agent_base_port=9100,
            probe_watch_window_seconds=180,
        ),
        service_runtime=SupervisorServiceRuntimeConfig(
            health_check_interval=45,
            memory_compression_interval=7200,
            self_evolution_review_interval=900,
            endogenous_drive_enabled=True,
            endogenous_drive_interval=600,
            endogenous_drive_max_candidates=2,
        ),
        body_runtime=SupervisorBodyRuntimeConfig(
            slots_dir_name=".slots-segmented",
            registry_file_name=".registry-segmented.json",
            slot_a_name="slot-blue",
            slot_b_name="slot-green",
        ),
    )
    supervisor = Supervisor(config)

    assert config.execution.gateway_address == "http://gateway.segmented.local"
    assert config.execution.memory_gateway_path == "/memory-api/"
    assert config.execution.agent_base_port == 9100
    assert config.execution.probe_watch_window_seconds == 180
    assert config.service_runtime.health_check_interval == 45
    assert config.service_runtime.memory_compression_interval == 7200
    assert config.service_runtime.self_evolution_review_interval == 900
    assert config.service_runtime.endogenous_drive_enabled is True
    assert config.service_runtime.endogenous_drive_interval == 600
    assert config.service_runtime.endogenous_drive_max_candidates == 2
    assert config.ui_enabled is True
    assert config.ui_auto_open is True
    assert config.ui_event_interval_seconds == 3.0
    assert config.ui_activity_buffer_size == 100
    assert config.body_runtime.slots_dir_name == ".slots-segmented"
    assert config.body_runtime.registry_file_name == ".registry-segmented.json"
    assert config.body_runtime.slot_a_name == "slot-blue"
    assert config.body_runtime.slot_b_name == "slot-green"
    assert supervisor._body_upgrade_executor.config.probe_watch_window_seconds == 180
    assert supervisor._memory_maintenance_executor.config.memory_gateway_path == "/memory-api/"
    registry = supervisor._body_registry.load_registry()
    assert registry.active_slot == "slot-blue"
    assert registry.shell_slot == "slot-green"


@pytest.mark.unit
def test_supervisor_routes_no_longer_publish_deprecated_execution_surface(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    deprecated_routes = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in supervisor.app.routes
        if getattr(route, "deprecated", False)
    }
    route_paths = {route.path for route in supervisor.app.routes}

    assert deprecated_routes == set()
    assert "/upgrade/history" not in route_paths
    assert "/upgrade/legacy" not in route_paths


@pytest.mark.unit
def test_supervisor_mounts_built_in_room_ui_when_enabled(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    route_paths = {route.path for route in supervisor.app.routes}

    assert "/ui" in route_paths
    assert "/ui/state" in route_paths
    assert "/ui/events" in route_paths
    assert "/runtime/timeline" in route_paths
    assert "/runtime/traces" in route_paths
    assert "/runtime/traces/{trace_id}" in route_paths

    with TestClient(supervisor.app) as client:
        page = client.get("/ui")
        state = client.get("/ui/state")

    assert page.status_code == 200
    assert "VoidCube Supervisor Room" in page.text
    assert 'EventSource("/ui/events")' in page.text
    assert state.status_code == 200
    payload = state.json()
    assert payload["status"] == "ok"
    assert payload["scene"] in {"idle", "drive", "maintenance", "learning", "body_switch", "execution"}
    assert "timeline" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_view_aggregates_queue_activity_governance_and_gateway(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Trace self-learning evidence path",
            "task_family": "self_learning",
            "source": "self_learning",
            "metadata": {
                "trace_id": "trace-runtime-1",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    trace_id = planned["tasks"][0]["trace_id"]
    decided = await supervisor.decide_self_evolution_task(
        task_id,
        {
            "decision": "approve",
            "reason": "Trace test approval.",
            "decision_id": "decision-runtime-1",
        },
    )
    supervisor._record_supervisor_ui_activity(
        "trace_marker",
        scene="learning",
        summary="Trace marker from supervisor activity.",
        metadata={
            "trace_id": trace_id,
            "task_id": task_id,
            "governance_task_type": "self_learning",
            "task_family": "self_learning",
            "decision_id": "decision-runtime-1",
        },
    )

    async def fake_gateway_activity_log(trace_id=None, limit=200):
        assert trace_id == planned["tasks"][0]["trace_id"]
        assert limit == 200
        return {
            "status": "ok",
            "events": [
                {
                    "activity_id": "gateway-activity-1",
                    "activity_kind": "self_learning",
                    "recorded_at": "2026-05-25T12:05:00",
                    "source_service": "self-learning",
                    "session_id": None,
                    "metadata": {
                        "trace_id": trace_id,
                        "task_id": task_id,
                        "governance_task_type": "self_learning",
                        "task_family": "self_learning",
                        "decision_id": "decision-runtime-1",
                    },
                }
            ],
        }

    supervisor._fetch_gateway_activity_log = fake_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.get_runtime_trace(trace_id)

    assert decided["status"] == "approved"
    assert result["status"] == "ok"
    assert result["found"] is True
    assert result["summary"]["trace_id"] == trace_id
    assert result["summary"]["task_ids"] == [task_id]
    assert result["summary"]["decision_ids"] == ["decision-runtime-1"]
    assert result["summary"]["governance_task_types"] == ["self_learning"]
    assert result["summary"]["task_families"] == ["self_learning"]
    assert result["sources"]["self_evolution_queue"] >= 2
    assert result["sources"]["supervisor_activity"] >= 1
    assert result["sources"]["mem_governor_history"] >= 1
    assert result["sources"]["gateway_activity_log"] == 1
    event_sources = {event["source"] for event in result["timeline"]}
    assert {
        "self_evolution_queue",
        "supervisor_activity",
        "mem_governor_history",
        "gateway_activity_log",
    }.issubset(event_sources)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_list_summarizes_known_traces_without_gateway_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    first = await supervisor.plan_self_evolution_task({"title": "First trace"})
    second = await supervisor.plan_self_evolution_task({"title": "Second trace"})

    async def unavailable_gateway_activity_log(trace_id=None, limit=200):
        raise RuntimeError("gateway unavailable")

    supervisor._fetch_gateway_activity_log = unavailable_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.list_runtime_traces(limit=10)
    trace_ids = {trace["trace_id"] for trace in result["traces"]}

    assert result["status"] == "ok"
    assert first["tasks"][0]["trace_id"] in trace_ids
    assert second["tasks"][0]["trace_id"] in trace_ids
    assert result["sources"]["self_evolution_queue"] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_timeline_exposes_recent_unified_trace_records(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Timeline-backed UI observation",
            "trace_id": "trace-timeline-1",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    trace_id = planned["tasks"][0]["trace_id"]

    async def fake_gateway_activity_log(trace_id=None, limit=200):
        assert trace_id is None
        return {
            "status": "ok",
            "events": [
                {
                    "activity_id": "gateway-timeline-1",
                    "activity_kind": "self_learning",
                    "recorded_at": "2026-05-25T12:05:00",
                    "metadata": {
                        "trace_id": trace_id or "trace-timeline-1",
                        "task_id": task_id,
                        "governance_task_type": "self_learning",
                        "task_family": "self_learning",
                    },
                }
            ],
        }

    supervisor._fetch_gateway_activity_log = fake_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.get_runtime_timeline(limit=10)

    assert result["status"] == "ok"
    assert result["count"] >= 3
    sources = {event["source"] for event in result["timeline"]}
    assert {
        "self_evolution_queue",
        "supervisor_activity",
        "mem_governor_history",
        "gateway_activity_log",
    }.issubset(sources)
    assert {event["trace_id"] for event in result["timeline"]} == {trace_id}
    assert {event["task_id"] for event in result["timeline"] if event.get("task_id")} == {task_id}


@pytest.mark.unit
def test_supervisor_can_disable_built_in_room_ui(tmp_path):
    config = _make_supervisor_config(tmp_path).model_copy(update={"ui_enabled": False})
    supervisor = Supervisor(config)
    route_paths = {route.path for route in supervisor.app.routes}

    assert "/ui" not in route_paths
    assert "/ui/state" not in route_paths
    assert "/ui/events" not in route_paths


@pytest.mark.unit
def test_supervisor_room_ui_event_frame_uses_sse_state_event(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    frame = supervisor._format_supervisor_ui_event(
        "state",
        {
            "status": "ok",
            "scene": "planning",
            "title": "Xizi is thinking",
        }
    )

    assert frame.startswith("event: state\n")
    assert '"status":"ok"' in frame
    assert '"scene":"planning"' in frame
    assert frame.endswith("\n\n")


@pytest.mark.unit
def test_supervisor_room_ui_records_bounded_activity_timeline(tmp_path):
    config = _make_supervisor_config(tmp_path).model_copy(
        update={"ui_activity_buffer_size": 2}
    )
    supervisor = Supervisor(config)

    supervisor._record_supervisor_ui_activity("first", summary="First event")
    supervisor._record_supervisor_ui_activity("second", summary="Second event")
    supervisor._record_supervisor_ui_activity("third", summary="Third event")

    timeline = supervisor._recent_supervisor_ui_activity(limit=10)
    assert [event["event_type"] for event in timeline] == ["third", "second"]
    assert timeline[0]["summary"] == "Third event"
    persisted = supervisor._supervisor_ui_activity_path.read_text(encoding="utf-8")
    assert "third" in persisted
    assert "first" not in persisted


@pytest.mark.unit
def test_supervisor_room_ui_restores_activity_timeline_from_runtime_store(tmp_path):
    config = _make_supervisor_config(tmp_path).model_copy(
        update={"ui_activity_buffer_size": 3}
    )
    first = Supervisor(config)
    first._record_supervisor_ui_activity("remembered", summary="Persisted event")

    second = Supervisor(config)
    timeline = second._recent_supervisor_ui_activity(limit=10)

    assert timeline[0]["event_type"] == "remembered"
    assert timeline[0]["summary"] == "Persisted event"
    assert second._supervisor_ui_activity_path == first._supervisor_ui_activity_path


@pytest.mark.unit
def test_supervisor_room_ui_activity_is_mirrored_to_governance_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    event = supervisor._record_supervisor_ui_activity(
        "task_decided",
        scene="execution",
        summary="Decision mirrored to governance history",
        metadata={
            "trace_id": "trace-ui-1",
            "task_id": "task-ui-1",
            "task_type": "self_learning_followup",
            "governance_task_type": "self_learning",
            "task_family": "self_learning",
            "decision_id": "decision-ui-1",
        },
    )

    history = supervisor._governor.list_history(limit=5)
    record = history[-1]
    assert record["kind"] == "supervisor_activity"
    assert record["request"]["event_id"] == event["event_id"]
    assert record["request"]["event_type"] == "task_decided"
    assert record["request"]["trace_id"] == "trace-ui-1"
    assert record["request"]["governance_task_type"] == "self_learning"
    assert record["evolution_lineage"]["decision_id"] == "decision-ui-1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_read_does_not_mirror_observation_to_governance_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    before = len(supervisor._governor.list_history(limit=100))

    state = await supervisor.get_supervisor_ui_state()

    after = supervisor._governor.list_history(limit=100)
    assert state["status"] == "ok"
    assert len(after) == before


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_maps_memory_task_to_memory_scene(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    await supervisor.plan_self_evolution_task(
        {
            "title": "Run memory continuity sweep",
            "execution_kind": "memory_maintenance",
        }
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "maintenance"
    assert state["tasks"][0]["title"] == "Run memory continuity sweep"
    assert "整理记忆" in state["title"]
    assert "tasks_planned" in [event["event_type"] for event in state["timeline"]]
    assert "supervisor_activity" in [event["source"] for event in state["timeline"]]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_read_does_not_create_timeline_events(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.get_runtime_timeline = AsyncMock(return_value={"timeline": []})  # type: ignore[method-assign]
    supervisor.evaluate_idle_window = AsyncMock(
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": True},
                "general_self_evolution": {"eligible_for_planning": True},
            },
            "governance_task_type_decisions": {
                "memory_maintenance": {"eligible_for_planning": True},
                "self_learning": {"eligible_for_planning": True},
                "self_evolution": {"eligible_for_planning": True},
            },
        }
    )  # type: ignore[method-assign]

    state = await supervisor.get_supervisor_ui_state()

    assert state["drive_candidates"]
    assert state["timeline"] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_governance_preview_for_shadow_review(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_self_evolution_task(
        {
            "items": [
                {"title": "Duplicate learning branch"},
                {"title": "Canonical learning branch"},
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
        return {
            tasks_by_title["Duplicate learning branch"]: {
                "action": "merge",
                "reason": "Duplicate branch should merge into the canonical one.",
                "shadow": {
                    "action": "merge",
                    "reason": "Duplicate branch should merge into the canonical one.",
                    "merge_into": tasks_by_title["Canonical learning branch"],
                },
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_queue", fake_lm_review)

    await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    duplicate = next(task for task in state["tasks"] if task["title"] == "Duplicate learning branch")
    preview = duplicate["governance_preview"]["lm_queue_shadow"]
    assert preview["action"] == "merge"
    assert preview["merge_into"] == tasks_by_title["Canonical learning branch"]
    assert state["metrics"]["governance"]["shadow_recommendations"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_applied_priority_updates(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

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

    await supervisor.review_self_evolution_tasks(
        {
            "idle_window": {"now": "2026-05-25T01:00:00"},
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    task = next(item for item in state["tasks"] if item["task_id"] == task_id)
    assert task["priority"] == "high"
    assert task["governance_preview"]["lm_queue_priority"]["priority"] == "high"
    assert state["metrics"]["governance"]["priority_updates"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_task_identity_for_body_improvement(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Improve shell body from learning",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
                "execution_request": {
                    "kind": "body_improvement",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    state = await supervisor.get_supervisor_ui_state()
    task = next(item for item in state["tasks"] if item["task_id"] == task_id)

    assert task["task_identity"]["task_family"] == "body_upgrade"
    assert task["task_identity"]["execution_kind"] == "body_improvement"
    assert task["task_identity"]["requested_kind"] == "body_improvement"
    assert task["task_identity"]["display_kind"] == "body_improvement"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_uses_single_slot_governance_layout(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    supervisor.evaluate_idle_window = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {"in_execution_window": True},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": True},
                "body_upgrade": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
                "memory_maintenance": {"eligible_for_planning": True, "eligible_for_execution": True},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
        }
    )

    supervisor_task_1 = await supervisor.plan_self_evolution_task(
        {
            "title": "Supervisor first task",
            "task_family": "memory_maintenance",
            "metadata": {"task_family": "memory_maintenance"},
        }
    )
    supervisor_task_2 = await supervisor.plan_self_evolution_task(
        {
            "title": "Supervisor second task",
            "task_family": "general_self_evolution",
            "metadata": {"task_family": "general_self_evolution"},
        }
    )
    agent_task_1 = await supervisor.plan_self_evolution_task(
        {
            "title": "Agent first creative task",
            "task_type": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    agent_task_2 = await supervisor.plan_self_evolution_task(
        {
            "title": "Agent second creative task",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
            "metadata": {
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
            },
        }
    )

    await supervisor.decide_self_evolution_task(
        supervisor_task_1["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "first supervisor task"},
    )
    await supervisor.decide_self_evolution_task(
        agent_task_1["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "first agent task"},
    )

    state = await supervisor.get_supervisor_ui_state()
    layout = state["queue_layout"]
    timed_titles = [item["title"] for item in layout["timed_queue"]]

    assert layout["supervisor_active"]["title"] == "Supervisor first task"
    assert layout["supervisor_active"]["display_status"] == "待执行"
    assert layout["agent_active"]["title"] == "Agent first creative task"
    assert layout["agent_active"]["display_status"] == "待执行"
    assert timed_titles == ["Supervisor second task", "Agent second creative task"]
    assert [item["display_status"] for item in layout["timed_queue"]] == ["预设时间", "预设时间"]
    assert [item["lane"] for item in layout["timed_queue"]] == ["supervisor", "agent"]
    assert layout["window"]["label"] == "预设时间"
    assert layout["window"]["status_text"] == "限时自动执行中"
    assert state["metrics"]["slot_overview"] == "slot-A / slot-B"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_candidate_list_deduplicates_timed_queue_by_schedule(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_idle_window = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {"in_execution_window": False},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": False},
                "general_self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": False},
                "self_evolution": {"eligible_for_planning": True, "eligible_for_execution": False},
            },
        }
    )
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "candidates": [
                {
                    "title": "Duplicate scheduled candidate",
                    "stable_key": "candidate-dup",
                    "value_tags": ["continuity"],
                    "utility": 0.91,
                    "metadata": {
                        "endogenous_drive_key": "candidate-dup",
                        "scheduled_for": "2026-06-28T01:00:00",
                    },
                },
                {
                    "title": "Unique scheduled candidate",
                    "stable_key": "candidate-unique",
                    "value_tags": ["creativity"],
                    "utility": 0.88,
                    "metadata": {
                        "endogenous_drive_key": "candidate-unique",
                        "scheduled_for": "2026-06-28T02:00:00",
                    },
                },
            ]
        }
    )

    await supervisor.plan_self_evolution_task(
        {
            "title": "Existing timed queue task",
            "metadata": {
                "scheduled_for": "2026-06-28T01:00:00",
            },
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    layout = state["queue_layout"]

    assert layout["timed_queue"][0]["title"] == "Existing timed queue task"
    assert [item["title"] for item in layout["candidate_list"]] == ["Unique scheduled candidate"]
    assert layout["candidate_list"][0]["display_status"] == "等待监督者治理"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_keeps_supervisor_idle_when_only_agent_task_is_waiting(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidates": []}
    )
    supervisor.evaluate_idle_window = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {"in_execution_window": True},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
            "governance_task_type_decisions": {
                "self_learning": {"eligible_for_planning": True, "eligible_for_execution": True},
            },
        }
    )

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Agent waiting creative task",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    await supervisor.decide_self_evolution_task(
        planned["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "creative task ready"},
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "idle"
    assert state["queue_layout"]["supervisor_active"] is None
    assert state["queue_layout"]["agent_active"]["title"] == "Agent waiting creative task"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_delegates_memory_compression_to_maintenance_adapter(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    expected = {
        "status": "compressed",
        "execution_route_hint": build_execution_route_hint("memory.compress"),
    }
    supervisor._execution_facade.memory_maintenance.trigger_memory_compression = AsyncMock(return_value=expected)  # type: ignore[method-assign]

    result = await _trigger_memory_compression(supervisor, {"namespace": "default"})

    assert result == expected
    assert result["execution_route_hint"]["preferred_entrypoint"]["gateway_path"] == "/api/executor/memory/compress"
    supervisor._execution_facade.memory_maintenance.trigger_memory_compression.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_compression_runtime_does_not_route_through_execution_facade_helper(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._self_evolution_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._run_self_evolution_cycle = AsyncMock(return_value={"reviewed": 0, "dispatched": []})  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(  # type: ignore[method-assign]
        side_effect=asyncio.CancelledError()
    )
    original_memory_maintenance = supervisor._execution_facade.memory_maintenance
    facade_memory_maintenance = SimpleNamespace(
        trigger_memory_compression=AsyncMock(
            side_effect=AssertionError(
                "periodic compression should use the canonical maintenance executor directly"
            )
        )
    )
    supervisor._execution_facade.memory_maintenance = facade_memory_maintenance

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"memory_compression_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()

    # Compression is now owned by the Memory Service (architecture baseline §3.4).
    # The supervisor no longer runs a compression loop — verify it's gone.
    # Compression task was removed from supervisor (baseline §3.4)
    assert not hasattr(supervisor, '_compression_task'), (
        "Supervisor should not have a _compression_task attribute "
        "(compression is now owned by Memory Service per baseline §3.4)"
    )
    supervisor._execution_facade.memory_maintenance = original_memory_maintenance

    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Review and drive loops are only started in Governor Mode now.
    # They are None by default (Memory Mode) and were set to None above.
    assert supervisor._self_evolution_review_task is None, (
        "Review loop should not be running in Memory Mode"
    )
    assert supervisor._endogenous_drive_task is None, (
        "Drive loop should not be running in Memory Mode"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_self_evolution_cycle_dispatches_approved_formal_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._body_upgrade_executor.execute_body_upgrade = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "upgrade_executed"}
    )

    planned = await supervisor.plan_self_evolution_task(
        {
            "title": "Auto-dispatch formal body switch",
            "metadata": {
                "execution_kind": "body_switch",
                "target_slot_id": "slot-B",
            },
            "evidence": {
                "probe_report_ref": "probe-reports/slot-B/latest.json",
                "git_lineage": {
                    "candidate_commit": "bbb222",
                    "rollback_commit": "aaa111",
                    "changed_files": ["agent/stream_handler.py"],
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]

    supervisor.evaluate_idle_window = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
            "task_family_decisions": {
                "body_switch": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                }
            },
            "governance_task_type_decisions": {
                "self_evolution": {
                    "eligible_for_planning": True,
                    "eligible_for_execution": True,
                }
            },
        }
    )

    cycle = await supervisor._run_self_evolution_cycle()

    queued = await supervisor.get_self_evolution_task(task_id)
    assert cycle["reviewed"] == 1
    assert cycle["dispatched"] == [{"task_id": task_id, "status": "formal_self_evolution_executed"}]
    assert queued["status"] == "completed"
    assert queued["metadata"]["execution_result"]["status"] == "formal_self_evolution_executed"
    supervisor._body_upgrade_executor.execute_body_upgrade.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_self_evolution_review_runtime_invokes_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._self_evolution_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_self_evolution_cycle = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"self_evolution_review_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    # Review loop is not started in Memory Mode — activate Governor Mode
    await supervisor._start_governor_mode()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._self_evolution_review_task

    supervisor._run_self_evolution_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Drive loop was also started by Governor Mode
    supervisor._endogenous_drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_endogenous_drive_runtime_invokes_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._self_evolution_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_self_evolution_cycle = AsyncMock(return_value={"reviewed": 0, "dispatched": []})  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"endogenous_drive_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    # Drive loop is not started in Memory Mode — activate Governor Mode
    await supervisor._start_governor_mode()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task

    supervisor._run_endogenous_drive_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Review loop was also started by Governor Mode
    supervisor._self_evolution_review_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._self_evolution_review_task


@pytest.mark.unit
def test_supervisor_fastapi_lifespan_starts_and_stops_periodic_runtime(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.register_with_gateway = AsyncMock(return_value="service-1")  # type: ignore[method-assign]
    supervisor._start_periodic_tasks = AsyncMock()  # type: ignore[method-assign]
    supervisor._stop_periodic_tasks = AsyncMock()  # type: ignore[method-assign]

    with TestClient(supervisor.app) as client:
        response = client.get("/")
        assert response.status_code == 200

    supervisor.register_with_gateway.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._start_periodic_tasks.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._stop_periodic_tasks.assert_awaited_once_with()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_self_evolution_review_loop_survives_iteration_exception(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._self_evolution_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_self_evolution_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("transient review failure"), asyncio.CancelledError()]
    )
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"self_evolution_review_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    # Review loop only starts in Governor Mode
    await supervisor._start_governor_mode()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._self_evolution_review_task

    assert supervisor._run_self_evolution_cycle.await_count == 2  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Drive loop was also started by Governor Mode
    supervisor._endogenous_drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_internal_body_upgrade_pipeline_does_not_route_through_facade_execution_helpers(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    (tmp_path / "run_agent.py").write_text("print('agent entrypoint')\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(exist_ok=True)
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "model_tools.py").write_text("# probe smoke\n", encoding="utf-8")

    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        execute_body_upgrade=supervisor._body_upgrade_executor.execute_body_upgrade,
        run_body_probe=AsyncMock(
            side_effect=AssertionError("execution facade run_body_probe should not be used internally")
        ),
        start_managed_agent=AsyncMock(
            side_effect=AssertionError("execution facade start_managed_agent should not be used internally")
        ),
    )
    try:
        result = await _execute_body_upgrade(supervisor, {"body_version": "v2"})
    finally:
        supervisor._execution_facade = original_facade

    assert result["status"] == "upgrade_executed"
    assert result["probe_execution"]["report"]["overall_passed"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_accepts_self_learning_conclusion_submission_into_queue(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    learning = SelfLearningService(tmp_path / "self-learning")

    topic = learning.create_topic(
        title="Gateway-backed idle window",
        reason="Need a formal self-evolution proposal backed by learning evidence.",
        tags=["gateway", "idle"],
    )
    session = learning.plan_session(topic=topic, planned_minutes=20, trigger="idle")
    experiment = learning.record_experiment(
        topic=topic,
        session=session,
        hypothesis="Gateway activity facts should gate idle judgement.",
        method="Compare clock-only judgement with gateway activity markers.",
        observations=["Gateway markers better match real user interruption patterns."],
        outcome="passed",
        compared_against=["clock-only"],
    )
    conclusion = learning.submit_conclusion(
        topic=topic,
        session=session,
        experiments=[experiment],
        comparisons=["gateway-facts > clock-only"],
        summary="Promote gateway-backed idle judgement into the supervisor planning queue.",
        verified=True,
        recommendations=[
            LearningRecommendation(
                recommendation_type="propose_evolution_task",
                title="Adopt gateway-backed idle judgement",
                summary="Queue an evolution task instead of changing runtime directly.",
                evidence={"priority_reason": "reduces false idle windows"},
            )
        ],
    )

    submission = learning.build_supervisor_payload(conclusion)
    assert "task_type" not in submission["proposals"][0]
    result = await supervisor.submit_self_learning_conclusion(submission)

    assert result["status"] == "accepted"
    assert result["count"] == 1
    assert result["tasks"][0]["title"] == "Adopt gateway-backed idle judgement"
    assert result["tasks"][0]["task_type"] == "self_evolution"
    assert result["tasks"][0]["governance_task_type"] == "self_evolution"
    assert result["tasks"][0]["task_family"] == "general_self_evolution"
    assert result["tasks"][0]["execution_kind"] == "general_self_evolution"
    assert result["tasks"][0]["metadata"]["conclusion_id"] == conclusion.conclusion_id
    supervisor._touch_gateway_activity.assert_awaited_once_with(  # type: ignore[attr-defined]
        "self_learning",
        metadata={
            "action": "self_learning_submission",
            "count": 1,
            "conclusion_id": conclusion.conclusion_id,
        },
    )
