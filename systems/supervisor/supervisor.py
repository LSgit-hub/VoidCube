import logging
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from systems.governor import GovernorRequest
from systems.supervisor.config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from systems.supervisor.planning_runtime import PlanningRuntimeMixin
from systems.supervisor.process_gateway_runtime import ProcessGatewayRuntimeMixin
from systems.supervisor.runtime_assemblers import (
    assemble_supervisor_execution_runtime,
    assemble_supervisor_runtime_state,
)
from systems.supervisor.service_runtime import ServiceRuntimeMixin
from systems.supervisor.trace_runtime import TraceRuntimeMixin
from systems.supervisor.ui_runtime import SupervisorUIMixin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("supervisor")


class AgentInstance(BaseModel):
    instance_id: str
    name: str
    pid: Optional[int] = None
    port: int
    status: str = "stopped"
    started_at: Optional[datetime] = None
    last_health_check: Optional[datetime] = None
    healthy: bool = False
    version: str = "unknown"
    slot_id: Optional[str] = None
    body_worktree: Optional[str] = None
    body_runtime: Optional[str] = None
    body_logs: Optional[str] = None
    gateway_service_id: Optional[str] = None


class HealthCheckResult(BaseModel):
    instance_id: str
    healthy: bool
    timestamp: datetime
    details: Dict[str, Any] = {}


@dataclass(slots=True)
class WatchWindowRuntimeState:
    task: Optional[Any] = None
    last_outcome: Optional[Dict[str, Any]] = None


class Supervisor(
    PlanningRuntimeMixin,
    ProcessGatewayRuntimeMixin,
    ServiceRuntimeMixin,
    TraceRuntimeMixin,
    SupervisorUIMixin,
):
    def __init__(self, config: SupervisorConfig | None = None):
        self.config = config or SupervisorConfig()
        self.app = FastAPI(
            title="VoidCube Supervisor",
            version="1.0",
            lifespan=self._app_lifespan,
        )
        self._subprocess_module = subprocess
        self._agent_model = AgentInstance
        self._agents: Dict[str, AgentInstance] = {}
        self._initialize_service_runtime()
        self._watch_window_runtime = WatchWindowRuntimeState()
        assemble_supervisor_runtime_state(self)
        self._initialize_supervisor_ui_runtime()
        assemble_supervisor_execution_runtime(self)
        self._setup_routes()

    @property
    def _watch_window_task(self) -> Optional[Any]:
        return self._watch_window_runtime.task

    @_watch_window_task.setter
    def _watch_window_task(self, task: Optional[Any]) -> None:
        self._watch_window_runtime.task = task

    @property
    def _watch_window_last_outcome(self) -> Optional[Dict[str, Any]]:
        return self._watch_window_runtime.last_outcome

    @_watch_window_last_outcome.setter
    def _watch_window_last_outcome(self, result: Optional[Dict[str, Any]]) -> None:
        self._watch_window_runtime.last_outcome = result

    def _setup_routes(self):
        async def execute_governor_review_request(request: dict):
            try:
                governor_request = GovernorRequest.model_validate(request)
                return self._governor_review_executor.execute_governor_request(governor_request)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        self.app.add_api_route("/", self.health_check, methods=["GET"])
        if self.config.ui_enabled:
            self.app.add_api_route(self.config.ui_path, self.get_supervisor_ui, methods=["GET"])
            self.app.add_api_route("/ui/state", self.get_supervisor_ui_state, methods=["GET"])
            self.app.add_api_route("/ui/events", self.get_supervisor_ui_events, methods=["GET"])
        self.app.add_api_route("/runtime/activity", self.get_runtime_activity, methods=["GET"])
        self.app.add_api_route("/runtime/timeline", self.get_runtime_timeline, methods=["GET"])
        self.app.add_api_route("/runtime/traces", self.list_runtime_traces, methods=["GET"])
        self.app.add_api_route("/runtime/traces/{trace_id}", self.get_runtime_trace, methods=["GET"])
        self.app.add_api_route("/runtime/idle-window/evaluate", self.evaluate_idle_window, methods=["POST"])
        self.app.add_api_route("/runtime/endogenous-drive/evaluate", self.evaluate_endogenous_drive, methods=["POST"])
        self.app.add_api_route("/self-evolution/tasks", self.list_self_evolution_tasks, methods=["GET"])
        self.app.add_api_route("/self-evolution/tasks", self.plan_self_evolution_task, methods=["POST"])
        self.app.add_api_route(
            "/self-evolution/tasks/review",
            self.review_self_evolution_tasks,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/self-learning/conclusions/submit",
            self.submit_self_learning_conclusion,
            methods=["POST"],
        )
        self.app.add_api_route("/self-evolution/tasks/{task_id}", self.get_self_evolution_task, methods=["GET"])
        self.app.add_api_route(
            "/self-evolution/tasks/{task_id}/decision",
            self.decide_self_evolution_task,
            methods=["POST"],
        )
        self.app.add_api_route("/agents", self.list_agents, methods=["GET"])
        self.app.add_api_route("/agents/{instance_id}", self.get_agent, methods=["GET"])
        self.app.add_api_route("/health/check", self.run_health_checks, methods=["POST"])
        self.app.add_api_route("/body/registry", self.get_body_registry, methods=["GET"])
        self.app.add_api_route("/body/active-target", self.get_active_body_target, methods=["GET"])
        self.app.add_api_route("/body/slots", self.list_body_slots, methods=["GET"])
        self.app.add_api_route("/body/slots/{slot_id}", self.get_body_slot, methods=["GET"])
        self.app.add_api_route("/body/review", execute_governor_review_request, methods=["POST"])
        self.app.add_api_route("/body/governor/history", self.get_governor_history, methods=["GET"])

    async def get_body_registry(self) -> Dict[str, Any]:
        return self._execution_facade.get_body_registry()

    async def list_body_slots(self) -> Dict[str, Any]:
        slots = self._execution_facade.list_body_slots()["slots"]
        return {
            "slots": list(slots.values()),
            "count": len(slots),
        }

    async def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        return self._execution_facade.get_body_slot(slot_id)

    async def get_active_body_target(self) -> Dict[str, Any]:
        return self._execution_facade.get_active_body_target()

    def _ensure_watch_window_task(self) -> None:
        self._watch_window_executor.ensure_watch_window_task()

    async def _watch_window_loop(self) -> None:
        await self._watch_window_executor.run_watch_window_loop()

    @asynccontextmanager
    async def _app_lifespan(self, app: FastAPI):
        del app
        await self.register_with_gateway()
        await self._start_periodic_tasks()
        self._maybe_open_supervisor_ui()
        try:
            yield
        finally:
            await self._stop_periodic_tasks()

    async def start(self):
        import uvicorn

        logger.info(f"Starting supervisor on {self.config.host}:{self.config.port}")
        await uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=self.config.host,
                port=self.config.port,
                log_level="info"
            )
        ).serve()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="VoidCube Supervisor")
    parser.add_argument("--host", default="127.0.0.1", help="Service host")
    parser.add_argument("--port", type=int, default=6002, help="Service port")
    parser.add_argument("--gateway", default="http://127.0.0.1:6000", help="Gateway address")
    args = parser.parse_args()
    
    config = SupervisorConfig(
        host=args.host,
        port=args.port,
        execution=SupervisorExecutionConfig(gateway_address=args.gateway),
    )
    supervisor = Supervisor(config)
    
    import asyncio
    asyncio.run(supervisor.start())
