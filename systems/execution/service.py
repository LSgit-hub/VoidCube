from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException

from .facade import VoidCubeExecutionFacade


class VoidCubeExecutionService:
    """Standard executor-facing API wrapper over the execution facade.

    This is intentionally thin: it gives gateway/CLI a future executor entry
    point without moving decision logic into the executor.
    """

    EXECUTOR_LEGAL_SCENES: frozenset = frozenset({"idle", "body_switch"})

    def __init__(
        self,
        facade: VoidCubeExecutionFacade,
    ) -> None:
        self.facade = facade
        self.app = FastAPI(title="VoidCube Executor", version="1.0")
        # ── Scene state (baseline §3.5 / §8.1) ──
        # The executor (body lifecycle mechanical surface) is the only
        # component that may report `body_switch`.  The supervisor and
        # Agent never report it (§3.6 边界).
        self._scene: str = "idle"
        self._scene_lock = asyncio.Lock()
        self._scene_changed_at: datetime = datetime.utcnow()
        self._scene_idle_timeout_seconds: float = 120.0
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.add_api_route("/", self.health_check, methods=["GET"])
        prefix = "/executor"
        self.app.add_api_route(f"{prefix}/body/registry", self.get_body_registry, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/active-target", self.get_active_body_target, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/slots", self.list_body_slots, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/slots/{{slot_id}}", self.get_body_slot, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/watch-window/status", self.get_watch_window_status, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/watch-window/evaluate", self.evaluate_watch_window, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/slots/{{slot_id}}/prepare", self.prepare_body_slot, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/slots/{{slot_id}}/candidate", self.mark_body_candidate, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/upgrade/execute", self.execute_body_upgrade, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/probe/report", self.record_body_probe_report, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/probe/run", self.run_body_probe, methods=["POST"])
        self.app.add_api_route(f"{prefix}/self-evolution/execute", self.execute_self_evolution_request, methods=["POST"])
        self.app.add_api_route(f"{prefix}/memory/compress", self.trigger_memory_compression, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/improvement-report", self.submit_body_improvement_report, methods=["POST"])
        self.app.add_api_route(f"{prefix}/body/slots/{{slot_id}}/health", self.get_slot_health, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/slots/{{slot_id}}/health/history", self.get_slot_health_history, methods=["GET"])
        self.app.add_api_route(f"{prefix}/body/slots/{{slot_id}}/health/reset", self.reset_slot_health, methods=["POST"])
        # ── Scene endpoints (baseline §8.1) ──
        self.app.add_api_route("/executor/scene", self.get_executor_scene, methods=["GET"])
        self.app.add_api_route("/executor/scene", self.set_executor_scene, methods=["POST"])

    async def health_check(self) -> Dict[str, Any]:
        await self._maybe_revert_stale_scene()
        return {
            "status": "healthy",
            "service": "executor",
            "boundary": "execution_only",
            "decision_policy": "external_governor_required",
            "facade": "VoidCubeExecutionFacade",
            "preferred_gateway_prefix": "/api/executor",
            "direct_executor_prefix": "/executor",
            "scene": self._scene,
            "scene_changed_at": self._scene_changed_at.isoformat(),
            "executor_access_policy": {
                "failure_mode": "executor_required",
            },
            "routes": {
                "body_lifecycle": [
                    "/body/slots/{slot_id}/prepare",
                    "/body/slots/{slot_id}/candidate",
                    "/body/probe/report",
                    "/body/probe/run",
                    "/body/watch-window/status",
                    "/body/watch-window/evaluate",
                ],
                "body_upgrade": ["/body/upgrade/execute"],
                "formal_self_evolution": ["/self-evolution/execute"],
                "self_learning": ["/self-learning/execute"],
                "memory_maintenance": ["/memory/compress"],
            },
            "compatibility_notes": {
                "self_learning": (
                    "legacy health advertisement only; autonomous self_learning "
                    "tasks execute through the API-A pull path, not this executor"
                ),
            },
        }

    # ── Scene control (baseline §8.1) ──

    async def get_executor_scene(self) -> Dict[str, Any]:
        await self._maybe_revert_stale_scene()
        return {
            "service": "executor",
            "scene": self._scene,
            "scene_changed_at": self._scene_changed_at.isoformat(),
            "legal_scenes": sorted(self.EXECUTOR_LEGAL_SCENES),
        }

    async def set_executor_scene(self, request: dict) -> Dict[str, Any]:
        scene = str(request.get("scene") or "").strip()
        if scene not in self.EXECUTOR_LEGAL_SCENES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid executor scene={scene!r}. Legal scenes: "
                    f"{sorted(self.EXECUTOR_LEGAL_SCENES)}"
                ),
            )
        async with self._scene_lock:
            self._scene = scene
            self._scene_changed_at = datetime.utcnow()
        return await self.get_executor_scene()

    async def _maybe_revert_stale_scene(self) -> None:
        """Auto-revert stale `body_switch` scene to `idle`.

        Body switches should complete (or fail) within a bounded window.
        If something hangs, this prevents the status bar from being
        stuck in `body_switch` forever.
        """
        if self._scene == "idle":
            return
        age = (datetime.utcnow() - self._scene_changed_at).total_seconds()
        if age < self._scene_idle_timeout_seconds:
            return
        async with self._scene_lock:
            if self._scene == "idle":
                return
            import logging
            logger = logging.getLogger("executor")
            logger.info(
                "Executor scene %r aged %.1fs (timeout=%.1fs); reverting to idle",
                self._scene, age, self._scene_idle_timeout_seconds,
            )
            self._scene = "idle"
            self._scene_changed_at = datetime.utcnow()

    async def get_body_registry(self) -> Dict[str, Any]:
        return self.facade.get_body_registry()

    async def get_active_body_target(self) -> Dict[str, Any]:
        return self.facade.get_active_body_target()

    async def list_body_slots(self) -> Dict[str, Any]:
        return self.facade.list_body_slots()

    async def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        return self.facade.get_body_slot(slot_id)

    async def get_watch_window_status(self) -> Dict[str, Any]:
        return self.facade.get_watch_window_status()

    async def evaluate_watch_window(self, request: Optional[dict] = Body(default=None)) -> Dict[str, Any]:
        return await self.facade.evaluate_watch_window(request)

    async def prepare_body_slot(
        self,
        slot_id: str,
        request: Optional[dict] = Body(default=None),
    ) -> Dict[str, Any]:
        return await self.facade.prepare_body_slot(slot_id, request)

    async def mark_body_candidate(
        self,
        slot_id: str,
        request: Optional[dict] = Body(default=None),
    ) -> Dict[str, Any]:
        return await self.facade.mark_body_candidate(slot_id, request)

    async def execute_body_upgrade(self, request: Optional[dict] = Body(default=None)) -> Dict[str, Any]:
        return await self.facade.execute_body_upgrade(request)

    async def execute_self_evolution_request(self, request: dict = Body(default_factory=dict)) -> Dict[str, Any]:
        try:
            return await self.facade.execute_self_evolution_request(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def record_body_probe_report(self, request: dict = Body(default_factory=dict)) -> Dict[str, Any]:
        return await self.facade.record_body_probe_report(request)

    async def run_body_probe(self, request: dict = Body(default_factory=dict)) -> Dict[str, Any]:
        return await self.facade.run_body_probe(request)

    async def trigger_memory_compression(self, request: Optional[dict] = Body(default=None)) -> Dict[str, Any]:
        return await self.facade.trigger_memory_compression(request)

    async def submit_body_improvement_report(self, request: dict = Body(default_factory=dict)) -> Dict[str, Any]:
        try:
            return await self.facade.submit_body_improvement_report(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def get_slot_health(self, slot_id: str) -> Dict[str, Any]:
        result = self.facade.get_slot_health(slot_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    async def get_slot_health_history(self, slot_id: str) -> Dict[str, Any]:
        result = self.facade.get_slot_health_history(slot_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    async def reset_slot_health(self, slot_id: str) -> Dict[str, Any]:
        result = self.facade.reset_slot_health(slot_id)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
