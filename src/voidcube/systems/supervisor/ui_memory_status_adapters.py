"""Memory service status adapters for the Supervisor UI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


@dataclass(frozen=True, slots=True)
class SupervisorUIMemoryStatusContext:
    """Gateway resource needed to load one bounded memory status snapshot."""

    gateway_url: str


def _memory_service_address(services_payload: Any) -> Any:
    services = (
        list(services_payload.get("services") or [])
        if isinstance(services_payload, dict)
        and "services" in services_payload
        else list(services_payload.values())
        if isinstance(services_payload, dict)
        else list(services_payload)
        if isinstance(services_payload, list)
        else []
    )
    for service in services:
        if isinstance(service, dict) and service.get("service_type") == "memory":
            return service.get("address")
    return None


def _memory_active(effective_activity_at: Any) -> bool:
    if not effective_activity_at:
        return False
    try:
        activity_at = datetime.fromisoformat(str(effective_activity_at))
        if activity_at.tzinfo is None:
            activity_at = activity_at.replace(tzinfo=timezone.utc)
        return activity_at > datetime.now(timezone.utc) - timedelta(seconds=7200)
    except Exception:
        return False


async def fetch_tier1_stats(
    *,
    context: SupervisorUIMemoryStatusContext,
) -> Dict[str, Any]:
    """Fetch Tier 1 stats and memory rule execution health for the UI."""

    try:
        import aiohttp
        from ...infrastructure.gateway.presence import gateway_auth_headers

        async with aiohttp.ClientSession() as session:
            gateway_request_kwargs: Dict[str, Any] = {
                "timeout": aiohttp.ClientTimeout(total=3),
            }
            gateway_headers = gateway_auth_headers()
            if gateway_headers:
                gateway_request_kwargs["headers"] = gateway_headers
            async with session.get(
                f"{context.gateway_url}/admin/services",
                **gateway_request_kwargs,
            ) as response:
                if response.status != 200:
                    return {
                        "memory_unavailable": True,
                        "memory_unavailable_reason": f"gateway_services_status_{response.status}",
                        "memory_active": False,
                    }
                services_payload = (await response.json()).get("services", {})

            memory_url = _memory_service_address(services_payload)
            if not memory_url:
                return {
                    "memory_unavailable": True,
                    "memory_unavailable_reason": "memory_service_not_registered",
                    "memory_active": False,
                }

            memory_error: str | None = None

            async def load_json(path: str) -> Dict[str, Any]:
                nonlocal memory_error
                try:
                    async with session.get(
                        f"{memory_url}{path}",
                        timeout=aiohttp.ClientTimeout(total=3),
                    ) as response:
                        if response.status == 200:
                            payload = await response.json()
                            return dict(payload) if isinstance(payload, dict) else {}
                except Exception as exc:
                    if memory_error is None:
                        memory_error = type(exc).__name__
                    return {}
                return {}

            stats_data, rules_data, health_data = await asyncio.gather(
                load_json("/tier1/stats"),
                load_json("/compressed/rules-status"),
                load_json("/health"),
            )

            if not stats_data and not rules_data and not health_data:
                return {
                    "memory_unavailable": True,
                    "memory_unavailable_reason": memory_error
                    or "memory_service_unavailable",
                    "memory_active": False,
                }

            result = dict(stats_data)
            result["rules"] = rules_data.get("rules", {})
            result["llm_healthy"] = rules_data.get("llm_healthy", False)
            result["llm_model"] = rules_data.get("llm_model")
            result["llm_error"] = rules_data.get("llm_error")
            result["effective_activity_at"] = rules_data.get("effective_activity_at")
            result["llm_health_checked_at"] = rules_data.get("llm_health_checked_at")
            result["maintenance_run"] = dict(
                rules_data.get("maintenance_run") or {}
            )
            maintenance = dict(health_data.get("maintenance") or {})
            tier2_bridge = dict(maintenance.get("tier2_bridge") or {})
            eligible_candidate_count = tier2_bridge.get("eligible_candidate_count")
            if eligible_candidate_count is not None:
                try:
                    result["pending_compression_count"] = max(
                        0, int(eligible_candidate_count)
                    )
                except Exception:
                    pass
            if "turn_count" not in result:
                for source in (health_data, stats_data, rules_data):
                    if isinstance(source, dict) and "turn_count" in source:
                        result["turn_count"] = source.get("turn_count")
                        break
            result["memory_active"] = _memory_active(
                rules_data.get("effective_activity_at")
            )
            result["transport_outboxes"] = dict(
                health_data.get("transport_outboxes")
                or health_data.get("agent_outbox")
                or {}
            )
            return result
    except Exception as exc:
        return {
            "memory_unavailable": True,
            "memory_unavailable_reason": type(exc).__name__,
            "memory_active": False,
        }
