import logging
import re
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..body_registry import BodyImprovementReport
from ..evolution_candidate_generation import EvolutionCandidateGenerationRequest
from .autonomous_chain_store import StaleExecutionLeaseError
from ..governor import GovernorRequest
from .autonomous_chain_contract import (
    AUTONOMOUS_CHAIN_CYCLE_ROUTE,
    AUTONOMOUS_CHAIN_TASKS_ROUTE,
    AUTONOMOUS_CHAIN_TASK_CLEAR_ROUTE,
    AUTONOMOUS_CHAIN_TASK_REVIEW_ROUTE,
    autonomous_chain_task_decision_route,
    autonomous_chain_task_route,
)
from .config_models import (
    SupervisorBodyRuntimeConfig,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)
from .planning_runtime import PlanningRuntimeMixin
from .provider_pool_service import (
    CompanionWorkerAssignmentsRequest,
    ProviderPoolConflictError,
    ProviderPoolEntryRequest,
    ProviderPoolManagedError,
    ProviderPoolProbeError,
    ProviderPoolService,
)
from .runtime_assemblers import (
    assemble_supervisor_execution_runtime,
    assemble_supervisor_runtime_state,
    assemble_supervisor_ui_runtime,
)
from .scheduled_tasks import ScheduledTaskRuntimeMixin
from .service_runtime import ServiceRuntimeMixin
from .trace_runtime import TraceRuntimeMixin
from .ui_routes import (
    SupervisorUIRoutePorts,
    mount_supervisor_ui_routes,
)
from ..voice import VoiceConfig, VoiceSessionManager

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


class CompanionMessageRequest(BaseModel):
    text: str
    session_id: str = ""


class CompanionWorkerTestRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class EvolutionCandidateGenerationTriggerRequest(BaseModel):
    mode: Literal["manual", "shadow"] = "shadow"
    request_id: Optional[str] = None


class VoiceToggleRequest(BaseModel):
    enabled: bool


class VoiceCaptureRequest(BaseModel):
    session_id: str = ""


class VoiceEnrollmentRequest(BaseModel):
    duration_seconds: float = Field(default=3.0, ge=2.0, le=10.0)
    sample_count: int = Field(default=3, ge=2, le=5)


class VoiceContinuousRequest(BaseModel):
    session_id: str = ""


class CompanionReminderPolicyRequest(BaseModel):
    enabled: bool
    tts_enabled: bool
    cooldown_seconds: int = Field(ge=0, le=86400)
    dnd_start: str = ""
    dnd_end: str = ""

    @field_validator("dnd_start", "dnd_end")
    @classmethod
    def validate_dnd_time(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", normalized):
            raise ValueError("must be empty or use HH:MM in 24-hour time")
        return normalized


class Supervisor(
    PlanningRuntimeMixin,
    ScheduledTaskRuntimeMixin,
    ServiceRuntimeMixin,
    TraceRuntimeMixin,
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
        self._provider_pool_service = ProviderPoolService()
        self._voice_manager = VoiceSessionManager(
            VoiceConfig.from_env(),
            companion_callback=self.handle_companion_message,
        )
        # Watch-window state is owned by executor adapter (§3.6 / S-02/03).
        # Supervisor holds a plain holder that gets proxied after assembly.
        self._watch_window_runtime: Any = type("_WatchWindowHolder", (), {
            "task": None, "last_outcome": None, "last_body_upgrade_trace_id": None,
        })()
        assemble_supervisor_runtime_state(self)
        assemble_supervisor_ui_runtime(self)
        assemble_supervisor_execution_runtime(self)
        try:
            recovery_result = self._autonomous_chain_recovery_service.recover()
            recovery = self._service_runtime.recovery
            recovery.mark_healthy(
                recovery_cursor=str(recovery_result.get("event_count") or 0),
                last_successful_event=str(
                    recovery_result.get("updated_task_count")
                    or recovery_result.get("added_task_count")
                    or 0
                ),
            )
        except Exception as exc:
            self._service_runtime.recovery.mark_failed(exc)
            logger.error("Autonomous-chain Mem governance recovery failed", exc_info=True)
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
                return self._execution_facade.review_body(governor_request)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        self.app.add_api_route("/", self.health_check, methods=["GET"])
        mount_supervisor_ui_routes(
            SupervisorUIRoutePorts(
                app=self.app,
                enabled=self.config.ui_enabled,
                ui_path=self.config.ui_path,
                get_ui=self._ui_runtime.get_ui,
                get_state=self._ui_runtime.get_state,
                get_events=self._ui_runtime.get_events,
                get_voice_levels=self._ui_runtime.get_voice_levels,
                get_media_events=self._ui_runtime.get_media_events,
                enqueue_media=self._ui_runtime.enqueue_media_endpoint,
                enqueue_media_playlist=self._ui_runtime.enqueue_media_playlist_endpoint,
                control_media=self._ui_runtime.control_media_endpoint,
                get_delivery_events=self._ui_runtime.get_delivery_events,
                push_delivery=self._ui_runtime.push_delivery_endpoint,
                control_delivery=self._ui_runtime.control_delivery_endpoint,
                upload_delivery_asset=self._ui_runtime.upload_delivery_asset_endpoint,
                get_delivery_asset=self._ui_runtime.get_delivery_asset_endpoint,
                get_identity_archive=self._ui_runtime.get_identity_archive,
                get_identity_turns=self._ui_runtime.get_identity_turns,
                get_evolution_audit=self._ui_runtime.get_evolution_promotion_audit,
                get_evolution_candidates=(
                    self._ui_runtime.get_evolution_promotion_candidates
                ),
                consent_evolution_candidate=(
                    self._ui_runtime.consent_evolution_promotion_candidate
                ),
                verify_identity_experience=self._ui_runtime.verify_identity_experience,
                list_accounts=self._ui_runtime.list_accounts,
                add_account=self._ui_runtime.add_account,
                delete_account=self._ui_runtime.delete_account_endpoint,
                verify_account=self._ui_runtime.verify_account_endpoint,
            )
        )
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
        self.app.add_api_route(
            "/runtime/evolution-candidate-generation",
            self.get_evolution_candidate_generation_status,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/runtime/evolution-candidate-generation/requests",
            self.register_evolution_candidate_generation_request,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/runtime/evolution-candidate-generation/trigger",
            self.trigger_evolution_candidate_generation,
            methods=["POST"],
        )
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        self.app.add_api_route("/ready", self.readiness_check, methods=["GET"])
        self.app.add_api_route(AUTONOMOUS_CHAIN_TASKS_ROUTE, self.list_autonomous_chain_tasks, methods=["GET"])
        self.app.add_api_route(
            AUTONOMOUS_CHAIN_TASKS_ROUTE,
            self._autonomous_chain_planning_service.plan,
            methods=["POST"],
        )
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
            self._autonomous_cycle_service.run,
            methods=["POST"],
        )
        self.app.add_api_route("/autonomous-chain-gate/activate", self.activate_autonomous_chain_gate, methods=["POST"])
        self.app.add_api_route("/autonomous-chain-gate/deactivate", self.deactivate_autonomous_chain_gate, methods=["POST"])
        self.app.add_api_route("/autonomous-chain-gate/status", self.get_autonomous_chain_gate_status, methods=["GET"])
        self.app.add_api_route("/stellar-mode/status", self.get_stellar_mode_status, methods=["GET"])
        self.app.add_api_route("/companion/message", self.companion_message, methods=["POST"])
        self.app.add_api_route("/scheduled-tasks", self.list_scheduled_tasks, methods=["GET"])
        self.app.add_api_route("/scheduled-tasks", self.create_scheduled_task, methods=["POST"])
        self.app.add_api_route("/scheduled-tasks/claim", self.claim_scheduled_task, methods=["POST"])
        self.app.add_api_route("/scheduled-tasks/{schedule_id}", self.get_scheduled_task, methods=["GET"])
        self.app.add_api_route("/scheduled-tasks/{schedule_id}", self.update_scheduled_task, methods=["PUT"])
        self.app.add_api_route("/scheduled-tasks/{schedule_id}", self.delete_scheduled_task, methods=["DELETE"])
        self.app.add_api_route("/scheduled-tasks/{schedule_id}/pause", self.pause_scheduled_task, methods=["POST"])
        self.app.add_api_route("/scheduled-tasks/{schedule_id}/resume", self.resume_scheduled_task, methods=["POST"])
        self.app.add_api_route("/scheduled-tasks/{schedule_id}/cancel", self.cancel_scheduled_task, methods=["POST"])
        self.app.add_api_route(
            "/scheduled-task-runs/{run_id}/renew",
            self.renew_scheduled_task_run,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/scheduled-task-runs/{run_id}/finish",
            self.finish_scheduled_task_run,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/companion/reminder-policy",
            self.get_companion_reminder_policy,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/companion/reminder-policy",
            self.set_companion_reminder_policy,
            methods=["POST"],
        )
        self.app.add_api_route("/provider-pool", self.get_provider_pool, methods=["GET"])
        self.app.add_api_route(
            "/provider-pool/providers/{provider_key}",
            self.upsert_provider_pool_entry,
            methods=["PUT"],
        )
        self.app.add_api_route(
            "/provider-pool/providers/{provider_key}",
            self.delete_provider_pool_entry,
            methods=["DELETE"],
        )
        self.app.add_api_route(
            "/provider-pool/providers/{provider_key}/test",
            self.test_provider_pool_entry,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/provider-pool/providers/{provider_key}/models",
            self.refresh_provider_pool_models,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/provider-pool/scheduler",
            self.get_provider_pool_scheduler,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/provider-pool/providers/{provider_key}/cooldown/reset",
            self.reset_provider_pool_cooldown,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/provider-pool/worker-roles",
            self.set_provider_pool_worker_roles,
            methods=["PUT"],
        )
        self.app.add_api_route(
            "/provider-pool/worker-tests",
            self.list_provider_pool_worker_tests,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/provider-pool/worker-tests/{worker_role}",
            self.create_provider_pool_worker_test,
            methods=["POST"],
        )
        self.app.add_api_route(
            "/provider-pool/worker-tests/{test_id}",
            self.get_provider_pool_worker_test,
            methods=["GET"],
        )
        self.app.add_api_route("/voice/status", self.voice_status, methods=["GET"])
        self.app.add_api_route("/voice/microphone", self.set_voice_microphone, methods=["POST"])
        self.app.add_api_route("/voice/fingerprint", self.set_voice_fingerprint, methods=["POST"])
        self.app.add_api_route(
            "/voice/owner-template",
            self.record_owner_voice_template,
            methods=["POST"],
        )
        self.app.add_api_route("/voice/session/start", self.start_voice_session, methods=["POST"])
        self.app.add_api_route("/voice/session/interrupt", self.interrupt_voice_session, methods=["POST"])
        self.app.add_api_route("/voice/continuous/start", self.start_continuous_voice, methods=["POST"])
        self.app.add_api_route("/voice/continuous/stop", self.stop_continuous_voice, methods=["POST"])

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

    async def get_stellar_mode_status(self) -> Dict[str, Any]:
        """Return the canonical daily-companion/auto-evolution mode state."""
        return self._autonomous_chain_gate_status()

    async def companion_message(self, request: CompanionMessageRequest) -> Dict[str, Any]:
        return await self.handle_companion_message(
            text=request.text,
            session_id=request.session_id,
        )

    def _companion_reminder_policy_payload(self) -> Dict[str, Any]:
        runtime = self.config.service_runtime
        return {
            "enabled": bool(runtime.companion_proactive_reminder_enabled),
            "tts_enabled": bool(runtime.companion_proactive_reminder_tts_enabled),
            "cooldown_seconds": int(
                runtime.companion_proactive_reminder_cooldown_seconds
            ),
            "dnd_start": str(runtime.companion_proactive_dnd_start or ""),
            "dnd_end": str(runtime.companion_proactive_dnd_end or ""),
        }

    async def get_companion_reminder_policy(self) -> Dict[str, Any]:
        from ...infrastructure.config.configuration import is_managed

        return {
            **self._companion_reminder_policy_payload(),
            "managed": is_managed(),
        }

    async def set_companion_reminder_policy(
        self,
        request: CompanionReminderPolicyRequest,
    ) -> Dict[str, Any]:
        from ...infrastructure.config.configuration import (
            format_managed_message,
            is_managed,
            read_raw_config,
            save_config,
        )

        if is_managed():
            raise HTTPException(
                status_code=409,
                detail=format_managed_message("change the companion reminder policy"),
            )

        raw_config = read_raw_config()
        if not isinstance(raw_config, dict):
            raw_config = {}
        supervisor_config = raw_config.get("supervisor")
        if not isinstance(supervisor_config, dict):
            supervisor_config = {}
        else:
            supervisor_config = dict(supervisor_config)
        service_runtime = supervisor_config.get("service_runtime")
        if not isinstance(service_runtime, dict):
            service_runtime = {}
        else:
            service_runtime = dict(service_runtime)

        persisted_fields = {
            "companion_proactive_reminder_enabled": request.enabled,
            "companion_proactive_reminder_tts_enabled": request.tts_enabled,
            "companion_proactive_reminder_cooldown_seconds": request.cooldown_seconds,
            "companion_proactive_dnd_start": request.dnd_start,
            "companion_proactive_dnd_end": request.dnd_end,
        }
        service_runtime.update(persisted_fields)
        supervisor_config["service_runtime"] = service_runtime
        raw_config = dict(raw_config)
        raw_config["supervisor"] = supervisor_config

        try:
            save_config(raw_config, preserve_structure=True)
        except Exception as exc:
            logger.exception("Failed to save companion reminder policy")
            raise HTTPException(
                status_code=500,
                detail="Failed to save companion reminder policy",
            ) from exc

        runtime = self.config.service_runtime
        for field_name, value in persisted_fields.items():
            setattr(runtime, field_name, value)

        return {
            **self._companion_reminder_policy_payload(),
            "managed": False,
            "status": "saved",
        }

    async def get_provider_pool(self) -> Dict[str, Any]:
        return self._provider_pool_service.snapshot()

    async def get_provider_pool_scheduler(self) -> Dict[str, Any]:
        policy = self._provider_pool_service.dispatch_policy()
        state = self._scheduled_task_store.dispatch_state(**policy)
        return {"status": "ok", **state}

    async def reset_provider_pool_cooldown(
        self, provider_key: str
    ) -> Dict[str, Any]:
        provider = str(provider_key or "").strip().lower()
        snapshot = self._provider_pool_service.snapshot()
        if not any(item.get("key") == provider for item in snapshot["providers"]):
            raise HTTPException(status_code=404, detail="Provider not found")
        cleared = self._scheduled_store_call(
            "clear_provider_cooldown", provider
        )
        policy = self._provider_pool_service.dispatch_policy()
        state = self._scheduled_task_store.dispatch_state(**policy)
        return {
            "status": "reset",
            "provider": provider,
            "cleared": cleared,
            "scheduler": state,
        }

    @staticmethod
    def _provider_pool_error(error: Exception) -> HTTPException:
        if isinstance(error, KeyError):
            return HTTPException(status_code=404, detail="Provider not found")
        if isinstance(error, ProviderPoolProbeError):
            return HTTPException(status_code=error.status_code, detail=str(error))
        if isinstance(error, (ProviderPoolConflictError, ProviderPoolManagedError)):
            return HTTPException(status_code=409, detail=str(error))
        return HTTPException(status_code=400, detail=str(error))

    async def upsert_provider_pool_entry(
        self,
        provider_key: str,
        request: ProviderPoolEntryRequest,
    ) -> Dict[str, Any]:
        try:
            return self._provider_pool_service.upsert_provider(provider_key, request)
        except (KeyError, ValueError, ProviderPoolManagedError) as exc:
            raise self._provider_pool_error(exc) from exc

    async def delete_provider_pool_entry(self, provider_key: str) -> Dict[str, Any]:
        try:
            return self._provider_pool_service.delete_provider(provider_key)
        except (KeyError, ValueError, ProviderPoolManagedError) as exc:
            raise self._provider_pool_error(exc) from exc

    async def test_provider_pool_entry(self, provider_key: str) -> Dict[str, Any]:
        try:
            return await self._provider_pool_service.test_provider(provider_key)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise self._provider_pool_error(exc) from exc

    async def refresh_provider_pool_models(self, provider_key: str) -> Dict[str, Any]:
        try:
            return await self._provider_pool_service.refresh_model_catalog(provider_key)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise self._provider_pool_error(exc) from exc

    async def set_provider_pool_worker_roles(
        self,
        request: CompanionWorkerAssignmentsRequest,
    ) -> Dict[str, Any]:
        try:
            return self._provider_pool_service.save_worker_assignments(request)
        except (KeyError, ValueError, ProviderPoolManagedError) as exc:
            raise self._provider_pool_error(exc) from exc

    @staticmethod
    def _provider_pool_worker_assignment(
        worker_role: str,
        *,
        strict: bool = False,
    ) -> Dict[str, str]:
        from ...application.companion_workers import (
            companion_worker_roles,
            resolve_companion_worker_role,
        )
        from ...infrastructure.config.configuration import load_config

        config = load_config()
        if strict and str(worker_role or "").strip().lower() not in companion_worker_roles(config):
            raise ValueError("worker role is unknown or disabled")
        role = resolve_companion_worker_role(config, worker_role)
        providers = config.get("providers")
        providers = providers if isinstance(providers, dict) else {}
        provider_key = role.provider or str(
            (config.get("runtime") or {}).get("active_provider") or ""
        ).strip().lower()
        provider = providers.get(provider_key)
        provider = provider if isinstance(provider, dict) else {}
        return {
            "role": role.role,
            "label": role.label,
            "provider": provider_key,
            "model": role.model or str(provider.get("selected_model") or "").strip(),
        }

    async def create_provider_pool_worker_test(
        self,
        worker_role: str,
        request: CompanionWorkerTestRequest,
    ) -> Dict[str, Any]:
        try:
            assignment = self._provider_pool_worker_assignment(worker_role, strict=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        task = self._scheduled_task_store.create(
            {
                "title": f"员工测试 · {assignment['label']}",
                "instruction": request.instruction.strip(),
                "schedule_type": "once",
                "run_at": datetime.now(timezone.utc).isoformat(),
                "created_by": "api_b",
                "requested_via": "provider_pool_test",
                "worker_role": assignment["role"],
            }
        )
        return {
            "status": "queued",
            "test_id": task["schedule_id"],
            "worker_role": assignment["role"],
            "worker_label": assignment["label"],
            "provider": assignment["provider"],
            "model": assignment["model"],
            "created_at": task.get("created_at"),
        }

    def _provider_pool_worker_test_payload(
        self,
        task: Dict[str, Any],
        run: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        assignment = self._provider_pool_worker_assignment(str(task.get("worker_role") or ""))
        status = str((run or {}).get("status") or "queued")
        elapsed_ms = (run or {}).get("elapsed_ms")
        if elapsed_ms is None and run and run.get("claimed_at"):
            start = datetime.fromisoformat(str(run["claimed_at"]).replace("Z", "+00:00"))
            end_value = run.get("completed_at")
            end = (
                datetime.fromisoformat(str(end_value).replace("Z", "+00:00"))
                if end_value else datetime.now(timezone.utc)
            )
            elapsed_ms = max(0, round((end - start).total_seconds() * 1000))
        return {
            "status": status,
            "test_id": str(task.get("schedule_id") or ""),
            "worker_role": assignment["role"],
            "worker_label": assignment["label"],
            "provider": str((run or {}).get("execution_provider") or assignment["provider"]),
            "model": str((run or {}).get("execution_model") or assignment["model"]),
            "elapsed_ms": elapsed_ms,
            "result": str((run or {}).get("result_summary") or "")[:4000],
            "error": str((run or {}).get("error") or "")[:1000],
            "recorded_at": str(
                (run or {}).get("completed_at")
                or (run or {}).get("claimed_at")
                or task.get("created_at")
                or ""
            ),
        }

    async def list_provider_pool_worker_tests(self) -> Dict[str, Any]:
        tasks = sorted(
            (
                task for task in self._scheduled_task_store.list(include_completed=True)
                if task.get("requested_via") == "provider_pool_test"
            ),
            key=lambda task: str(task.get("created_at") or ""),
            reverse=True,
        )[:200]
        runs_by_schedule = {
            str(run.get("schedule_id") or ""): run
            for run in self._scheduled_task_store.recent_runs(limit=200)
        }
        tests: Dict[str, Dict[str, Any]] = {}
        provider_health: Dict[str, Dict[str, Any]] = {}
        tested_providers: set[str] = set()
        for task in tasks:
            payload = self._provider_pool_worker_test_payload(
                task,
                runs_by_schedule.get(str(task.get("schedule_id") or "")),
            )
            tests.setdefault(payload["worker_role"], payload)
            provider = payload["provider"]
            if (
                provider
                and payload["status"] in {"completed", "failed"}
                and provider not in tested_providers
            ):
                tested_providers.add(provider)
                if payload["status"] == "completed":
                    provider_health[provider] = {
                        "provider": provider,
                        "status": "healthy",
                        "model": payload["model"],
                        "elapsed_ms": payload["elapsed_ms"],
                        "tested_at": payload["recorded_at"],
                        "worker_role": payload["worker_role"],
                    }
        return {
            "status": "ok",
            "tests": list(tests.values()),
            "provider_health": list(provider_health.values()),
        }

    async def get_provider_pool_worker_test(self, test_id: str) -> Dict[str, Any]:
        task = self._scheduled_store_call("get", test_id)
        if task.get("requested_via") != "provider_pool_test":
            raise HTTPException(status_code=404, detail="worker test not found")
        run = next(
            (
                candidate
                for candidate in self._scheduled_task_store.recent_runs(limit=200)
                if str(candidate.get("schedule_id") or "") == test_id
            ),
            None,
        )
        return self._provider_pool_worker_test_payload(task, run)

    async def voice_status(self) -> Dict[str, Any]:
        return self._voice_manager.status()

    async def set_voice_microphone(self, request: VoiceToggleRequest) -> Dict[str, Any]:
        if (
            request.enabled
            and self._service_runtime.stellar_mode.value != "daily_companion"
        ):
            return {
                "status": "unavailable",
                "reason": "stellar_auto_evolution_active",
            }
        self._voice_manager.set_enabled(request.enabled)
        if not request.enabled:
            await self._voice_manager.stop_continuous()
        elif self._service_runtime.stellar_mode.value == "daily_companion":
            await self.flush_pending_proactive_reminder()
        return self._voice_manager.status()

    async def set_voice_fingerprint(self, request: VoiceToggleRequest) -> Dict[str, Any]:
        if self._service_runtime.stellar_mode.value != "daily_companion":
            return {
                "status": "unavailable",
                "reason": "stellar_auto_evolution_active",
            }
        return self._voice_manager.set_fingerprint_enabled(request.enabled)

    async def record_owner_voice_template(
        self,
        request: VoiceEnrollmentRequest,
    ) -> Dict[str, Any]:
        if self._service_runtime.stellar_mode.value != "daily_companion":
            return {
                "status": "unavailable",
                "reason": "stellar_auto_evolution_active",
            }
        return await self._voice_manager.record_owner_template(
            duration_seconds=request.duration_seconds,
            sample_count=request.sample_count,
        )

    async def start_voice_session(
        self,
        request: VoiceCaptureRequest,
    ) -> Dict[str, Any]:
        if self._service_runtime.stellar_mode.value != "daily_companion":
            return {
                "status": "unavailable",
                "reason": "stellar_auto_evolution_active",
            }
        return await self._voice_manager.run_once(
            session_id=request.session_id,
        )

    async def interrupt_voice_session(self) -> Dict[str, Any]:
        self._voice_manager.interrupt()
        if self._voice_manager.status().get("continuous_task_running"):
            return await self._voice_manager.stop_continuous()
        return self._voice_manager.status()

    async def start_continuous_voice(
        self,
        request: VoiceContinuousRequest,
    ) -> Dict[str, Any]:
        if self._service_runtime.stellar_mode.value != "daily_companion":
            return {
                "status": "unavailable",
                "reason": "stellar_auto_evolution_active",
            }
        return self._voice_manager.start_continuous(session_id=request.session_id)

    async def stop_continuous_voice(self) -> Dict[str, Any]:
        return await self._voice_manager.stop_continuous()

    async def get_body_registry(self) -> Dict[str, Any]:
        return self._execution_facade.get_body_registry()

    async def get_evolution_candidate_generation_status(self) -> Dict[str, Any]:
        return self._evolution_candidate_generation_scheduler.status()

    async def register_evolution_candidate_generation_request(
        self,
        request: EvolutionCandidateGenerationRequest,
    ) -> Dict[str, Any]:
        return self._evolution_candidate_generation_scheduler.register(request)

    async def trigger_evolution_candidate_generation(
        self,
        request: EvolutionCandidateGenerationTriggerRequest,
    ) -> Dict[str, Any]:
        return await self._evolution_candidate_generation_scheduler.trigger(
            mode=request.mode,
            request_id=request.request_id,
        )

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
        from ..body_registry import BodyImprovementReport
        task_id = str(report.get("task_id") or "").strip()
        lease = report.get("execution_lease")
        lease = lease if isinstance(lease, dict) else {}
        try:
            self._autonomous_chain_store.validate_execution_lease(
                task_id,
                generation=int(lease.get("generation") or 0),
                attempt_id=str(lease.get("attempt_id") or ""),
                owner_session_id=str(report.get("session_id") or ""),
            )
        except (KeyError, TypeError, ValueError, StaleExecutionLeaseError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "stale_execution_lease", "message": str(exc)},
            ) from exc
        report = dict(report)
        report.pop("execution_lease", None)
        report.pop("session_id", None)
        parsed = BodyImprovementReport(**report)
        result = await self._body_improvement_review_service.review(parsed)
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
        self._ui_runtime.maybe_open()
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
