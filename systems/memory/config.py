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
    redact_before_store: bool = False
    decay_interval_hours: int = Field(default=24, gt=0)
    compression_interval: int = Field(default=3600, ge=60)
    tier2_trigger_candidate_count: int = Field(default=100, ge=1, le=100000)
    tier2_trigger_oldest_age_seconds: int = Field(
        default=8 * 24 * 3600, ge=60, le=365 * 24 * 3600
    )
    tier2_bridge_failure_degraded_after: int = Field(default=3, ge=1, le=100)
    tier1_retention_days: int = Field(default=7, ge=1, le=365)
    tier1_max_turns: int = 10000
    tier1_decay_rate: float = Field(default=0.99, ge=0.0, le=1.0)
    tier1_min_relevance: float = 0.1
    tier1_archive_keep_original: bool = True
    tier2_batch_size: int = Field(default=25, ge=1, le=1000)
    tier2_scope_timeout_seconds: int = Field(default=180, ge=30, le=3600)
    tier2_min_backlink_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    tier2_max_compression_ratio: float = Field(default=1.0, gt=0.0)
    tier2_max_degraded_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    tier2_min_source_support: float = Field(default=0.35, ge=0.0, le=1.0)
    tier2_min_identifier_fidelity: float = Field(default=1.0, ge=0.0, le=1.0)
    tier2_min_polarity_consistency: float = Field(default=1.0, ge=0.0, le=1.0)
    lifecycle_min_source_support: float = Field(default=0.15, ge=0.0, le=1.0)
    lifecycle_min_identifier_fidelity: float = Field(default=0.8, ge=0.0, le=1.0)
    lifecycle_max_quality_retries: int = Field(default=3, ge=1, le=20)
    lifecycle_retry_base_hours: float = Field(default=1.0, gt=0.0, le=168.0)
    lifecycle_cadence_days: int = Field(default=7, ge=1, le=365)
    lifecycle_event_to_scene_days: int = Field(default=14, ge=1, le=3650)
    lifecycle_scene_to_arc_days: int = Field(default=60, ge=1, le=3650)
    lifecycle_arc_to_epoch_days: int = Field(default=180, ge=1, le=3650)
    lifecycle_epoch_to_final_days: int = Field(default=365, ge=1, le=3650)
    lifecycle_final_review_days: int = Field(default=90, ge=1, le=3650)
    backup_retention_count: int = Field(default=5, ge=1, le=100)
    recall_default_limit: int = Field(default=5, ge=1, le=50)
    recall_candidate_limit: int = Field(default=200, ge=10, le=2000)
    recall_max_context_chars: int = Field(default=3500, ge=256, le=20000)
    recall_min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    recall_graph_min_relevance: float = Field(default=0.15, ge=0.0, le=1.0)
    agent_outbox_report_stale_seconds: int = Field(default=45, ge=5, le=3600)
    agent_outbox_pending_stale_seconds: int = Field(default=300, ge=5, le=86400)

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
