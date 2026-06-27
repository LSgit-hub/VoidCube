from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SupervisorExecutionConfig(BaseModel):
    gateway_address: str = "http://127.0.0.1:6000"
    memory_gateway_path: str = "/mem/"
    agent_base_port: int = 6080
    git_repo_path: str = "./"
    probe_watch_window_seconds: int = 300


class SupervisorServiceRuntimeConfig(BaseModel):
    health_check_interval: int = 30
    self_evolution_review_interval: int = 300
    # Deprecated: compression is now owned by MemoryService (baseline §3.4).
    # Kept for config-file compatibility; no longer read by the supervisor.
    memory_compression_interval: int = 3600
    endogenous_drive_enabled: bool = True
    endogenous_drive_interval: int = 300
    endogenous_drive_max_candidates: int = 3
    # ── Body improvement config (baseline §7.4) ──
    body_improvement_min_quality: float = 60.0  # learning quality threshold to trigger
    body_improvement_editable_dirs: list[str] = ["skills/", "tools/", "agent/", "prompts/"]
    body_improvement_forbidden_patterns: list[str] = ["**/credential*", "**/.env*", "systems/**"]
    body_improvement_max_files: int = 5
    # Execution window: self_evolution / body_upgrade tasks only auto-execute
    # during [execution_window_start_hour, execution_window_end_hour).
    # Baseline default: 0–6. Automatic execution outside this window stays queued.
    execution_window_start_hour: int = 0
    execution_window_end_hour: int = 6
    # Interval in seconds for the structured 4-layer memory maintenance loop
    # (Event→Scene→Arc→Epoch compression via MemoryMaintenanceEngine).
    # Runs in Memory Mode independently of the task queue.  0 = disabled.
    structured_memory_maintenance_interval: int = 3600


class SupervisorBodyRuntimeConfig(BaseModel):
    slots_dir_name: str = ".body-slots"
    registry_file_name: str = ".body-registry.json"
    slot_a_name: str = "slot-A"
    slot_b_name: str = "slot-B"
    stable_window_days: int = 3
    stable_health_checks: int = 3


class SupervisorConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6002
    execution: SupervisorExecutionConfig = Field(default_factory=SupervisorExecutionConfig)
    service_runtime: SupervisorServiceRuntimeConfig = Field(default_factory=SupervisorServiceRuntimeConfig)
    body_runtime: SupervisorBodyRuntimeConfig = Field(default_factory=SupervisorBodyRuntimeConfig)
    ui_enabled: bool = True
    ui_auto_open: bool = True
    ui_auto_open_delay_seconds: float = 1.0
    ui_event_interval_seconds: float = 3.0
    ui_activity_buffer_size: int = 100
    ui_path: str = "/ui"
    soul_store_path: Optional[str] = None
    self_evolution_queue_path: Optional[str] = None
