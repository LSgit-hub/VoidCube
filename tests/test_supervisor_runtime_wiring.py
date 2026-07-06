from __future__ import annotations

import asyncio
import json
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
from systems.supervisor.ui_runtime import UI_HTML
from systems.self_learning import LearningRecommendation
from systems.self_learning.conclusion_store import SelfLearningConclusionStore


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    return SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path))
    )


def _make_supervisor(tmp_path: Path) -> Supervisor:
    return Supervisor(_make_supervisor_config(tmp_path))


def _find_autonomous_observation_task(state: dict, *, title: str = "", task_id: str = "") -> dict:
    observation = dict(state.get("autonomous_observation") or {})
    candidates: list[dict] = []

    def _append(item):
        if isinstance(item, dict) and item:
            candidates.append(item)

    def _append_many(items):
        for item in list(items or []):
            _append(item)

    loop = dict(observation.get("loop") or {})
    for stage in list(loop.get("stages") or []):
        if isinstance(stage, dict):
            _append(stage.get("focus_task"))
    chain = dict(observation.get("chain") or {})
    for section in list(chain.get("segments") or []):
        if isinstance(section, dict):
            _append_many(section.get("items"))

    for item in candidates:
        if title and str(item.get("title") or "") == title:
            return item
        if task_id and str(item.get("task_id") or "") == task_id:
            return item
    raise AssertionError(f"task not found in autonomous observation: title={title!r} task_id={task_id!r}")


def _observation_section(observation: dict, key: str) -> dict:
    chain = dict(observation.get("chain") or {})
    for section in list(chain.get("segments") or []):
        if isinstance(section, dict) and str(section.get("key") or "").strip() == key:
            return section
    raise AssertionError(f"section not found: {key!r}")
def _observation_loop_stage(observation: dict, key: str) -> dict:
    loop = dict(observation.get("loop") or {})
    for stage in list(loop.get("stages") or []):
        if isinstance(stage, dict) and str(stage.get("key") or "").strip() == key:
            return stage
    raise AssertionError(f"loop stage not found: {key!r}")


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
def test_supervisor_room_frontend_uses_chain_panel_contract():
    assert 'id="panelChain"' in UI_HTML
    assert 'id="panelChainBody"' in UI_HTML
    assert 'data-panel="chain"' in UI_HTML
    assert 'renderChainPanel' in UI_HTML
    assert 'chain-stage-rail' in UI_HTML
    assert 'API-B 判断输入' in UI_HTML
    assert 'API-B 当前判断' in UI_HTML
    assert 'data-chain-group="' in UI_HTML
    assert 'data-chain-trace="' in UI_HTML
    assert 'data-chain-trace-expanded="' in UI_HTML
    assert 'data-chain-trace-source="' in UI_HTML
    assert 'panelTasks' not in UI_HTML
    assert 'renderTasksPanel' not in UI_HTML


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
            autonomous_chain_review_interval=900,
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
    assert config.service_runtime.autonomous_chain_review_interval == 900
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
    assert "/autonomous-chain/cycle" in route_paths
    assert "/autonomous-chain/tasks" in route_paths
    assert "/autonomous-chain/tasks/{task_id}" in route_paths
    assert "/autonomous-chain/tasks/{task_id}/decision" in route_paths
    assert "/autonomous-chain/tasks/review" in route_paths
    assert "/autonomous-chain/tasks/clear" in route_paths
    assert "/self-evolution/autonomous-cycle" not in route_paths
    assert "/self-evolution/tasks" not in route_paths
    assert "/self-evolution/tasks/{task_id}" not in route_paths
    assert "/self-evolution/tasks/{task_id}/decision" not in route_paths
    deprecated_autonomous_cycle_route = "/self-evolution/" + "auto" + "-cycle"
    assert deprecated_autonomous_cycle_route not in route_paths
    assert "/autonomous-chain-gate/activate" in route_paths
    assert "/autonomous-chain-gate/deactivate" in route_paths
    assert "/autonomous-chain-gate/status" in route_paths
    deprecated_gate_prefix = "/" + "governor" + "-mode"
    assert f"{deprecated_gate_prefix}/activate" not in route_paths
    assert f"{deprecated_gate_prefix}/deactivate" not in route_paths
    assert f"{deprecated_gate_prefix}/status" not in route_paths


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
    assert "/runtime/activity-guards/evaluate" in route_paths
    assert "/runtime/idle-window/evaluate" not in route_paths

    with TestClient(supervisor.app) as client:
        page = client.get("/ui")
        state = client.get("/ui/state")

    assert page.status_code == 200
    assert "VoidCube Supervisor Room" in page.text
    assert 'EventSource("/ui/events")' in page.text
    assert state.status_code == 200
    payload = state.json()
    assert payload["status"] == "ok"
    assert payload["scene"] in {"idle", "planning", "drive", "memory", "maintenance", "handoff"}
    assert "timeline" in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_view_aggregates_autonomous_activity_governance_and_gateway(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
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
    decided = await supervisor.decide_autonomous_chain_task(
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
    assert "自主学习" in result["summary"]["governance_labels"]
    assert "链路存储" in result["summary"]["source_labels"]
    assert result["summary"]["task_families"] == ["self_learning"]
    assert result["sources"]["autonomous_chain_store"] >= 2
    assert result["sources"]["supervisor_activity"] >= 1
    assert result["sources"]["mem_governor_history"] >= 1
    assert result["sources"]["gateway_activity_log"] == 1
    event_sources = {event["source"] for event in result["timeline"]}
    assert {
        "autonomous_chain_store",
        "supervisor_activity",
        "mem_governor_history",
        "gateway_activity_log",
    }.issubset(event_sources)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_list_summarizes_known_traces_without_gateway_history(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    first = await supervisor.plan_autonomous_chain_task({"title": "First trace"})
    second = await supervisor.plan_autonomous_chain_task({"title": "Second trace"})

    async def unavailable_gateway_activity_log(trace_id=None, limit=200):
        raise RuntimeError("gateway unavailable")

    supervisor._fetch_gateway_activity_log = unavailable_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.list_runtime_traces(limit=10)
    trace_ids = {trace["trace_id"] for trace in result["traces"]}

    assert result["status"] == "ok"
    assert first["tasks"][0]["trace_id"] in trace_ids
    assert second["tasks"][0]["trace_id"] in trace_ids
    assert result["sources"]["autonomous_chain_store"] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_trace_includes_writeback_and_cancelled_chain_records(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    async def empty_gateway_activity_log(trace_id=None, limit=200):
        return {"status": "ok", "events": []}

    supervisor._fetch_gateway_activity_log = empty_gateway_activity_log  # type: ignore[method-assign]

    completed = await supervisor.plan_autonomous_chain_task(
        {"title": "Completed trace record", "trace_id": "trace-runtime-projection-1"}
    )
    cancelled = await supervisor.plan_autonomous_chain_task(
        {"title": "Cancelled trace record", "trace_id": "trace-runtime-projection-2"}
    )

    completed_id = completed["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        completed_id,
        status="approved",
        actor="test",
        reason="approved for execution handoff",
    )
    supervisor._autonomous_chain_store.update_status(
        completed_id,
        status="running",
        actor="test",
        reason="execution handoff in progress",
    )
    supervisor._autonomous_chain_store.update_status(
        completed_id,
        status="completed",
        actor="test",
        reason="writeback finished",
    )
    cancelled_id = cancelled["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        cancelled_id,
        status="cancelled",
        actor="test",
        reason="cancelled during governance review",
    )

    completed_trace = await supervisor.get_runtime_trace(completed["tasks"][0]["trace_id"])
    cancelled_trace = await supervisor.get_runtime_trace(cancelled["tasks"][0]["trace_id"])

    assert completed_trace["found"] is True
    assert cancelled_trace["found"] is True
    assert completed_trace["summary"]["task_ids"] == [completed_id]
    assert cancelled_trace["summary"]["task_ids"] == [cancelled_id]
    assert completed_trace["sources"]["autonomous_chain_store"] >= 2
    assert cancelled_trace["sources"]["autonomous_chain_store"] >= 2


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_runtime_timeline_exposes_recent_unified_trace_records(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
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
                },
                {
                    "activity_id": "gateway-user-request-1",
                    "activity_kind": "user_request",
                    "recorded_at": "2026-05-25T12:06:00",
                    "session_id": "user-chat-session",
                    "metadata": {
                        "trace_id": "trace-timeline-1",
                        "request_id": "user-request-1",
                        "prompt_preview": "USER_CHAT_SECRET_SHOULD_NOT_RENDER",
                    },
                },
                {
                    "activity_id": "gateway-user-chat-scene-1",
                    "activity_kind": "agent_scene",
                    "recorded_at": "2026-05-25T12:07:00",
                    "session_id": "user-chat-session",
                    "metadata": {
                        "trace_id": "trace-timeline-1",
                        "agent_role": "user_chat",
                        "scene": "executing",
                        "subagent_focus_preview": "USER_CHAT_SUBAGENT_SHOULD_NOT_RENDER",
                    },
                },
            ],
        }

    supervisor._fetch_gateway_activity_log = fake_gateway_activity_log  # type: ignore[method-assign]

    result = await supervisor.get_runtime_timeline(limit=10)

    assert result["status"] == "ok"
    assert result["count"] >= 3
    sources = {event["source"] for event in result["timeline"]}
    assert {
        "autonomous_chain_store",
        "supervisor_activity",
        "mem_governor_history",
        "gateway_activity_log",
    }.issubset(sources)
    assert {event["trace_id"] for event in result["timeline"]} == {trace_id}
    assert {event["task_id"] for event in result["timeline"] if event.get("task_id")} == {task_id}
    rendered = json.dumps(result["timeline"], ensure_ascii=False)
    assert "USER_CHAT_SECRET_SHOULD_NOT_RENDER" not in rendered
    assert "USER_CHAT_SUBAGENT_SHOULD_NOT_RENDER" not in rendered
    gateway_events = [
        event for event in result["timeline"]
        if event.get("source") == "gateway_activity_log"
    ]
    assert [event["event_type"] for event in gateway_events] == ["self_learning"]


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
    await supervisor.plan_autonomous_chain_task(
        {
            "title": "Run memory continuity sweep",
            "execution_kind": "memory_maintenance",
        }
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "maintenance"
    backlog = _observation_section(state["autonomous_observation"], "api_b_backlog")
    assert backlog["items"][0]["title"] == "Run memory continuity sweep"
    assert "tasks" not in state
    assert "整理记忆" in state["title"]
    assert "tasks_planned" in [event["event_type"] for event in state["timeline"]]
    assert "supervisor_activity" in [event["source"] for event in state["timeline"]]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_read_does_not_create_timeline_events(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.get_runtime_timeline = AsyncMock(return_value={"timeline": []})  # type: ignore[method-assign]
    supervisor.evaluate_activity_guards = AsyncMock(
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 2, "counts": {}},
            "thresholds": {"user_idle_seconds": 600},
            "user_chain_signal": {
                "scope": "soft_signal_only",
                "active_sessions": 2,
                "is_quiet": False,
                "quiet_after_seconds": 600,
            },
            "decisions": {
                "eligible_for_planning": True,
                "eligible_for_execution": False,
            },
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

    candidate_section = _observation_section(state["autonomous_observation"], "api_b_candidates")
    assert candidate_section["items"] == []
    assert state["timeline"] == []
    assert "in_execution_window" not in state
    assert "active_executions" not in state
    assert "drive_candidates" not in state
    assert "drive_available" not in state
    assert "autonomous_chain_gate" not in state
    assert "active_sessions" not in state
    assert "activity_guards" not in state
    assert "metrics" not in state
    runtime = state["autonomous_observation"]["runtime"]
    assert runtime["drive_available"] is True
    assert runtime["autonomous_chain_gate_active"] is False
    assert runtime["activity_guards"]["scope"] == "user_chain_soft_signal_only"
    assert runtime["user_chain_signal"]["active_sessions"] == 2
    assert runtime["user_chain_signal"]["is_quiet"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_falls_back_to_fast_default_snapshots_when_live_probes_fail(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_activity_guards = AsyncMock(side_effect=RuntimeError("gateway down"))  # type: ignore[method-assign]
    supervisor._fetch_tier1_stats = AsyncMock(side_effect=RuntimeError("memory down"))  # type: ignore[method-assign]
    supervisor.get_runtime_timeline = AsyncMock(side_effect=RuntimeError("timeline down"))  # type: ignore[method-assign]

    state = await supervisor.get_supervisor_ui_state()

    runtime = state["autonomous_observation"]["runtime"]
    assert runtime["drive_available"] is False
    assert runtime["activity_guards"]["snapshot_source"] == "default"
    assert runtime["user_chain_signal"]["active_sessions"] == 0
    assert state["tier1_stats"]["memory_unavailable"] is True
    assert state["tier1_stats"]["snapshot_source"] == "default"
    assert state["timeline"] == []


@pytest.mark.unit
def test_supervisor_room_labels_active_sessions_as_user_chain_idle_signal():
    ui_source = Path("systems/supervisor/ui_runtime.py").read_text(encoding="utf-8")

    assert "用户链路感知" in ui_source
    assert "label:'活跃会话'" not in ui_source


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_governance_preview_for_shadow_review(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
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
            "last_autonomous_chain_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, activity_guards):
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

    monkeypatch.setattr(supervisor, "_lm_review_task_governance", fake_lm_review)

    await supervisor.review_autonomous_chain_tasks(
        {
            "activity_guards": {"now": "2026-05-25T01:00:00"},
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    duplicate = _find_autonomous_observation_task(
        state,
        title="Duplicate learning branch",
    )
    assert "lm_queue_shadow" not in duplicate["governance_preview"]
    assert all(
        "lm_queue_shadow" not in dict(entry.get("context") or {})
        for entry in duplicate.get("decision_history", [])
        if isinstance(entry, dict)
    )
    preview = duplicate["governance_preview"]["lm_governance_shadow"]
    assert preview["action"] == "merge"
    assert preview["merge_into"] == tasks_by_title["Canonical learning branch"]
    assert preview["merge_into_title"] == "Canonical learning branch"
    assert "监督者保留建议" in preview["summary"]
    assert "Canonical learning branch" in duplicate["governance_preview"]["summary"]
    assert state["autonomous_observation"]["metrics"]["governance"]["shadow_recommendations"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_applied_priority_updates(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
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
            "last_autonomous_chain_activity_at": "2026-05-25T00:00:00",
            "counts": {},
            "active_sessions": 0,
        }

    supervisor._fetch_gateway_activity_snapshot = idle_snapshot  # type: ignore[method-assign]

    async def fake_lm_review(tasks, *, activity_guards):
        return {
            task_id: {
                "action": "reprioritize",
                "priority": "high",
                "reason": "This follow-up now blocks higher-value evolution work.",
            }
        }

    monkeypatch.setattr(supervisor, "_lm_review_task_governance", fake_lm_review)

    await supervisor.review_autonomous_chain_tasks(
        {
            "activity_guards": {"now": "2026-05-25T01:00:00"},
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    task = _find_autonomous_observation_task(
        state,
        task_id=task_id,
    )
    assert task["priority"] == "high"
    assert "lm_queue_priority" not in task["governance_preview"]
    assert all(
        "lm_queue_priority" not in dict(entry.get("context") or {})
        for entry in task.get("decision_history", [])
        if isinstance(entry, dict)
    )
    assert task["governance_preview"]["lm_governance_priority"]["priority"] == "high"
    assert task["governance_preview"]["lm_governance_priority"]["priority_label"] == "高"
    assert "监督者已重排优先级" in task["governance_preview"]["summary"]
    assert state["autonomous_observation"]["metrics"]["governance"]["priority_updates"] >= 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_task_identity_for_body_improvement(tmp_path, monkeypatch):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
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
    task = _find_autonomous_observation_task(
        state,
        task_id=task_id,
    )

    assert task["task_identity"]["task_family"] == "body_upgrade"
    assert task["task_identity"]["execution_kind"] == "body_improvement"
    assert task["task_identity"]["requested_kind"] == "body_improvement"
    assert task["task_identity"]["display_kind"] == "body_improvement"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_uses_autonomous_observation_model(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]
    supervisor.evaluate_activity_guards = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
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

    supervisor_task_1 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Supervisor first task",
            "task_family": "memory_maintenance",
            "metadata": {"task_family": "memory_maintenance"},
        }
    )
    supervisor_task_2 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Supervisor second task",
            "task_family": "general_self_evolution",
            "metadata": {"task_family": "general_self_evolution"},
        }
    )
    agent_task_1 = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Agent first creative task",
            "task_type": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    agent_task_2 = await supervisor.plan_autonomous_chain_task(
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

    await supervisor.decide_autonomous_chain_task(
        supervisor_task_1["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "first supervisor task"},
    )
    await supervisor.decide_autonomous_chain_task(
        agent_task_1["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "first agent task"},
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]
    loop_stage_keys = [item["key"] for item in observation["loop"]["stages"]]
    group_keys = [group["key"] for group in observation["chain"]["segments"]]
    api_b_backlog = _observation_section(observation, "api_b_backlog")
    api_a_ready = _observation_section(observation, "api_a_ready")

    assert "queue_layout" not in state
    assert "panels" not in state
    assert observation["read_model_version"] == 8
    assert "observed_tasks" not in observation
    assert "candidates" not in observation
    assert observation["mode"]["scope"] == "api_b_autonomous_chain_only"
    assert observation["loop"]["stages"][0]["key"] == "api_b_judgement"
    assert observation["loop"]["stages"][1]["key"] == "api_a_execution"
    assert observation["loop"]["recent_writebacks"] == []
    assert observation["board"]["headline"] == "自主链路闭环观测"
    assert "watch_groups" not in observation["board"]
    assert loop_stage_keys == [
        "api_b_judgement",
        "api_a_execution",
        "mem_writeback",
        "api_b_reread",
    ]
    assert group_keys == ["api_b_candidates", "api_b_backlog", "api_a_ready", "mem_recent"]
    assert "queue" not in observation
    assert observation["chain"]["headline"] == "自主链路分段观察"
    assert "presentation" not in observation
    assert observation["board"]["primary_focus"]["title"] == "Supervisor first task"
    assert observation["board"]["primary_focus"]["status"] == "当前在途"
    assert observation["board"]["primary_focus"]["observation_role"] == "api_b_judgement"
    assert observation["board"]["primary_focus"]["stage_key"] == "api_b_judgement"
    assert "current_cards" not in observation["board"]
    assert _observation_loop_stage(observation, "api_a_execution")["status"] == "ready"
    assert _observation_loop_stage(observation, "api_a_execution")["focus_task"]["title"] == "Agent first creative task"
    assert [group["key"] for group in observation["chain"]["segments"]] == group_keys
    assert api_b_backlog["owner"] == "API-B"
    assert api_b_backlog["stage_label"] == "判断与治理"
    assert api_b_backlog["segment_kind"] == "governance_backlog"
    assert api_b_backlog["projection_scope"] == "chain_segment_projection"
    assert api_b_backlog["payload_count"] == 3
    assert api_b_backlog["event_count"] >= 1
    assert api_b_backlog["trace_count"] >= 1
    assert api_b_backlog["segment_status"] in {"active", "ready"}
    assert api_b_backlog["segment_status_label"] in {"当前有流动", "已有观测"}
    assert api_b_backlog["focus_item"]["observation_role"] == "api_b_judgement"
    assert api_b_backlog["latest_item"]["title"] == "Supervisor first task"
    assert api_b_backlog["latest_summary"]
    assert isinstance(api_b_backlog["recent_events"], list)
    assert api_b_backlog["recent_event_count"] >= 1
    assert isinstance(api_b_backlog["recent_traces"], list)
    assert api_b_backlog["recent_traces"][0]["trace_id"] == supervisor_task_1["tasks"][0]["trace_id"]
    assert api_b_backlog["recent_traces"][0]["detail"]["record_count"] >= 1
    assert isinstance(
        api_b_backlog["recent_traces"][0]["detail"]["source_counts"],
        dict,
    )
    assert isinstance(
        api_b_backlog["recent_traces"][0]["detail"]["timeline_preview"],
        list,
    )
    assert isinstance(
        api_b_backlog["recent_traces"][0]["detail"]["timeline_events"],
        list,
    )
    assert api_b_backlog["latest_trace_detail"]["trace_id"] == supervisor_task_1["tasks"][0]["trace_id"]
    assert "api_b" not in observation
    assert "api_a" not in observation
    assert "mem" not in observation
    assert "reread" not in observation
    assert _observation_loop_stage(observation, "api_b_judgement")["status"] == "active"
    assert _observation_loop_stage(observation, "api_a_execution")["status"] == "ready"
    assert _observation_loop_stage(observation, "mem_writeback")["status"] == "idle"
    assert _observation_loop_stage(observation, "api_b_judgement")["focus_task"]["title"] == "Supervisor first task"
    assert _observation_loop_stage(observation, "api_b_judgement")["focus_task"]["display_status"] == "待执行"
    assert _observation_loop_stage(observation, "api_a_execution")["focus_task"]["title"] == "Agent first creative task"
    assert _observation_loop_stage(observation, "api_a_execution")["focus_task"]["display_status"] == "待执行"
    assert [item["title"] for item in api_b_backlog["items"]] == [
        "Supervisor first task",
        "Supervisor second task",
        "Agent second creative task",
    ]
    assert [item["display_status"] for item in api_b_backlog["items"]] == ["待执行", "待审核", "待审核"]
    assert [item["lane"] for item in api_b_backlog["items"]] == ["supervisor", "supervisor", "supervisor"]
    assert [item["title"] for item in api_a_ready["items"]] == ["Agent first creative task"]
    assert [item["display_status"] for item in api_a_ready["items"]] == ["待执行"]
    assert [item["lane"] for item in api_a_ready["items"]] == ["agent"]
    assert observation["metrics"]["slot_overview"] == "slot-A / slot-B"
    assert observation["metrics"]["observed_task_total"] == 4
    assert observation["metrics"]["autonomous_task_total"] == 2
    assert observation["metrics"]["api_b_task_total"] == 2
    assert observation["runtime"]["activity_guards"]["snapshot_source"] == "live"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_keeps_running_api_a_task_out_of_ready_segment(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Running creative task",
            "task_type": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        reason="approved for pull",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="running",
        reason="claimed by API-A executor",
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]
    api_a_ready = _observation_section(observation, "api_a_ready")
    api_a_execution = _observation_loop_stage(observation, "api_a_execution")

    assert api_a_ready["items"] == []
    assert api_a_ready["payload_count"] == 0
    assert api_a_execution["status"] == "active"
    assert api_a_execution["focus_task"]["title"] == "Running creative task"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_observed_candidates_deduplicate_tasks_by_key(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_activity_guards = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
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
    supervisor._record_supervisor_ui_activity(
        "endogenous_drive_evaluated",
        scene="drive",
        summary="Cached endogenous drive candidates.",
        metadata={
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
        },
    )

    await supervisor.plan_autonomous_chain_task(
        {
            "title": "Existing observed governance task",
            "metadata": {
                "endogenous_drive_key": "candidate-dup",
                "scheduled_for": "2026-06-28T01:00:00",
            },
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    observation = state["autonomous_observation"]

    backlog = _observation_section(observation, "api_b_backlog")
    candidates = _observation_section(observation, "api_b_candidates")

    assert backlog["items"][0]["title"] == "Existing observed governance task"
    assert [item["title"] for item in candidates["items"]] == ["Unique scheduled candidate"]
    assert candidates["items"][0]["display_status"] == "候选形成"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_exposes_recent_mem_writebacks_in_autonomous_loop(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(return_value={"candidates": []})  # type: ignore[method-assign]

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Completed autonomous learning writeback",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_result": {
                    "summary": "Summarized learning result for Mem writeback.",
                },
            },
        }
    )
    task_id = planned["tasks"][0]["task_id"]
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="approved",
        reason="Approved autonomous learning writeback.",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="running",
        reason="Autonomous learning writeback running.",
    )
    supervisor._autonomous_chain_store.update_status(
        task_id,
        status="completed",
        reason="Completed autonomous learning writeback.",
    )

    state = await supervisor.get_supervisor_ui_state()
    writeback = state["autonomous_observation"]["loop"]["recent_writebacks"][0]
    mem_recent = state["autonomous_observation"]["chain"]["segments"][3]["items"][0]
    mem_stage = _observation_loop_stage(state["autonomous_observation"], "mem_writeback")

    assert writeback["title"] == "Completed autonomous learning writeback"
    assert writeback["lane"] == "agent"
    assert writeback["status"] == "completed"
    assert mem_stage["focus_task"]["title"] == "Completed autonomous learning writeback"
    assert mem_stage["status"] == "ready"
    assert mem_recent["lane"] == "mem"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_ui_state_projects_cognition_judgement_and_uncertainty_for_web_room(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidates": []}
    )
    supervisor.evaluate_activity_guards = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
            "idle_seconds": {},
            "activity": {"active_sessions": 0, "counts": {}},
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }
    )
    supervisor._persist_endogenous_cognition_state(
        {
            "perception": {
                "system_posture": "truth_guarded",
                "user_mode": "quiet",
                "governance_backlog_count": 2,
                "active_sessions": 0,
                "recent_errors": 1,
                "learning_quality": 61,
                "correction_signals": 2,
                "idle_seconds": {"user": 120, "memory": 15},
            },
            "world_model": {
                "governance_load_state": "strained",
                "memory_pressure": 0.22,
                "truthfulness_pressure": 0.71,
                "learning_momentum": 0.33,
                "self_confidence": 0.44,
            },
            "needs": [
                {
                    "need_type": "truthfulness_repair",
                    "severity": 0.83,
                    "urgency": 0.8,
                    "confidence": 0.66,
                    "rationale": "Recent corrections suggest unresolved truthfulness debt.",
                }
            ],
            "intents": [
                {
                    "intent_type": "protect_truthfulness",
                    "priority": 0.86,
                    "output_channel": "governance_review",
                    "target_horizon": "next_cycle",
                    "rationale": "Protect truthfulness before expanding output.",
                }
            ],
            "signals": [
                {
                    "signal_type": "truthfulness_alert",
                    "priority": 0.72,
                    "message": "Truthfulness alerts have been rising.",
                }
            ],
            "adaptive_policy": {
                "learning_expansion_bias": 0.12,
                "truthfulness_bias": 0.77,
                "memory_continuity_bias": 0.15,
                "governance_hygiene_bias": 0.54,
                "body_growth_bias": 0.08,
                "observation_bias": 0.63,
                "candidate_throttle": 0.4,
                "candidate_budget": 2,
                "exploratory_learning_quota": 0,
                "body_growth_quota": 0,
                "preferred_focus": "truthfulness",
            },
            "judgement_core": {
                "primary_need": {"need_type": "truthfulness_repair"},
                "primary_intent": {"intent_type": "protect_truthfulness"},
            },
            "governance": {
                "preferred_focus": "truthfulness",
                "dominant_constraint": "governance_backlog_blockage",
            },
            "proposal_cognition": {
                "assessment_trace": {
                    "available": True,
                    "dominant_constraint": "governance_backlog_blockage",
                    "current_judgement": "review should dominate until grounding is repaired",
                    "why_not_improvement_now": "Prioritize truthfulness governance before direct body improvement.",
                    "why_not_improvement_now_count": 1,
                    "self_iteration_target": "truthfulness",
                    "self_iteration_hypothesis": "Repair truthfulness signals before body work.",
                },
                "meta_cognition_profile": {
                    "current_judgement": "",
                    "dominant_constraint": "",
                    "self_iteration_focus": {
                        "domain": "truthfulness",
                        "hypothesis": "Repair truthfulness signals before body work.",
                    },
                },
            },
            "uncertainty_ledger": {
                "active_count": 1,
                "highest_risk_domain": "truthfulness",
                "entries": [
                    {
                        "domain": "truthfulness",
                        "risk": 0.72,
                        "confidence": 0.64,
                        "why_uncertain": "Corrections are visible but still need targeted review.",
                        "observation_target": "truthfulness",
                        "recommended_probe": "review recent uncertain answers and correction signals",
                    }
                ],
            },
            "observation_program": {
                "highest_priority_target": "truthfulness",
                "entries": [
                    {
                        "target": "truthfulness",
                        "recommended_probe": "review recent uncertain answers and correction signals",
                        "recommended_next_step": "collect_observation",
                        "persistence_state": "stalled",
                    }
                ],
            },
        }
    )

    state = await supervisor.get_supervisor_ui_state()
    cognition = state["cognition"]
    judgement = cognition["judgement"]
    uncertainty = cognition["uncertainty"]
    top_item = uncertainty["top_items"][0]

    assert judgement["focus_label"] == "真实性"
    assert judgement["dominant_constraint_label"] == "治理积压阻塞"
    assert judgement["primary_need_label"] == "修补真实性风险"
    assert judgement["primary_intent_label"] == "保护真实性"
    assert judgement["observation_target_label"] == "真实性侧"
    assert judgement["why_not_direct_improvement"][0] == "先处理真实性风险，再考虑直接替身改进"
    assert "真实性" in judgement["summary"]
    assert uncertainty["highest_risk_label"] == "真实性侧"
    assert uncertainty["summary"] == "当前最需要补证据的是真实性侧。"
    assert top_item["domain_label"] == "真实性侧"
    assert top_item["risk_label"] == "72%"
    assert top_item["confidence_label"] == "64%"
    assert top_item["recommended_probe_label"] == "复核近期不确定回答与修正信号"
    assert top_item["recommended_next_step_label"] == "补观察证据"
    assert top_item["persistence_label"] == "长期未化解"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_room_state_keeps_supervisor_idle_when_only_agent_task_is_waiting(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor.evaluate_endogenous_drive = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidates": []}
    )
    supervisor.evaluate_activity_guards = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "checks": {},
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

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Agent waiting creative task",
            "task_family": "self_learning",
            "metadata": {
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
            },
        }
    )
    await supervisor.decide_autonomous_chain_task(
        planned["tasks"][0]["task_id"],
        {"decision": "approve", "reason": "creative task ready"},
    )

    state = await supervisor.get_supervisor_ui_state()

    assert state["scene"] == "idle"
    assert "api_b" not in state["autonomous_observation"]
    assert _observation_loop_stage(state["autonomous_observation"], "api_b_judgement")["focus_task"] is None
    assert _observation_loop_stage(state["autonomous_observation"], "api_a_execution")["focus_task"]["title"] == "Agent waiting creative task"


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
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0, "handed_off": []})  # type: ignore[method-assign]
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
    # Review and drive loops are only started behind the supervisor autonomous-chain gate.
    # They remain disabled during baseline health-check startup.
    assert supervisor._autonomous_chain_review_task is None, (
        "Review loop should not be running before the supervisor autonomous-chain gate is enabled"
    )
    assert supervisor._endogenous_drive_task is None, (
        "Drive loop should not be running before the supervisor autonomous-chain gate is enabled"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_autonomous_chain_review_cycle_hands_off_approved_formal_task(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._body_upgrade_executor.execute_body_upgrade = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "upgrade_executed"}
    )

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Auto-handoff formal body switch",
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

    supervisor.evaluate_activity_guards = AsyncMock(  # type: ignore[method-assign]
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

    cycle = await supervisor._run_autonomous_chain_review_cycle()

    task_snapshot = await supervisor.get_autonomous_chain_task(task_id)
    assert cycle["reviewed"] == 1
    assert cycle["handed_off"] == [{"task_id": task_id, "status": "autonomous_chain_execution_executed"}]
    assert task_snapshot["status"] == "completed"
    assert task_snapshot["metadata"]["execution_result"]["status"] == "autonomous_chain_execution_executed"
    supervisor._body_upgrade_executor.execute_body_upgrade.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_execution_handoff_unknown_executor_status_retries_instead_of_completing(tmp_path):
    supervisor = _make_supervisor(tmp_path)

    class UnknownStatusFacade:
        async def execute_autonomous_chain_request(self, _payload):
            return {"status": "accepted"}

    supervisor._execution_facade = UnknownStatusFacade()

    planned = await supervisor.plan_autonomous_chain_task(
        {
            "title": "Do not complete on unknown executor status",
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

    supervisor.evaluate_activity_guards = AsyncMock(  # type: ignore[method-assign]
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

    cycle = await supervisor._run_autonomous_chain_review_cycle()
    task_snapshot = await supervisor.get_autonomous_chain_task(task_id)

    assert cycle["handed_off"] == [{"task_id": task_id, "status": "accepted"}]
    assert task_snapshot["status"] == "approved"
    assert task_snapshot["metadata"]["execution_failed"] is True
    assert task_snapshot["metadata"]["execution_failure_count"] == 1
    assert task_snapshot["metadata"]["execution_result"]["status"] == "accepted"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_autonomous_chain_review_runtime_invokes_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"autonomous_chain_review_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    # Review loop is not started during baseline startup — enable the autonomous-chain gate.
    await supervisor._start_autonomous_chain_gate()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._autonomous_chain_review_task

    supervisor._run_autonomous_chain_review_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Drive loop was also started by the autonomous-chain gate.
    supervisor._endogenous_drive_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_periodic_endogenous_drive_runtime_invokes_cycle(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(return_value={"reviewed": 0, "handed_off": []})  # type: ignore[method-assign]
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
    # Drive loop is not started during baseline startup — enable the autonomous-chain gate.
    await supervisor._start_autonomous_chain_gate()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._endogenous_drive_task

    supervisor._run_endogenous_drive_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Review loop was also started by the autonomous-chain gate.
    supervisor._autonomous_chain_review_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._autonomous_chain_review_task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_immediate_endogenous_drive_logs_and_survives_exception(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._run_endogenous_drive_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("transient immediate drive failure")
    )

    with patch("systems.supervisor.service_runtime.asyncio.sleep", new=AsyncMock()):
        await supervisor._run_immediate_endogenous_drive_once()

    supervisor._run_endogenous_drive_cycle.assert_awaited_once_with()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_start_autonomous_chain_gate_renotifies_gateway_when_already_active(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._service_runtime.autonomous_chain_gate_active = True
    supervisor._notify_gateway_autonomous_chain_gate = AsyncMock()  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock()  # type: ignore[method-assign]
    supervisor._run_endogenous_drive_cycle = AsyncMock()  # type: ignore[method-assign]

    await supervisor._start_autonomous_chain_gate()

    supervisor._notify_gateway_autonomous_chain_gate.assert_awaited_once_with(active=True)  # type: ignore[attr-defined]
    assert supervisor._autonomous_chain_review_task is None
    assert supervisor._endogenous_drive_task is None


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
async def test_supervisor_autonomous_chain_review_loop_survives_iteration_exception(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._health_check_task = None
    supervisor._autonomous_chain_review_task = None
    supervisor._endogenous_drive_task = None
    supervisor._ensure_watch_window_task = Mock()  # type: ignore[method-assign]
    supervisor.run_health_checks = AsyncMock(return_value={"results": []})  # type: ignore[method-assign]
    supervisor._memory_maintenance_executor.trigger_memory_compression = AsyncMock(return_value={"status": "compressed"})  # type: ignore[method-assign]
    supervisor._run_autonomous_chain_review_cycle = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("transient review failure"), asyncio.CancelledError()]
    )
    supervisor._run_endogenous_drive_cycle = AsyncMock(return_value={"planned": 0})  # type: ignore[method-assign]

    config = supervisor.config.model_copy(
        update={
            "service_runtime": supervisor.config.service_runtime.model_copy(
                update={"autonomous_chain_review_interval": 0}
            )
        }
    )
    supervisor.config = config

    await supervisor._start_periodic_tasks()
    # Review loop only starts after the supervisor autonomous-chain gate is enabled.
    await supervisor._start_autonomous_chain_gate()

    with pytest.raises(asyncio.CancelledError):
        await supervisor._autonomous_chain_review_task

    assert supervisor._run_autonomous_chain_review_cycle.await_count == 2  # type: ignore[attr-defined]
    supervisor._health_check_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await supervisor._health_check_task
    # Drive loop was also started by the autonomous-chain gate.
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
async def test_supervisor_accepts_self_learning_conclusion_submission_into_backlog(tmp_path):
    supervisor = _make_supervisor(tmp_path)
    supervisor._touch_gateway_activity = AsyncMock()  # type: ignore[method-assign]
    learning = SelfLearningConclusionStore(tmp_path / "self-learning")

    topic = learning.create_topic(
        title="Gateway-backed activity guard",
        reason="Need a formal autonomous-chain proposal backed by learning evidence.",
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
        summary="Promote gateway-backed idle judgement into the supervisor governance backlog.",
        verified=True,
        recommendations=[
            LearningRecommendation(
                recommendation_type="propose_evolution_task",
                title="Adopt gateway-backed idle judgement",
                summary="Create a governance-backlog task instead of changing runtime directly.",
                evidence={"priority_reason": "reduces false activity-guard approvals"},
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





