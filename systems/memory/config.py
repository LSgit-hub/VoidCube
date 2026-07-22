from __future__ import annotations

from pydantic import BaseModel, Field

from VoidCube_core.runtime_paths import get_runtime_layout


def _default_memory_db_path() -> str:
    return str(get_runtime_layout().memory_db)


class MemoryServiceConfig(BaseModel):
    """Single runtime configuration model for the Memory Service."""

    host: str = "127.0.0.1"
    port: int = 6001
    db_path: str = Field(default_factory=_default_memory_db_path)
    gateway_address: str = "http://127.0.0.1:6000"
    gateway_registration_check_interval: int = 30
    decay_interval_hours: int = Field(default=24, gt=0)
    compression_interval: int = 3600
    tier1_retention_days: int = 30
    tier1_max_turns: int = 10000
    tier1_decay_rate: float = Field(default=0.99, ge=0.0, le=1.0)
    tier1_min_relevance: float = 0.1
    tier1_archive_keep_original: bool = True
    tier2_min_event_coverage: float = Field(default=0.8, ge=0.0, le=1.0)
    tier2_min_backlink_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    tier2_max_compression_ratio: float = Field(default=1.0, gt=0.0)
    tier2_max_degraded_fraction: float = Field(default=1.0, ge=0.0, le=1.0)
    backup_retention_count: int = Field(default=5, ge=1, le=100)
