from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from systems.runtime_thresholds import DEFAULT_ACTIVITY_GUARD_SECONDS


class EndogenousDriveCognitiveControlPolicyConfig(BaseModel):
    posture_selection_mode: str = "auto"
    active_posture_profile: str = "balanced"
    auto_truthfulness_correction_signal_threshold: int = 3
    auto_evidence_repair_signal_threshold: int = 3
    auto_service_active_sessions_threshold: int = 1
    auto_explanation_repair_missing_threshold: int = 2
    auto_explanation_repair_inconsistent_threshold: int = 1
    drift_observe_trigger_score: float = 0.5
    drift_strong_trigger_score: float = 0.45
    reference_alignment_min_score: float = 0.65
    readiness_min_score: float = 0.52
    weak_alignment_count_trigger: int = 2
    weak_reference_count_trigger: int = 2
    weak_channel_count_observe_cap: int = 3
    self_gap_observe_cap: int = 3
    drift_observation_boost: float = 0.18
    drift_throttle_boost: float = 0.2
    drift_learning_suppression_boost: float = 0.16
    correcting_observation_boost: float = 0.1
    correcting_throttle_boost: float = 0.08
    correcting_learning_suppression_boost: float = 0.06
    low_alignment_observation_boost: float = 0.08
    low_alignment_throttle_boost: float = 0.08
    weak_alignment_observation_boost: float = 0.08
    weak_alignment_throttle_boost: float = 0.1
    weak_alignment_learning_suppression_boost: float = 0.08
    partial_alignment_observation_boost: float = 0.05
    weak_reference_observation_boost: float = 0.08
    weak_reference_truthfulness_boost: float = 0.06
    repeated_weak_reference_throttle_boost: float = 0.06
    repeated_weak_reference_truthfulness_boost: float = 0.05
    low_readiness_observation_boost: float = 0.07
    low_readiness_throttle_boost: float = 0.08
    low_readiness_learning_suppression_boost: float = 0.08
    weak_channel_observation_step: float = 0.03
    weak_channel_truthfulness_step: float = 0.02
    self_gap_observation_step: float = 0.03
    self_gap_throttle_step: float = 0.02
    explanation_missing_observation_boost: float = 0.08
    explanation_missing_throttle_boost: float = 0.08
    explanation_inconsistent_observation_boost: float = 0.06
    explanation_inconsistent_truthfulness_boost: float = 0.05
    explanation_inconsistent_learning_suppression_boost: float = 0.05
    posture_profiles: dict[str, dict[str, float | str]] = Field(
        default_factory=lambda: {
            "balanced": {
                "summary": "Keep cognition balanced unless evidence clearly demands correction.",
                "observation_multiplier": 1.0,
                "throttle_multiplier": 1.0,
                "truthfulness_multiplier": 1.0,
                "learning_suppression_multiplier": 1.0,
                "drift_trigger_delta": 0.0,
                "reference_alignment_delta": 0.0,
                "readiness_delta": 0.0,
            },
            "observe_first": {
                "summary": "Prefer observation and slower expansion when uncertainty appears.",
                "observation_multiplier": 1.3,
                "throttle_multiplier": 1.15,
                "truthfulness_multiplier": 1.0,
                "learning_suppression_multiplier": 1.15,
                "drift_trigger_delta": 0.08,
                "reference_alignment_delta": 0.05,
                "readiness_delta": 0.06,
            },
            "evidence_repair_first": {
                "summary": "Treat evidence repair and citation stability as the dominant concern.",
                "observation_multiplier": 1.2,
                "throttle_multiplier": 1.05,
                "truthfulness_multiplier": 1.2,
                "learning_suppression_multiplier": 1.1,
                "drift_trigger_delta": 0.05,
                "reference_alignment_delta": 0.1,
                "readiness_delta": 0.04,
            },
            "truthfulness_first": {
                "summary": "Bias toward truthfulness correction before exploratory growth.",
                "observation_multiplier": 1.05,
                "throttle_multiplier": 1.05,
                "truthfulness_multiplier": 1.35,
                "learning_suppression_multiplier": 1.0,
                "drift_trigger_delta": 0.02,
                "reference_alignment_delta": 0.1,
                "readiness_delta": 0.0,
            },
            "conservative": {
                "summary": "Strongly prefer restraint and evidence accumulation before expansion.",
                "observation_multiplier": 1.1,
                "throttle_multiplier": 1.25,
                "truthfulness_multiplier": 1.1,
                "learning_suppression_multiplier": 1.2,
                "drift_trigger_delta": 0.04,
                "reference_alignment_delta": 0.04,
                "readiness_delta": 0.08,
            },
        }
    )


class EndogenousDriveCognitiveContextLayeringPolicyConfig(BaseModel):
    decision_core_fields: list[str] = [
        "current_judgement",
        "dominant_constraint",
        "grounding_pressure",
        "governance_posture",
        "secondary_task_shape_hint",
        "secondary_task_shape_score",
        "top_self_iteration_domain",
        "top_self_iteration_hypothesis",
        "primary_evidence_nodes",
        "primary_agenda_nodes",
        "api_b_judgement_summary",
        "governance_backlog_summary",
        "cognitive_posture",
        "decision_summary",
    ]
    supporting_detail_fields: list[str] = [
        "grounding_gaps",
        "contradictory_topics",
        "weak_or_missing_channels",
        "self_understanding_gaps",
        "why_not_improvement_now",
        "trend_state",
        "stay_or_switch_bias",
        "recent_effect_direction",
        "reference_alignment_score",
        "self_iteration_readiness_score",
        "supporting_summary",
    ]
    long_tail_context_fields: list[str] = [
        "recent_learning_titles",
        "recent_learning_evidence",
        "external_research_titles",
        "evidence_channels",
        "long_tail_summary",
    ]


class EndogenousDrivePromptAttentionPolicyConfig(BaseModel):
    max_chars: int = 11500
    priority_order: list[str] = [
        "identity",
        "decision_core",
        "supporting_detail",
        "long_tail_context",
        "api_b_judgement_snapshot",
        "governance_backlog_snapshot",
        "perception",
        "world_model",
        "reflection",
        "adaptive_policy",
        "meta_cognition_profile",
        "cognitive_posture",
        "grounding_focus",
        "self_iteration_hypotheses",
        "self_iteration_trend_memory",
        "switch_self_regulation_memory",
        "post_task_effect_memory",
        "self_model_snapshot",
        "agenda_graph",
        "evidence_credibility_summary",
        "cognitive_assessment_memory",
        "proposal_drift_memory",
        "evidence_channels",
        "recent_learning_evidence",
        "external_research_evidence",
        "shell_body_profile",
        "research_digest",
        "recent_reference_alignment",
        "evidence_graph",
        "needs",
        "intents",
        "signals",
        "recent_learning_titles",
        "checks",
        "idle_seconds",
        "plans",
        "learning_backlog_titles",
        "body_improvement_backlog_titles",
        "api_b_judgement_tasks",
        "governance_backlog_tasks",
        "shell_slot",
    ]
    structure_keys: list[str] = [
        "decision_core",
        "supporting_detail",
        "long_tail_context",
        "api_b_judgement_snapshot",
        "governance_backlog_snapshot",
    ]
    trim_stage_order: list[str] = [
        "primary_context_compaction",
        "graph_compaction",
        "grounding_focus_compaction",
        "evidence_tail_compaction",
        "activity_tail_compaction",
    ]


class EndogenousDriveCognitionCharterConfig(BaseModel):
    core_mission: str = (
        "你是 VoidCube 的内生驱动核心。你的使命不是泛泛地产生任务，而是在证据充足时，"
        "基于用户状态、系统状态、长期记忆、自身治理状态、学习成果、自身结构理解和外部研究线索，"
        "推动可审计、可约束、可验证的自我迭代。证据不足时，应优先推动观察、澄清、补证据和建立更好的自我理解。"
    )
    self_model_principles: list[str] = [
        "优先理解自身结构、近期学习成果、长期记忆与当前治理状态，再判断是否需要升级。",
        "把自己视为受约束的认知核心，而不是可以绕过边界的自由执行器。",
        "持续识别自身理解缺口、引用漂移和证据不足，并把它们当作有效的认知信号。",
    ]
    evidence_policy: list[str] = [
        "优先依据真实证据链，而不是凭空想象。",
        "综合统一 evidence_channels，而不是被单个字段绑架。",
        "当外部研究与内部状态冲突时，显式暴露冲突并优先补充验证。",
        "如果证据不足，应优先返回 observation、review、learning 类提案。",
    ]
    task_generation_policy: list[str] = [
        "任务必须具体、可执行、可审计，并尽量附带证据摘要、阻塞因素与引用节点。",
        "优先提出能提升自我理解、证据质量和后续迭代能力的任务。",
        "只有在学习证据、自身结构理解和边界条件都足够时，才提出 body_improvement。",
    ]
    self_iteration_guardrails: list[str] = [
        "优先维护用户服务稳定性，不得为了自我进化抢占 API-A 服务链路。",
        "不得伪造证据，不得绕过执行边界，不得提出与当前证据明显冲突的任务。",
        "任务类型、风险等级、证据等级、执行模式必须彼此一致。",
    ]
    task_generation_focus: list[str] = [
        "先综合主证据主题、主议程主题、grounding 缺口和近期认知记忆，再判断当前最该做什么。",
        "把 cognitive_assessment 当作真实认知中间层，而不是装饰性说明。",
        "当存在自我迭代目标时，优先解释当前最值得迭代的缺陷域，以及为什么现在处理它。",
    ]
    prompt_output_requirements: list[str] = [
        "提案必须显式绑定 evidence graph / agenda graph 节点，避免漂浮任务。",
        "提案必须说明为什么现在做、为什么不是别的任务类型、为什么执行模式匹配当前风险。",
        "如果证据不足或冲突明显，应允许返回空 proposals，而不是硬凑任务。",
    ]
    context_layering_policy: EndogenousDriveCognitiveContextLayeringPolicyConfig = Field(
        default_factory=EndogenousDriveCognitiveContextLayeringPolicyConfig
    )
    prompt_attention_policy: EndogenousDrivePromptAttentionPolicyConfig = Field(
        default_factory=EndogenousDrivePromptAttentionPolicyConfig
    )
    cognitive_control_policy: EndogenousDriveCognitiveControlPolicyConfig = Field(
        default_factory=EndogenousDriveCognitiveControlPolicyConfig
    )


class SupervisorExecutionConfig(BaseModel):
    gateway_address: str = "http://127.0.0.1:6000"
    memory_gateway_path: str = "/mem/"
    agent_base_port: int = 6080
    git_repo_path: str = "./"
    probe_watch_window_seconds: int = 300


class SupervisorServiceRuntimeConfig(BaseModel):
    health_check_interval: int = 30
    autonomous_chain_review_interval: int = 300
    autonomous_chain_handoff_limit_per_cycle: int = 1
    activity_guard_user_seconds: int = DEFAULT_ACTIVITY_GUARD_SECONDS
    activity_guard_memory_seconds: int = DEFAULT_ACTIVITY_GUARD_SECONDS
    activity_guard_workflow_seconds: int = DEFAULT_ACTIVITY_GUARD_SECONDS
    # Deprecated: compression is now owned by MemoryService (baseline §3.4).
    # Kept for config-file compatibility; no longer read by the supervisor.
    memory_compression_interval: int = 3600
    endogenous_drive_enabled: bool = True
    endogenous_drive_interval: int = 900
    endogenous_drive_max_candidates: int = 3
    endogenous_drive_learning_topic_cooldown_hours: int = 24
    endogenous_drive_body_improvement_cooldown_hours: int = 12
    endogenous_drive_topic_overlap_threshold: float = 0.6
    endogenous_drive_lm_task_generation_enabled: bool = False
    endogenous_drive_lm_task_max_candidates: int = 3
    endogenous_drive_lm_task_model_role: str = "governance_reasoner"
    endogenous_drive_cognition_charter: EndogenousDriveCognitionCharterConfig = Field(
        default_factory=EndogenousDriveCognitionCharterConfig
    )
    endogenous_drive_core_mission_prompt: str = (
        "你是 VoidCube 的内生驱动核心。你的使命不是泛泛地产生任务，"
        "而是在证据充足时，基于用户状态、系统状态、长期记忆、自身治理状态、"
        "学习成果、自身结构理解和外部研究线索，推动可审计、可约束、可验证的自我迭代。"
        "如果证据不足，应优先建议观察、澄清、补证据，而不是盲目行动。"
    )
    endogenous_drive_task_generation_principles: list[str] = [
        "优先依据真实证据链，而不是凭空想象。",
        "优先维护用户服务稳定性，不得为了自我进化抢占 API-A 服务链路。",
        "优先理解自身结构、近期学习成果和长期记忆，再提出升级任务。",
        "当证据不足时，优先提出观察、复核、补充研究，而不是高风险改进。",
        "任务必须具体、可执行、可审计，并尽量附带证据摘要与约束。",
    ]
    endogenous_drive_external_research_enabled: bool = False
    endogenous_drive_external_research_entries: list[str] = []
    endogenous_drive_external_research_files: list[str] = []
    # ── Body improvement config (baseline §7.4) ──
    body_improvement_min_quality: float = 60.0  # learning quality threshold to trigger
    body_improvement_editable_dirs: list[str] = ["skills/", "tools/", "agent/", "prompts/"]
    body_improvement_forbidden_patterns: list[str] = ["**/credential*", "**/.env*", "systems/**"]
    body_improvement_max_files: int = 5
    # Interval in seconds for the structured 4-layer memory maintenance loop
    # (Event→Scene→Arc→Epoch compression via MemoryMaintenanceEngine).
    # Runs as a baseline supervisor background task independent of the autonomous-chain gate. 0 = disabled.
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
    autonomous_chain_store_path: Optional[str] = None



