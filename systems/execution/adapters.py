from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, MutableMapping, Optional, Protocol

from fastapi import HTTPException

from systems.governor import GovernorRequest
from systems.lifecycle import LifecycleActionResult
from systems.probe import ProbeReport
from systems.runtime_task_profile import derive_runtime_task_profile
logger = logging.getLogger(__name__)


class WatchWindowRuntimeStateProtocol(Protocol):
    task: Optional[asyncio.Task[Any]]
    last_outcome: Optional[Dict[str, Any]]
    last_body_upgrade_trace_id: Optional[str]


class GovernorRequestExecutorProtocol(Protocol):
    def execute_governor_request(self, governor_request: GovernorRequest) -> Dict[str, Any]: ...


class WatchWindowRuntimeSyncProtocol(Protocol):
    def sync_runtime_after_governor_response(self, governor_response: Any) -> Dict[str, Any]: ...


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
        if governor_request.event_type == "improvement_rollback_request":
            raise ValueError(
                "Body improvement rollback must use the dedicated rollback executor so probe "
                "verification and Mem writeback remain atomic."
            )
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

    Owns its runtime state directly (S-02/S-03).
    """

    def __init__(
        self,
        *,
        body_registry: Any,
        agents: MutableMapping[str, Any],
        stop_agent: Optional[Callable[[str], Awaitable[Dict[str, Any]]]],
        run_health_checks: Callable[[], Awaitable[Dict[str, Any]]],
        governor_request_executor: Optional[GovernorRequestExecutorProtocol] = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.body_registry = body_registry
        self.agents = agents
        self.stop_agent = stop_agent
        self.run_health_checks = run_health_checks
        self._state = WatchWindowState()
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
        if self.stop_agent is None:
            return []
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
            stopped_instances = await self.stop_agents_for_slot(failed_slot)
            return {
                "action": "failed_slot_drained",
                "slot_id": failed_slot,
                "restored_slot_id": restored_slot,
                "restored_instance_id": (
                    restored_agent.instance_id if restored_agent is not None else None
                ),
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
        attach_execution_route_hint: Callable[[Dict[str, Any], str], Dict[str, Any]],
        agents: MutableMapping[str, Any],
        governance_repository: Any,
        governor_request_executor: Optional[GovernorRequestExecutorProtocol] = None,
    ) -> None:
        self.config = config
        self.body_registry = body_registry
        self.run_body_probe = run_body_probe
        self.attach_execution_route_hint = attach_execution_route_hint
        self.agents = agents
        self.governance_repository = governance_repository
        self.governor_request_executor = governor_request_executor

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
                abandonment = self._execute_governor_request(
                    GovernorRequest(
                        request_id=request.get(
                            "abandon_request_id",
                            f"abandon-{uuid.uuid4()}",
                        ),
                        trace_id=trace_id,
                        task_type=str(task_type),
                        decision_id=decision_id,
                        event_type="health_review_request",
                        body_id=slot_id,
                        source_actor=str(source_actor or "body_upgrade_pipeline"),
                        summary="Discard candidate after required probe checks failed.",
                        evidence={
                            "probe_passed": False,
                            "probe_report": probe_execution["report"],
                            "runtime_task_profile": runtime_task_profile,
                        },
                        constraints={"target_transition": "probe_to_shell"},
                    )
                )
                outcome = {
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
                    "abandonment": abandonment,
                }
                await self._writeback_execution_outcome(outcome)
                return self.attach_execution_route_hint(
                    outcome,
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

            active_target = self.body_registry.load_active_body_pointer().model_dump(mode="json")
            outcome = {
                "status": "upgrade_awaiting_user_consent",
                "slot_id": slot_id,
                "task_id": str(request.get("execution_request", {}).get("task_id", "")),
                "previous_active_slot": pre_switch_registry.active_slot,
                "retired_slot": switch_review["registry"]["retired_slot"],
                "requires_user_consent": True,
                "prepared_slot": (
                    prepared_slot.model_dump(mode="json")
                    if prepared_slot is not None
                    else None
                ),
                "candidate_slot": candidate_slot.model_dump(mode="json"),
                "probe_review": probe_approval,
                "probe_execution": probe_execution,
                "switch_review": switch_review,
                "running_agents": self._serialize_running_agents(),
                "active_target": active_target,
            }
            result = self.attach_execution_route_hint(outcome, "body.upgrade.execute")
            # E-04: writeback execution outcome to Mem governance
            await self._writeback_execution_outcome(outcome)
            return result
        except HTTPException:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def confirm_body_switch(self, request: dict | None = None) -> Dict[str, Any]:
        request = request or {}
        slot_id = request.get("slot_id")
        if not slot_id:
            awaiting_slots = [
                slot.slot_id
                for slot in self.body_registry.list_slots().values()
                if getattr(slot, "body_state", None) == "awaiting_user_consent"
            ]
            if len(awaiting_slots) == 1:
                slot_id = awaiting_slots[0]
        if not slot_id:
            raise HTTPException(
                status_code=400,
                detail="slot_id is required when no single slot is awaiting user consent.",
            )
        if request.get("approved") is not True:
            raise HTTPException(status_code=400, detail="approved=true is required to activate a body slot.")

        try:
            slot_meta = self.body_registry.load_slot_meta(slot_id)
            if slot_meta.body_state != "awaiting_user_consent":
                raise HTTPException(
                    status_code=400,
                    detail=f"Slot {slot_id} must be awaiting user consent before activation.",
                )

            consent_request = dict(slot_meta.switch_consent_request or {})
            watch_window_seconds = int(
                request.get("watch_window_seconds")
                or consent_request.get("watch_window_seconds")
                or self.config.probe_watch_window_seconds
            )
            stable_window_days = int(
                request.get("stable_window_days")
                or consent_request.get("stable_window_days")
                or 3
            )
            stable_health_checks = int(
                request.get("stable_health_checks")
                or consent_request.get("stable_health_checks")
                or 3
            )
            runtime_task_profile = dict(consent_request.get("runtime_task_profile") or {})
            decision_id = request.get("decision_id")
            trace_id = request.get("trace_id")

            switch_activation = self._execute_governor_request(
                GovernorRequest(
                    request_id=request.get("request_id", f"user-consent-switch-{uuid.uuid4()}"),
                    trace_id=trace_id,
                    task_type=str(request.get("task_type") or runtime_task_profile.get("task_type") or "self_evolution"),
                    decision_id=decision_id,
                    event_type="health_review_request",
                    body_id=slot_id,
                    source_actor=str(request.get("source_actor") or "user_consent"),
                    summary=str(request.get("summary") or "User approved activating the probe-passed body slot."),
                    evidence={
                        "user_consent_approved": True,
                        "user_consent_actor": request.get("source_actor") or "user",
                        "user_consent_at": datetime.utcnow().isoformat(),
                        **dict(request.get("evidence") or {}),
                    },
                    constraints={
                        "target_transition": "probe_to_active",
                        "watch_window_seconds": watch_window_seconds,
                        "stable_window_days": stable_window_days,
                        "stable_health_checks": stable_health_checks,
                        **dict(request.get("constraints") or {}),
                    },
                )
            )
            registry = self.body_registry.load_registry()
            return self.attach_execution_route_hint(
                {
                    "status": "body_switch_activated",
                    "slot_id": slot_id,
                    "switch_activation": switch_activation,
                    "registry": registry.model_dump(mode="json"),
                    "active_target": self.body_registry.load_active_body_pointer().model_dump(mode="json"),
                },
                "body.switch.consent",
            )
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
            execution_request = dict(outcome.get("execution_request") or {})
            runtime_task_profile = dict(
                execution_request.get("runtime_task_profile")
                or outcome.get("runtime_task_profile")
                or {}
            )
            if not runtime_task_profile:
                runtime_task_profile = {
                    "governance_task_type": "self_evolution",
                    "task_family": "body_upgrade",
                    "execution_kind": "body_improvement",
                }
            execution_result = {
                **dict(outcome),
                "title": (
                    execution_request.get("title")
                    or outcome.get("title")
                    or "Body upgrade execution outcome"
                ),
                "summary": (
                    execution_request.get("summary")
                    or outcome.get("summary")
                    or f"Body upgrade: {outcome.get('status', 'unknown')}"
                ),
                "runtime_task_profile": runtime_task_profile,
                "constraints": dict(execution_request.get("constraints") or {}),
            }
            self.governance_repository.append(GovernanceEvent.create(
                event_type=GovernanceEventType.EXECUTION_OUTCOME,
                source_actor="executor",
                decision=(
                    GovernanceDecision.COMPLETED
                    if outcome.get("status") == "upgrade_awaiting_user_consent"
                    else GovernanceDecision.FAILED
                ),
                reason=f"Body upgrade: {outcome.get('status', 'unknown')}",
                task_id=outcome.get("task_id", ""),
                execution_result=execution_result,
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
        governor: Any = None,
    ) -> None:
        self.config = config
        self.body_registry = body_registry
        self.lifecycle = lifecycle
        self.probe_runner = probe_runner
        self.probe_executor = probe_executor
        self.governor_storage_root = governor_storage_root
        self.attach_execution_route_hint = attach_execution_route_hint
        self.governor = governor

    def get_body_registry(self) -> Dict[str, Any]:
        integrity = self.body_registry.inspect_layout()
        slots: Dict[str, Any] = {}
        for slot_id in self.body_registry.slot_ids:
            try:
                slots[slot_id] = self.body_registry.load_slot_meta(slot_id).model_dump(
                    mode="json"
                )
            except (OSError, ValueError, FileNotFoundError):
                continue
        return {
            "registry": integrity.get("registry"),
            "slots": slots,
            "integrity": integrity,
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

    async def rollback_body_improvement(
        self,
        slot_id: str,
        request: dict | None = None,
    ) -> Dict[str, Any]:
        request = request or {}
        if self.governor is None:
            raise HTTPException(status_code=503, detail="Governor is unavailable.")

        try:
            slot_meta = self.body_registry.load_slot_meta(slot_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Slot not found: {slot_id}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        current_commit = (
            self.body_registry._git_head_for_isolated_worktree(
                Path(slot_meta.worktree_path).resolve()
            )
            or slot_meta.current_healthy_commit
            or slot_meta.candidate_commit
            or ""
        )
        request_id = str(request.get("request_id") or uuid.uuid4())
        trace_id = str(request.get("trace_id") or uuid.uuid4())
        failure_reason = str(
            request.get("failure_reason")
            or request.get("reason")
            or "destructive_body_improvement"
        ).strip()
        runtime_task_profile = {
            "task_type": "self_evolution",
            "governance_task_type": "self_evolution",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement_rollback",
        }
        governor_request = GovernorRequest(
            request_id=request_id,
            trace_id=trace_id,
            task_type="self_evolution",
            event_type="improvement_rollback_request",
            body_id=slot_id,
            source_actor=str(request.get("source_actor") or "supervisor_body_health_review"),
            summary=f"Restore slot {slot_id} to its previous healthy improvement commit.",
            evidence={
                "destructive_change_detected": bool(
                    request.get("destructive_change_detected")
                ),
                "probe_failed": bool(request.get("probe_failed")),
                "regression_detected": bool(request.get("regression_detected")),
                "failure_reason": failure_reason,
                "current_commit": current_commit,
                "runtime_task_profile": runtime_task_profile,
                "git_lineage": {
                    "source_branch": slot_meta.source_branch,
                    "source_commit": slot_meta.source_commit,
                    "candidate_branch": slot_meta.candidate_branch,
                    "candidate_commit": current_commit,
                    "rollback_ref": slot_meta.rollback_ref,
                    "rollback_commit": slot_meta.previous_healthy_commit,
                    "changed_files": list(slot_meta.changed_files),
                },
            },
            constraints={
                "previous_healthy_commit": slot_meta.previous_healthy_commit,
                "isolated_worktree_required": True,
                "fresh_probe_required": True,
            },
        )

        response = self.governor.review(governor_request, slot_meta=slot_meta)
        execution_report = self.lifecycle.apply_governor_response(response)
        restore_applied = any(
            result.action_type == "restore_healthy_commit" and result.status == "applied"
            for result in execution_report.action_results
        )
        probe_result: Dict[str, Any] | None = None
        if restore_applied:
            try:
                probe_result = await self.run_body_probe(
                    {
                        "slot_id": slot_id,
                        "options": request.get("probe_options"),
                    }
                )
                probe_report = dict(probe_result.get("report") or {})
            except Exception as exc:
                detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                probe_report = {
                    "slot_id": slot_id,
                    "overall_passed": False,
                    "overall_status": "failed",
                    "summary": str(detail),
                    "checks": [],
                }
                self.body_registry.write_probe_report(slot_id, probe_report)
                probe_result = {
                    "status": "probe_execution_failed",
                    "report": probe_report,
                }

            finalized = self.body_registry.finalize_previous_healthy_commit_restore(
                slot_id,
                probe_report=probe_report,
            )
            probe_passed = bool(probe_report.get("overall_passed"))
            execution_report.action_results.append(
                LifecycleActionResult(
                    action_type="verify_healthy_commit_rollback",
                    status="applied" if probe_passed else "failed",
                    slot_id=slot_id,
                    details={
                        "probe_passed": probe_passed,
                        "probe_report": probe_report,
                        "body_state": finalized.body_state,
                        "health_score": finalized.health_score,
                        "rollback": finalized.last_improvement_rollback,
                    },
                )
            )

        registry = self.body_registry.load_registry()
        self.governor.record_execution_outcome(
            request=governor_request,
            response=response,
            execution_report=execution_report,
            registry=registry,
        )
        final_meta = self.body_registry.load_slot_meta(slot_id)
        return self.attach_execution_route_hint(
            {
                "status": (
                    "body_improvement_rollback_verified"
                    if restore_applied
                    and final_meta.last_improvement_rollback
                    and final_meta.last_improvement_rollback.get("probe_passed")
                    else "body_improvement_rollback_not_verified"
                    if restore_applied
                    else "body_improvement_rollback_not_executed"
                ),
                "request": governor_request.model_dump(mode="json"),
                "governor_response": response.model_dump(mode="json"),
                "execution_report": execution_report.model_dump(mode="json"),
                "probe": probe_result,
                "slot": final_meta.model_dump(mode="json"),
                "registry": registry.model_dump(mode="json"),
            },
            "body.improvement.rollback",
        )



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

        # ── Canonical memory-service rule compression ──
        try:
            rule_compression = await self._run_memory_rule_compression(request)
            result["rule_compression"] = rule_compression
        except Exception as exc:
            logger.warning("Memory rule compression failed: %s", exc)
            result["rule_compression"] = {"status": "error", "error": str(exc)}

        return self.attach_execution_route_hint(result, "memory.compress")

    # ── Structured maintenance helpers ──────────────────────────────

    def _build_scholar_backend(self):
        """Build LLMScholarBackend with HeuristicScholarBackend fallback."""
        try:
            from memai.model_config import resolve_mem_llm_client

            client, _ = resolve_mem_llm_client(role="default")
            if client is None:
                logger.info("No LLM API key configured; using heuristic scholar backend")
                from memai.scholar import HeuristicScholarBackend
                return HeuristicScholarBackend()

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

    async def _run_memory_rule_compression(self, _request: dict) -> Dict[str, Any]:
        """Run full five-rule compression via the memory service."""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"{self.config.gateway_address}{self.config.memory_gateway_path}compressed/run-all-rules"
                async with session.post(url, json={}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info("Five-rule compression: %s",
                                    {k: type(v).__name__ for k, v in result.get("rules", {}).items()})
                        return result
                    return {
                        "status": "rule_compression_error",
                        "error": f"Memory rule compression endpoint returned HTTP {resp.status}",
                    }
            return {"status": "rule_compression_error", "error": "Memory rule compression endpoint unreachable"}
        except Exception as exc:
            logger.warning("Memory compression failed: %s", exc)
            return {"status": "rule_compression_error", "error": str(exc)}

