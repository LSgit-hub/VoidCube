from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

logger = logging.getLogger("supervisor")


class ProcessGatewayRuntimeMixin:
    """Supervisor runtime helpers for local process and gateway coordination."""

    async def list_agents(self):
        return {"agents": [agent.dict() for agent in self._agents.values()], "count": len(self._agents)}

    async def get_agent(self, instance_id: str):
        agent = self._agents.get(instance_id)
        if agent:
            return agent.dict()
        raise HTTPException(status_code=404, detail="Agent not found")

    async def _spawn_agent_process(self, agent) -> None:
        """Delegate to AgentProcessManager (S-01).

        Architecture baseline §3.6: Supervisor must not directly start/stop
        agent processes.  If ProcessManager is absent this fails explicitly
        rather than falling back to inline Popen.
        """
        pm = getattr(self, '_process_manager', None)
        if pm is None:
            raise RuntimeError(
                "AgentProcessManager not wired — supervisor cannot spawn "
                "agent processes directly per architecture baseline §3.6."
            )
        target = self._body_registry.load_active_body_pointer()
        pm.spawn(
            agent=agent,
            body_target=target,
            gateway_address=self.config.execution.gateway_address,
        )

    async def _terminate_agent_process(self, agent) -> None:
        """Delegate to AgentProcessManager (S-01)."""
        pm = getattr(self, '_process_manager', None)
        if pm is None:
            raise RuntimeError(
                "AgentProcessManager not wired — supervisor cannot terminate "
                "agent processes directly per architecture baseline §3.6."
            )
        pm.terminate(agent)

    async def _register_agent_with_gateway(self, agent) -> Optional[str]:
        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/register"
                payload = {
                    "service_name": agent.name,
                    "service_type": "agent",
                    "address": f"http://127.0.0.1:{agent.port}",
                    "health_endpoint": "/health",
                    "metadata": {
                        "slot_id": agent.slot_id,
                        "body_version": agent.version,
                    },
                }

                async with session.post(url, json=payload) as response:
                    if response.status == 201:
                        result = await response.json()
                        agent.gateway_service_id = result.get("service_id")
                        logger.info(f"Agent registered with gateway: {result}")
                        return result.get("service_id")

        except Exception as exc:
            logger.warning(f"Failed to register agent with gateway: {exc}")
            return None

        return None

    async def _sync_gateway_body_activation(self, instance_id: str) -> Optional[Dict[str, Any]]:
        agent = self._agents.get(instance_id)
        if not agent or not agent.slot_id:
            return None

        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/admin/body/activate"
                payload = {
                    "service_id": agent.gateway_service_id,
                    "slot_id": agent.slot_id,
                }
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
                    logger.warning(
                        "Gateway body activation sync failed for slot %s with status %s",
                        agent.slot_id,
                        response.status,
                    )
                    return None
        except Exception as exc:
            logger.warning(f"Failed to sync gateway body activation: {exc}")
            return None

    async def _unregister_agent_from_gateway(self, service_id: str) -> None:
        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/admin/services/{service_id}"
                async with session.delete(url) as response:
                    if response.status == 200:
                        logger.info(f"Agent unregistered from gateway: {service_id}")

        except Exception as exc:
            logger.warning(f"Failed to unregister agent from gateway: {exc}")
