"""Pure normalization for the endogenous cognition charter."""

from __future__ import annotations

from typing import Any, Dict, Iterable


def resolve_cognition_charter(
    *,
    charter_model: Any = None,
    core_mission: Any = None,
    task_generation_principles: Iterable[Any] | None = None,
) -> Dict[str, Any]:
    if hasattr(charter_model, "model_dump"):
        cognition_charter = charter_model.model_dump(mode="json")
    else:
        cognition_charter = dict(charter_model or {})
    if not str(cognition_charter.get("core_mission") or "").strip():
        cognition_charter["core_mission"] = str(core_mission or "").strip()
    if not list(cognition_charter.get("task_generation_policy") or []):
        cognition_charter["task_generation_policy"] = list(task_generation_principles or [])
    if not list(cognition_charter.get("task_generation_focus") or []):
        cognition_charter["task_generation_focus"] = [
            "先综合主证据主题、主议程主题、grounding 缺口和近期认知记忆，再判断当前最该做什么。",
            "把 cognitive_assessment 当作真实认知中间层，而不是装饰性说明。",
            "当存在自我迭代目标时，优先解释当前最值得迭代的缺陷域，以及为什么现在处理它。",
        ]
    if not list(cognition_charter.get("prompt_output_requirements") or []):
        cognition_charter["prompt_output_requirements"] = [
            "提案必须显式绑定 evidence graph / agenda graph 节点，避免漂浮任务。",
            "提案必须说明为什么现在做、为什么不是别的任务类型、为什么执行模式匹配当前风险。",
            "如果证据不足或冲突明显，应允许返回空 proposals，而不是硬凑任务。",
        ]
    context_layering_policy = dict(cognition_charter.get("context_layering_policy") or {})
    if not list(context_layering_policy.get("decision_core_fields") or []):
        context_layering_policy["decision_core_fields"] = [
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
            "cognitive_posture",
            "decision_summary",
        ]
    if not list(context_layering_policy.get("supporting_detail_fields") or []):
        context_layering_policy["supporting_detail_fields"] = [
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
    if not list(context_layering_policy.get("long_tail_context_fields") or []):
        context_layering_policy["long_tail_context_fields"] = [
            "recent_learning_titles",
            "recent_learning_evidence",
            "external_research_titles",
            "evidence_channels",
            "long_tail_summary",
        ]
    cognition_charter["context_layering_policy"] = context_layering_policy
    prompt_attention_policy = dict(cognition_charter.get("prompt_attention_policy") or {})
    if not int(prompt_attention_policy.get("max_chars") or 0):
        # Derive from the model's actual context window when not explicitly
        # configured.  Uses 50% of context window × 2.5 chars/token so the
        # packet fits comfortably with headroom for system prompt + response.
        try:
            from memai.llm_client import get_memory_context_max_chars
            prompt_attention_policy["max_chars"] = get_memory_context_max_chars()
        except Exception:
            prompt_attention_policy["max_chars"] = 11500
    if not list(prompt_attention_policy.get("priority_order") or []):
        prompt_attention_policy["priority_order"] = [
            "identity",
            "decision_core",
            "supporting_detail",
            "long_tail_context",
            "api_b_judgement_snapshot",
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
            "shell_slot",
        ]
    if not list(prompt_attention_policy.get("structure_keys") or []):
        prompt_attention_policy["structure_keys"] = [
            "decision_core",
            "supporting_detail",
            "long_tail_context",
            "api_b_judgement_snapshot",
        ]
    if not list(prompt_attention_policy.get("trim_stage_order") or []):
        prompt_attention_policy["trim_stage_order"] = [
            "primary_context_compaction",
            "graph_compaction",
            "grounding_focus_compaction",
            "evidence_tail_compaction",
            "activity_tail_compaction",
        ]
    cognition_charter["prompt_attention_policy"] = prompt_attention_policy
    return cognition_charter
