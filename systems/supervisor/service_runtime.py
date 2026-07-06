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
    structured_maintenance_task: Optional[asyncio.Task[Any]] = None
    # Scheduling visibility for the web UI countdown
    last_review_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    last_drive_at: Optional[datetime] = None
    next_drive_at: Optional[datetime] = None
    suppress_candidate_refresh: bool = False


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
        """Start baseline supervisor background tasks.

        This always starts the health-check loop. The review loop and
        endogenous-drive loop remain behind the autonomous-chain gate and are
        activated by _start_autonomous_chain_gate().
        """
        runtime_config = self.config.service_runtime
        if self._health_check_task:
            self._health_check_task.cancel()

        async def health_check_loop() -> None:
            while True:
                try:
                    await self.run_health_checks()
                    # Re-register with gateway if the connection was lost
                    # (e.g., gateway restarted).  Verify registration is still
                    # valid by checking if our service_id is still known.
                    gid = getattr(self, '_gateway_service_id', None)
                    needs_reregister = not gid
                    if gid:
                        # Verify: Gateway may have restarted and lost our registration
                        try:
                            import aiohttp
                            gw = self.config.execution.gateway_address
                            async with aiohttp.ClientSession() as s:
                                async with s.get(f"{gw}/admin/services/{gid}", timeout=5) as r:
                                    needs_reregister = r.status != 200
                        except Exception:
                            needs_reregister = True
                    if needs_reregister:
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

        # Autonomous-chain loops are NOT started here — they are activated
        # on demand via _start_autonomous_chain_gate().

        # ── Structured memory maintenance loop (baseline background task) ──
        maintenance_interval = getattr(
            runtime_config, "structured_memory_maintenance_interval", 0
        )
        if maintenance_interval > 0:
            if self._structured_maintenance_task:
                self._structured_maintenance_task.cancel()

            async def structured_maintenance_loop() -> None:
                await asyncio.sleep(60)  # startup grace period
                base_interval = maintenance_interval
                current_interval = base_interval
                min_interval = max(600, base_interval // 6)    # min 10 min
                max_interval = min(86400, base_interval * 4)   # max 24 h
                last_event_count = 0
                while True:
                    try:
                        logger.debug("Running structured memory maintenance (interval=%ds)", current_interval)
                        facade = getattr(self, "_execution_facade", None)
                        if facade is not None:
                            result = await facade.trigger_memory_compression({})
                            # Adapt interval based on memory growth
                            structured = result.get("structured_maintenance", {}) if isinstance(result, dict) else {}
                            revision_count = int(structured.get("revision_count", 0) or 0)
                            if revision_count > 2:
                                current_interval = max(min_interval, current_interval // 2)
                            elif revision_count == 0:
                                current_interval = min(max_interval, current_interval * 2)
                            else:
                                current_interval = base_interval
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Structured memory maintenance loop iteration failed: %s", exc
                        )
                    await asyncio.sleep(current_interval)

            self._structured_maintenance_task = asyncio.create_task(structured_maintenance_loop())
            logger.info("Structured memory maintenance loop started (interval=%ds)", maintenance_interval)

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
            while True:
                now = datetime.now(timezone.utc)
                self._service_runtime.last_review_at = now
                self._service_runtime.next_review_at = now + timedelta(
                    seconds=runtime_config.autonomous_chain_review_interval
                )
                await asyncio.sleep(runtime_config.autonomous_chain_review_interval)
                try:
                    await self._run_autonomous_chain_review_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Autonomous-chain review loop iteration failed: {exc}")

        self._autonomous_chain_review_task = asyncio.create_task(autonomous_chain_review_loop())
        logger.info("Autonomous chain: review loop started (interval=%ds)", runtime_config.autonomous_chain_review_interval)

        # ── Immediate first review after drive has had time to produce candidates ──
        async def _immediate_first_review():
            await asyncio.sleep(5)  # wait for drive's immediate first-run
            await self._run_autonomous_chain_review_cycle()
        asyncio.create_task(_immediate_first_review())

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
            logger.info("Autonomous chain: drive loop started (interval=%ds)", runtime_config.endogenous_drive_interval)

            # ── Immediate first-run: fire the first cycle without waiting for the interval ──
            asyncio.create_task(self._run_immediate_endogenous_drive_once())
        else:
            self._endogenous_drive_task = None
            logger.info("Autonomous chain: drive loop disabled (endogenous_drive_enabled=False)")

    async def _run_immediate_endogenous_drive_once(self) -> None:
        await asyncio.sleep(2)  # short grace for gateway notification
        try:
            await self._run_endogenous_drive_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Immediate endogenous-drive cycle failed: {exc}")

    async def _stop_autonomous_chain_gate(self) -> None:
        """Stop the autonomous chain review/drive loops immediately.

        Idempotent — if the autonomous chain is not active this is a no-op.
        Does NOT stop the health-check loop.
        """
        if not self._service_runtime.autonomous_chain_gate_active:
            return
        self._service_runtime.autonomous_chain_gate_active = False
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

        await cancel_task(self._endogenous_drive_task)
        self._endogenous_drive_task = None

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

        await cancel_task(self._health_check_task)
        self._health_check_task = None

        await cancel_task(self._autonomous_chain_review_task)
        self._autonomous_chain_review_task = None

        await cancel_task(self._endogenous_drive_task)
        self._endogenous_drive_task = None

        watch_window_task = getattr(self, "_watch_window_task", None)
        await cancel_task(watch_window_task)
        if hasattr(self, "_watch_window_task"):
            self._watch_window_task = None

        await cancel_task(self._structured_maintenance_task)
        self._structured_maintenance_task = None

        self._service_runtime_started = False

