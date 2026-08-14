import os
import json
from typing import Literal, Optional
import yaml
from pydantic import BaseModel, Field, TypeAdapter

from systems.memory.config import MemoryServiceConfig
from systems.supervisor.config_models import SupervisorConfig


_EVOLUTION_CAPABILITY_POLICY_PROFILE = TypeAdapter(
    Literal["development", "ci", "production"]
)


def _parse_string_list_env(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass
    if "||" in text:
        return [part.strip() for part in text.split("||") if part.strip()]
    return [line.strip() for line in text.splitlines() if line.strip()]


def _apply_string_list_override(target: BaseModel, env_name: str, field_name: str) -> None:
    values = _parse_string_list_env(os.getenv(env_name, ""))
    if values:
        setattr(target, field_name, values)


def _apply_float_override(target: BaseModel, env_name: str, field_name: str) -> None:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return
    try:
        setattr(target, field_name, float(raw))
    except ValueError:
        return


def _apply_int_override(target: BaseModel, env_name: str, field_name: str) -> None:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return
    try:
        setattr(target, field_name, int(raw))
    except ValueError:
        return


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6000
    auth_token: Optional[str] = None
    log_level: str = "INFO"


class AgentConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6080
    gateway_address: str = "http://127.0.0.1:6000"


class SystemConfig(BaseModel):
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    memory: MemoryServiceConfig = Field(default_factory=MemoryServiceConfig)
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


def _apply_canonical_supervisor_config(config: SystemConfig) -> None:
    """Load Supervisor settings from VOIDCUBE_HOME/config.yaml.

    Environment overrides are applied afterwards, preserving the repository's
    existing precedence contract.
    """
    try:
        from VoidCube_core.constants import get_config_path

        path = get_config_path()
        if not path.is_file():
            return
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        supervisor = dict(raw.get("supervisor") or {})
        service_runtime = dict(supervisor.get("service_runtime") or {})
        if service_runtime:
            current = config.supervisor.service_runtime.model_dump(mode="python")
            current.update(service_runtime)
            config.supervisor.service_runtime = type(
                config.supervisor.service_runtime
            ).model_validate(current)
    except Exception:
        return


def load_config_from_env() -> SystemConfig:
    config = SystemConfig()
    _apply_canonical_supervisor_config(config)
    
    config.gateway.host = os.getenv("GATEWAY_HOST", config.gateway.host)
    config.gateway.port = int(os.getenv("GATEWAY_PORT", config.gateway.port))
    config.gateway.auth_token = os.getenv("GATEWAY_AUTH_TOKEN", config.gateway.auth_token)
    config.gateway.log_level = os.getenv("GATEWAY_LOG_LEVEL", config.gateway.log_level)
    
    config.memory.host = os.getenv("MEMORY_HOST", config.memory.host)
    config.memory.port = int(os.getenv("MEMORY_PORT", config.memory.port))
    config.memory.db_path = os.getenv("MEMORY_DB_PATH", config.memory.db_path)
    config.memory.gateway_address = os.getenv("MEMORY_GATEWAY_ADDRESS", config.memory.gateway_address)
    config.memory.gateway_registration_check_interval = int(
        os.getenv(
            "MEMORY_GATEWAY_REGISTRATION_CHECK_INTERVAL",
            config.memory.gateway_registration_check_interval,
        )
    )
    config.memory.decay_interval_hours = int(os.getenv("MEMORY_DECAY_INTERVAL", config.memory.decay_interval_hours))
    for env_name, field_name in (
        ("MEMORY_COMPRESSION_INTERVAL", "compression_interval"),
        ("MEMORY_TIER1_RETENTION_DAYS", "tier1_retention_days"),
        ("MEMORY_TIER2_BATCH_SIZE", "tier2_batch_size"),
        ("MEMORY_TIER2_SCOPE_TIMEOUT_SECONDS", "tier2_scope_timeout_seconds"),
        ("MEMORY_LIFECYCLE_CADENCE_DAYS", "lifecycle_cadence_days"),
        ("MEMORY_EVENT_TO_SCENE_DAYS", "lifecycle_event_to_scene_days"),
        ("MEMORY_SCENE_TO_ARC_DAYS", "lifecycle_scene_to_arc_days"),
        ("MEMORY_ARC_TO_EPOCH_DAYS", "lifecycle_arc_to_epoch_days"),
        ("MEMORY_EPOCH_TO_FINAL_DAYS", "lifecycle_epoch_to_final_days"),
        ("MEMORY_FINAL_REVIEW_DAYS", "lifecycle_final_review_days"),
    ):
        _apply_int_override(config.memory, env_name, field_name)
    config.memory.recall_default_limit = int(
        os.getenv("MEMORY_RECALL_LIMIT", config.memory.recall_default_limit)
    )
    config.memory.recall_candidate_limit = int(
        os.getenv(
            "MEMORY_RECALL_CANDIDATE_LIMIT",
            config.memory.recall_candidate_limit,
        )
    )
    config.memory.recall_max_context_chars = int(
        os.getenv(
            "MEMORY_RECALL_MAX_CONTEXT_CHARS",
            config.memory.recall_max_context_chars,
        )
    )
    _apply_float_override(
        config.memory,
        "MEMORY_RECALL_MIN_SCORE",
        "recall_min_score",
    )
    
    config.supervisor.host = os.getenv("SUPERVISOR_HOST", config.supervisor.host)
    config.supervisor.port = int(os.getenv("SUPERVISOR_PORT", config.supervisor.port))
    config.supervisor.ui_enabled = (
        os.getenv("SUPERVISOR_UI_ENABLED", str(config.supervisor.ui_enabled)).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.ui_auto_open = (
        os.getenv("SUPERVISOR_UI_AUTO_OPEN", str(config.supervisor.ui_auto_open)).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.ui_auto_open_delay_seconds = float(
        os.getenv(
            "SUPERVISOR_UI_AUTO_OPEN_DELAY_SECONDS",
            config.supervisor.ui_auto_open_delay_seconds,
        )
    )
    config.supervisor.ui_event_interval_seconds = float(
        os.getenv(
            "SUPERVISOR_UI_EVENT_INTERVAL_SECONDS",
            config.supervisor.ui_event_interval_seconds,
        )
    )
    config.supervisor.ui_activity_buffer_size = int(
        os.getenv(
            "SUPERVISOR_UI_ACTIVITY_BUFFER_SIZE",
            config.supervisor.ui_activity_buffer_size,
        )
    )
    config.supervisor.ui_path = os.getenv("SUPERVISOR_UI_PATH", config.supervisor.ui_path)
    config.supervisor.soul_store_path = os.getenv(
        "SUPERVISOR_SOUL_STORE_PATH",
        config.supervisor.soul_store_path,
    )
    config.supervisor.autonomous_chain_store_path = os.getenv(
        "SUPERVISOR_AUTONOMOUS_CHAIN_STORE_PATH",
        config.supervisor.autonomous_chain_store_path,
    )
    config.supervisor.execution.gateway_address = os.getenv(
        "SUPERVISOR_GATEWAY_ADDRESS",
        config.supervisor.execution.gateway_address,
    )
    config.supervisor.service_runtime.health_check_interval = int(
        os.getenv(
            "SUPERVISOR_HEALTH_INTERVAL",
            config.supervisor.service_runtime.health_check_interval,
        )
    )
    config.supervisor.service_runtime.companion_proactive_reminder_enabled = (
        os.getenv(
            "SUPERVISOR_COMPANION_PROACTIVE_REMINDER_ENABLED",
            str(config.supervisor.service_runtime.companion_proactive_reminder_enabled),
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.service_runtime.companion_proactive_reminder_tts_enabled = (
        os.getenv(
            "SUPERVISOR_COMPANION_PROACTIVE_REMINDER_TTS_ENABLED",
            str(config.supervisor.service_runtime.companion_proactive_reminder_tts_enabled),
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.service_runtime.companion_proactive_reminder_cooldown_seconds = int(
        os.getenv(
            "SUPERVISOR_COMPANION_PROACTIVE_REMINDER_COOLDOWN_SECONDS",
            config.supervisor.service_runtime.companion_proactive_reminder_cooldown_seconds,
        )
    )
    config.supervisor.service_runtime.companion_proactive_dnd_start = os.getenv(
        "SUPERVISOR_COMPANION_PROACTIVE_DND_START",
        config.supervisor.service_runtime.companion_proactive_dnd_start,
    )
    config.supervisor.service_runtime.companion_proactive_dnd_end = os.getenv(
        "SUPERVISOR_COMPANION_PROACTIVE_DND_END",
        config.supervisor.service_runtime.companion_proactive_dnd_end,
    )
    config.supervisor.service_runtime.autonomous_chain_review_interval = int(
        os.getenv(
            "SUPERVISOR_AUTONOMOUS_CHAIN_REVIEW_INTERVAL",
            config.supervisor.service_runtime.autonomous_chain_review_interval,
        )
    )
    capability_policy_profile = os.getenv(
        "SUPERVISOR_EVOLUTION_CAPABILITY_POLICY_PROFILE",
        config.supervisor.service_runtime.evolution_capability_policy_profile,
    )
    config.supervisor.service_runtime.evolution_capability_policy_profile = (
        _EVOLUTION_CAPABILITY_POLICY_PROFILE.validate_python(
            str(capability_policy_profile).strip().lower()
        )
    )
    config.supervisor.service_runtime.endogenous_drive_enabled = (
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_ENABLED",
            str(config.supervisor.service_runtime.endogenous_drive_enabled),
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.service_runtime.endogenous_drive_interval = int(
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_INTERVAL",
            config.supervisor.service_runtime.endogenous_drive_interval,
        )
    )
    config.supervisor.service_runtime.endogenous_drive_max_candidates = int(
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_MAX_CANDIDATES",
            config.supervisor.service_runtime.endogenous_drive_max_candidates,
        )
    )
    config.supervisor.service_runtime.endogenous_drive_lm_task_generation_enabled = (
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_GENERATION_ENABLED",
            str(config.supervisor.service_runtime.endogenous_drive_lm_task_generation_enabled),
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    config.supervisor.service_runtime.endogenous_drive_lm_task_max_candidates = int(
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_MAX_CANDIDATES",
            config.supervisor.service_runtime.endogenous_drive_lm_task_max_candidates,
        )
    )
    config.supervisor.service_runtime.endogenous_drive_lm_task_model_role = os.getenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_LM_TASK_MODEL_ROLE",
        config.supervisor.service_runtime.endogenous_drive_lm_task_model_role,
    )
    cognition_charter = config.supervisor.service_runtime.endogenous_drive_cognition_charter
    charter_core_mission = os.getenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_CORE_MISSION",
        "",
    ).strip()
    if charter_core_mission:
        cognition_charter.core_mission = charter_core_mission
    _apply_string_list_override(
        cognition_charter,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_SELF_MODEL_PRINCIPLES",
        "self_model_principles",
    )
    _apply_string_list_override(
        cognition_charter,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_EVIDENCE_POLICY",
        "evidence_policy",
    )
    _apply_string_list_override(
        cognition_charter,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_TASK_GENERATION_POLICY",
        "task_generation_policy",
    )
    _apply_string_list_override(
        cognition_charter,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_TASK_GENERATION_FOCUS",
        "task_generation_focus",
    )
    _apply_string_list_override(
        cognition_charter,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_PROMPT_OUTPUT_REQUIREMENTS",
        "prompt_output_requirements",
    )
    _apply_string_list_override(
        cognition_charter,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CHARTER_SELF_ITERATION_GUARDRAILS",
        "self_iteration_guardrails",
    )
    context_layering_policy = cognition_charter.context_layering_policy
    _apply_string_list_override(
        context_layering_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTEXT_DECISION_CORE_FIELDS",
        "decision_core_fields",
    )
    _apply_string_list_override(
        context_layering_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTEXT_SUPPORTING_DETAIL_FIELDS",
        "supporting_detail_fields",
    )
    _apply_string_list_override(
        context_layering_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTEXT_LONG_TAIL_FIELDS",
        "long_tail_context_fields",
    )
    prompt_attention_policy = cognition_charter.prompt_attention_policy
    _apply_int_override(
        prompt_attention_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_MAX_CHARS",
        "max_chars",
    )
    _apply_string_list_override(
        prompt_attention_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_PRIORITY_ORDER",
        "priority_order",
    )
    _apply_string_list_override(
        prompt_attention_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_STRUCTURE_KEYS",
        "structure_keys",
    )
    _apply_string_list_override(
        prompt_attention_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_PROMPT_ATTENTION_TRIM_STAGE_ORDER",
        "trim_stage_order",
    )
    cognitive_control_policy = cognition_charter.cognitive_control_policy
    posture_selection_mode = os.getenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_POSTURE_SELECTION_MODE",
        "",
    ).strip()
    if posture_selection_mode:
        cognitive_control_policy.posture_selection_mode = posture_selection_mode
    active_posture_profile = os.getenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_ACTIVE_POSTURE_PROFILE",
        "",
    ).strip()
    if active_posture_profile:
        cognitive_control_policy.active_posture_profile = active_posture_profile
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_AUTO_TRUTHFULNESS_CORRECTION_SIGNAL_THRESHOLD",
        "auto_truthfulness_correction_signal_threshold",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_AUTO_EVIDENCE_REPAIR_SIGNAL_THRESHOLD",
        "auto_evidence_repair_signal_threshold",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_AUTO_SERVICE_ACTIVE_SESSIONS_THRESHOLD",
        "auto_service_active_sessions_threshold",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_AUTO_EXPLANATION_REPAIR_MISSING_THRESHOLD",
        "auto_explanation_repair_missing_threshold",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_AUTO_EXPLANATION_REPAIR_INCONSISTENT_THRESHOLD",
        "auto_explanation_repair_inconsistent_threshold",
    )
    _apply_float_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_DRIFT_OBSERVE_TRIGGER_SCORE",
        "drift_observe_trigger_score",
    )
    _apply_float_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_DRIFT_STRONG_TRIGGER_SCORE",
        "drift_strong_trigger_score",
    )
    _apply_float_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_REFERENCE_ALIGNMENT_MIN_SCORE",
        "reference_alignment_min_score",
    )
    _apply_float_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_READINESS_MIN_SCORE",
        "readiness_min_score",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_ALIGNMENT_COUNT_TRIGGER",
        "weak_alignment_count_trigger",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_REFERENCE_COUNT_TRIGGER",
        "weak_reference_count_trigger",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_CHANNEL_COUNT_OBSERVE_CAP",
        "weak_channel_count_observe_cap",
    )
    _apply_int_override(
        cognitive_control_policy,
        "SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_SELF_GAP_OBSERVE_CAP",
        "self_gap_observe_cap",
    )
    for env_name, field_name in (
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_DRIFT_OBSERVATION_BOOST", "drift_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_DRIFT_THROTTLE_BOOST", "drift_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_DRIFT_LEARNING_SUPPRESSION_BOOST", "drift_learning_suppression_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_CORRECTING_OBSERVATION_BOOST", "correcting_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_CORRECTING_THROTTLE_BOOST", "correcting_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_CORRECTING_LEARNING_SUPPRESSION_BOOST", "correcting_learning_suppression_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_LOW_ALIGNMENT_OBSERVATION_BOOST", "low_alignment_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_LOW_ALIGNMENT_THROTTLE_BOOST", "low_alignment_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_ALIGNMENT_OBSERVATION_BOOST", "weak_alignment_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_ALIGNMENT_THROTTLE_BOOST", "weak_alignment_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_ALIGNMENT_LEARNING_SUPPRESSION_BOOST", "weak_alignment_learning_suppression_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_PARTIAL_ALIGNMENT_OBSERVATION_BOOST", "partial_alignment_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_REFERENCE_OBSERVATION_BOOST", "weak_reference_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_REFERENCE_TRUTHFULNESS_BOOST", "weak_reference_truthfulness_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_REPEATED_WEAK_REFERENCE_THROTTLE_BOOST", "repeated_weak_reference_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_REPEATED_WEAK_REFERENCE_TRUTHFULNESS_BOOST", "repeated_weak_reference_truthfulness_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_LOW_READINESS_OBSERVATION_BOOST", "low_readiness_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_LOW_READINESS_THROTTLE_BOOST", "low_readiness_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_LOW_READINESS_LEARNING_SUPPRESSION_BOOST", "low_readiness_learning_suppression_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_CHANNEL_OBSERVATION_STEP", "weak_channel_observation_step"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_WEAK_CHANNEL_TRUTHFULNESS_STEP", "weak_channel_truthfulness_step"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_SELF_GAP_OBSERVATION_STEP", "self_gap_observation_step"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_SELF_GAP_THROTTLE_STEP", "self_gap_throttle_step"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_EXPLANATION_MISSING_OBSERVATION_BOOST", "explanation_missing_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_EXPLANATION_MISSING_THROTTLE_BOOST", "explanation_missing_throttle_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_EXPLANATION_INCONSISTENT_OBSERVATION_BOOST", "explanation_inconsistent_observation_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_EXPLANATION_INCONSISTENT_TRUTHFULNESS_BOOST", "explanation_inconsistent_truthfulness_boost"),
        ("SUPERVISOR_ENDOGENOUS_DRIVE_COGNITION_CONTROL_EXPLANATION_INCONSISTENT_LEARNING_SUPPRESSION_BOOST", "explanation_inconsistent_learning_suppression_boost"),
    ):
        _apply_float_override(cognitive_control_policy, env_name, field_name)
    core_mission_prompt = os.getenv(
        "SUPERVISOR_ENDOGENOUS_DRIVE_CORE_MISSION_PROMPT",
        "",
    ).strip()
    if core_mission_prompt:
        config.supervisor.service_runtime.endogenous_drive_core_mission_prompt = core_mission_prompt
        cognition_charter.core_mission = core_mission_prompt
    task_generation_principles = _parse_string_list_env(
        os.getenv("SUPERVISOR_ENDOGENOUS_DRIVE_TASK_GENERATION_PRINCIPLES", "")
    )
    if task_generation_principles:
        config.supervisor.service_runtime.endogenous_drive_task_generation_principles = (
            task_generation_principles
        )
        cognition_charter.task_generation_policy = task_generation_principles
    config.supervisor.service_runtime.endogenous_drive_external_research_enabled = (
        os.getenv(
            "SUPERVISOR_ENDOGENOUS_DRIVE_EXTERNAL_RESEARCH_ENABLED",
            str(config.supervisor.service_runtime.endogenous_drive_external_research_enabled),
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )
    external_research_entries = _parse_string_list_env(
        os.getenv("SUPERVISOR_ENDOGENOUS_DRIVE_EXTERNAL_RESEARCH_ENTRIES", "")
    )
    if external_research_entries:
        config.supervisor.service_runtime.endogenous_drive_external_research_entries = (
            external_research_entries
        )
    external_research_files = _parse_string_list_env(
        os.getenv("SUPERVISOR_ENDOGENOUS_DRIVE_EXTERNAL_RESEARCH_FILES", "")
    )
    if external_research_files:
        config.supervisor.service_runtime.endogenous_drive_external_research_files = (
            external_research_files
        )
    config.supervisor.execution.git_repo_path = os.getenv(
        "SUPERVISOR_GIT_REPO",
        config.supervisor.execution.git_repo_path,
    )
    config.supervisor.body_runtime.stable_window_days = int(
        os.getenv("BODY_STABLE_WINDOW_DAYS", config.supervisor.body_runtime.stable_window_days)
    )
    config.supervisor.body_runtime.stable_health_checks = int(
        os.getenv("BODY_STABLE_HEALTH_CHECKS", config.supervisor.body_runtime.stable_health_checks)
    )
    
    config.agent.host = os.getenv("AGENT_HOST", config.agent.host)
    config.agent.port = int(os.getenv("AGENT_PORT", config.agent.port))
    config.agent.gateway_address = os.getenv("AGENT_GATEWAY_ADDRESS", config.agent.gateway_address)

    config.supervisor.body_runtime.state_root = os.getenv(
        "BODY_STATE_ROOT",
        config.supervisor.body_runtime.state_root,
    )
    config.supervisor.body_runtime.slot_a_name = os.getenv(
        "BODY_SLOT_A_NAME",
        config.supervisor.body_runtime.slot_a_name,
    )
    config.supervisor.body_runtime.slot_b_name = os.getenv(
        "BODY_SLOT_B_NAME",
        config.supervisor.body_runtime.slot_b_name,
    )
    config.supervisor.execution.probe_watch_window_seconds = int(
        os.getenv(
            "BODY_PROBE_WATCH_WINDOW_SECONDS",
            config.supervisor.execution.probe_watch_window_seconds,
        )
    )
    
    return config


def get_config() -> SystemConfig:
    return load_config_from_env()
