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
    self_evolution_review_task: Optional[asyncio.Task[Any]] = None
    endogenous_drive_task: Optional[asyncio.Task[Any]] = None
    started: bool = False
    governor_mode_active: bool = False
    structured_maintenance_task: Optional[asyncio.Task[Any]] = None
    # Scheduling visibility for the web UI countdown
    last_review_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    last_drive_at: Optional[datetime] = None
    next_drive_at: Optional[datetime] = None


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

    @property
    def _structured_maintenance_task(self) -> Optional[asyncio.Task[Any]]:
        return self._service_runtime.structured_maintenance_task

    @_structured_maintenance_task.setter
    def _structured_maintenance_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._service_runtime.structured_maintenance_task = task

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
        import asyncio as _asyncio

        execution_config = self.config.execution
        payload = {
            "service_name": "supervisor",
            "service_type": "supervisor",
            "address": f"http://{self.config.host}:{self.config.port}",
            "health_endpoint": "/",
            "metadata": {"version": "1.0"},
        }
        url = f"{execution_config.gateway_address}/register"

        max_retries = 5
        base_delay = 1.0  # seconds

        for attempt in range(1, max_retries + 1):
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=10) as response:
                        if response.status == 201:
                            result = await response.json()
                            logger.info(f"Registered with gateway (attempt %d): %s", attempt, result)
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
            "Failed to register with gateway after %d attempts at %s",
            max_retries,
            url,
        )
        return None

    async def _start_periodic_tasks(self) -> None:
        """Start the health-check loop only (Memory Mode default).

        The self-evolution review loop and endogenous drive loop are NOT
        started here — they are activated by _start_governor_mode() when
        the user or system enters Governor Mode.
        """
        runtime_config = self.config.service_runtime
        if self._health_check_task:
            self._health_check_task.cancel()

        async def health_check_loop() -> None:
            while True:
                try:
                    await self.run_health_checks()
                    # Re-register with gateway if the connection was lost
                    # (e.g., gateway restarted).  Idempotent — gateway
                    # deduplicates by service_name.
                    gid = getattr(self, '_gateway_service_id', None)
                    if not gid:
                        svc_id = await self.register_with_gateway()
                        if svc_id:
                            self._gateway_service_id = svc_id
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Health-check loop iteration failed: {exc}")
                await asyncio.sleep(runtime_config.health_check_interval)

        self._health_check_task = asyncio.create_task(health_check_loop())

        # ── Memory compression is now owned by the Memory Service ──
        # Per architecture baseline §3.4, Mem is responsible for its own
        # maintenance (compression, decay, summarisation).  The supervisor
        # only schedules / decides, not executes maintenance.
        # The memory service runs its own background compression loop via
        # its FastAPI lifespan.

        # Governor Mode loops are NOT started here — they are activated
        # on demand via _start_governor_mode().

        # ── Structured memory maintenance loop (Memory Mode auto-trigger) ──
        maintenance_interval = getattr(
            runtime_config, "structured_memory_maintenance_interval", 0
        )
        if maintenance_interval > 0:
            if self._structured_maintenance_task:
                self._structured_maintenance_task.cancel()

            async def structured_maintenance_loop() -> None:
                await asyncio.sleep(60)  # startup grace period
                while True:
                    try:
                        logger.debug("Running structured memory maintenance loop iteration")
                        facade = getattr(self, "_execution_facade", None)
                        if facade is not None:
                            await facade.trigger_memory_compression({})
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Structured memory maintenance loop iteration failed: %s", exc
                        )
                    await asyncio.sleep(maintenance_interval)

            self._structured_maintenance_task = asyncio.create_task(structured_maintenance_loop())
            logger.info("Structured memory maintenance loop started (interval=%ds)", maintenance_interval)

        self._ensure_watch_window_task()
        self._service_runtime_started = True

    async def _start_governor_mode(self) -> None:
        """Enter Governor Mode: start review and drive loops persistently.

        Idempotent — if Governor Mode is already active this is a no-op.
        """
        if self._service_runtime.governor_mode_active:
            return
        self._service_runtime.governor_mode_active = True
        runtime_config = self.config.service_runtime

        if self._self_evolution_review_task:
            self._self_evolution_review_task.cancel()

        async def self_evolution_review_loop() -> None:
            while True:
                now = datetime.now(timezone.utc)
                self._service_runtime.last_review_at = now
                self._service_runtime.next_review_at = now + timedelta(
                    seconds=runtime_config.self_evolution_review_interval
                )
                await asyncio.sleep(runtime_config.self_evolution_review_interval)
                try:
                    await self._run_self_evolution_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Self-evolution review loop iteration failed: {exc}")

        self._self_evolution_review_task = asyncio.create_task(self_evolution_review_loop())
        logger.info("Governor Mode: review loop started (interval=%ds)", runtime_config.self_evolution_review_interval)

        if self._endogenous_drive_task:
            self._endogenous_drive_task.cancel()

        if runtime_config.endogenous_drive_enabled:
            async def endogenous_drive_loop() -> None:
                while True:
                    now = datetime.now(timezone.utc)
                    self._service_runtime.last_drive_at = now
                    self._service_runtime.next_drive_at = now + timedelta(
                        seconds=runtime_config.endogenous_drive_interval
                    )
                    await asyncio.sleep(runtime_config.endogenous_drive_interval)
                    try:
                        await self._run_endogenous_drive_cycle()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"Endogenous-drive loop iteration failed: {exc}")

            self._endogenous_drive_task = asyncio.create_task(endogenous_drive_loop())
            logger.info("Governor Mode: drive loop started (interval=%ds)", runtime_config.endogenous_drive_interval)
        else:
            self._endogenous_drive_task = None
            logger.info("Governor Mode: drive loop disabled (endogenous_drive_enabled=False)")

    async def _stop_governor_mode(self) -> None:
        """Exit Governor Mode: stop review and drive loops immediately.

        Idempotent — if Governor Mode is not active this is a no-op.
        Does NOT stop the health-check loop.
        """
        if not self._service_runtime.governor_mode_active:
            return
        self._service_runtime.governor_mode_active = False

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
                logger.warning(f"Governor-mode task exited with error during deactivation: {exc}")

        await cancel_task(self._self_evolution_review_task)
        self._self_evolution_review_task = None

        await cancel_task(self._endogenous_drive_task)
        self._endogenous_drive_task = None

        logger.info("Governor Mode deactivated — returned to Memory Mode")

    def _governor_mode_status(self) -> Dict[str, Any]:
        """Return the current Governor Mode state."""
        return {
            "governor_mode_active": self._service_runtime.governor_mode_active,
            "review_loop_running": (
                self._service_runtime.self_evolution_review_task is not None
                and not self._service_runtime.self_evolution_review_task.done()
            ),
            "drive_loop_running": (
                self._service_runtime.endogenous_drive_task is not None
                and not self._service_runtime.endogenous_drive_task.done()
            ),
            "endogenous_drive_enabled": self.config.service_runtime.endogenous_drive_enabled,
        }

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

        await cancel_task(self._self_evolution_review_task)
        self._self_evolution_review_task = None

        await cancel_task(self._endogenous_drive_task)
        self._endogenous_drive_task = None

        watch_window_task = getattr(self, "_watch_window_task", None)
        await cancel_task(watch_window_task)
        if hasattr(self, "_watch_window_task"):
            self._watch_window_task = None

        await cancel_task(self._structured_maintenance_task)
        self._structured_maintenance_task = None

        self._service_runtime_started = False
