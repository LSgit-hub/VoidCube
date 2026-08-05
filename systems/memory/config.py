from __future__ import annotations

from ipaddress import ip_address

from pydantic import BaseModel, ConfigDict, Field, field_validator

from VoidCube_core.runtime_paths import get_runtime_layout


def _default_memory_db_path() -> str:
    return str(get_runtime_layout().memory_db)


class MemoryServiceConfig(BaseModel):
    """Single runtime configuration model for the Memory Service."""

    model_config = ConfigDict(validate_assignment=True)

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
    tier2_batch_size: int = Field(default=100, ge=1, le=1000)
    tier2_min_event_coverage: float = Field(default=0.8, ge=0.0, le=1.0)
    tier2_min_backlink_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    tier2_max_compression_ratio: float = Field(default=1.0, gt=0.0)
    tier2_max_degraded_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    tier2_min_source_support: float = Field(default=0.35, ge=0.0, le=1.0)
    tier2_min_identifier_fidelity: float = Field(default=1.0, ge=0.0, le=1.0)
    tier2_min_polarity_consistency: float = Field(default=1.0, ge=0.0, le=1.0)
    backup_retention_count: int = Field(default=5, ge=1, le=100)
    recall_default_limit: int = Field(default=5, ge=1, le=50)
    recall_candidate_limit: int = Field(default=200, ge=10, le=2000)
    recall_max_context_chars: int = Field(default=3500, ge=256, le=20000)
    recall_min_score: float = Field(default=0.2, ge=0.0, le=1.0)

    @field_validator("host")
    @classmethod
    def _require_loopback_host(cls, value: str) -> str:
        host = str(value or "").strip().strip("[]").lower()
        if host == "localhost":
            return host
        try:
            address = ip_address(host)
        except ValueError as exc:
            raise ValueError(
                "Memory Service host must be localhost or a loopback address"
            ) from exc
        if not address.is_loopback:
            raise ValueError(
                "Memory Service must remain loopback-only; configure a private "
                "authenticated proxy instead of exposing port 6001"
            )
        return host
