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
        try:
            execution_config = self.config.execution
            env = os.environ.copy()
            env["AGENT_PORT"] = str(agent.port)
            env["GATEWAY_ADDRESS"] = execution_config.gateway_address
            target = self._body_registry.load_active_body_pointer()
            env["VOIDCUBE_ACTIVE_SLOT"] = target.slot_id
            env["VOIDCUBE_BODY_WORKTREE"] = target.worktree_path
            env["VOIDCUBE_BODY_RUNTIME"] = target.runtime_path
            env["VOIDCUBE_BODY_LOGS"] = target.logs_path
            env["VOIDCUBE_BODY_VERSION"] = target.body_version

            script_path = Path(target.launch_script_path)
            cwd = target.launch_cwd

            if sys.platform == "win32":
                process = self._subprocess_module.Popen(
                    [sys.executable, str(script_path), "--port", str(agent.port)],
                    env=env,
                    cwd=cwd,
                    creationflags=self._subprocess_module.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                process = self._subprocess_module.Popen(
                    [sys.executable, str(script_path), "--port", str(agent.port)],
                    env=env,
                    cwd=cwd,
                    start_new_session=True,
                )

            agent.pid = process.pid
            agent.status = "running"
            agent.started_at = datetime.now()
            agent.healthy = False
            agent.version = target.body_version
            agent.slot_id = target.slot_id
            agent.name = f"agent-{target.slot_id}"
            agent.body_worktree = target.worktree_path
            agent.body_runtime = target.runtime_path
            agent.body_logs = target.logs_path

            asyncio.create_task(self._monitor_agent(process, agent))

        except Exception as exc:
            logger.error(f"Failed to spawn agent process: {exc}")
            raise

    async def _terminate_agent_process(self, agent) -> None:
        if not agent.pid:
            return

        try:
            import psutil

            process = psutil.Process(agent.pid)
            children = process.children(recursive=True)
            for child in children:
                child.terminate()
            process.terminate()
            _, alive = psutil.wait_procs([process, *children], timeout=3)
            for remaining in alive:
                remaining.kill()
            return
        except Exception:
            pass

        if sys.platform == "win32":
            self._subprocess_module.run(["taskkill", "/F", "/T", "/PID", str(agent.pid)], check=False)
            return

        self._subprocess_module.run(["kill", "-TERM", str(agent.pid)], check=True)
        time.sleep(1)
        self._subprocess_module.run(["kill", "-KILL", str(agent.pid)], check=False)

    async def _monitor_agent(self, process, agent) -> None:
        while True:
            retcode = process.poll()
            if retcode is not None:
                agent.status = "exited"
                agent.healthy = False
                logger.warning(f"Agent {agent.name} exited with code {retcode}")

                if retcode != 0:
                    await asyncio.sleep(5)
                    await self._restart_agent(agent)

                break

            await asyncio.sleep(1)

    async def _restart_agent(self, agent) -> None:
        logger.info(f"Restarting failed agent: {agent.name}")
        agent.status = "restarting"

        try:
            await self._spawn_agent_process(agent)
            logger.info(f"Agent {agent.name} restarted successfully")
        except Exception as exc:
            logger.error(f"Failed to restart agent {agent.name}: {exc}")

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
