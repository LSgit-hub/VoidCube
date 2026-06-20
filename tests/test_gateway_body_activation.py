from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.gateway.internal_gateway import InternalGateway, GatewayConfig


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gateway_activate_body_syncs_api_route_to_slot_agent():
    gateway = InternalGateway(GatewayConfig())

    await gateway.register_service(
        FakeRequest({
            "service_id": "svc-old",
            "service_name": "agent-slot-A",
            "service_type": "agent",
            "address": "http://127.0.0.1:9001",
            "health_endpoint": "/health",
            "metadata": {"slot_id": "slot-A", "body_version": "v1"},
        })
    )
    initial_status = await gateway.get_body_status()
    assert initial_status["active_body"]["slot_id"] == "slot-A"
    assert initial_status["body_routing"]["api_route_target_instance"] == "svc-old"

    await gateway.register_service(
        FakeRequest({
            "service_id": "svc-new",
            "service_name": "agent-slot-B",
            "service_type": "agent",
            "address": "http://127.0.0.1:9002",
            "health_endpoint": "/health",
            "metadata": {"slot_id": "slot-B", "body_version": "v2"},
        })
    )

    result = await gateway.activate_body(FakeRequest({"slot_id": "slot-B"}))
    status = await gateway.get_body_status()
    routes = await gateway.list_routes()
    services = await gateway.list_services()

    assert result["status"] == "activated"
    assert result["active_body"]["slot_id"] == "slot-B"
    assert result["body_routing"]["api_route_target_instance"] == "svc-new"
    assert status["active_body"]["slot_id"] == "slot-B"
    assert status["body_routing"]["api_route_target_instance"] == "svc-new"
    assert any(
        body["lifecycle_state"] == "draining"
        for body in status["body_slots"]
        if body["slot_id"] == "slot-A"
    )
    assert any(service["lifecycle_state"] == "draining" for service in services["services"] if service["service_id"] == "svc-old")
    assert any(route["target_instance"] == "svc-new" for route in routes["routes"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_gateway_refuses_unhealthy_body_activation_without_route_drift():
    gateway = InternalGateway(GatewayConfig())

    await gateway.register_service(
        FakeRequest({
            "service_id": "svc-active",
            "service_name": "agent-slot-A",
            "service_type": "agent",
            "address": "http://127.0.0.1:9001",
            "health_endpoint": "/health",
            "metadata": {"slot_id": "slot-A", "body_version": "v1"},
        })
    )
    await gateway.register_service(
        FakeRequest({
            "service_id": "svc-unhealthy",
            "service_name": "agent-slot-B",
            "service_type": "agent",
            "address": "http://127.0.0.1:9002",
            "health_endpoint": "/health",
            "metadata": {"slot_id": "slot-B", "body_version": "v2"},
        })
    )
    await gateway.update_health("svc-unhealthy", FakeRequest({"healthy": False}))

    with pytest.raises(Exception) as exc_info:
        await gateway.activate_body(FakeRequest({"slot_id": "slot-B"}))

    status = await gateway.get_body_status()
    routes = await gateway.list_routes()

    assert getattr(exc_info.value, "status_code", None) == 503
    assert status["active_body"]["slot_id"] == "slot-A"
    assert status["body_routing"]["api_route_target_instance"] == "svc-active"
    api_route = next(route for route in routes["routes"] if route["path_prefix"] == "/api/")
    assert api_route["target_instance"] == "svc-active"
