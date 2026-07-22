import logging
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from systems.body_registry import BodyImprovementReport
from systems.governor import GovernorRequest
from systems.supervisor.autonomous_chain_contract import (
    AUTONOMOUS_CHAIN_CYCLE_ROUTE,
    AUTONOMOUS_CHAIN_TASKS_ROUTE,
    AUTONOMOUS_CHAIN_TASK_CLEAR_ROUTE,
    AUTONOMOUS_CHAIN_TASK_REVIEW_ROUTE,
    autonomous_chain_task_decision_route,
    autonomous_chain_task_route,
)
from systems.supervisor.config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from systems.supervisor.planning_runtime import PlanningRuntimeMixin
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



class Supervisor(
    PlanningRuntimeMixin,
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
        # Watch-window state is owned by executor adapter (§3.6 / S-02/03).
        # Supervisor holds a plain holder that gets proxied after assembly.
        self._watch_window_runtime: Any = type("_WatchWindowHolder", (), {
            "task": None, "last_outcome": None, "last_body_upgrade_trace_id": None,
        })()
        assemble_supervisor_runtime_state(self)
        self._initialize_supervisor_ui_runtime()
        assemble_supervisor_execution_runtime(self)
        try:
            self._recover_autonomous_chain_store_from_mem_governance()
        except Exception:
            logger.debug("Autonomous-chain Mem governance recovery skipped", exc_info=True)
        # Proxy supervisor._watch_window_runtime → adapter._state
        if hasattr(self, '_watch_window_executor'):
            self._watch_window_runtime = self._watch_window_executor._state
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
        self.app.add_api_route("/runtime/observation-input", self.get_runtime_observation_input, methods=["GET"])
        self.app.add_api_route("/runtime/timeline", self.get_runtime_timeline, methods=["GET"])
        self.app.add_api_route("/runtime/traces", self.list_runtime_traces, methods=["GET"])
        self.app.add_api_route("/runtime/traces/{trace_id}", self.get_runtime_trace, methods=["GET"])
        self.app.add_api_route("/runtime/drive-input/evaluate", self.evaluate_drive_input, methods=["POST"])
        self.app.add_api_route("/runtime/endogenous-drive/evaluate", self.evaluate_endogenous_drive, methods=["POST"])
        self.app.add_api_route("/runtime/endogenous-drive/events", self.get_endogenous_governance_events, methods=["GET"])
        self.app.add_api_route("/runtime/endogenous-drive/self-regulation", self.get_endogenous_self_regulation, methods=["GET"])
        self.app.add_api_route("/runtime/endogenous-drive/cognition", self.get_endogenous_cognition_state, methods=["GET"])
        self.app.add_api_route("/runtime/endogenous-drive/state", self.get_endogenous_governance_state, methods=["GET"])
        self.app.add_api_route(AUTONOMOUS_CHAIN_TASKS_ROUTE, self.list_autonomous_chain_tasks, methods=["GET"])
        self.app.add_api_route(AUTONOMOUS_CHAIN_TASKS_ROUTE, self.plan_autonomous_chain_task, methods=["POST"])
        self.app.add_api_route(
            AUTONOMOUS_CHAIN_TASK_CLEAR_ROUTE,
            self.clear_autonomous_chain_runtime,
            methods=["POST"],
        )
        self.app.add_api_route(
            AUTONOMOUS_CHAIN_TASK_REVIEW_ROUTE,
            self.review_autonomous_chain_tasks,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/autonomous-chain/recover-from-mem",
            self.recover_autonomous_chain_from_mem,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/self-learning/conclusions/submit",
            self.submit_self_learning_conclusion,
            methods=["POST"],
        )
        self.app.add_api_route(autonomous_chain_task_route("{task_id}"), self.get_autonomous_chain_task, methods=["GET"])
        self.app.add_api_route(
            autonomous_chain_task_decision_route("{task_id}"),
            self.decide_autonomous_chain_task,
            methods=["POST"],
        )
        self.app.add_api_route("/health/check", self.run_health_checks, methods=["POST"])
        self.app.add_api_route("/body/registry", self.get_body_registry, methods=["GET"])
        self.app.add_api_route("/body/active-target", self.get_active_body_target, methods=["GET"])
        self.app.add_api_route("/body/slots", self.list_body_slots, methods=["GET"])
        self.app.add_api_route("/body/slots/{slot_id}", self.get_body_slot, methods=["GET"])
        self.app.add_api_route("/body/review", execute_governor_review_request, methods=["POST"])
        self.app.add_api_route("/body/switch/consent", self.confirm_body_switch, methods=["POST"])
        self.app.add_api_route("/body/governor/history", self.get_governor_history, methods=["GET"])
        self.app.add_api_route("/body/improvement-report", self.receive_improvement_report, methods=["POST"])
        self.app.add_api_route(
            "/body/{slot_id}/improvement/rollback",
            self.rollback_body_improvement,
            methods=["POST"],
        )
        self.app.add_api_route("/body/{slot_id}/health", self.get_slot_health, methods=["GET"])
        self.app.add_api_route(
            AUTONOMOUS_CHAIN_CYCLE_ROUTE,
            self.run_autonomous_cycle,
            methods=["POST"],
        )
        self.app.add_api_route("/autonomous-chain-gate/activate", self.activate_autonomous_chain_gate, methods=["POST"])
        self.app.add_api_route("/autonomous-chain-gate/deactivate", self.deactivate_autonomous_chain_gate, methods=["POST"])
        self.app.add_api_route("/autonomous-chain-gate/status", self.get_autonomous_chain_gate_status, methods=["GET"])

    async def activate_autonomous_chain_gate(self, request: dict | None = None) -> Dict[str, Any]:
        """Ensure the autonomous chain runtime is active."""
        await self._start_autonomous_chain_gate()
        return self._autonomous_chain_gate_status()

    async def deactivate_autonomous_chain_gate(self, request: dict | None = None) -> Dict[str, Any]:
        """Disable the autonomous-chain runtime, keeping health-check alive."""
        del request
        await self._stop_autonomous_chain_gate()
        return self._autonomous_chain_gate_status()

    async def get_autonomous_chain_gate_status(self) -> Dict[str, Any]:
        """Return current autonomous-chain gate state."""
        return self._autonomous_chain_gate_status()

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

    async def confirm_body_switch(self, request: dict | None = None) -> Dict[str, Any]:
        return await self._execution_facade.confirm_body_switch(request)

    async def receive_improvement_report(self, report: dict) -> Dict[str, Any]:
        """Agent 提交替身改进报告 → 监督者审查评分"""
        from systems.body_registry import BodyImprovementReport
        parsed = BodyImprovementReport(**report)
        result = await self._review_body_improvement(parsed)
        return {"status": "reviewed", **result}

    async def rollback_body_improvement(
        self,
        slot_id: str,
        request: dict | None = None,
    ) -> Dict[str, Any]:
        return await self._execution_facade.rollback_body_improvement(slot_id, request)

    async def get_slot_health(self, slot_id: str) -> Dict[str, Any]:
        """查询指定槽位的健康值"""
        result = self._execution_facade.get_slot_health(slot_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=f"Slot {slot_id} not found")
        history = self._execution_facade.get_slot_health_history(slot_id)
        return {**result, "health_history": history.get("health_history", [])}

    def _ensure_watch_window_task(self) -> None:
        self._watch_window_executor.ensure_watch_window_task()

    async def _watch_window_loop(self) -> None:
        await self._watch_window_executor.run_watch_window_loop()

    @asynccontextmanager
    async def _app_lifespan(self, app: FastAPI):
        del app
        service_id = await self.register_with_gateway()
        if not service_id:
            logger.warning(
                "Supervisor started without gateway registration — "
                "gateway-mediated routes and activity tracking will be unavailable."
            )
        else:
            self._gateway_service_id = service_id
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


