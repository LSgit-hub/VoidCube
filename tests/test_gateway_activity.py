from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.gateway.internal_gateway import GatewayConfig, InternalGateway, ServiceInfo


def test_gateway_activity_touch_endpoint_updates_snapshot():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.get("/admin/activity")
    assert response.status_code == 200
    initial = response.json()
    assert initial["last_user_request_at"] is None
    assert initial["last_self_learning_activity_at"] is None
    assert initial["last_autonomous_chain_activity_at"] is None
    assert initial["counts"]["self_learning_activity_count"] == 0
    assert initial["counts"]["autonomous_chain_activity_count"] == 0

    touch_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain",
            "source_service": "supervisor",
        },
    )
    assert touch_response.status_code == 200

    updated = client.get("/admin/activity").json()
    assert updated["last_autonomous_chain_activity_at"] is not None
    assert updated["counts"]["autonomous_chain_activity_count"] == 1

    plan_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_plan",
            "source_service": "supervisor",
        },
    )
    execute_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_execute",
            "source_service": "executor",
        },
    )

    assert plan_response.status_code == 200
    assert execute_response.status_code == 200
    refined = client.get("/admin/activity").json()
    assert refined["last_autonomous_chain_plan_at"] is not None
    assert refined["last_autonomous_chain_execute_at"] is not None
    assert refined["counts"]["autonomous_chain_activity_count"] == 3
    assert refined["counts"]["autonomous_chain_plan_count"] == 1
    assert refined["counts"]["autonomous_chain_execute_count"] == 1
    assert refined["recent_metadata"]["autonomous_chain"]["source_service"] == "executor"
    assert refined["recent_metadata"]["autonomous_chain_plan"]["source_service"] == "supervisor"
    assert refined["recent_metadata"]["autonomous_chain_execute"]["source_service"] == "executor"


def test_gateway_activity_touch_derives_runtime_task_profile_from_broad_metadata():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_plan",
            "source_service": "supervisor",
            "metadata": {
                "trace_id": "trace-plan-1",
                "task_type": "self_evolution",
                "kind": "body_switch",
                "decision_id": "decision-plan-1",
            },
        },
    )

    assert response.status_code == 200
    activity = client.get("/admin/activity").json()
    metadata = activity["recent_metadata"]["autonomous_chain_plan"]
    assert metadata["trace_id"] == "trace-plan-1"
    assert metadata["governance_task_type"] == "self_evolution"
    assert metadata["governance_task_type_label"] == "自主改进"
    assert metadata["task_family"] == "body_switch"
    assert metadata["task_family_label"] == "身体切换"
    assert metadata["execution_kind"] == "body_switch"
    assert metadata["execution_kind_label"] == "身体切换"
    assert metadata["decision_id"] == "decision-plan-1"
    assert metadata["task_identity"]["display_kind"] == "body_switch"
    assert metadata["task_identity"]["display_label"] == "身体切换"
    assert metadata["task_identity"]["requested_kind"] == "body_switch"
    assert metadata["task_identity"]["requested_kind_label"] == "身体切换"
    assert metadata["task_identity"]["summary"] == "身体切换"
    assert "task_type" not in metadata
    assert "task_type_label" not in metadata


def test_gateway_activity_touch_derives_runtime_task_profile_from_nested_runtime_profile():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_execute",
            "source_service": "executor",
            "metadata": {
                "trace_id": "trace-exec-2",
                "runtime_task_profile": {
                    "governance_task_type": "memory_maintenance",
                    "task_family": "memory_maintenance",
                    "execution_kind": "memory_maintenance",
                },
                "decision_id": "decision-exec-2",
            },
        },
    )

    assert response.status_code == 200
    activity = client.get("/admin/activity").json()
    metadata = activity["recent_metadata"]["autonomous_chain_execute"]
    assert metadata["trace_id"] == "trace-exec-2"
    assert metadata["governance_task_type"] == "memory_maintenance"
    assert metadata["governance_task_type_label"] == "记忆维护"
    assert metadata["task_family"] == "memory_maintenance"
    assert metadata["task_family_label"] == "记忆维护"
    assert metadata["execution_kind"] == "memory_maintenance"
    assert metadata["execution_kind_label"] == "记忆维护"
    assert metadata["decision_id"] == "decision-exec-2"


def test_gateway_activity_log_is_bounded_and_trace_filterable():
    gateway = InternalGateway(GatewayConfig(activity_log_limit=2))
    client = TestClient(gateway.app)

    first = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_plan",
            "source_service": "supervisor",
            "metadata": {
                "trace_id": "trace-log-1",
                "task_id": "task-log-1",
                "task_family": "body_switch",
            },
        },
    )
    second = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_learning",
            "source_service": "self-learning",
            "metadata": {
                "trace_id": "trace-log-2",
                "task_id": "task-log-2",
                "task_family": "self_learning",
            },
        },
    )
    ignored = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "unsupported",
            "source_service": "unknown",
            "metadata": {"trace_id": "trace-ignored"},
        },
    )
    third = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_execute",
            "source_service": "executor",
            "metadata": {
                "trace_id": "trace-log-1",
                "task_id": "task-log-3",
                "kind": "body_switch",
            },
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert ignored.status_code == 200
    assert third.status_code == 200

    log = client.get("/admin/activity/log").json()
    assert log["status"] == "ok"
    assert log["activity_log_limit"] == 2
    assert log["count"] == 2
    assert [event["activity_kind"] for event in log["events"]] == [
        "autonomous_chain_execute",
        "self_learning",
    ]
    assert all(event["metadata"].get("trace_id") != "trace-ignored" for event in log["events"])

    filtered = client.get(
        "/admin/activity/log",
        params={"trace_id": "trace-log-1", "limit": 10},
    ).json()
    assert filtered["count"] == 1
    assert filtered["events"][0]["activity_kind"] == "autonomous_chain_execute"
    assert filtered["events"][0]["metadata"]["trace_id"] == "trace-log-1"
    assert filtered["events"][0]["metadata"]["task_family"] == "body_switch"
    assert filtered["events"][0]["metadata"]["execution_kind"] == "body_switch"
    assert filtered["events"][0]["metadata"]["task_identity"]["display_kind"] == "body_switch"


def test_gateway_does_not_publish_self_learning_service_route():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "self-learning-1",
            "service_name": "archived-learning-record-service",
            "service_type": "self_learning",
            "address": "http://127.0.0.1:65526",
        },
    )
    assert register_response.status_code == 201

    health = client.get("/").json()
    assert "self_learning" not in health["registered_services"]

    routes = client.get("/admin/routes").json()
    retired_proxy_prefix = "/" + "self" + "-learning/"
    assert all(route["path_prefix"] != retired_proxy_prefix for route in routes["routes"])

    response = client.post(
        "/api/" + "self" + "-learning/conclusions/submit",
        json={"summary": "noop"},
    )

    assert response.status_code == 404
    activity = client.get("/admin/activity").json()
    assert activity["last_self_learning_activity_at"] is None
    assert activity["counts"]["self_learning_activity_count"] == 0


def test_gateway_agent_query_rejects_legacy_proxy_but_still_records_user_activity():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_name": "agent-slot-A",
            "service_type": "agent",
            "address": "http://127.0.0.1:65530",
            "metadata": {"slot_id": "slot-A", "body_version": "bootstrap"},
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/v1/agent/query",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 410
    assert "Gateway agent proxy has been removed" in response.json()["detail"]

    activity = client.get("/admin/activity").json()
    assert activity["last_user_request_at"] is not None
    assert activity["counts"]["user_request_count"] == 1
    assert activity["counts"]["agent_work_count"] == 0


def test_gateway_agent_query_still_records_activity_while_autonomous_gate_is_active():
    gateway = InternalGateway(GatewayConfig())
    gateway._autonomous_chain_gate_active = True
    client = TestClient(gateway.app)

    response = client.post(
        "/v1/agent/query",
        json={"session_id": "cli-session-1", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 410
    assert "Gateway agent proxy has been removed" in response.json()["detail"]

    activity = client.get("/admin/activity").json()
    assert activity["last_user_request_at"] is not None
    assert activity["counts"]["user_request_count"] == 1


def test_gateway_exposes_only_autonomous_chain_gate_admin_route():
    gateway = InternalGateway(GatewayConfig())
    route_paths = {route.path for route in gateway.app.routes}

    assert "/admin/autonomous-chain-gate" in route_paths
    deprecated_gate_route = "/admin/" + "gover" + "nor-mode"
    assert deprecated_gate_route not in route_paths


def test_gateway_body_status_exposes_cli_executor_and_passive_body_slots_only():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/register",
        json={
            "service_id": "agent-slot-a",
            "service_name": "agent-slot-A",
            "service_type": "agent",
            "address": "http://127.0.0.1:65530",
            "metadata": {"slot_id": "slot-A", "body_version": "bootstrap"},
        },
    )
    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    status = client.get("/admin/body/status")
    assert status.status_code == 200
    payload = status.json()
    assert "active_body" not in payload
    assert "body_routing" not in payload
    assert payload["active_cli_executor"]["session_id"] == "cli-session-1"
    assert payload["body_slots"][0]["slot_id"] == "slot-A"
    assert "lifecycle_state" not in payload["body_slots"][0]
    assert "is_active_body" not in payload["body_slots"][0]


def test_gateway_activity_touch_adds_task_identity_summary():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_work",
            "source_service": "cli_agent",
            "metadata": {
                "task_id": "body-1",
                "title": "改进 shell 替身",
                "execution_kind": "body_improvement",
                "task_family": "body_upgrade",
                "governance_task_type": "self_evolution",
            },
        },
    )

    assert response.status_code == 200
    activity = client.get("/admin/activity").json()
    metadata = activity["recent_metadata"]["agent_work"]
    identity = metadata["task_identity"]
    assert identity["task_id"] == "body-1"
    assert identity["title"] == "改进 shell 替身"
    assert identity["execution_kind"] == "body_improvement"
    assert identity["display_kind"] == "body_improvement"
    assert identity["governance_task_type_label"] == "自主改进"
    assert identity["task_family_label"] == "替身升级"
    assert identity["execution_kind_label"] == "替身改进"
    assert identity["display_label"] == "替身改进"
    assert "改进 shell 替身 (替身改进)" == identity["summary"]
    assert "labels" not in identity
    assert "label_texts" not in identity


def test_gateway_agent_scene_touch_updates_scene_cache_and_prefers_cli_agent():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    session_response = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    assert session_response.status_code == 200

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": "cli-session-1",
            "metadata": {
                "scene": "learning",
                "task_id": "learn-1",
                "execution_kind": "self_learning",
                "subagent_foreground_count": 2,
                "subagent_background_count": 1,
                "subagent_total_count": 3,
                "subagent_focus_task_id": "delegate-1",
                "subagent_focus_tool": "read_file",
                "subagent_focus_preview": "read_file",
            },
        },
    )

    assert response.status_code == 200
    scenes = client.get("/admin/scenes").json()["scenes"]
    summary = client.get("/admin/scenes").json()["summary"]
    assert scenes["agent"]["scene"] == "learning"
    assert scenes["agent"]["scene_projection_scope"] == "agent_top_level_projection"
    assert scenes["agent"]["canonical_lanes"] == ["supervisor_task", "user_chat"]
    assert scenes["agent"]["lane_contract"]["supervisor_task"] == "autonomous_chain_observation"
    assert scenes["agent"]["lane_contract"]["user_chat"] == "user_chain_status"
    assert scenes["agent"]["scene_task_id"] == "learn-1"
    assert scenes["agent"]["source_service"] == "cli_agent"
    assert scenes["agent"]["subagent_foreground_count"] == 2
    assert scenes["agent"]["subagent_background_count"] == 1
    assert scenes["agent"]["subagent_focus_tool"] == "read_file"
    assert summary["agent"] == "idle"
    assert summary["agent_user_chat"] == "idle"
    assert summary["agent_supervisor_task"] == "learning"

    health = client.get("/").json()
    active_cli = health["active_cli_executor"]
    assert active_cli["session_id"] == "cli-session-1"
    assert active_cli["is_active_cli_executor"] is True
    assert active_cli["scene"] == "learning"
    assert active_cli["scene_projection_scope"] == "agent_lane"
    assert active_cli["agent_lane"] == "supervisor_task"
    assert active_cli["subagent_foreground_count"] == 2
    assert active_cli["subagent_background_count"] == 1
    assert active_cli["subagent_focus_preview"] == "read_file"
    assert active_cli["lease_status"] == "healthy"
    assert active_cli["is_stale"] is False


def test_gateway_active_cli_executor_marks_stale_lease():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-stale",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    assert register.status_code == 200

    gateway._agent_session_cache["cli-session-stale"]["last_used_at"] = (
        datetime.now() - timedelta(seconds=gateway._active_cli_stale_after_seconds + 5)
    )

    status = client.get("/admin/body/status").json()
    active_cli = status["active_cli_executor"]
    assert active_cli["session_id"] == "cli-session-stale"
    assert active_cli["lease_status"] == "stale"
    assert active_cli["is_stale"] is True
    assert active_cli["idle_seconds"] >= gateway._active_cli_stale_after_seconds


def test_gateway_register_session_does_not_override_existing_active_cli_executor():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    first = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    second = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-2",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    status = client.get("/admin/body/status").json()
    assert status["active_cli_executor"]["session_id"] == "cli-session-1"


def test_gateway_idle_scene_does_not_steal_active_cli_executor():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": "cli-session-1",
            "metadata": {
                "scene": "learning",
                "task_id": "learn-1",
                "execution_kind": "self_learning",
            },
        },
    )
    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-2",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    idle_touch = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": "cli-session-2",
            "metadata": {
                "scene": "idle",
            },
        },
    )

    assert idle_touch.status_code == 200
    status = client.get("/admin/body/status").json()
    assert status["active_cli_executor"]["session_id"] == "cli-session-1"
    assert status["active_cli_executor"]["scene"] == "learning"


def test_gateway_active_cli_executor_does_not_fallback_to_agent_top_level_scene():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    # Simulate a stale top-level agent scene without a matching lane owner.
    gateway._active_cli_session_id = "cli-session-1"
    gateway._scenes_cache["agent"].update(
        {
            "scene": "executing",
            "scene_projection_scope": "agent_top_level_projection",
            "source_service": "cli_agent",
            "subagent_focus_tool": "grep",
        }
    )

    status = client.get("/admin/body/status").json()
    active_cli = status["active_cli_executor"]

    assert active_cli["session_id"] == "cli-session-1"
    assert active_cli.get("scene") is None
    assert active_cli.get("scene_projection_scope") is None
    assert active_cli.get("agent_lane") is None
    assert active_cli.get("subagent_focus_tool") is None


def test_gateway_task_decision_forwards_metadata_to_supervisor(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    gateway._services["supervisor-1"] = ServiceInfo(
        service_id="supervisor-1",
        service_name="supervisor",
        service_type="supervisor",
        address="http://127.0.0.1:6002",
        health_endpoint="http://127.0.0.1:6002/health",
        registered_at=datetime.now(),
        last_health_check=datetime.now(),
        healthy=True,
    )
    gateway._agent_session_cache["cli-session-1"] = {"source": "cli", "last_used_at": datetime.now()}
    client = TestClient(gateway.app)

    captured = {}

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._payload

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return _FakeResponse({"status": "running"})

        def get(self, url, timeout=None):
            captured["get_url"] = url
            return _FakeResponse({
                "task_id": "learn-9",
                "status": "approved",
                "governance_task_type": "self_learning",
                "metadata": {},
            })

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    response = client.post(
        "/v1/tasks/learn-9/decision",
        json={
            "decision": "running",
            "actor": "cli_agent",
            "reason": "Agent pulled task",
            "context": {"session_id": "cli-session-1"},
            "metadata": {"owner_session_id": "cli-session-1", "execution_source": "cli_agent_pull"},
        },
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/autonomous-chain/tasks/learn-9/decision")
    assert captured["json"]["metadata"]["owner_session_id"] == "cli-session-1"
    assert captured["json"]["metadata"]["execution_source"] == "cli_agent_pull"


def test_gateway_memory_search_route_does_not_update_memory_activity_when_upstream_fails():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_name": "memory-service",
            "service_type": "memory",
            "address": "http://127.0.0.1:65529",
        },
    )
    assert register_response.status_code == 201

    response = client.post("/api/mem/memories/search", json={"query": "hello"})
    assert response.status_code in {500, 504}

    activity = client.get("/admin/activity").json()
    assert activity["last_memory_task_at"] is None
    assert activity["counts"]["memory_task_count"] == 0


def test_gateway_memory_write_route_updates_memory_activity_even_when_upstream_fails():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_name": "memory-service",
            "service_type": "memory",
            "address": "http://127.0.0.1:65529",
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/api/mem/memories/write",
        json={"namespace": "default", "content": "hello"},
    )
    assert response.status_code in {500, 504}

    activity = client.get("/admin/activity").json()
    assert activity["last_memory_task_at"] is not None
    assert activity["counts"]["memory_task_count"] == 1


def test_gateway_records_tier1_turn_with_single_atomic_memory_call(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    gateway._memory_service_url = "http://memory-service"
    posted = []

    class _FakeResponse:
        async def __aenter__(self):
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

        def post(self, url, json=None, timeout=None):
            posted.append({"url": url, "json": json, "timeout": timeout})
            return _FakeResponse()

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    asyncio.run(
        gateway._record_turn_to_tier1(
            "session-atomic",
            "user",
            "hello",
            metadata={"source": "test"},
        )
    )

    assert len(posted) == 1
    assert posted[0]["url"] == "http://memory-service/sessions/session-atomic/turns"
    assert posted[0]["json"]["speaker"] == "user"
    assert posted[0]["json"]["text"] == "hello"


def test_gateway_tier1_write_failure_updates_activity_counter(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    gateway._memory_service_url = "http://memory-service"
    client = TestClient(gateway.app)

    class _FailingSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            raise OSError("memory down")

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FailingSession)

    asyncio.run(
        gateway._record_turn_to_tier1(
            "session-fail",
            "agent",
            "finding",
            metadata={"task_id": "task-fail"},
        )
    )

    activity = client.get("/admin/activity").json()

    assert activity["last_memory_write_failure_at"] is not None
    assert activity["counts"]["memory_write_failure_count"] == 1
    metadata = activity["recent_metadata"]["memory_write_failure"]
    assert metadata["task_id"] == "task-fail"
    assert metadata["speaker"] == "agent"
    assert "memory down" in metadata["error"]


def test_gateway_task_pull_preserves_supervisor_http_status(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    client = TestClient(gateway.app)

    class _FakeResponse:
        status = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"detail": "supervisor temporarily unavailable"}

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, timeout=None):
            return _FakeResponse()

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    response = client.get("/v1/tasks?status=approved&task_type=self_learning")

    assert response.status_code == 503
    assert "Supervisor returned 503" in response.json()["detail"]


def test_gateway_task_pull_forwards_running_status(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    client = TestClient(gateway.app)
    calls = []

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"tasks": [{"task_id": "running-1", "status": "running"}]}

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None, timeout=None):
            calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
            return _FakeResponse()

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    response = client.get("/v1/tasks?status=running&execution_kind=body_improvement")

    assert response.status_code == 200
    assert response.json()["tasks"][0]["status"] == "running"
    assert calls[0]["params"]["status"] == "running"
    assert calls[0]["params"]["execution_kind"] == "body_improvement"


def test_gateway_executor_registration_adds_standard_route_and_health_count():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "executor-1",
            "service_name": "executor-service",
            "service_type": "executor",
            "address": "http://127.0.0.1:65528",
        },
    )

    assert register_response.status_code == 201
    health = client.get("/").json()
    routes = client.get("/admin/routes").json()
    assert health["registered_services"]["executor"] == 1
    assert health["executor_access_policy"]["preferred_gateway_prefix"] == "/api/executor"
    assert health["executor_access_policy"]["direct_executor_prefix"] == "/executor"
    assert health["executor_access_policy"]["failure_mode"] == "executor_required"
    assert routes["executor_access_policy"]["preferred_gateway_prefix"] == "/api/executor"
    executor_route = next(
        route for route in routes["routes"] if route["path_prefix"] == "/executor/"
    )
    assert executor_route["target_service"] == "executor"
    assert executor_route["target_instance"] == "executor-1"
    assert executor_route["route_policy"]["status"] == "preferred_execution_surface"
    assert executor_route["route_policy"]["preferred_gateway_prefix"] == "/api/executor"

    services = client.get("/admin/services").json()
    executor_service = next(
        service for service in services["services"] if service["service_id"] == "executor-1"
    )
    assert executor_service["executor_access_policy"]["preferred_gateway_prefix"] == "/api/executor"
    assert executor_service["executor_access_policy"]["failure_mode"] == "executor_required"
    assert executor_service["metadata"] == {}

    service_detail = client.get("/admin/services/executor-1").json()
    assert service_detail["executor_access_policy"]["direct_executor_prefix"] == "/executor"
    assert "task_type" not in service_detail["metadata"]
    assert "governance_task_type" not in service_detail["metadata"]
    assert "task_family" not in service_detail["metadata"]
    assert "execution_kind" not in service_detail["metadata"]

    health_executor_route = next(
        route for route in health["routes"] if route["path_prefix"] == "/executor/"
    )
    assert health_executor_route["route_policy"]["status"] == "preferred_execution_surface"


def test_gateway_supervisor_route_is_marked_as_governance_runtime_surface():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "supervisor-1",
            "service_name": "supervisor-service",
            "service_type": "supervisor",
            "address": "http://127.0.0.1:65527",
        },
    )

    assert register_response.status_code == 201
    routes = client.get("/admin/routes").json()
    health = client.get("/").json()

    supervisor_route = next(
        route for route in routes["routes"] if route["path_prefix"] == "/supervisor/"
    )
    assert supervisor_route["target_service"] == "supervisor"
    assert supervisor_route["target_instance"] == "supervisor-1"
    assert supervisor_route["route_policy"]["status"] == "governance_runtime_surface"
    assert supervisor_route["route_policy"]["service_role"] == "planning_governance_runtime"

    health_supervisor_route = next(
        route for route in health["routes"] if route["path_prefix"] == "/supervisor/"
    )
    assert health_supervisor_route["route_policy"]["status"] == "governance_runtime_surface"


def test_gateway_executor_route_updates_execute_activity_even_when_upstream_fails():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "executor-1",
            "service_name": "executor-service",
            "service_type": "executor",
            "address": "http://127.0.0.1:65528",
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/api/executor/body/upgrade/execute",
        json={
            "slot_id": "slot-B",
            "trace_id": "trace-exec-1",
            "task_type": "self_evolution",
            "decision_id": "decision-exec-1",
            "execution_request": {
                "task_id": "task-exec-1",
                "trace_id": "trace-exec-1",
                "task_type": "self_evolution",
                "decision_id": "decision-exec-1",
                "kind": "body_switch",
                "source_actor": "mem_supervisor",
            },
        },
    )

    assert response.status_code in {500, 504}
    activity = client.get("/admin/activity").json()
    assert activity["last_autonomous_chain_execute_at"] is not None
    assert activity["last_autonomous_chain_activity_at"] is not None
    assert activity["counts"]["autonomous_chain_execute_count"] == 1
    assert activity["counts"]["autonomous_chain_activity_count"] == 1
    assert activity["recent_metadata"]["autonomous_chain_execute"]["trace_id"] == "trace-exec-1"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["governance_task_type"] == "self_evolution"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["governance_task_type_label"] == "自主改进"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_family"] == "body_switch"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_family_label"] == "身体切换"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["execution_kind"] == "body_switch"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["execution_kind_label"] == "身体切换"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["decision_id"] == "decision-exec-1"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_id"] == "task-exec-1"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_identity"]["display_kind"] == "body_switch"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_identity"]["display_label"] == "身体切换"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_identity"]["requested_kind"] == "body_switch"
    assert "task_type" not in activity["recent_metadata"]["autonomous_chain_execute"]
    assert "task_type_label" not in activity["recent_metadata"]["autonomous_chain_execute"]


def _post_agent_scene(client, session_id, metadata):
    return client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": session_id,
            "metadata": metadata,
        },
    )


def test_gateway_agent_lanes_keep_supervisor_and_user_chat_separate():
    """A user_chat report must not overwrite the supervisor_task lane."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    # supervisor-task executor reports its subagents
    resp = _post_agent_scene(
        client,
        "supervisor-session",
        {
            "scene": "learning",
            "task_id": "learn-9",
            "execution_kind": "self_learning",
            "agent_role": "supervisor_task",
            "subagent_foreground_count": 3,
            "subagent_focus_tool": "read_file",
        },
    )
    assert resp.status_code == 200

    # main CLI user-chat executor reports afterwards (would overwrite top-level)
    resp = _post_agent_scene(
        client,
        "user-session",
        {
            "scene": "executing",
            "agent_role": "user_chat",
            "subagent_foreground_count": 1,
            "subagent_focus_tool": "grep",
        },
    )
    assert resp.status_code == 200

    scenes_payload = client.get("/admin/scenes").json()
    agent_scene = scenes_payload["scenes"]["agent"]
    summary = scenes_payload["summary"]
    assert agent_scene["scene"] == "executing"
    assert agent_scene["scene_projection_scope"] == "agent_top_level_projection"
    assert agent_scene["subagent_focus_tool"] == "grep"

    lanes = agent_scene["lanes"]
    # supervisor_task lane is preserved, NOT overwritten by the later user_chat push
    assert lanes["supervisor_task"]["scene"] == "learning"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 3
    assert lanes["supervisor_task"]["subagent_focus_tool"] == "read_file"
    assert lanes["supervisor_task"]["session_id"] == "supervisor-session"
    # user_chat lane holds its own data
    assert lanes["user_chat"]["scene"] == "executing"
    assert lanes["user_chat"]["subagent_foreground_count"] == 1
    assert summary["agent"] == "executing"
    assert summary["agent_user_chat"] == "executing"
    assert summary["agent_supervisor_task"] == "learning"

    active_cli = client.get("/").json()["active_cli_executor"]
    assert active_cli["session_id"] == "user-session"
    assert active_cli["scene_projection_scope"] == "agent_lane"
    assert active_cli["agent_lane"] == "user_chat"
    assert active_cli["subagent_focus_tool"] == "grep"


def test_gateway_agent_lane_scene_heuristic_fallback_without_role():
    """Older reporters without agent_role still route by scene heuristic."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    _post_agent_scene(
        client,
        "legacy-session",
        {
            "scene": "code_editing",
            "task_id": "body-1",
            "subagent_foreground_count": 2,
        },
    )
    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["scene"] == "code_editing"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 2
    assert lanes["user_chat"]["scene"] == "idle"


def test_gateway_agent_idle_clears_only_that_sessions_lane():
    """An idle push clears the lane the session owned, leaving the other intact."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    _post_agent_scene(
        client,
        "supervisor-session",
        {
            "scene": "learning",
            "agent_role": "supervisor_task",
            "subagent_foreground_count": 3,
        },
    )
    _post_agent_scene(
        client,
        "user-session",
        {
            "scene": "executing",
            "agent_role": "user_chat",
            "subagent_foreground_count": 1,
        },
    )

    # supervisor session goes idle -> only supervisor_task lane is cleared
    _post_agent_scene(
        client,
        "supervisor-session",
        {"scene": "idle", "agent_role": "supervisor_task"},
    )

    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["scene"] == "idle"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 0
    # user_chat lane untouched by the supervisor session's idle
    assert lanes["user_chat"]["scene"] == "executing"
    assert lanes["user_chat"]["subagent_foreground_count"] == 1


def test_gateway_deleting_session_clears_its_agent_lane():
    """Deleting a session blanks the lane it owned and drops it from the map."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/v1/sessions/register",
        json={"session_id": "sup-1", "model": "m", "provider": "p", "source": "cli"},
    )
    _post_agent_scene(
        client,
        "sup-1",
        {
            "scene": "learning",
            "agent_role": "supervisor_task",
            "subagent_foreground_count": 4,
        },
    )
    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 4

    session_info = client.get("/v1/sessions/sup-1").json()
    assert session_info["lease_status"] == "healthy"
    assert session_info["is_stale"] is False
    assert session_info["stale_after_seconds"] == gateway._active_cli_stale_after_seconds

    resp = client.request("DELETE", "/v1/sessions/sup-1")
    assert resp.status_code == 200

    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["scene"] == "idle"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 0
    assert gateway._agent_session_lane.get("sup-1") is None


def _register_supervisor(gateway, address="http://127.0.0.1:6002"):
    gateway._services["supervisor-1"] = ServiceInfo(
        service_id="supervisor-1",
        service_name="supervisor",
        service_type="supervisor",
        address=address,
        health_endpoint=f"{address}/health",
        registered_at=datetime.now(),
        last_health_check=datetime.now(),
        healthy=True,
    )


def test_completed_task_writeback_records_finding_to_tier1(monkeypatch):
    # P0-2 成果回流: a completed autonomous task carrying final_response + session_id
    # must trigger a Tier1 turn write so the agent's finding leaves the CLI.
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    gateway._agent_session_cache["cli-session-9"] = {"source": "cli", "last_used_at": datetime.now()}
    client = TestClient(gateway.app)

    recorded = {}
    forwarded = {}

    async def _fake_record(session_id, speaker, text, metadata=None):
        recorded["session_id"] = session_id
        recorded["speaker"] = speaker
        recorded["text"] = text
        recorded["metadata"] = metadata or {}

    monkeypatch.setattr(gateway, "_record_turn_to_tier1", _fake_record)

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._payload

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            forwarded["url"] = url
            forwarded["json"] = json or {}
            return _FakeResponse({"status": "completed"})

        def get(self, url, timeout=None):
            return _FakeResponse({
                "task_id": "learn-42",
                "status": "running",
                "governance_task_type": "self_learning",
                "metadata": {
                    "owner_session_id": "cli-session-9",
                    "execution_source": "cli_agent_pull",
                },
            })

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    response = client.post(
        "/v1/tasks/learn-42/complete",
        json={
            "decision": "completed",
            "reason": "done",
            "final_response": "Findings: X improves Y.",
            "session_id": "cli-session-9",
            "context": {"source": "cli_agent_pull", "execution_kind": "self_learning"},
        },
    )

    assert response.status_code == 200
    assert recorded["session_id"] == "cli-session-9"
    assert recorded["speaker"] == "agent"
    assert recorded["text"] == "Findings: X improves Y."
    assert recorded["metadata"]["task_id"] == "learn-42"
    assert recorded["metadata"]["execution_kind"] == "self_learning"
    assert forwarded["url"].endswith("/autonomous-chain/tasks/learn-42/decision")
    assert forwarded["json"]["final_response"] == "Findings: X improves Y."


def test_failed_task_writeback_does_not_record_finding(monkeypatch):
    # A non-completed decision must never write a Tier1 finding.
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    gateway._agent_session_cache["cli-session-9"] = {"source": "cli", "last_used_at": datetime.now()}
    client = TestClient(gateway.app)

    called = {"n": 0}

    async def _fake_record(*args, **kwargs):
        called["n"] += 1

    monkeypatch.setattr(gateway, "_record_turn_to_tier1", _fake_record)

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._payload

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            return _FakeResponse({"status": "failed"})

        def get(self, url, timeout=None):
            return _FakeResponse({
                "task_id": "learn-43",
                "status": "running",
                "governance_task_type": "self_learning",
                "metadata": {
                    "owner_session_id": "cli-session-9",
                    "execution_source": "cli_agent_pull",
                },
            })

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    response = client.post(
        "/v1/tasks/learn-43/decision",
        json={
            "decision": "failed",
            "final_response": "partial junk",
            "session_id": "cli-session-9",
        },
    )
    assert response.status_code == 200
    assert called["n"] == 0


def test_gateway_rejects_agent_pull_writeback_from_non_owner(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    gateway._agent_session_cache["cli-session-wrong"] = {
        "source": "cli",
        "last_used_at": datetime.now(),
    }
    client = TestClient(gateway.app)
    posted = {"called": False}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "task_id": "learn-44",
                "status": "running",
                "governance_task_type": "self_learning",
                "metadata": {
                    "owner_session_id": "cli-session-owner",
                    "execution_source": "cli_agent_pull",
                },
            }

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, timeout=None):
            return _FakeResponse()

        def post(self, url, json=None, timeout=None):
            posted["called"] = True
            return _FakeResponse()

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    response = client.post(
        "/v1/tasks/learn-44/complete",
        json={"decision": "completed", "session_id": "cli-session-wrong"},
    )

    assert response.status_code == 409
    assert posted["called"] is False


def test_improvement_report_forwards_to_supervisor(monkeypatch):
    # P0-2 成果回流 (body path): the gateway forwards an Agent improvement report
    # to the supervisor's /body/improvement-report endpoint.
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    client = TestClient(gateway.app)

    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"status": "reviewed", "score_delta": 12}

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr("systems.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    report = {
        "slot_id": "slot-B",
        "task_id": "imp-7",
        "commit_hash": "abc123",
        "diff_summary": "skills/foo.py | 3 +-",
        "changed_files": ["skills/foo.py"],
        "improvement_description": "Improved foo handling.",
    }
    response = client.post("/v1/body/improvement-report", json=report)

    assert response.status_code == 200
    assert response.json()["score_delta"] == 12
    assert captured["url"].endswith("/body/improvement-report")
    assert captured["json"]["slot_id"] == "slot-B"
    assert captured["json"]["commit_hash"] == "abc123"


def test_improvement_report_503_when_no_supervisor():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)
    response = client.post("/v1/body/improvement-report", json={"slot_id": "slot-B"})
    assert response.status_code == 503
