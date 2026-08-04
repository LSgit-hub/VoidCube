from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from VoidCube_core.runtime_paths import (
    get_legacy_project_runtime_layout,
    get_runtime_layout,
)
from plugins.memory.mem.governor_bridge import MemGovernorBridge
from systems.body_registry import BodyRegistryManager
from systems.body_runtime_migration import migrate_body_runtime
from systems.execution import (
    BodyLifecycleExecutionAdapter,
    BodyUpgradeExecutionAdapter,
    GovernorReviewExecutionAdapter,
    MemoryMaintenanceExecutionAdapter,
    VoidCubeExecutionFacade,
    WatchWindowExecutionAdapter,
    attach_execution_route_hint,
)
from systems.execution.service import VoidCubeExecutionService
from systems.lifecycle import BodyLifecycleExecutor
from systems.governor import GovernorDecisionEngine
from systems.governance_runtime_migration import consolidate_governance_event_logs
from systems.probe import ProbeExecutor, ProbeRunner
from systems.supervisor.endogenous_drive import EndogenousDriveEngine
from systems.supervisor.endogenous_governance_event_consumer import (
    EndogenousGovernanceEventConsumer,
)
from systems.supervisor.endogenous_state_repository import EndogenousStateRepository
from systems.supervisor.endogenous_strategy_memory_service import (
    EndogenousStrategyMemoryService,
)
from systems.supervisor.autonomous_chain_store import AutonomousChainStore
from systems.supervisor.autonomous_task_review import build_autonomous_chain_auto_decision
from systems.supervisor.autonomous_task_review_cycle_service import (
    AutonomousTaskReviewCycleService,
)
from systems.supervisor.autonomous_task_review_service import AutonomousTaskReviewService
from systems.supervisor.autonomous_task_state import AutonomousTaskStateService
from systems.supervisor.scheduled_tasks import ScheduledTaskStore
from systems.supervisor.schedule_allocator import ScheduleAllocator
from systems.supervisor.task_profile_policy import TaskProfilePolicy
from systems.supervisor.runtime_migration import migrate_supervisor_runtime


logger = logging.getLogger("supervisor")


def assemble_supervisor_runtime_state(supervisor: Any) -> None:
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

    def record_autonomous_task_status_change(
        task: Any,
        event_type: str,
    ) -> None:
        supervisor._record_endogenous_drive_outcome(task, event_type=event_type)

    supervisor._autonomous_task_state = AutonomousTaskStateService(
        store=supervisor._autonomous_chain_store,
        governance_repository=supervisor._governor.governance_repository,
        on_status_change=record_autonomous_task_status_change,
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
    supervisor._endogenous_governance_event_consumer = EndogenousGovernanceEventConsumer(
        load_events=lambda: supervisor._load_endogenous_governance_events(),
        persist_events=lambda snapshot: supervisor._persist_endogenous_governance_events(
            snapshot
        ),
        load_regulation=lambda: supervisor._load_endogenous_self_regulation(),
        persist_regulation=lambda snapshot: supervisor._persist_endogenous_self_regulation(
            snapshot
        ),
    )
    supervisor._endogenous_strategy_memory_service = EndogenousStrategyMemoryService()
    supervisor._task_profile_policy = TaskProfilePolicy()
    supervisor._schedule_allocator = ScheduleAllocator(
        slot_interval_seconds=int(
            supervisor.config.service_runtime.autonomous_chain_review_interval or 300
        )
    )

    def autonomous_task_auto_decision(
        task: Any,
        drive_input: dict[str, Any],
    ) -> tuple[str, str]:
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
        )

    supervisor._autonomous_task_review_service = AutonomousTaskReviewService(
        store=supervisor._autonomous_chain_store,
        task_profile_policy=supervisor._task_profile_policy,
        schedule_allocator=supervisor._schedule_allocator,
        task_state=supervisor._autonomous_task_state,
        resolve_drive_input=supervisor._resolve_runtime_drive_input_request,
        auto_decision=autonomous_task_auto_decision,
        normalize_context=supervisor._normalize_runtime_decision_context,
        build_execution_request=supervisor._build_autonomous_chain_execution_request,
        propose_memory_promotion=supervisor._propose_verified_conclusion_memory_promotion,
        build_response_fields=supervisor._build_drive_input_response_fields,
        serialize_task=supervisor._serialize_autonomous_chain_task,
        build_activity_metadata=supervisor._build_autonomous_chain_activity_metadata,
        record_activity=supervisor._record_supervisor_ui_activity,
        touch_activity=supervisor._touch_gateway_activity,
        get_active_tasks=supervisor._active_autonomous_chain_tasks,
        get_review_statuses=lambda: ["planned", "deferred", "paused"],
    )
    supervisor._autonomous_task_review_cycle_service = AutonomousTaskReviewCycleService(
        task_profile_policy=supervisor._task_profile_policy,
        task_state=supervisor._autonomous_task_state,
        list_execution_lane_tasks=(
            lambda status: supervisor._autonomous_chain_store.list_api_a_execution_lane_tasks(
                status=status
            )
        ),
        get_task=supervisor._autonomous_chain_store.get_task,
        fetch_cli_session=lambda session_id: supervisor._fetch_gateway_cli_session(session_id),
        review_tasks=lambda request: supervisor.review_autonomous_chain_tasks(request),
        consume_governance_events=lambda: supervisor._endogenous_governance_event_consumer.consume_governance_review_requests(),
        consume_alignment_events=lambda: supervisor._endogenous_governance_event_consumer.consume_alignment_requests(),
        consume_truthfulness_alerts=lambda: supervisor._endogenous_governance_event_consumer.consume_truthfulness_alerts(),
        handoff_execution=lambda task: supervisor._handoff_autonomous_chain_execution_request(task),
        handoff_limit=lambda: supervisor.config.service_runtime.autonomous_chain_handoff_limit_per_cycle,
    )
    supervisor._endogenous_drive_engine = EndogenousDriveEngine(config=supervisor.config)
    supervisor._body_lifecycle_state_executor = BodyLifecycleExecutor(supervisor._body_registry)
    supervisor._probe_runner = ProbeRunner()
    supervisor._probe_executor = ProbeExecutor()


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
    supervisor._execution_service = VoidCubeExecutionService(
        supervisor._execution_facade,
        app=supervisor.app,
        standalone=False,
    )


