from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("supervisor")


@dataclass(slots=True)
class ServiceRuntimeState:
    health_check_task: Optional[asyncio.Task[Any]] = None
    autonomous_chain_review_task: Optional[asyncio.Task[Any]] = None
    endogenous_drive_task: Optional[asyncio.Task[Any]] = None
    started: bool = False
    autonomous_chain_gate_active: bool = False
    # Observation cadence timestamps for API-B read-only monitoring
    last_review_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    last_drive_at: Optional[datetime] = None
    next_drive_at: Optional[datetime] = None
    suppress_candidate_refresh: bool = False


class ServiceRuntimeMixin:
    """Supervisor-local health polling and periodic maintenance runtime helpers."""

    def _initialize_service_runtime(self) -> None:
        self._service_runtime = ServiceRuntimeState()
        self._gateway_service_id: Optional[str] = None
        self._gateway_executor_service_id: Optional[str] = None

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
    def _autonomous_chain_review_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.autonomous_chain_review_task

    @_autonomous_chain_review_task.setter
    def _autonomous_chain_review_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.autonomous_chain_review_task = task

    @property
    def _endogenous_drive_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.endogenous_drive_task

    @_endogenous_drive_task.setter
    def _endogenous_drive_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.endogenous_drive_task = task

    async def health_check(self) -> Dict[str, Any]:
        body_integrity = self._body_registry.inspect_layout()
        registry = dict(body_integrity.get("registry") or {})
        return {
            "status": "healthy" if body_integrity["healthy"] else "degraded",
            "service": "supervisor",
            "agents": len(self._agents),
            "body_runtime": {
                "active_slot": registry.get("active_slot"),
                "shell_slot": registry.get("shell_slot"),
                "retired_slot": registry.get("retired_slot"),
                "healthy": body_integrity["healthy"],
                "violations": body_integrity["violations"],
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

        body_integrity = self._body_registry.inspect_layout()
        return {
            "healthy": body_integrity["healthy"]
            and all(result["healthy"] for result in results),
            "results": results,
            "body_runtime": body_integrity,
        }

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
        supervisor_id = await self._register_gateway_service_type("supervisor")
        if not supervisor_id:
            return None
        if not await self._register_gateway_service_type("executor"):
            logger.warning(
                "Supervisor registered without its embedded executor route; "
                "gateway /api/executor will remain unavailable until re-registration."
            )
        return supervisor_id

    def _gateway_registration_payload(self, service_type: str) -> Dict[str, Any]:
        address = f"http://{self.config.host}:{self.config.port}"
        if service_type == "supervisor":
            return {
                "service_name": "supervisor",
                "service_type": "supervisor",
                "address": address,
                "health_endpoint": "/",
                "metadata": {"version": "1.0"},
            }
        if service_type == "executor":
            return {
                "service_name": "executor",
                "service_type": "executor",
                "address": address,
                "health_endpoint": "/executor/health",
                "metadata": {"version": "1.0", "embedded_in": "supervisor"},
            }
        raise ValueError(f"Unsupported gateway service type: {service_type}")

    async def _register_gateway_service_type(
        self,
        service_type: str,
    ) -> Optional[str]:
        url = f"{self.config.execution.gateway_address}/register"
        service_id = await self._register_gateway_service(
            url,
            self._gateway_registration_payload(service_type),
        )
        if service_type == "supervisor":
            self._gateway_service_id = service_id
        elif service_type == "executor":
            self._gateway_executor_service_id = service_id
        return service_id

    async def _restore_gateway_registrations(
        self,
        missing_service_types: set[str],
    ) -> None:
        for service_type in ("supervisor", "executor"):
            if service_type not in missing_service_types:
                continue
            service_id = await self._register_gateway_service_type(service_type)
            if not service_id:
                logger.warning(
                    "Failed to restore %s gateway registration.",
                    service_type,
                )

    async def _missing_gateway_service_types(self) -> set[str]:
        registration_ids = {
            "supervisor": self._gateway_service_id,
            "executor": self._gateway_executor_service_id,
        }
        missing_service_types = {
            service_type
            for service_type, service_id in registration_ids.items()
            if not service_id
        }
        registered_service_ids = {
            service_type: service_id
            for service_type, service_id in registration_ids.items()
            if service_id
        }
        if not registered_service_ids:
            return missing_service_types

        import aiohttp

        gateway_address = self.config.execution.gateway_address
        try:
            async with aiohttp.ClientSession() as session:
                for service_type, service_id in registered_service_ids.items():
                    try:
                        async with session.get(
                            f"{gateway_address}/admin/services/{service_id}",
                            timeout=5,
                        ) as response:
                            if response.status != 200:
                                missing_service_types.add(service_type)
                    except Exception as exc:
                        logger.debug(
                            "Failed to verify %s gateway registration: %s",
                            service_type,
                            exc,
                        )
                        missing_service_types.add(service_type)
        except Exception as exc:
            logger.debug("Failed to create gateway verification session: %s", exc)
            missing_service_types.update(registered_service_ids)
        return missing_service_types

    async def _register_gateway_service(
        self,
        url: str,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        import asyncio as _asyncio

        max_retries = 5
        base_delay = 1.0  # seconds
        service_type = str(payload.get("service_type") or "service")

        for attempt in range(1, max_retries + 1):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=10) as response:
                        if response.status == 201:
                            result = await response.json()
                            logger.info(
                                "Registered %s with gateway (attempt %d): %s",
                                service_type,
                                attempt,
                                result,
                            )
                            return result["service_id"]
                        else:
                            logger.debug(
                                "Gateway registration attempt %d returned status %d",
                                attempt,
                                response.status,
                            )
            except Exception as exc:
                logger.debug("Gateway registration attempt %d failed: %s", attempt, exc)

            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))  # 1s, 2s, 4s, 8s, 16s
                logger.info(
                    "Waiting %.1fs before retrying gateway registration (attempt %d/%d)...",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await _asyncio.sleep(delay)

        logger.warning(
            "Failed to register %s with gateway after %d attempts at %s",
            service_type,
            max_retries,
            url,
        )
        return None

    async def _start_periodic_tasks(self) -> None:
        """Start baseline supervisor background tasks.

        This always starts the health-check loop. By default the autonomous
        chain also starts on boot, so Supervisor owns its drive/review cadence
        without waiting for the CLI /auto surface.
        """
        runtime_config = self.config.service_runtime
        if self._health_check_task:
            self._health_check_task.cancel()

        async def health_check_loop() -> None:
            while True:
                try:
                    await self.run_health_checks()
                    missing_service_types = (
                        await self._missing_gateway_service_types()
                    )
                    if missing_service_types:
                        await self._restore_gateway_registrations(
                            missing_service_types
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Health-check loop iteration failed: {exc}")
                await asyncio.sleep(runtime_config.health_check_interval)

        self._health_check_task = asyncio.create_task(health_check_loop())

        self._ensure_watch_window_task()
        self._service_runtime_started = True

    async def _start_autonomous_chain_gate(self) -> None:
        """Enable the autonomous chain and start review/drive loops.

        Idempotent — if the autonomous chain is already active this is a no-op.
        """
        if self._service_runtime.autonomous_chain_gate_active:
            await self._notify_gateway_autonomous_chain_gate(active=True)
            return
        self._service_runtime.autonomous_chain_gate_active = True
        await self._notify_gateway_autonomous_chain_gate(active=True)
        runtime_config = self.config.service_runtime

        if self._autonomous_chain_review_task:
            self._autonomous_chain_review_task.cancel()

        async def autonomous_chain_review_loop() -> None:
            delay = min(5, runtime_config.autonomous_chain_review_interval)
            while True:
                self._service_runtime.next_review_at = datetime.now(timezone.utc) + timedelta(
                    seconds=delay
                )
                await asyncio.sleep(delay)
                now = datetime.now(timezone.utc)
                self._service_runtime.last_review_at = now
                try:
                    await self._run_autonomous_chain_review_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Autonomous-chain review loop iteration failed: {exc}")
                delay = runtime_config.autonomous_chain_review_interval

        self._autonomous_chain_review_task = asyncio.create_task(autonomous_chain_review_loop())
        logger.info("Autonomous chain: review loop started (interval=%ds)", runtime_config.autonomous_chain_review_interval)

        if self._endogenous_drive_task:
            self._endogenous_drive_task.cancel()

        if runtime_config.endogenous_drive_enabled:
            async def endogenous_drive_loop() -> None:
                delay = min(2, runtime_config.endogenous_drive_interval)
                while True:
                    self._service_runtime.next_drive_at = datetime.now(timezone.utc) + timedelta(
                        seconds=delay
                    )
                    await asyncio.sleep(delay)
                    now = datetime.now(timezone.utc)
                    self._service_runtime.last_drive_at = now
                    try:
                        await self._run_endogenous_drive_cycle()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"Endogenous-drive loop iteration failed: {exc}")
                    delay = runtime_config.endogenous_drive_interval

            self._endogenous_drive_task = asyncio.create_task(endogenous_drive_loop())
            logger.info("Autonomous chain: drive loop started (interval=%ds)", runtime_config.endogenous_drive_interval)

        else:
            self._endogenous_drive_task = None
            logger.info("Autonomous chain: drive loop disabled (endogenous_drive_enabled=False)")

    async def _stop_autonomous_chain_gate(self) -> None:
        """Stop the autonomous chain review/drive loops immediately.

        Idempotent — if the autonomous chain is not active this is a no-op.
        Does NOT stop the health-check loop.
        """
        was_active = self._service_runtime.autonomous_chain_gate_active
        self._service_runtime.autonomous_chain_gate_active = False
        if was_active:
            await self._notify_gateway_autonomous_chain_gate(active=False)

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
                logger.warning(f"Autonomous chain task exited with error during deactivation: {exc}")

        await cancel_task(self._autonomous_chain_review_task)
        self._autonomous_chain_review_task = None
        self._service_runtime.next_review_at = None

        await cancel_task(self._endogenous_drive_task)
        self._endogenous_drive_task = None
        self._service_runtime.next_drive_at = None

        for task in self._autonomous_chain_store.list_api_a_running_tasks():
            self._update_task_status(
                task.task_id,
                status="failed",
                actor="supervisor_gate",
                reason="Autonomous-chain execution was interrupted when the gate was deactivated.",
                context={"failure_kind": "interrupted_by_gate_deactivation"},
                event_type="gate_deactivation_interruption",
            )

        logger.info("Autonomous chain stopped — baseline health-check loop still running")

    def _autonomous_chain_gate_status(self) -> Dict[str, Any]:
        """Return the current autonomous-chain gate state.

        The payload exposes the canonical autonomous-chain gate state.
        """
        return {
            "autonomous_chain_gate_active": self._service_runtime.autonomous_chain_gate_active,
            "review_loop_running": (
                self._service_runtime.autonomous_chain_review_task is not None
                and not self._service_runtime.autonomous_chain_review_task.done()
            ),
            "drive_loop_running": (
                self._service_runtime.endogenous_drive_task is not None
                and not self._service_runtime.endogenous_drive_task.done()
            ),
            "endogenous_drive_enabled": self.config.service_runtime.endogenous_drive_enabled,
        }

    async def _notify_gateway_autonomous_chain_gate(self, *, active: bool) -> None:
        """Notify the gateway that the autonomous-chain gate is active/inactive."""
        try:
            import aiohttp
            gateway_url = f"{self.config.execution.gateway_address}/admin/autonomous-chain-gate"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    gateway_url,
                    json={"active": active},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        logger.debug("Gateway autonomous-chain-gate notification returned %d", resp.status)
        except Exception as exc:
            logger.debug("Failed to notify gateway of autonomous chain gate change: %s", exc)

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

        await self._stop_autonomous_chain_gate()

        await cancel_task(self._health_check_task)
        self._health_check_task = None

        watch_window_task = getattr(self, "_watch_window_task", None)
        await cancel_task(watch_window_task)
        if hasattr(self, "_watch_window_task"):
            self._watch_window_task = None

        self._service_runtime_started = False

