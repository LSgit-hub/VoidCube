from __future__ import annotations

from pathlib import Path
from typing import Any

from plugins.memory.mem.governor_bridge import MemGovernorBridge
from systems.body_registry import BodyRegistryManager
from systems.execution import (
    AgentLifecycleExecutionAdapter,
    BodyLifecycleExecutionAdapter,
    BodyUpgradeExecutionAdapter,
    GovernorReviewExecutionAdapter,
    MemoryMaintenanceExecutionAdapter,
    VoidCubeExecutionFacade,
    WatchWindowExecutionAdapter,
    attach_execution_route_hint,
)
from systems.lifecycle import BodyLifecycleExecutor
from systems.probe import ProbeExecutor, ProbeRunner
from systems.supervisor.endogenous_drive import EndogenousDriveEngine
from systems.supervisor.task_queue import SelfEvolutionTaskQueue


def assemble_supervisor_runtime_state(supervisor: Any) -> None:
    execution_config = supervisor.config.execution
    body_runtime_config = supervisor.config.body_runtime
    supervisor._body_registry = BodyRegistryManager(
        execution_config.git_repo_path,
        slot_ids=(body_runtime_config.slot_a_name, body_runtime_config.slot_b_name),
        slots_dir_name=body_runtime_config.slots_dir_name,
        registry_file_name=body_runtime_config.registry_file_name,
    )
    supervisor._body_registry.initialize_layout()

    runtime_root = Path(
        supervisor.config.soul_store_path
        or (Path(execution_config.git_repo_path) / ".soul-runtime")
    )
    supervisor._runtime_root = runtime_root
    supervisor._governor = MemGovernorBridge(storage_root=runtime_root)
    supervisor._self_evolution_queue = SelfEvolutionTaskQueue(
        supervisor.config.self_evolution_queue_path
        or (runtime_root / "self_evolution_queue.json")
    )
    supervisor._endogenous_drive_engine = EndogenousDriveEngine()
    supervisor._body_lifecycle_state_executor = BodyLifecycleExecutor(supervisor._body_registry)
    supervisor._probe_runner = ProbeRunner()
    supervisor._probe_executor = ProbeExecutor()


def assemble_supervisor_execution_runtime(supervisor: Any) -> None:
    execution_config = supervisor.config.execution

    # S-01: Agent process lifecycle is now owned by the executor layer.
    # The supervisor injects the ProcessManager rather than owning the
    # spawn/terminate/monitor implementations directly.
    from systems.execution.process_manager import AgentProcessManager
    supervisor._process_manager = AgentProcessManager(
        agent_model=supervisor._agent_model,
    )

    supervisor._agent_lifecycle_executor = AgentLifecycleExecutionAdapter(
        config=execution_config,
        agents=supervisor._agents,
        agent_model=supervisor._agent_model,
        spawn_agent_process=supervisor._spawn_agent_process,
        terminate_agent_process=supervisor._terminate_agent_process,
        register_agent_with_gateway=supervisor._register_agent_with_gateway,
        unregister_agent_from_gateway=supervisor._unregister_agent_from_gateway,
        attach_execution_route_hint=attach_execution_route_hint,
    )
    supervisor._body_lifecycle_executor = BodyLifecycleExecutionAdapter(
        config=execution_config,
        body_registry=supervisor._body_registry,
        lifecycle=supervisor._body_lifecycle_state_executor,
        probe_runner=supervisor._probe_runner,
        probe_executor=supervisor._probe_executor,
        governor_storage_root=(
            str(supervisor._governor.storage_root)
            if hasattr(supervisor._governor, "storage_root")
            else str(Path(supervisor.config.execution.git_repo_path) / ".soul-runtime")
        ),
        attach_execution_route_hint=attach_execution_route_hint,
    )
    supervisor._watch_window_executor = WatchWindowExecutionAdapter(
        body_registry=supervisor._body_registry,
        agents=supervisor._agents,
        stop_agent=supervisor._agent_lifecycle_executor.stop_agent,
        run_health_checks=supervisor.run_health_checks,
        runtime_state=supervisor._watch_window_runtime,
        sync_gateway_body_activation=supervisor._sync_gateway_body_activation,
    )
    supervisor._body_upgrade_executor = BodyUpgradeExecutionAdapter(
        config=execution_config,
        body_registry=supervisor._body_registry,
        run_body_probe=supervisor._body_lifecycle_executor.run_body_probe,
        start_agent=supervisor._agent_lifecycle_executor.start_managed_agent,
        wait_for_health=supervisor._wait_for_health,
        sync_gateway_body_activation=supervisor._sync_gateway_body_activation,
        attach_execution_route_hint=attach_execution_route_hint,
        agents=supervisor._agents,
        governor_storage_root=(
            getattr(getattr(supervisor, "_governor", None), "storage_root", None)
            or str(runtime_root)
        ),
    )
    supervisor._memory_maintenance_executor = MemoryMaintenanceExecutionAdapter(
        config=execution_config,
        attach_execution_route_hint=attach_execution_route_hint,
        mem_state_path=None,  # auto-resolve ~/.VoidCube/mem_state.json
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
        agent_lifecycle=supervisor._agent_lifecycle_executor,
        watch_window=supervisor._watch_window_executor,
        body_lifecycle=supervisor._body_lifecycle_executor,
        body_upgrade=supervisor._body_upgrade_executor,
        memory_maintenance=supervisor._memory_maintenance_executor,
        supervisor=supervisor,
    )
