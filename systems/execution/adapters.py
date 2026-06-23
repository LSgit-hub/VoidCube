from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
from pathlib import Path
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, MutableMapping, Optional, Protocol

from fastapi import HTTPException

from systems.governor import GovernorRequest
from systems.probe import ProbeReport
from systems.runtime_task_profile import derive_runtime_task_profile
from systems.self_learning import (
    SelfLearningService,
    SelfLearningSkillDelegate,
)

logger = logging.getLogger(__name__)


class WatchWindowRuntimeStateProtocol(Protocol):
    task: Optional[asyncio.Task[Any]]
    last_outcome: Optional[Dict[str, Any]]
    last_body_upgrade_trace_id: Optional[str]


class GovernorRequestExecutorProtocol(Protocol):
    def execute_governor_request(self, governor_request: GovernorRequest) -> Dict[str, Any]: ...


class WatchWindowRuntimeSyncProtocol(Protocol):
    def sync_runtime_after_governor_response(self, governor_response: Any) -> Dict[str, Any]: ...


class AgentLifecycleExecutionAdapter:
    """Execution boundary for starting, stopping, and switching agent processes."""

    def __init__(
        self,
        *,
        config: Any,
        agents: MutableMapping[str, Any],
        agent_model: type,
        spawn_agent_process: Callable[[Any], Awaitable[None]],
        terminate_agent_process: Callable[[Any], Awaitable[None]],
        register_agent_with_gateway: Callable[[Any], Awaitable[Any]],
        unregister_agent_from_gateway: Callable[[str], Awaitable[None]],
        attach_execution_route_hint: Callable[[Dict[str, Any], str], Dict[str, Any]],
    ) -> None:
        self.config = config
        self.agents = agents
        self.agent_model = agent_model
        self.spawn_agent_process = spawn_agent_process
        self.terminate_agent_process = terminate_agent_process
        self.register_agent_with_gateway = register_agent_with_gateway
        self.unregister_agent_from_gateway = unregister_agent_from_gateway
        self.attach_execution_route_hint = attach_execution_route_hint
        self._agent_counter = 0

    def get_body_registry(self) -> Dict[str, Any]:
        registry = self.body_registry.load_registry()
        slots = self.body_registry.list_slots()
        return {
            "registry": registry.model_dump(mode="json"),
            "slots": {
                slot_id: meta.model_dump(mode="json")
                for slot_id, meta in slots.items()
            },
        }

    def get_active_body_target(self) -> Dict[str, Any]:
        return self.body_registry.load_active_body_pointer().model_dump(mode="json")

    def list_body_slots(self) -> Dict[str, Any]:
        slots = self.body_registry.list_slots()
        return {
            "slots": {
                slot_id: meta.model_dump(mode="json")
                for slot_id, meta in slots.items()
            }
        }

    def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        try:
            return self.body_registry.load_slot_meta(slot_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Slot not found: {slot_id}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def start_agent(self, request: dict, *, agent_counter: int) -> tuple[Dict[str, Any], int]:
        if "color" in request:
            raise HTTPException(
                status_code=400,
                detail="Legacy color-based agent selection has been removed. Start from the active body pointer.",
            )
        next_counter = agent_counter + 1
        port = self.config.agent_base_port + next_counter

        instance_id = str(uuid.uuid4())
        agent = self.agent_model(
            instance_id=instance_id,
            name=f"managed-agent-{next_counter}",
            port=port,
            status="starting",
        )

        await self.spawn_agent_process(agent)
        self.agents[instance_id] = agent
        await asyncio.sleep(2)
        await self.register_agent_with_gateway(agent)

        return (
            self.attach_execution_route_hint(
                {
                    "instance_id": instance_id,
                    "status": "started",
                    "port": port,
                    "slot_id": agent.slot_id,
                    "body_version": agent.version,
                },
                "agents.start",
            ),
            next_counter,
        )

    async def start_managed_agent(self, request: dict) -> Dict[str, Any]:
        result, next_counter = await self.start_agent(
            request,
            agent_counter=self._agent_counter,
        )
        self._agent_counter = next_counter
        return result

    async def stop_agent(self, instance_id: str) -> Dict[str, Any]:
        agent = self.agents.get(instance_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        await self.terminate_agent_process(agent)
        agent.status = "stopped"
        agent.pid = None
        agent.healthy = False

        if agent.gateway_service_id:
            await self.unregister_agent_from_gateway(agent.gateway_service_id)

        return self.attach_execution_route_hint({"status": "stopped"}, "agents.stop")

    async def activate_body(self, request: dict) -> Dict[str, Any]:
        slot_id = request.get("slot_id")
        service_id = request.get("service_id")
        if not slot_id and not service_id:
            raise HTTPException(status_code=400, detail="slot_id or service_id is required")

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/admin/body/activate"
                payload = {}
                if slot_id:
                    payload["slot_id"] = slot_id
                if service_id:
                    payload["service_id"] = service_id

                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        return self.attach_execution_route_hint(result, "body.activate")

            raise HTTPException(status_code=500, detail="Body activation failed")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))


class GovernorReviewExecutionAdapter:
    """Coordinator for governor-reviewed body lifecycle transitions."""

    def __init__(
        self,
        *,
        body_registry: Any,
        governor: Any,
        lifecycle: Any,
        watch_window_runtime_sync: WatchWindowRuntimeSyncProtocol,
    ) -> None:
        self.body_registry = body_registry
        self.governor = governor
        self.lifecycle = lifecycle
        self.watch_window_runtime_sync = watch_window_runtime_sync

    def execute_governor_request(self, governor_request: GovernorRequest) -> Dict[str, Any]:
        slot_meta = None
        try:
            slot_meta = self.body_registry.load_slot_meta(governor_request.body_id)
        except (FileNotFoundError, ValueError):
            slot_meta = None

        self._attach_probe_evidence(governor_request, slot_meta)

        governor_response = self.governor.review(
            governor_request,
            slot_meta=slot_meta,
        )
        execution_report = self.lifecycle.apply_governor_response(governor_response)
        registry = self.body_registry.load_registry()
        runtime_followup = self.watch_window_runtime_sync.sync_runtime_after_governor_response(
            governor_response
        )
        self.governor.record_execution_outcome(
            request=governor_request,
            response=governor_response,
            execution_report=execution_report,
            registry=registry,
        )

        return {
            "request": governor_request.model_dump(mode="json"),
            "governor_response": governor_response.model_dump(mode="json"),
            "execution_report": execution_report.model_dump(mode="json"),
            "runtime_followup": runtime_followup,
            "registry": registry.model_dump(mode="json"),
        }

    def _attach_probe_evidence(self, governor_request: GovernorRequest, slot_meta: Any) -> None:
        if not slot_meta or governor_request.event_type not in {"switch_request", "health_review_request"}:
            return

        if "probe_report" in governor_request.evidence or not slot_meta.last_probe_result:
            return

        target_transition = governor_request.constraints.get("target_transition")
        if governor_request.event_type != "switch_request" and target_transition != "probe_to_active":
            return

        governor_request.evidence["probe_report"] = slot_meta.last_probe_result
        governor_request.evidence.setdefault(
            "probe_passed",
            bool(slot_meta.last_probe_result.get("overall_passed")),
        )


@dataclass
class WatchWindowState:
    """Watch-window runtime state owned by the executor (S-02/S-03)."""
    task: Optional[Any] = None
    last_outcome: Optional[Dict[str, Any]] = None
    last_body_upgrade_trace_id: Optional[str] = None


class WatchWindowExecutionAdapter:
    """Execution boundary for watch-window runtime mechanics and cleanup.

    Owns its runtime state directly (S-02/S-03) — the supervisor no longer
    injects a ``runtime_state`` protocol.
    """

    def __init__(
        self,
        *,
        body_registry: Any,
        agents: MutableMapping[str, Any],
        stop_agent: Callable[[str], Awaitable[Dict[str, Any]]],
        run_health_checks: Callable[[], Awaitable[Dict[str, Any]]],
        runtime_state: Any = None,  # deprecated; state is now self-owned (S-02/03)
        sync_gateway_body_activation: Optional[
            Callable[[str], Awaitable[Dict[str, Any] | None]]
        ] = None,
        governor_request_executor: Optional[GovernorRequestExecutorProtocol] = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.body_registry = body_registry
        self.agents = agents
        self.stop_agent = stop_agent
        self.run_health_checks = run_health_checks
        self._state = WatchWindowState()
        self.sync_gateway_body_activation = sync_gateway_body_activation
        self.governor_request_executor = governor_request_executor
        self.poll_interval_seconds = poll_interval_seconds

    def bind_governor_request_executor(
        self,
        governor_request_executor: GovernorRequestExecutorProtocol,
    ) -> None:
        self.governor_request_executor = governor_request_executor

    def _get_task(self) -> Optional[asyncio.Task[Any]]:
        return self._state.task

    def _set_task(self, task: Optional[asyncio.Task[Any]]) -> None:
        self._state.task = task

    def _is_task_running(self) -> bool:
        task = self._get_task()
        return bool(task and not task.done())

    def _get_last_outcome(self) -> Optional[Dict[str, Any]]:
        return self._state.last_outcome

    def _set_last_outcome(self, result: Dict[str, Any]) -> None:
        self._state.last_outcome = result

    def _execute_governor_request(self, governor_request: GovernorRequest) -> Dict[str, Any]:
        if self.governor_request_executor is None:
            raise RuntimeError("WatchWindowExecutionAdapter is not bound to a governor request executor.")
        return self.governor_request_executor.execute_governor_request(governor_request)

    def ensure_watch_window_task(self) -> Optional[asyncio.Task[Any]]:
        task = self._get_task()
        if task and not task.done():
            return task

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return None

        task = asyncio.create_task(self.run_watch_window_loop())
        self._set_task(task)
        return task

    def sync_runtime_after_governor_response(self, governor_response: Any) -> Dict[str, Any]:
        decision = getattr(governor_response, "decision", None)
        if decision != "approve_with_watch":
            return {
                "status": "no_watch_window_runtime_change",
                "decision": decision,
            }

        existing_task = self._get_task()
        task = self.ensure_watch_window_task()
        return {
            "status": "watch_window_runtime_ensured",
            "decision": decision,
            "task_running": bool(task and not task.done()),
            "task_created": bool(task is not None and task is not existing_task),
        }

    async def run_watch_window_loop(self) -> None:
        current_task = asyncio.current_task()
        if current_task is not None and self._get_task() is None:
            self._set_task(current_task)

        try:
            while True:
                try:
                    poll = await self.poll_watch_window()
                    if poll.get("should_evaluate"):
                        await self.evaluate_watch_window(dict(poll.get("request") or {}))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Watch-window loop iteration failed: {exc}")

                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            if current_task is not None and self._get_task() is current_task:
                self._set_task(None)

    def get_watch_window_status(self) -> Dict[str, Any]:
        registry = self.body_registry.load_registry()
        return {
            "watch_window": registry.watch_window.model_dump(mode="json"),
            "task_running": self._is_task_running(),
            "last_outcome": self._get_last_outcome(),
        }

    def build_watch_window_evidence(
        self,
        *,
        instance_id: Optional[str] = None,
        healthy_override: Optional[bool] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        registry = self.body_registry.load_registry()
        active_instance_id = instance_id
        active_agent = None

        if active_instance_id:
            active_agent = self.agents.get(active_instance_id)
        elif self.agents:
            running = [agent for agent in self.agents.values() if agent.status == "running"]
            if running:
                active_agent = running[-1]
                active_instance_id = active_agent.instance_id

        healthy = (
            bool(healthy_override)
            if healthy_override is not None
            else bool(active_agent.healthy if active_agent else True)
        )
        observation = {
            "active_slot": registry.active_slot,
            "retired_slot": registry.retired_slot,
            "watch_window_status": registry.watch_window.status,
            "active_instance_id": active_instance_id,
            "active_agent_status": active_agent.status if active_agent else None,
            "active_agent_healthy": active_agent.healthy if active_agent else None,
            "metrics": metrics or {},
            "evaluated_at": datetime.utcnow().isoformat(),
        }
        return {
            "healthy": healthy,
            "observation": observation,
            "registry": registry,
            "active_instance_id": active_instance_id,
        }

    async def evaluate_watch_window(self, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request = request or {}
        evaluation = self.build_watch_window_evidence(
            instance_id=request.get("instance_id"),
            healthy_override=request.get("healthy_override"),
            metrics=request.get("metrics"),
        )
        registry = evaluation["registry"]
        retired_slot = registry.retired_slot
        if not retired_slot:
            raise HTTPException(status_code=400, detail="No retired slot is available for watch-window evaluation.")

        body_id = registry.active_slot or ""
        if not body_id:
            raise HTTPException(status_code=400, detail="No active slot is registered for watch-window evaluation.")

        trace_id = request.get("trace_id") or self._state.last_body_upgrade_trace_id
        if evaluation["healthy"]:
            governor_request = GovernorRequest(
                request_id=request.get("request_id", f"watch-pass-{uuid.uuid4()}"),
                trace_id=trace_id,
                task_type="self_evolution",
                event_type="post_switch_review",
                body_id=retired_slot,
                source_actor="supervisor_watch_window",
                summary="Watch window passed for the new active body.",
                evidence={
                    "watch_window_passed": True,
                    "active_body_healthy": True,
                    "watch_window_observation": evaluation["observation"],
                },
            )
        else:
            governor_request = GovernorRequest(
                request_id=request.get("request_id", f"watch-fail-{uuid.uuid4()}"),
                trace_id=trace_id,
                task_type="self_evolution",
                event_type="rollback_request",
                body_id=body_id,
                source_actor="supervisor_watch_window",
                summary="Watch window failed for the new active body.",
                evidence={
                    "watch_window_failed": True,
                    "active_body_healthy": False,
                    "watch_window_observation": evaluation["observation"],
                },
                constraints={"retired_slot": retired_slot},
            )

        outcome = self._execute_governor_request(governor_request)
        result = {
            "status": "watch_window_evaluated",
            "evaluation": {
                "healthy": evaluation["healthy"],
                "observation": evaluation["observation"],
                "active_instance_id": evaluation["active_instance_id"],
            },
            **outcome,
        }
        self._set_last_outcome(result)
        result["execution_followup"] = await self.reconcile_watch_window_outcome(
            result=result,
            previous_retired_slot=retired_slot,
        )
        return result

    async def poll_watch_window(self) -> Dict[str, Any]:
        registry = self.body_registry.load_registry()
        if (
            registry.watch_window.status != "active"
            or not registry.retired_slot
            or not registry.active_slot
        ):
            return {
                "should_evaluate": False,
                "reason": "inactive",
            }

        now = datetime.utcnow()
        expired = (
            registry.watch_window.expires_at is not None
            and now >= registry.watch_window.expires_at
        )

        running_agents = [agent for agent in self.agents.values() if agent.status == "running"]
        if running_agents:
            await self.run_health_checks()

        healthy = True
        if running_agents:
            healthy = any(agent.healthy for agent in running_agents)

        if not healthy:
            return {
                "should_evaluate": True,
                "request": {
                    "healthy_override": False,
                    "metrics": {"reason": "automatic_watch_window_health_failure"},
                },
            }

        if expired:
            return {
                "should_evaluate": True,
                "request": {
                    "healthy_override": True,
                    "metrics": {"reason": "automatic_watch_window_expired_cleanly"},
                },
            }

        return {
            "should_evaluate": False,
            "reason": "waiting",
        }

    def _running_agents_for_slot(self, slot_id: Optional[str]) -> list[Any]:
        if not slot_id:
            return []
        return [
            agent
            for agent in self.agents.values()
            if agent.status == "running" and agent.slot_id == slot_id
        ]

    async def stop_agents_for_slot(
        self,
        slot_id: Optional[str],
        *,
        exclude_instance_ids: Optional[set[str]] = None,
    ) -> list[str]:
        exclude_instance_ids = exclude_instance_ids or set()
        stopped: list[str] = []
        for agent in list(self._running_agents_for_slot(slot_id)):
            if agent.instance_id in exclude_instance_ids:
                continue
            await self.stop_agent(agent.instance_id)
            stopped.append(agent.instance_id)
        return stopped

    async def reconcile_watch_window_outcome(
        self,
        *,
        result: Dict[str, Any],
        previous_retired_slot: Optional[str],
    ) -> Dict[str, Any]:
        decision = result.get("governor_response", {}).get("decision")

        if decision == "approve":
            stopped_instances = await self.stop_agents_for_slot(previous_retired_slot)
            return {
                "action": "retired_slot_recycled",
                "slot_id": previous_retired_slot,
                "stopped_instance_ids": stopped_instances,
            }

        if decision == "rollback_required":
            failed_slot = result.get("request", {}).get("body_id")
            restored_slot = self._restored_slot_after_rollback(result, failed_slot)
            restored_agent = self._latest_running_agent_for_slot(restored_slot)
            gateway_activation = None
            if restored_agent is not None and self.sync_gateway_body_activation is not None:
                gateway_activation = await self.sync_gateway_body_activation(
                    restored_agent.instance_id
                )
            stopped_instances = await self.stop_agents_for_slot(failed_slot)
            return {
                "action": "failed_slot_drained",
                "slot_id": failed_slot,
                "restored_slot_id": restored_slot,
                "restored_instance_id": (
                    restored_agent.instance_id if restored_agent is not None else None
                ),
                "gateway_activation": gateway_activation,
                "stopped_instance_ids": stopped_instances,
            }

        return {
            "action": "noop",
            "slot_id": None,
            "stopped_instance_ids": [],
        }

    def _restored_slot_after_rollback(
        self,
        result: Dict[str, Any],
        failed_slot: Optional[str],
    ) -> Optional[str]:
        registry = result.get("registry")
        if isinstance(registry, dict):
            active_slot = registry.get("active_slot")
            if active_slot and active_slot != failed_slot:
                return str(active_slot)

        try:
            current_registry = self.body_registry.load_registry()
        except Exception:
            return None

        active_slot = getattr(current_registry, "active_slot", None)
        if active_slot and active_slot != failed_slot:
            return str(active_slot)
        return None

    def _latest_running_agent_for_slot(self, slot_id: Optional[str]) -> Any | None:
        running_agents = self._running_agents_for_slot(slot_id)
        if not running_agents:
            return None
        return running_agents[-1]



    # NOTE(E-04): Execution results should be systematically written back to
    # Mem via the governance bridge.  Currently only governor_review does this;
    # body_upgrade and self_learning adapters should also record outcomes.
    # See baseline §7.4, state-boundary.md §7.

class BodyUpgradeExecutionAdapter:
    """Execution boundary for the child-agent upgrade pipeline."""

    def __init__(
        self,
        *,
        config: Any,
        body_registry: Any,
        run_body_probe: Callable[[dict], Awaitable[Dict[str, Any]]],
        start_agent: Callable[[dict], Awaitable[Dict[str, Any]]],
        wait_for_health: Callable[..., Awaitable[None]],
        sync_gateway_body_activation: Callable[[str], Awaitable[Dict[str, Any] | None]],
        attach_execution_route_hint: Callable[[Dict[str, Any], str], Dict[str, Any]],
        agents: MutableMapping[str, Any],
        governor_request_executor: Optional[GovernorRequestExecutorProtocol] = None,
        governor_storage_root: Optional[str] = None,
    ) -> None:
        self.config = config
        self.body_registry = body_registry
        self.run_body_probe = run_body_probe
        self.start_agent = start_agent
        self.wait_for_health = wait_for_health
        self.sync_gateway_body_activation = sync_gateway_body_activation
        self.attach_execution_route_hint = attach_execution_route_hint
        self.agents = agents
        self.governor_request_executor = governor_request_executor
        self._governor_storage_root = governor_storage_root

    def bind_governor_request_executor(
        self,
        governor_request_executor: GovernorRequestExecutorProtocol,
    ) -> None:
        self.governor_request_executor = governor_request_executor

    def _execute_governor_request(self, governor_request: GovernorRequest) -> Dict[str, Any]:
        if self.governor_request_executor is None:
            raise RuntimeError("BodyUpgradeExecutionAdapter is not bound to a governor request executor.")
        return self.governor_request_executor.execute_governor_request(governor_request)

    async def execute_body_upgrade(self, request: dict | None = None) -> Dict[str, Any]:
        request = request or {}
        registry = self.body_registry.load_registry()
        slot_id = request.get("slot_id") or registry.shell_slot
        if not slot_id:
            raise HTTPException(
                status_code=400,
                detail="No shell slot is available for upgrade execution. Specify slot_id explicitly.",
            )

        body_version = request.get("body_version")
        build_from_commit = request.get("build_from_commit")
        execution_request = dict(request.get("execution_request") or {})
        trace_id = request.get("trace_id") or execution_request.get("trace_id")
        task_type = request.get("task_type") or execution_request.get("task_type") or "self_evolution"
        governance_task_type = (
            request.get("governance_task_type")
            or execution_request.get("governance_task_type")
            or (
                "memory_maintenance"
                if task_type == "memory_maintenance"
                else "self_evolution"
            )
        )
        execution_kind = (
            request.get("execution_kind")
            or execution_request.get("execution_kind")
            or execution_request.get("kind")
        )
        task_family = (
            request.get("task_family")
            or execution_request.get("task_family")
            or execution_kind
            or (
                "memory_maintenance"
                if task_type == "memory_maintenance"
                else "general_self_evolution"
            )
        )
        decision_id = request.get("decision_id") or execution_request.get("decision_id")
        source_actor = request.get("source_actor") or execution_request.get("source_actor")
        switch_source_actor = (
            request.get("switch_source_actor")
            or execution_request.get("source_actor")
        )
        git_lineage = {
            **dict(request.get("git_lineage") or {}),
            **dict(execution_request.get("git_lineage") or {}),
        }
        runtime_task_profile = {
            "task_type": str(task_type),
            **derive_runtime_task_profile(
                task_type=task_type,
                governance_task_type=governance_task_type,
                task_family=task_family,
                execution_kind=execution_kind,
                kind=execution_request.get("kind"),
                default_task_family="general_self_evolution",
            ),
        }
        watch_window_seconds = int(request.get("watch_window_seconds", self.config.probe_watch_window_seconds))
        stable_window_days = int(request.get("stable_window_days", 3))
        stable_health_checks = int(request.get("stable_health_checks", 3))
        start_new_agent = bool(request.get("start_agent", False))
        wait_for_new_agent_healthy = bool(request.get("wait_for_new_agent_healthy", start_new_agent))
        new_agent_health_timeout = int(request.get("new_agent_health_timeout", 30))
        start_agent_request = dict(request.get("start_agent_request") or {})
        pre_switch_registry = registry.model_copy(deep=True)

        try:
            prepared_slot = None
            if request.get("prepare_workspace", True):
                prepared_slot = self.body_registry.prepare_slot_workspace(
                    slot_id,
                    source_slot_id=request.get("source_slot_id"),
                    source_path=request.get("source_path"),
                    clear_existing=bool(request.get("clear_existing", True)),
                )

            candidate_slot = self.body_registry.mark_candidate(
                slot_id,
                body_version=body_version,
                build_from_commit=build_from_commit,
                source_branch=git_lineage.get("source_branch"),
                source_commit=git_lineage.get("source_commit"),
                candidate_branch=git_lineage.get("candidate_branch"),
                candidate_commit=git_lineage.get("candidate_commit") or build_from_commit,
                active_ref=git_lineage.get("active_ref"),
                rollback_ref=git_lineage.get("rollback_ref"),
                rollback_commit=git_lineage.get("rollback_commit"),
                diff_summary=git_lineage.get("diff_summary"),
                changed_files=git_lineage.get("changed_files"),
            )

            probe_approval = self._execute_governor_request(
                GovernorRequest(
                    request_id=request.get("probe_review_request_id", f"health-{uuid.uuid4()}"),
                    trace_id=trace_id,
                    task_type=str(task_type),
                    decision_id=decision_id,
                    event_type="health_review_request",
                    body_id=slot_id,
                    source_actor=str(source_actor or "body_upgrade_pipeline"),
                    summary=str(
                        request.get(
                            "probe_review_summary",
                            "Candidate build complete and ready for probe review.",
                        )
                    ),
                    evidence={
                        "build_complete": True,
                        "git_lineage": git_lineage,
                        "runtime_task_profile": runtime_task_profile,
                        **dict(request.get("probe_review_evidence") or {}),
                    },
                    constraints={
                        "target_transition": "candidate_to_probe",
                        **dict(request.get("probe_review_constraints") or {}),
                    },
                )
            )

            probe_slot = self.body_registry.load_slot_meta(slot_id)
            if probe_slot.body_state != "probe":
                return self.attach_execution_route_hint(
                    {
                        "status": "upgrade_halted",
                        "stage": "probe_review",
                        "slot_id": slot_id,
                        "prepared_slot": (
                            prepared_slot.model_dump(mode="json")
                            if prepared_slot is not None
                            else None
                        ),
                        "candidate_slot": candidate_slot.model_dump(mode="json"),
                        "probe_review": probe_approval,
                    },
                    "body.upgrade.execute",
                )

            probe_execution = await self.run_body_probe(
                {
                    "slot_id": slot_id,
                    "options": request.get("probe_options"),
                }
            )
            if not probe_execution["report"]["overall_passed"]:
                return self.attach_execution_route_hint(
                    {
                        "status": "upgrade_halted",
                        "stage": "probe_execution",
                        "slot_id": slot_id,
                        "prepared_slot": (
                            prepared_slot.model_dump(mode="json")
                            if prepared_slot is not None
                            else None
                        ),
                        "candidate_slot": candidate_slot.model_dump(mode="json"),
                        "probe_review": probe_approval,
                        "probe_execution": probe_execution,
                    },
                    "body.upgrade.execute",
                )

            switch_review = self._execute_governor_request(
                GovernorRequest(
                    request_id=request.get("switch_request_id", f"switch-{uuid.uuid4()}"),
                    trace_id=trace_id,
                    task_type=str(task_type),
                    decision_id=decision_id,
                    event_type="switch_request",
                    body_id=slot_id,
                    source_actor=str(switch_source_actor or "body_upgrade_pipeline"),
                    summary=str(
                        request.get(
                            "switch_summary",
                            "Promote body after probe pass through automated upgrade pipeline.",
                        )
                    ),
                    evidence={
                        "git_lineage": git_lineage,
                        "runtime_task_profile": runtime_task_profile,
                        **dict(request.get("switch_evidence") or {}),
                    },
                    constraints={
                        "watch_window_seconds": watch_window_seconds,
                        "stable_window_days": stable_window_days,
                        "stable_health_checks": stable_health_checks,
                        **dict(request.get("switch_constraints") or {}),
                    },
                )
            )

            started_agent = None
            gateway_activation = None
            if start_new_agent:
                started_agent = await self.start_agent(start_agent_request)
                if wait_for_new_agent_healthy and started_agent.get("instance_id"):
                    await self.wait_for_health(
                        started_agent["instance_id"],
                        timeout=new_agent_health_timeout,
                    )
                gateway_activation = await self.sync_gateway_body_activation(started_agent["instance_id"])

            outcome = {
                "status": "upgrade_executed",
                "slot_id": slot_id,
                "task_id": str(request.get("execution_request", {}).get("task_id", "")),
                "previous_active_slot": pre_switch_registry.active_slot,
                "retired_slot": switch_review["registry"]["retired_slot"],
                "prepared_slot": (
                    prepared_slot.model_dump(mode="json")
                    if prepared_slot is not None
                    else None
                ),
                "candidate_slot": candidate_slot.model_dump(mode="json"),
                "probe_review": probe_approval,
                "probe_execution": probe_execution,
                "switch_review": switch_review,
                "started_agent": started_agent,
                "gateway_activation": gateway_activation,
                "running_agents": self._serialize_running_agents(),
                "active_target": self.body_registry.load_active_body_pointer().model_dump(mode="json"),
            }
            result = self.attach_execution_route_hint(outcome, "body.upgrade.execute")
            # E-04: writeback execution outcome to Mem governance
            await self._writeback_execution_outcome(outcome)
            return result
        except HTTPException:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def _writeback_execution_outcome(
        self, outcome: Dict[str, Any]
    ) -> None:
        """Best-effort writeback of execution outcome to Mem governance (E-04)."""
        try:
            from memai.governance import GovernanceEvent, GovernanceEventType, GovernanceDecision
            from memai.governance_repository import GovernanceEventRepository
            repo = GovernanceEventRepository(
                str(Path(self._governor_storage_root or ".") / "mem_governance.jsonl")
            )
            repo.append(GovernanceEvent.create(
                event_type=GovernanceEventType.EXECUTION_OUTCOME,
                source_actor="executor",
                decision=(
                    GovernanceDecision.COMPLETED
                    if outcome.get("status") == "upgrade_executed"
                    else GovernanceDecision.FAILED
                ),
                reason=f"Body upgrade: {outcome.get('status', 'unknown')}",
                task_id=outcome.get("task_id", ""),
            ))
        except Exception:
            pass  # best-effort; never block the upgrade path

    def _serialize_running_agents(self) -> list[Dict[str, Any]]:
        return [
            agent.model_dump(mode="json")
            for agent in self.agents.values()
            if getattr(agent, "status", None) == "running"
        ]


class BodyLifecycleExecutionAdapter:
    """Execution boundary for body slot materialization and probe plumbing."""

    def __init__(
        self,
        *,
        config: Any,
        body_registry: Any,
        lifecycle: Any,
        probe_runner: Any,
        probe_executor: Any,
        governor_storage_root: str,
        attach_execution_route_hint: Callable[[Dict[str, Any], str], Dict[str, Any]],
    ) -> None:
        self.config = config
        self.body_registry = body_registry
        self.lifecycle = lifecycle
        self.probe_runner = probe_runner
        self.probe_executor = probe_executor
        self.governor_storage_root = governor_storage_root
        self.attach_execution_route_hint = attach_execution_route_hint

    def get_body_registry(self) -> Dict[str, Any]:
        registry = self.body_registry.load_registry()
        slots = self.body_registry.list_slots()
        return {
            "registry": registry.model_dump(mode="json"),
            "slots": {
                slot_id: meta.model_dump(mode="json")
                for slot_id, meta in slots.items()
            },
        }

    def get_active_body_target(self) -> Dict[str, Any]:
        return self.body_registry.load_active_body_pointer().model_dump(mode="json")

    def list_body_slots(self) -> Dict[str, Any]:
        slots = self.body_registry.list_slots()
        return {
            "slots": {
                slot_id: meta.model_dump(mode="json")
                for slot_id, meta in slots.items()
            }
        }

    def get_body_slot(self, slot_id: str) -> Dict[str, Any]:
        try:
            return self.body_registry.load_slot_meta(slot_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Slot not found: {slot_id}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def prepare_body_slot(self, slot_id: str, request: dict | None = None) -> Dict[str, Any]:
        request = request or {}
        try:
            meta = self.body_registry.prepare_slot_workspace(
                slot_id,
                source_slot_id=request.get("source_slot_id"),
                source_path=request.get("source_path"),
                clear_existing=bool(request.get("clear_existing", True)),
            )
            return self.attach_execution_route_hint(
                {
                    "status": "slot_prepared",
                    "slot": meta.model_dump(mode="json"),
                    "runtime_manifest_path": str(
                        self.body_registry.slot_runtime_manifest_path(slot_id)
                    ),
                    "worktree_manifest_path": str(
                        self.body_registry.slot_worktree_manifest_path(slot_id)
                    ),
                },
                "body.prepare",
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Slot not found: {slot_id}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def mark_body_candidate(self, slot_id: str, request: dict | None = None) -> Dict[str, Any]:
        request = request or {}
        try:
            prepared_slot = None
            if request.get("prepare_workspace", True):
                prepared_slot = self.body_registry.prepare_slot_workspace(
                    slot_id,
                    source_slot_id=request.get("source_slot_id"),
                    source_path=request.get("source_path"),
                    clear_existing=bool(request.get("clear_existing", True)),
                )
            meta = self.body_registry.mark_candidate(
                slot_id,
                body_version=request.get("body_version"),
                build_from_commit=request.get("build_from_commit"),
                source_branch=request.get("source_branch"),
                source_commit=request.get("source_commit"),
                candidate_branch=request.get("candidate_branch"),
                candidate_commit=request.get("candidate_commit"),
                active_ref=request.get("active_ref"),
                rollback_ref=request.get("rollback_ref"),
                rollback_commit=request.get("rollback_commit"),
                diff_summary=request.get("diff_summary"),
                changed_files=request.get("changed_files"),
            )
            return self.attach_execution_route_hint(
                {
                    "status": "candidate_marked",
                    "slot": meta.model_dump(mode="json"),
                    "prepared_slot": (
                        prepared_slot.model_dump(mode="json")
                        if prepared_slot is not None
                        else None
                    ),
                },
                "body.candidate",
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Slot not found: {slot_id}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def record_body_probe_report(self, request: dict) -> Dict[str, Any]:
        slot_id = request.get("slot_id")
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id is required")

        try:
            if "report" in request:
                report = ProbeReport.model_validate(request["report"])
            else:
                report = self.probe_runner.build_report(
                    slot_id,
                    request.get("checks", []),
                    summary=request.get("summary"),
                    source_branch=request.get("source_branch"),
                    source_commit=request.get("source_commit"),
                    candidate_branch=request.get("candidate_branch"),
                    candidate_commit=request.get("candidate_commit"),
                    active_ref=request.get("active_ref"),
                    rollback_ref=request.get("rollback_ref"),
                    rollback_commit=request.get("rollback_commit"),
                    diff_summary=str(request.get("diff_summary") or ""),
                    changed_files=request.get("changed_files"),
                )
            result = self.lifecycle.record_probe_report(slot_id, report)
            if result.status == "failed":
                raise HTTPException(status_code=400, detail=result.details.get("reason", "Probe report rejected"))
            return self.attach_execution_route_hint(
                {
                    "status": "probe_report_recorded",
                    "result": result.model_dump(mode="json"),
                    "report": report.model_dump(mode="json"),
                },
                "body.probe.report",
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def run_body_probe(self, request: dict) -> Dict[str, Any]:
        slot_id = request.get("slot_id")
        if not slot_id:
            raise HTTPException(status_code=400, detail="slot_id is required")

        try:
            slot_meta = self.body_registry.load_slot_meta(slot_id)
            if slot_meta.body_state != "probe":
                raise HTTPException(
                    status_code=400,
                    detail=f"Slot {slot_id} must be in probe state before running a real probe.",
                )

            context = self.probe_executor.build_context(
                slot_id=slot_id,
                repo_root=self.config.git_repo_path,
                worktree_path=slot_meta.worktree_path,
                runtime_path=slot_meta.runtime_path,
                logs_path=slot_meta.logs_path,
                soul_store_path=self.governor_storage_root,
                options=request.get("options"),
            )
            report = self.probe_executor.run(context)
            report.source_branch = slot_meta.source_branch
            report.source_commit = slot_meta.source_commit
            report.candidate_branch = slot_meta.candidate_branch
            report.candidate_commit = slot_meta.candidate_commit or slot_meta.build_from_commit
            report.active_ref = slot_meta.active_ref
            report.rollback_ref = slot_meta.rollback_ref
            report.rollback_commit = slot_meta.rollback_commit
            report.diff_summary = slot_meta.diff_summary
            report.changed_files = list(slot_meta.changed_files)
            persistence = self.lifecycle.record_probe_report(slot_id, report)
            return self.attach_execution_route_hint(
                {
                    "status": "probe_executed",
                    "context": context.model_dump(mode="json"),
                    "report": report.model_dump(mode="json"),
                    "persistence": persistence.model_dump(mode="json"),
                },
                "body.probe.run",
            )
        except HTTPException:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))



    async def trigger_memory_decay(self, request: dict | None = None) -> Dict[str, Any]:
        """Apply memory decay to a namespace (E-03)."""
        request = request or {}
        namespace = request.get("namespace", "default")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/mem/memories/decay"
                async with session.post(url, json={
                    "namespace": namespace,
                    "decay_factor": request.get("decay_factor", 0.1),
                }) as resp:
                    if resp.status == 200:
                        return await resp.json()
            return {"status": "decay_failed", "namespace": namespace}
        except Exception as exc:
            logger.warning("Memory decay failed for %s: %s", namespace, exc)
            return {"status": "decay_error", "error": str(exc)}

    async def trigger_memory_cleanup(self, request: dict | None = None) -> Dict[str, Any]:
        """Remove stale/low-relevance entries from a namespace (E-03)."""
        request = request or {}
        namespace = request.get("namespace", "default")
        min_score = float(request.get("min_score", 0.01))
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}/mem/memories/namespace/{namespace}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return {"status": "cleanup_failed"}
                    data = await resp.json()
                entries = data.get("memories", [])
                removed = 0
                for entry in entries:
                    score = float(entry.get("relevance_score", 0))
                    memory_id = entry.get("memory_id")
                    if score < min_score and memory_id:
                        async with session.delete(
                            f"{self.config.gateway_address}/mem/memories/{memory_id}"
                        ) as del_resp:
                            if del_resp.status == 200:
                                removed += 1
                return {"status": "cleanup_complete", "removed": removed, "namespace": namespace}
        except Exception as exc:
            logger.warning("Memory cleanup failed for %s: %s", namespace, exc)
            return {"status": "cleanup_error", "error": str(exc)}

class MemoryMaintenanceExecutionAdapter:
    def __init__(
        self,
        *,
        config: Any,
        attach_execution_route_hint: Callable[[Dict[str, Any], str], Dict[str, Any]],
        mem_state_path: str | None = None,
    ) -> None:
        self.config = config
        self.attach_execution_route_hint = attach_execution_route_hint
        if mem_state_path:
            self._mem_state_path = Path(mem_state_path)
        else:
            from VoidCube_core.constants import get_VoidCube_home
            self._mem_state_path = get_VoidCube_home() / "mem_state.json"

    async def trigger_memory_compression(self, request: dict | None = None) -> Dict[str, Any]:
        request = request or {}
        result: Dict[str, Any] = {}

        # ── Structured 4-layer maintenance (primary) ──
        try:
            structured = await self._run_structured_maintenance(request)
            result["structured_maintenance"] = structured
        except Exception as exc:
            logger.warning("Structured memory maintenance failed: %s", exc)
            result["structured_maintenance"] = {"status": "error", "error": str(exc)}

        # ── Flat SQLite compression (secondary, backward-compatible) ──
        try:
            flat = await self._run_flat_compression(request)
            result["flat_compression"] = flat
        except Exception as exc:
            logger.warning("Flat memory compression failed: %s", exc)
            result["flat_compression"] = {"status": "error", "error": str(exc)}

        return self.attach_execution_route_hint(result, "memory.compress")

    # ── Structured maintenance helpers ──────────────────────────────

    def _build_scholar_backend(self):
        """Build LLMScholarBackend with HeuristicScholarBackend fallback."""
        try:
            import os
            api_key = (
                os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            )
            if not api_key:
                logger.info("No LLM API key configured; using heuristic scholar backend")
                from memai.scholar import HeuristicScholarBackend
                return HeuristicScholarBackend()

            model = os.environ.get("MEMAI_LLM_MODEL", os.environ.get("OPENAI_MODEL", "deepseek-chat"))
            base_url = os.environ.get(
                "MEMAI_LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
            )
            from memai.llm_client import OpenAICompatibleLLMClient
            client = OpenAICompatibleLLMClient(model=model, api_key=api_key, base_url=base_url)
            from memai.scholar import LLMScholarBackend
            return LLMScholarBackend(client)
        except Exception as exc:
            logger.warning("Failed to initialize LLM scholar backend: %s; falling back to heuristic", exc)
            from memai.scholar import HeuristicScholarBackend
            return HeuristicScholarBackend()

    async def _run_structured_maintenance(self, request: dict) -> Dict[str, Any]:
        """Load mem_state.json, run structured 4-layer maintenance, save."""
        import sys
        from pathlib import Path as _Path

        mem_src = _Path(__file__).resolve().parents[2] / "Mem" / "src"
        if str(mem_src) not in sys.path:
            sys.path.insert(0, str(mem_src))

        from memai.repository import MemoryStateRepository
        from memai.pipeline import ChroniclePipeline

        scholar = self._build_scholar_backend()
        pipeline = ChroniclePipeline(scholar_backend=scholar)
        repository = MemoryStateRepository(pipeline=pipeline)

        state_path = self._mem_state_path
        if not state_path.exists():
            return {"status": "no_state", "message": f"No memory state file at {state_path}"}

        state = repository.load(str(state_path))
        execution = state.result.apply_maintenance()

        # Update PipelineResult with compressed data
        state.result.events = list(execution.events)
        state.result.scenes = list(execution.scenes)
        state.result.arcs = list(execution.arcs)
        state.result.epochs = list(execution.epochs)

        from VoidCube_core.utils import atomic_json_write
        atomic_json_write(str(state_path), state.to_dict())

        return {
            "status": "structured_maintenance_complete",
            "revision_count": len(execution.revision_records),
            "compression_actions": [
                action.to_dict() for action in execution.plan.compression_actions
            ],
            "dormant_arc_ids": execution.plan.dormant_arc_ids,
            "policy_notes": execution.plan.policy_notes,
            "revision_records": [
                record.to_dict() for record in execution.revision_records
            ],
        }

    async def _run_flat_compression(self, request: dict) -> Dict[str, Any]:
        """Existing flat SQLite compression via HTTP (backward-compatible)."""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}{self.config.memory_gateway_path}memories/compress"
                payload = {
                    "namespace": request.get("namespace", "default"),
                    "max_entries": int(request.get("max_entries", 100)),
                }
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        return await response.json()
            return {"status": "flat_compression_error", "error": "Non-200 response"}
        except Exception as exc:
            return {"status": "flat_compression_error", "error": str(exc)}


class SelfLearningExecutionAdapter:
    """Learn-only executor boundary for supervisor-approved learning tasks.

    Routes learning tasks through Gateway → Agent → delegate_task (primary)
    with procedural skill delegate as fallback when Agent is unreachable.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        learning_service: SelfLearningService,
        attach_execution_route_hint: Callable[[Dict[str, Any], str], Dict[str, Any]],
        skill_delegate: Optional[SelfLearningSkillDelegate] = None,
    ) -> None:
        self.config = config
        self.learning_service = learning_service
        self.attach_execution_route_hint = attach_execution_route_hint
        self.skill_delegate = skill_delegate or SelfLearningSkillDelegate()

    async def _dispatch_to_agent(
        self, task: dict, title: str, summary: str, constraints: dict
    ) -> Dict[str, Any]:
        """Send learning task to Agent via Gateway for sub-agent execution."""
        import aiohttp

        cfg = self.config or {}
        gateway = (
            getattr(cfg, "gateway_address", None)
            or (cfg.get("gateway_address") if isinstance(cfg, dict) else None)
            or "http://127.0.0.1:6000"
        )
        url = f"{gateway}/v1/agent/governance-task"
        prompt = self._build_learning_prompt(title, summary, constraints, task)

        payload = {
            "task_type": "self_learning",
            "governance_task_type": "self_learning",
            "title": title,
            "prompt": prompt,
            "task_id": task.get("task_id", ""),
            "trace_id": task.get("trace_id", ""),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                    agent_result = await resp.json()
        except Exception as exc:
            # Agent unreachable — fall back to procedural skill delegate
            logger.warning("Agent governance task failed, falling back to procedural: %s", exc)
            result = self.skill_delegate.execute({"task": task})
            result["backend"] = "procedural_fallback"
            return result

        status = agent_result.get("status", "error")
        if status == "completed":
            parsed = agent_result.get("parsed_output") or {}
            tool_events = agent_result.get("tool_events", [])
            api_calls = agent_result.get("api_calls", 0)
            model = agent_result.get("model", "")
            return {
                "status": "skill_delegate_executed",
                "delegate": "AgentSubagent",
                "backend": "agent_delegate_task",
                "iterations_completed": 1,
                "skill": {"name": "self-learning", "version": "1.0.0",
                           "description": "Agent-executed learning via delegate_task"},
                "learning_plan": {"topic": title, "summary": summary,
                                  "evidence_plan": {"status": "agent_managed"}},
                "evidence": {"observations": parsed.get("observations", []),
                             "comparisons": parsed.get("comparisons", [])},
                "tool_events": tool_events,
                "tool_execution": {"status": "completed",
                                   "calls": [{"tool": te.get("tool", ""),
                                              "args_preview": te.get("args_preview", "")}
                                             for te in tool_events if te.get("kind") == "tool"],
                                   "summary": {"total": len(tool_events),
                                               "api_calls": api_calls,
                                               "model": model}},
                "capability_boundary": {"uses_agent_skill_contract": True,
                                        "performs_body_mutation": False,
                                        "performs_memory_mutation": False,
                                        "backend": "agent_delegate_task"},
                "subagent_metadata": {
                    "technology_evaluations": parsed.get("technology_evaluations", []),
                    "evidence_sources": parsed.get("evidence_sources", []),
                    "overall_summary": parsed.get("overall_summary", ""),
                },
            }
        # Agent failed or rejected — fall back to procedural
        logger.warning("Agent governance task returned %s, falling back to procedural", status)
        result = self.skill_delegate.execute({"task": task})
        result["backend"] = "procedural_fallback"
        return result

    def _build_learning_prompt(
        self, title: str, summary: str, constraints: dict, task: dict
    ) -> str:
        """Build the learning research prompt for the Agent's sub-agent."""
        evidence = dict(task.get("evidence") or {})
        learning_topic = evidence.get("learning_topic", "") or title
        search_queries = [
            f"{learning_topic} latest trends 2026",
            f"{learning_topic} best practices 2025 2026",
            f"{learning_topic} production ready GitHub",
            f"{learning_topic} state of the art",
        ]
        queries_text = "\n".join(f"- `{q}`" for q in search_queries)

        return "\n".join([
            "You are a focused research subagent for the VoidCube self-learning system.",
            "",
            "## MISSION",
            f"Research: **{learning_topic}**",
            f"Context: {summary}" if summary else "",
            "",
            "## SEARCH QUERIES",
            queries_text,
            "",
            "## OUTPUT (JSON at end of response)",
            "```json",
            '{"technology_evaluations":[{"name":"","url":"","scores":{',
            '"practicality":0,"cutting_edge":0,"maturity":0,',
            '"learning_cost":0,"long_term_value":0},',
            '"total_score":0,"recommendation":"core|archive|reference",',
            '"summary":""}],',
            '"evidence_sources":[{"type":"","url":"","description":""}],',
            '"observations":[],"comparisons":[],"overall_summary":""}',
            "```",
            "",
            "Use web_search, read_file, terminal, execute_code. "
            "Do NOT modify files. READ-ONLY research.",
        ])

    def _build_skill_evidence_summary(self, skill_execution: Dict[str, Any]) -> Dict[str, Any]:
        tool_execution = dict(skill_execution.get("tool_execution") or {})
        evidence_plan = dict(tool_execution.get("evidence_plan") or {})
        plan_policy = dict(evidence_plan.get("policy") or {})
        calls = [
            dict(call)
            for call in tool_execution.get("calls") or []
            if isinstance(call, dict)
        ]
        successful_calls = [call for call in calls if call.get("success")]
        failed_calls = [call for call in calls if not call.get("success")]
        return {
            "status": tool_execution.get("status"),
            "source_mix": list(evidence_plan.get("source_mix") or []),
            "total_calls": len(calls),
            "succeeded": len(successful_calls),
            "failed": len(failed_calls),
            "rejected_tools": list(plan_policy.get("rejected_tools") or []),
            "failed_tools": [
                {
                    "tool": str(call.get("tool") or ""),
                    "source_type": call.get("source_type"),
                    "error": str(call.get("error") or ""),
                }
                for call in failed_calls[:5]
            ],
            "evidence_preview": self._skill_evidence_preview(successful_calls),
        }

    def _skill_evidence_preview(self, calls: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        preview: list[Dict[str, Any]] = []
        for call in calls:
            result = call.get("result")
            if not isinstance(result, dict):
                continue
            source_type = call.get("source_type")
            if isinstance(result.get("web"), list):
                for item in result["web"][:2]:
                    if not isinstance(item, dict):
                        continue
                    preview.append(
                        {
                            "tool": call.get("tool"),
                            "source_type": source_type,
                            "title": str(item.get("title") or ""),
                            "url": str(item.get("url") or ""),
                        }
                    )
                    if len(preview) >= 5:
                        return preview
            elif isinstance(result.get("matches"), list):
                for item in result["matches"][:2]:
                    if not isinstance(item, dict):
                        continue
                    preview.append(
                        {
                            "tool": call.get("tool"),
                            "source_type": source_type,
                            "path": str(item.get("path") or ""),
                            "line": item.get("line"),
                        }
                    )
                    if len(preview) >= 5:
                        return preview
            elif result.get("path") or result.get("content"):
                preview.append(
                    {
                        "tool": call.get("tool"),
                        "source_type": source_type,
                        "path": str(result.get("path") or ""),
                    }
                )
                if len(preview) >= 5:
                    return preview
        return preview

    def _skill_evidence_observations(self, summary: Dict[str, Any]) -> list[str]:
        observations = [
            (
                "Self-learning evidence summary: "
                f"{summary.get('succeeded', 0)} succeeded, "
                f"{summary.get('failed', 0)} failed, "
                f"{summary.get('total_calls', 0)} total calls."
            )
        ]
        source_mix = summary.get("source_mix") or []
        if source_mix:
            observations.append(f"Self-learning evidence sources: {', '.join(map(str, source_mix))}.")
        rejected_tools = summary.get("rejected_tools") or []
        if rejected_tools:
            observations.append(f"Rejected disallowed evidence tools: {', '.join(map(str, rejected_tools))}.")
        for failed in summary.get("failed_tools") or []:
            if not isinstance(failed, dict):
                continue
            observations.append(
                "Self-learning evidence tool failed: "
                f"{failed.get('tool')} ({failed.get('source_type')}): {failed.get('error')}"
            )
        for item in summary.get("evidence_preview") or []:
            if not isinstance(item, dict):
                continue
            if item.get("url"):
                observations.append(
                    f"Evidence preview: {item.get('title') or item.get('tool')} <{item.get('url')}>."
                )
            elif item.get("path"):
                line_suffix = f":{item.get('line')}" if item.get("line") else ""
                observations.append(f"Evidence preview: {item.get('path')}{line_suffix}.")
        return observations
