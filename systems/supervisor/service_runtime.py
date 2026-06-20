from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("supervisor")


@dataclass(slots=True)
class ServiceRuntimeState:
    health_check_task: Optional[asyncio.Task[Any]] = None
    compression_task: Optional[asyncio.Task[Any]] = None
    self_evolution_review_task: Optional[asyncio.Task[Any]] = None
    endogenous_drive_task: Optional[asyncio.Task[Any]] = None
    started: bool = False


class ServiceRuntimeMixin:
    """Supervisor-local health polling and periodic maintenance runtime helpers."""

    def _initialize_service_runtime(self) -> None:
        self._service_runtime = ServiceRuntimeState()

    @property
    def _service_runtime_started(self) -> bool:
        return self._service_runtime.started

    @_service_runtime_started.setter
    def _service_runtime_started(self, started: bool) -> None:
        self._service_runtime.started = started

    @property
    def _health_check_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.health_check_task

    @_health_check_task.setter
    def _health_check_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.health_check_task = task

    @property
    def _compression_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.compression_task

    @_compression_task.setter
    def _compression_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.compression_task = task

    @property
    def _self_evolution_review_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.self_evolution_review_task

    @_self_evolution_review_task.setter
    def _self_evolution_review_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.self_evolution_review_task = task

    @property
    def _endogenous_drive_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.endogenous_drive_task

    @_endogenous_drive_task.setter
    def _endogenous_drive_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.endogenous_drive_task = task

    async def health_check(self) -> Dict[str, Any]:
        registry = self._body_registry.load_registry()
        return {
            "status": "healthy",
            "service": "supervisor",
            "agents": len(self._agents),
            "body_runtime": {
                "active_slot": registry.active_slot,
                "shell_slot": registry.shell_slot,
                "retired_slot": registry.retired_slot,
            },
        }

    async def run_health_checks(self, request: dict | None = None) -> Dict[str, Any]:
        results = []

        for instance_id, agent in self._agents.items():
            healthy = await self._check_agent_health(agent)
            agent.healthy = healthy
            agent.last_health_check = datetime.now()

            results.append(
                {
                    "instance_id": instance_id,
                    "name": agent.name,
                    "healthy": healthy,
                    "timestamp": datetime.now().isoformat(),
                }
            )

        return {"results": results}

    async def _wait_for_health(self, instance_id: str, timeout: int = 30) -> None:
        start = datetime.now()
        while (datetime.now() - start).total_seconds() < timeout:
            agent = self._agents.get(instance_id)
            if agent and agent.healthy:
                return
            await asyncio.sleep(2)

        raise TimeoutError(f"Agent {instance_id} failed to become healthy")

    async def _check_agent_health(self, agent: Any) -> bool:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{agent.port}/health"
                async with session.get(url, timeout=5) as response:
                    return response.status == 200
        except Exception:
            return False

    async def register_with_gateway(self) -> Optional[str]:
        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/register"
                payload = {
                    "service_name": "supervisor",
                    "service_type": "supervisor",
                    "address": f"http://{self.config.host}:{self.config.port}",
                    "health_endpoint": "/",
                    "metadata": {"version": "1.0"},
                }

                async with session.post(url, json=payload) as response:
                    if response.status == 201:
                        result = await response.json()
                        logger.info(f"Registered with gateway: {result}")
                        return result["service_id"]

        except Exception as exc:
            logger.warning(f"Failed to register with gateway: {exc}")
            return None

        return None

    async def _start_periodic_tasks(self) -> None:
        runtime_config = self.config.service_runtime
        if self._health_check_task:
            self._health_check_task.cancel()

        async def health_check_loop() -> None:
            while True:
                try:
                    await self.run_health_checks()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Health-check loop iteration failed: {exc}")
                await asyncio.sleep(runtime_config.health_check_interval)

        self._health_check_task = asyncio.create_task(health_check_loop())

        if self._compression_task:
            self._compression_task.cancel()

        async def compression_loop() -> None:
            while True:
                await asyncio.sleep(runtime_config.memory_compression_interval)
                try:
                    await self._memory_maintenance_executor.trigger_memory_compression()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Memory-compression loop iteration failed: {exc}")

        self._compression_task = asyncio.create_task(compression_loop())

        if self._self_evolution_review_task:
            self._self_evolution_review_task.cancel()

        async def self_evolution_review_loop() -> None:
            while True:
                await asyncio.sleep(runtime_config.self_evolution_review_interval)
                try:
                    await self._run_self_evolution_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Self-evolution review loop iteration failed: {exc}")

        self._self_evolution_review_task = asyncio.create_task(self_evolution_review_loop())

        if self._endogenous_drive_task:
            self._endogenous_drive_task.cancel()

        if runtime_config.endogenous_drive_enabled:
            async def endogenous_drive_loop() -> None:
                while True:
                    await asyncio.sleep(runtime_config.endogenous_drive_interval)
                    try:
                        await self._run_endogenous_drive_cycle()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"Endogenous-drive loop iteration failed: {exc}")

            self._endogenous_drive_task = asyncio.create_task(endogenous_drive_loop())
        else:
            self._endogenous_drive_task = None
        self._ensure_watch_window_task()
        self._service_runtime_started = True

    async def _stop_periodic_tasks(self) -> None:
        async def cancel_task(task: Optional[asyncio.Task[Any]]) -> None:
            if task is None:
                return
            try:
                if not task.done():
                    task.cancel()
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"Periodic runtime task exited with error during shutdown: {exc}")

        await cancel_task(self._health_check_task)
        self._health_check_task = None

        await cancel_task(self._compression_task)
        self._compression_task = None

        await cancel_task(self._self_evolution_review_task)
        self._self_evolution_review_task = None

        await cancel_task(self._endogenous_drive_task)
        self._endogenous_drive_task = None

        watch_window_task = getattr(self, "_watch_window_task", None)
        await cancel_task(watch_window_task)
        if hasattr(self, "_watch_window_task"):
            self._watch_window_task = None

        self._service_runtime_started = False
