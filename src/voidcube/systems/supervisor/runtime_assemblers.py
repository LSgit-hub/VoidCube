from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from ..evolution_evaluation import EnvironmentCapabilityPolicy
from ..evolution_candidate_generation import (
    JsonEvolutionCandidateGenerationRepository,
)
from ...infrastructure.runtime.layout import (
    get_legacy_project_runtime_layout,
    get_runtime_layout,
)
from ...infrastructure.memory.governor_bridge import MemGovernorBridge
from ..body_registry import BodyRegistryManager
from ..body_runtime_migration import migrate_body_runtime
from ..execution import (
    BodyLifecycleExecutionAdapter,
    BodyUpgradeExecutionAdapter,
    GovernorReviewExecutionAdapter,
    MemoryMaintenanceExecutionAdapter,
    VoidCubeExecutionFacade,
    WatchWindowExecutionAdapter,
    attach_execution_route_hint,
)
from ..execution.service import VoidCubeExecutionService
from ..lifecycle import BodyLifecycleExecutor
from ..governor import GovernorDecisionEngine
from ..governance_runtime_migration import consolidate_governance_event_logs
from ..probe import ProbeExecutor, ProbeRunner
from .endogenous_drive import EndogenousDriveEngine
from .evolution_evaluation_governance import (
    EvolutionEvaluationGovernanceVerifier,
)
from .evolution_candidate_generation_scheduler import (
    EvolutionCandidateGenerationScheduler,
    TERMINAL_BODY_TASK_STATUSES,
)
from .evolution_candidate_generation_service import (
    EvolutionCandidateGenerationService,
)
from .endogenous_governance_event_consumer import (
    EndogenousGovernanceEventConsumer,
)
from .endogenous_state_repository import EndogenousStateRepository
from .endogenous_drive_history_persistence_service import (
    EndogenousDriveHistoryPersistenceService,
)
from .autonomous_employee_dispatch_service import AutonomousEmployeeDispatchService
from .autonomous_chain_planning_service import (
    AutonomousChainPlanningService,
)
from .autonomous_chain_recovery_service import (
    AutonomousChainRecoveryService,
)
from .autonomous_chain_runtime_reset_service import (
    AutonomousChainRuntimeResetService,
)
from .autonomous_body_switch_consent_service import (
    AutonomousBodySwitchConsentService,
)
from .autonomous_cycle_service import AutonomousCycleService
from .body_improvement_review_service import (
    BodyImprovementReviewService,
)
from .endogenous_cognitive_history_summary_service import (
    EndogenousCognitiveHistorySummaryService,
)
from .endogenous_cognition_state_assembly_service import (
    EndogenousCognitionStateAssemblyService,
)
from .endogenous_governance_state_persistence_service import (
    EndogenousGovernanceStatePersistenceService,
)
from .endogenous_strategy_memory_service import (
    EndogenousStrategyMemoryService,
)
from .endogenous_self_regulation_service import (
    EndogenousSelfRegulationService,
)
from .endogenous_cognitive_posture_service import (
    EndogenousCognitivePostureService,
)
from .autonomous_chain_store import AutonomousChainStore
from .autonomous_task_review import build_autonomous_chain_auto_decision
from .autonomous_task_review_cycle_service import (
    AutonomousTaskReviewCycleService,
)
from .autonomous_task_governance_review_service import (
    AutonomousTaskGovernanceReviewService,
)
from .autonomous_task_memory_promotion_service import (
    AutonomousTaskMemoryPromotionService,
)
from .autonomous_task_review_service import AutonomousTaskReviewService
from .autonomous_task_state import AutonomousTaskStateService
from .scheduled_tasks import ScheduledTaskStore
from .schedule_allocator import ScheduleAllocator
from .task_profile_policy import TaskProfilePolicy
from .runtime_migration import migrate_supervisor_runtime
from .planning_runtime import SUPERVISOR_LEGAL_SCENES
from .ui_runtime import (
    SupervisorUIRuntime,
    SupervisorUIRuntimePorts,
)


logger = logging.getLogger("supervisor")


def assemble_supervisor_runtime_state(supervisor: Any) -> None:
    def record_ui_activity(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return supervisor._ui_runtime.record_activity(*args, **kwargs)

    def clear_ui_activity() -> None:
        supervisor._ui_runtime.clear_activity()

    execution_config = supervisor.config.execution
    body_runtime_config = supervisor.config.body_runtime
    body_state_root = Path(body_runtime_config.state_root)
    canonical_body_root = get_runtime_layout().body_root
    if body_state_root.resolve() == canonical_body_root.resolve():
        body_result = migrate_body_runtime(
            source_root=execution_config.git_repo_path,
            target_root=canonical_body_root,
        )
        if body_result.status == "migrated":
            logger.info(
                "Migrated Body runtime from %s to %s "
                "(%d files verified, %d linked worktrees repaired)",
                body_result.source_root,
                body_result.target_root,
                body_result.files_verified,
                body_result.linked_worktrees_repaired,
            )
    supervisor._body_registry = BodyRegistryManager(
        execution_config.git_repo_path,
        state_root=body_state_root,
        slot_ids=(body_runtime_config.slot_a_name, body_runtime_config.slot_b_name),
    )
    supervisor._body_registry.initialize_layout()

    runtime_root = Path(supervisor.config.soul_store_path)
    canonical_root = get_runtime_layout().supervisor_root
    if runtime_root.resolve() == canonical_root.resolve():
        result = migrate_supervisor_runtime(
            source=get_legacy_project_runtime_layout(
                execution_config.git_repo_path
            ).supervisor_root,
            target=canonical_root,
        )
        if result.status == "migrated":
            logger.info(
                "Migrated Supervisor runtime from %s to %s (%d files verified)",
                result.source,
                result.target,
                result.files_verified,
            )
        governance_result = consolidate_governance_event_logs(
            sources=(
                get_legacy_project_runtime_layout(
                    execution_config.git_repo_path
                ).mem_governance_log,
                canonical_root / "self-learning" / "mem_governance.jsonl",
            ),
            target=get_runtime_layout().supervisor_governance_log,
        )
        if governance_result.status in {"migrated", "recovered_retry", "normalized"}:
            logger.info(
                "Consolidated governance events into %s "
                "(%d source, %d existing, %d merged, %d duplicates removed)",
                governance_result.target,
                governance_result.source_events,
                governance_result.target_events,
                governance_result.merged_events,
                governance_result.duplicates_removed,
            )
    supervisor._runtime_root = runtime_root
    governor_engine = None
    if not supervisor.config.service_runtime.governor_llm_advisory_enabled:
        governor_engine = GovernorDecisionEngine()
    supervisor._governor = MemGovernorBridge(
        storage_root=runtime_root,
        engine=governor_engine,
    )
    supervisor._autonomous_chain_store = AutonomousChainStore(
        supervisor.config.autonomous_chain_store_path
        or (runtime_root / "autonomous_chain_store.json")
    )

    def load_mem_governance_events() -> list[Any]:
        return supervisor._governor.governance_repository.list_events()

    def mem_governance_repository_path() -> Path:
        repository = supervisor._governor.governance_repository
        repository_path = getattr(repository, "path", None)
        if repository_path:
            return Path(repository_path)
        return Path(supervisor._governor.storage_root) / "mem_governance.jsonl"

    supervisor._autonomous_chain_recovery_service = AutonomousChainRecoveryService(
        store=supervisor._autonomous_chain_store,
        load_governance_events=load_mem_governance_events,
        governance_repository_path=mem_governance_repository_path,
        touch_activity=supervisor._touch_gateway_activity,
    )
    def record_employee_task_status_change(
        task: Any,
        event_type: str,
    ) -> None:
        supervisor._record_endogenous_drive_outcome(task, event_type=event_type)

    supervisor._autonomous_task_state = AutonomousTaskStateService(
        store=supervisor._autonomous_chain_store,
        governance_repository=supervisor._governor.governance_repository,
        on_status_change=record_employee_task_status_change,
    )
    supervisor._autonomous_body_switch_consent_service = AutonomousBodySwitchConsentService(
        store=supervisor._autonomous_chain_store,
        task_state=supervisor._autonomous_task_state,
    )
    supervisor._autonomous_task_memory_promotion_service = (
        AutonomousTaskMemoryPromotionService(
            task_state=supervisor._autonomous_task_state,
            gateway_address=execution_config.gateway_address,
            gateway_memory_headers=supervisor._gateway_memory_headers,
        )
    )
    scheduled_store_path = (
        Path(supervisor.config.scheduled_task_store_path)
        if supervisor.config.scheduled_task_store_path
        else runtime_root / "scheduled_tasks.db"
    )
    supervisor._scheduled_task_store = ScheduledTaskStore(
        scheduled_store_path,
        legacy_json_path=(
            runtime_root / "scheduled_tasks.json"
            if not supervisor.config.scheduled_task_store_path
            else None
        ),
    )

    supervisor._endogenous_state_repository = EndogenousStateRepository(runtime_root)
    supervisor._endogenous_drive_history_persistence_service = (
        EndogenousDriveHistoryPersistenceService(supervisor._endogenous_state_repository)
    )

    async def clear_gateway_activity() -> None:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{execution_config.gateway_address}/admin/activity/clear",
                timeout=aiohttp.ClientTimeout(total=5),
            )

    def reset_autonomous_schedule() -> None:
        runtime = supervisor._service_runtime
        runtime.last_review_at = None
        runtime.next_review_at = None
        runtime.last_drive_at = None
        runtime.next_drive_at = None
        runtime.suppress_candidate_refresh = True

    supervisor._autonomous_chain_runtime_reset_service = AutonomousChainRuntimeResetService(
        list_tasks=supervisor._autonomous_chain_store.list_tasks,
        clear_tasks=supervisor._autonomous_task_state.clear_tasks,
        clear_ui_activity=clear_ui_activity,
        clear_governor_projection=supervisor._governor.clear_runtime_projection,
        default_drive_history=supervisor._endogenous_drive_history_persistence_service.default_snapshot,
        persist_drive_history=supervisor._endogenous_drive_history_persistence_service.persist,
        clear_gateway_activity=clear_gateway_activity,
        reset_schedule=reset_autonomous_schedule,
        reset_watch_window=lambda: setattr(supervisor, "_watch_window_last_outcome", None),
    )
    supervisor._endogenous_governance_state_persistence_service = (
        EndogenousGovernanceStatePersistenceService(
            supervisor._endogenous_state_repository,
            endogenous_drive_enabled=lambda: bool(
                supervisor.config.service_runtime.endogenous_drive_enabled
            ),
        )
    )
    supervisor._endogenous_governance_event_consumer = EndogenousGovernanceEventConsumer(
        load_events=supervisor._endogenous_governance_state_persistence_service.load_governance_events,
        persist_events=supervisor._endogenous_governance_state_persistence_service.persist_governance_events,
        load_regulation=supervisor._endogenous_governance_state_persistence_service.load_self_regulation,
        persist_regulation=supervisor._endogenous_governance_state_persistence_service.persist_self_regulation,
    )
    supervisor._endogenous_strategy_memory_service = EndogenousStrategyMemoryService()
    supervisor._endogenous_cognitive_history_summary_service = (
        EndogenousCognitiveHistorySummaryService()
    )
    supervisor._endogenous_cognitive_posture_service = EndogenousCognitivePostureService(
        runtime_config=supervisor.config.service_runtime,
    )
    supervisor._endogenous_cognition_state_assembly_service = (
        EndogenousCognitionStateAssemblyService(
            load_drive_history=supervisor._endogenous_drive_history_persistence_service.load,
            enabled=lambda: bool(
                supervisor.config.service_runtime.endogenous_drive_enabled
            ),
            drive_posture_from_deliberation=supervisor._drive_posture_signal_from_deliberation,
            derive_context_key=supervisor._derive_endogenous_context_key,
            build_observation_program=supervisor._build_endogenous_observation_program,
            build_meta_governance=supervisor._build_endogenous_meta_governance,
            load_reasoning_state=lambda: supervisor._lm_generation_application_state().reasoning_state,
            posture_service=supervisor._endogenous_cognitive_posture_service,
            history_summary_service=supervisor._endogenous_cognitive_history_summary_service,
        )
    )
    supervisor._endogenous_self_regulation_service = EndogenousSelfRegulationService()
    supervisor._task_profile_policy = TaskProfilePolicy()

    def resolve_autonomous_worker_role(requested_role: str) -> str:
        from ...application.companion_workers import resolve_companion_worker_role
        from ...infrastructure.config.configuration import load_config

        return resolve_companion_worker_role(load_config(), requested_role).role

    supervisor._autonomous_employee_dispatch_service = (
        AutonomousEmployeeDispatchService(
            task_state=supervisor._autonomous_task_state,
            task_store=supervisor._autonomous_chain_store,
            scheduled_task_store=supervisor._scheduled_task_store,
            task_profile_policy=supervisor._task_profile_policy,
            resolve_worker_role=resolve_autonomous_worker_role,
            touch_gateway_activity=supervisor._touch_gateway_activity,
            record_ui_activity=record_ui_activity,
            review_body_improvement=lambda report: supervisor._body_improvement_review_service.review(
                report
            ),
            promote_memory=supervisor._autonomous_task_memory_promotion_service.propose,
        )
    )
    supervisor._schedule_allocator = ScheduleAllocator(
        slot_interval_seconds=int(
            supervisor.config.service_runtime.autonomous_chain_review_interval or 300
        )
    )
    supervisor._autonomous_chain_planning_service = AutonomousChainPlanningService(
        store=supervisor._autonomous_chain_store,
        task_state=supervisor._autonomous_task_state,
        task_profile_policy=supervisor._task_profile_policy,
        schedule_allocator=supervisor._schedule_allocator,
        build_activity_metadata=supervisor._build_autonomous_chain_activity_metadata,
        record_activity=record_ui_activity,
        record_drive_outcome=supervisor._record_endogenous_drive_outcome,
        touch_activity=supervisor._touch_gateway_activity,
    )

    foundation_root = Path(
        getattr(supervisor, "_runtime_root", None)
        or supervisor.config.soul_store_path
    ) / "evolution-foundation"
    evolution_capability_policy = EnvironmentCapabilityPolicy.for_profile(
        supervisor.config.service_runtime.evolution_capability_policy_profile
    )
    supervisor._evolution_capability_policy = evolution_capability_policy
    supervisor._evolution_evaluation_governance_verifier = (
        EvolutionEvaluationGovernanceVerifier.from_root(
            foundation_root,
            capability_policy=evolution_capability_policy,
        )
    )

    candidate_repository = JsonEvolutionCandidateGenerationRepository(
        foundation_root / "candidate-generation"
    )
    candidate_service: EvolutionCandidateGenerationService | None = None

    async def execute_candidate_generation(
        request_id: str,
        *,
        lease_owner: str,
    ) -> Any:
        nonlocal candidate_service
        if candidate_service is None:
            candidate_service = EvolutionCandidateGenerationService.from_root(
                supervisor.config.execution.git_repo_path,
                foundation_root,
                capability_policy_profile=(
                    supervisor.config.service_runtime.evolution_capability_policy_profile
                ),
            )
        return await candidate_service.execute(
            request_id,
            lease_owner=lease_owner,
        )

    def has_active_body_task() -> bool:
        return any(
            supervisor._task_profile_policy.execution_kind(task)
            == "body_improvement"
            and str(task.status or "").strip().lower()
            not in TERMINAL_BODY_TASK_STATUSES
            for task in supervisor._autonomous_chain_store.list_chain_projection_tasks()
        )

    supervisor._evolution_candidate_generation_scheduler = (
        EvolutionCandidateGenerationScheduler(
            repository=candidate_repository,
            execute=execute_candidate_generation,
            automatic_enabled=lambda: bool(
                supervisor.config.service_runtime.evolution_candidate_generation_enabled
            ),
            load_runtime_observation=supervisor.get_runtime_observation_input,
            has_active_body_task=has_active_body_task,
        )
    )

    def autonomous_task_auto_decision(
        task: Any,
        drive_input: dict[str, Any],
    ) -> tuple[str, str]:
        if supervisor._task_profile_policy.execution_kind(task) == "body_improvement":
            result_id = str(
                dict(task.evidence or {}).get("experiment_result_id") or ""
            ).strip()
            evaluation_authorization = (
                supervisor._evolution_evaluation_governance_verifier.verify(result_id)
            )
        else:
            evaluation_authorization = {}
        return build_autonomous_chain_auto_decision(
            task=task,
            drive_input=drive_input,
            autonomous_chain_gate_active=bool(
                getattr(
                    supervisor._service_runtime,
                    "autonomous_chain_gate_active",
                    False,
                )
            ),
            task_profile_policy=supervisor._task_profile_policy,
            active_tasks=supervisor._active_autonomous_chain_tasks(),
            learning_history=supervisor._autonomous_chain_store.list_writeback_history(
                status="completed"
            ),
            now=datetime.now(timezone.utc),
            body_improvement_min_quality=float(
                getattr(
                    supervisor.config.service_runtime,
                    "body_improvement_min_quality",
                    60.0,
                )
                or 60.0
            ),
            evaluation_authorization=evaluation_authorization,
        )

    supervisor._autonomous_task_governance_review_service = (
        AutonomousTaskGovernanceReviewService(
            store=supervisor._autonomous_chain_store,
            task_profile_policy=supervisor._task_profile_policy,
            schedule_allocator=supervisor._schedule_allocator,
        )
    )
    supervisor._autonomous_task_review_service = AutonomousTaskReviewService(
        store=supervisor._autonomous_chain_store,
        task_profile_policy=supervisor._task_profile_policy,
        schedule_allocator=supervisor._schedule_allocator,
        task_state=supervisor._autonomous_task_state,
        resolve_drive_input=supervisor._resolve_runtime_drive_input_request,
        auto_decision=autonomous_task_auto_decision,
        normalize_context=supervisor._normalize_runtime_decision_context,
        propose_memory_promotion=supervisor._autonomous_task_memory_promotion_service.propose,
        build_response_fields=supervisor._build_drive_input_response_fields,
        serialize_task=supervisor._autonomous_chain_planning_service.serialize_task,
        build_activity_metadata=supervisor._build_autonomous_chain_activity_metadata,
        record_activity=record_ui_activity,
        touch_activity=supervisor._touch_gateway_activity,
        get_active_tasks=supervisor._active_autonomous_chain_tasks,
        get_review_statuses=lambda: ["planned", "deferred", "paused"],
        review_adviser=supervisor._autonomous_task_governance_review_service.review,
        planning_activity_kind_for_task=supervisor._planning_activity_kind_for_task,
    )
    supervisor._autonomous_task_review_cycle_service = AutonomousTaskReviewCycleService(
        list_execution_lane_tasks=(
            lambda status: supervisor._autonomous_chain_store.list_employee_execution_lane_tasks(
                status=status
            )
        ),
        get_task=supervisor._autonomous_chain_store.get_task,
        review_tasks=lambda request: supervisor._autonomous_task_review_service.review(request),
        consume_governance_events=lambda: supervisor._endogenous_governance_event_consumer.consume_governance_review_requests(),
        consume_alignment_events=lambda: supervisor._endogenous_governance_event_consumer.consume_alignment_requests(),
        consume_truthfulness_alerts=lambda: supervisor._endogenous_governance_event_consumer.consume_truthfulness_alerts(),
        dispatch_employee=supervisor._autonomous_employee_dispatch_service.dispatch,
        reconcile_employees=supervisor._autonomous_employee_dispatch_service.reconcile,
        dispatch_limit=lambda: supervisor.config.service_runtime.employee_dispatch_limit_per_cycle,
    )

    def update_drive_schedule(last_at: datetime, next_at: datetime) -> None:
        supervisor._service_runtime.last_drive_at = last_at
        supervisor._service_runtime.next_drive_at = next_at

    def update_review_schedule(last_at: datetime, next_at: datetime) -> None:
        supervisor._service_runtime.last_review_at = last_at
        supervisor._service_runtime.next_review_at = next_at

    supervisor._autonomous_cycle_service = AutonomousCycleService(
        runtime_config=supervisor.config.service_runtime,
        evaluate_drive=supervisor.evaluate_endogenous_drive,
        drive_input_fields_from_evaluation=supervisor._drive_input_fields_from_evaluation,
        load_drive_history=supervisor._endogenous_drive_history_persistence_service.load,
        load_governance_events=supervisor._endogenous_governance_state_persistence_service.load_governance_events,
        load_cognition_state=supervisor._endogenous_governance_state_persistence_service.load_cognition_state,
        persist_evaluation=supervisor._persist_endogenous_evaluation_for_candidates,
        restore_evaluation_snapshots=supervisor._restore_endogenous_evaluation_snapshots,
        lm_generation_application_state=supervisor._lm_generation_application_state,
        plan_autonomous_chain_task=supervisor._autonomous_chain_planning_service.plan,
        record_ui_activity=record_ui_activity,
        touch_gateway_activity=supervisor._touch_gateway_activity,
        run_review_cycle=supervisor._autonomous_task_review_cycle_service.run,
        update_drive_schedule=update_drive_schedule,
        update_review_schedule=update_review_schedule,
        schedule_candidate_generation=(
            supervisor._evolution_candidate_generation_scheduler.trigger
        ),
    )
    supervisor._endogenous_drive_engine = EndogenousDriveEngine(config=supervisor.config)
    supervisor._body_lifecycle_state_executor = BodyLifecycleExecutor(supervisor._body_registry)
    supervisor._probe_runner = ProbeRunner()
    supervisor._probe_executor = ProbeExecutor()


def assemble_supervisor_ui_runtime(supervisor: Any) -> None:
    def record_activity_history(event: dict[str, Any]) -> None:
        governor = getattr(supervisor, "_governor", None)
        if governor is None or not hasattr(governor, "record_supervisor_activity"):
            return
        try:
            governor.record_supervisor_activity(event=event)
        except Exception:
            return

    config = supervisor.config
    runtime_root = Path(
        getattr(supervisor, "_runtime_root", None) or config.soul_store_path
    ).resolve()
    supervisor._ui_runtime = SupervisorUIRuntime(
        SupervisorUIRuntimePorts(
            runtime_root=runtime_root,
            activity_buffer_size=config.ui_activity_buffer_size,
            legal_scenes=SUPERVISOR_LEGAL_SCENES,
            record_activity_history=record_activity_history,
            load_gateway_url=lambda: str(
                supervisor.config.execution.gateway_address
            ).rstrip("/"),
            gateway_memory_headers=supervisor._gateway_memory_headers,
            ui_event_interval_seconds=config.ui_event_interval_seconds,
            voice_realtime_status=supervisor._voice_manager.realtime_status,
            load_runtime_observation_input=supervisor.get_runtime_observation_input,
            inspect_body_layout=supervisor._body_registry.inspect_layout,
            load_body_slot_meta=supervisor._body_registry.load_slot_meta,
            collect_trace_records_from_tasks=supervisor._collect_trace_records_from_tasks,
            collect_trace_records_from_supervisor_activity=(
                supervisor._collect_trace_records_from_supervisor_activity
            ),
            collect_trace_records_from_governor_history=(
                supervisor._collect_trace_records_from_governor_history
            ),
            build_trace_timeline=supervisor._build_trace_timeline,
            summarize_single_trace=supervisor._summarize_single_trace,
            load_runtime_config=lambda: supervisor.config.service_runtime,
            list_chain_projection_tasks=(
                supervisor._autonomous_chain_store.list_chain_projection_tasks
            ),
            serialize_chain_task=(
                supervisor._autonomous_chain_planning_service.serialize_task
            ),
            load_cognition_state=(
                supervisor._endogenous_governance_state_persistence_service.load_cognition_state
            ),
            stellar_mode_status=supervisor._stellar_mode_status,
            voice_status=supervisor._voice_manager.status,
            ui_enabled=config.ui_enabled,
            ui_auto_open=config.ui_auto_open,
            ui_url=f"http://{config.host}:{config.port}{config.ui_path}",
            ui_auto_open_delay_seconds=config.ui_auto_open_delay_seconds,
        )
    )


def assemble_supervisor_execution_runtime(supervisor: Any) -> None:
    execution_config = supervisor.config.execution
    supervisor._body_lifecycle_executor = BodyLifecycleExecutionAdapter(
        config=execution_config,
        body_registry=supervisor._body_registry,
        lifecycle=supervisor._body_lifecycle_state_executor,
        probe_runner=supervisor._probe_runner,
        probe_executor=supervisor._probe_executor,
        governor_storage_root=(
            str(supervisor._governor.storage_root)
            if hasattr(supervisor._governor, "storage_root")
            else str(supervisor._runtime_root)
        ),
        attach_execution_route_hint=attach_execution_route_hint,
        governor=supervisor._governor,
    )
    supervisor._watch_window_executor = WatchWindowExecutionAdapter(
        body_registry=supervisor._body_registry,
        agents=supervisor._agents,
        stop_agent=None,
        run_health_checks=supervisor.run_health_checks,
    )
    supervisor._body_upgrade_executor = BodyUpgradeExecutionAdapter(
        config=execution_config,
        body_registry=supervisor._body_registry,
        run_body_probe=supervisor._body_lifecycle_executor.run_body_probe,
        attach_execution_route_hint=attach_execution_route_hint,
        agents=supervisor._agents,
        governance_repository=supervisor._governor.governance_repository,
    )
    supervisor._memory_maintenance_executor = MemoryMaintenanceExecutionAdapter(
        config=execution_config,
        attach_execution_route_hint=attach_execution_route_hint,
    )
    supervisor._governor_review_executor = GovernorReviewExecutionAdapter(
        body_registry=supervisor._body_registry,
        governor=supervisor._governor,
        lifecycle=supervisor._body_lifecycle_state_executor,
        watch_window_runtime_sync=supervisor._watch_window_executor,
    )
    supervisor._watch_window_executor.bind_governor_request_executor(supervisor._governor_review_executor)
    supervisor._body_upgrade_executor.bind_governor_request_executor(supervisor._governor_review_executor)
    supervisor._execution_facade = VoidCubeExecutionFacade(
        watch_window=supervisor._watch_window_executor,
        body_lifecycle=supervisor._body_lifecycle_executor,
        body_upgrade=supervisor._body_upgrade_executor,
        memory_maintenance=supervisor._memory_maintenance_executor,
        governor_review=supervisor._governor_review_executor,
        supervisor=supervisor,
    )
    supervisor._body_improvement_review_service = BodyImprovementReviewService(
        body_registry=supervisor._body_registry,
        task_store=supervisor._autonomous_chain_store,
        task_profile_policy=supervisor._task_profile_policy,
        execution_facade_provider=lambda: supervisor._execution_facade,
        evaluation_governance_verifier=(
            supervisor._evolution_evaluation_governance_verifier
        ),
    )
    supervisor._execution_service = VoidCubeExecutionService(
        supervisor._execution_facade,
        app=supervisor.app,
        standalone=False,
    )


