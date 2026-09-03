from __future__ import annotations

from ipaddress import ip_address
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from memai.repository.paths import get_mem_runtime_layout


def _default_memory_db_path() -> str:
    return str(get_mem_runtime_layout().memory_db)


class MemoryServiceConfig(BaseModel):
    """Single runtime configuration model for the Memory Service."""

    model_config = ConfigDict(validate_assignment=True)

    host: str = "127.0.0.1"
    port: int = 6001
    db_path: str = Field(default_factory=_default_memory_db_path)
    gateway_address: str = "http://127.0.0.1:6000"
    service_token: str | None = None
    service_tokens: dict[str, str] = Field(default_factory=dict)
    gateway_registration_check_interval: int = 30
    redact_before_store: bool = False
    time_summary_timezone: str = "Asia/Shanghai"
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
    dormant_arc_after_days: int = Field(default=30, ge=1, le=3650)
    purge_event_after_days: int = Field(default=180, ge=1, le=3650)
    purge_scene_after_days: int = Field(default=365, ge=1, le=3650)
    purge_arc_after_days: int = Field(default=730, ge=1, le=3650)
    purge_epoch_after_days: int = Field(default=1095, ge=1, le=3650)
    purge_event_max_importance: float = Field(default=0.35, ge=0.0, le=1.0)
    purge_scene_max_importance: float = Field(default=0.45, ge=0.0, le=1.0)
    purge_arc_max_importance: float = Field(default=0.55, ge=0.0, le=1.0)
    purge_epoch_max_importance: float = Field(default=0.65, ge=0.0, le=1.0)
    purge_max_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    purge_candidate_grace_days: int = Field(default=30, ge=1, le=3650)
    purge_audit_retention_days: int = Field(default=90, ge=1, le=3650)
    backup_retention_count: int = Field(default=5, ge=1, le=100)
    memory_write_queue_max_size: int = Field(default=256, ge=1, le=10000)
    memory_write_batch_size: int = Field(default=16, ge=1, le=256)
    memory_write_batch_wait_ms: float = Field(default=2.0, ge=0.0, le=1000.0)
    memory_write_enqueue_timeout_ms: float = Field(default=0.0, ge=0.0, le=60000.0)
    memory_write_shutdown_timeout_seconds: float = Field(
        default=5.0, ge=0.1, le=120.0
    )
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

    @field_validator("time_summary_timezone")
    @classmethod
    def _require_valid_time_summary_timezone(cls, value: str) -> str:
        timezone_name = str(value or "").strip()
        if not timezone_name:
            raise ValueError("Memory time-summary timezone is required")
        if timezone_name != "UTC" and not re.fullmatch(
            r"[A-Za-z_]+(?:/[A-Za-z0-9._+-]+)+",
            timezone_name,
        ):
            raise ValueError(
                f"Invalid Memory time-summary timezone name: {timezone_name}"
            )
        return timezone_name
