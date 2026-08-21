"""Memory service status adapters for the Supervisor UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


@dataclass(frozen=True, slots=True)
class SupervisorUIMemoryStatusContext:
    """Gateway resource needed to load one bounded memory status snapshot."""

    gateway_url: str


def _memory_service_address(services_payload: Any) -> Any:
    services = (
        list(services_payload.values())
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

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{context.gateway_url}/admin/services",
                timeout=aiohttp.ClientTimeout(total=3),
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

            stats_data: Dict[str, Any] = {}
            rules_data: Dict[str, Any] = {}
            health_data: Dict[str, Any] = {}
            async with session.get(
                f"{memory_url}/tier1/stats",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as response:
                if response.status == 200:
                    stats_data = await response.json()
            async with session.get(
                f"{memory_url}/compressed/rules-status",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as response:
                if response.status == 200:
                    rules_data = await response.json()
            async with session.get(
                f"{memory_url}/health",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as response:
                if response.status == 200:
                    health_data = await response.json()

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
