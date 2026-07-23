from __future__ import annotations

import logging
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
from systems.supervisor.autonomous_chain_store import AutonomousChainStore
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
        if governance_result.status in {"migrated", "recovered_retry"}:
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
    supervisor._endogenous_drive_history_path = runtime_root / "endogenous_drive_history.json"
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


