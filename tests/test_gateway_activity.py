from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.gateway.internal_gateway import GatewayConfig, InternalGateway


def test_gateway_activity_touch_endpoint_updates_snapshot():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.get("/admin/activity")
    assert response.status_code == 200
    initial = response.json()
    assert initial["last_user_request_at"] is None
    assert initial["last_self_learning_activity_at"] is None
    assert initial["last_self_evolution_activity_at"] is None
    assert initial["counts"]["self_learning_activity_count"] == 0
    assert initial["counts"]["self_evolution_activity_count"] == 0

    touch_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_evolution",
            "source_service": "supervisor",
        },
    )
    assert touch_response.status_code == 200

    updated = client.get("/admin/activity").json()
    assert updated["last_self_evolution_activity_at"] is not None
    assert updated["counts"]["self_evolution_activity_count"] == 1

    plan_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_evolution_plan",
            "source_service": "supervisor",
        },
    )
    execute_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_evolution_execute",
            "source_service": "executor",
        },
    )

    assert plan_response.status_code == 200
    assert execute_response.status_code == 200
    refined = client.get("/admin/activity").json()
    assert refined["last_self_evolution_plan_at"] is not None
    assert refined["last_self_evolution_execute_at"] is not None
    assert refined["counts"]["self_evolution_activity_count"] == 3
    assert refined["counts"]["self_evolution_plan_count"] == 1
    assert refined["counts"]["self_evolution_execute_count"] == 1
    assert refined["recent_metadata"]["self_evolution"]["source_service"] == "executor"
    assert refined["recent_metadata"]["self_evolution_plan"]["source_service"] == "supervisor"
    assert refined["recent_metadata"]["self_evolution_execute"]["source_service"] == "executor"


def test_gateway_activity_touch_derives_runtime_task_profile_from_broad_metadata():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_evolution_plan",
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
    metadata = activity["recent_metadata"]["self_evolution_plan"]
    assert metadata["trace_id"] == "trace-plan-1"
    assert metadata["task_type"] == "self_evolution"
    assert metadata["governance_task_type"] == "self_evolution"
    assert metadata["task_family"] == "body_switch"
    assert metadata["execution_kind"] == "body_switch"
    assert metadata["decision_id"] == "decision-plan-1"


def test_gateway_activity_touch_derives_runtime_task_profile_from_nested_runtime_profile():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_evolution_execute",
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
    metadata = activity["recent_metadata"]["self_evolution_execute"]
    assert metadata["trace_id"] == "trace-exec-2"
    assert metadata["governance_task_type"] == "memory_maintenance"
    assert metadata["task_family"] == "memory_maintenance"
    assert metadata["execution_kind"] == "memory_maintenance"
    assert metadata["decision_id"] == "decision-exec-2"


def test_gateway_activity_log_is_bounded_and_trace_filterable():
    gateway = InternalGateway(GatewayConfig(activity_log_limit=2))
    client = TestClient(gateway.app)

    first = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_evolution_plan",
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
            "activity_kind": "self_evolution_execute",
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
        "self_evolution_execute",
        "self_learning",
    ]
    assert all(event["metadata"].get("trace_id") != "trace-ignored" for event in log["events"])

    filtered = client.get(
        "/admin/activity/log",
        params={"trace_id": "trace-log-1", "limit": 10},
    ).json()
    assert filtered["count"] == 1
    assert filtered["events"][0]["activity_kind"] == "self_evolution_execute"
    assert filtered["events"][0]["metadata"]["trace_id"] == "trace-log-1"
    assert filtered["events"][0]["metadata"]["task_family"] == "body_switch"
    assert filtered["events"][0]["metadata"]["execution_kind"] == "body_switch"


def test_gateway_self_learning_route_updates_learning_activity_even_when_upstream_fails():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "self-learning-1",
            "service_name": "self-learning-service",
            "service_type": "self_learning",
            "address": "http://127.0.0.1:65526",
        },
    )
    assert register_response.status_code == 201

    health = client.get("/").json()
    assert health["registered_services"]["self_learning"] == 1

    routes = client.get("/admin/routes").json()
    self_learning_route = next(
        route for route in routes["routes"] if route["path_prefix"] == "/self-learning/"
    )
    assert self_learning_route["target_service"] == "self_learning"
    assert self_learning_route["target_instance"] == "self-learning-1"

    response = client.post("/api/self-learning/conclusions/submit", json={"summary": "noop"})

    assert response.status_code in {500, 504}
    activity = client.get("/admin/activity").json()
    assert activity["last_self_learning_activity_at"] is not None
    assert activity["counts"]["self_learning_activity_count"] == 1


def test_gateway_agent_query_updates_user_and_agent_activity_even_when_upstream_fails():
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
    assert response.status_code in {500, 504}

    activity = client.get("/admin/activity").json()
    assert activity["last_user_request_at"] is not None
    assert activity["last_agent_work_at"] is not None
    assert activity["counts"]["user_request_count"] == 1
    assert activity["counts"]["agent_work_count"] == 1


def test_gateway_memory_route_updates_memory_activity_even_when_upstream_fails():
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
    assert activity["last_memory_task_at"] is not None
    assert activity["counts"]["memory_task_count"] == 1


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
    assert activity["last_self_evolution_execute_at"] is not None
    assert activity["last_self_evolution_activity_at"] is not None
    assert activity["counts"]["self_evolution_execute_count"] == 1
    assert activity["counts"]["self_evolution_activity_count"] == 1
    assert activity["recent_metadata"]["self_evolution_execute"]["trace_id"] == "trace-exec-1"
    assert activity["recent_metadata"]["self_evolution_execute"]["task_type"] == "self_evolution"
    assert activity["recent_metadata"]["self_evolution_execute"]["governance_task_type"] == "self_evolution"
    assert activity["recent_metadata"]["self_evolution_execute"]["task_family"] == "body_switch"
    assert activity["recent_metadata"]["self_evolution_execute"]["execution_kind"] == "body_switch"
    assert activity["recent_metadata"]["self_evolution_execute"]["decision_id"] == "decision-exec-1"
    assert activity["recent_metadata"]["self_evolution_execute"]["task_id"] == "task-exec-1"
