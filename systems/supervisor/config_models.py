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
    memory_compression_interval: int = 3600
    self_evolution_review_interval: int = 300
    endogenous_drive_enabled: bool = True
    endogenous_drive_interval: int = 300
    endogenous_drive_max_candidates: int = 3


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
