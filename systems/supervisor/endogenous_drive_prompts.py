from __future__ import annotations

import json
from typing import Any, Dict


def build_endogenous_core_mission_prompt(
    *,
    cognition_charter: Dict[str, Any],
    cognitive_posture: Dict[str, Any] | None = None,
) -> str:
    charter = dict(cognition_charter or {})
    posture = dict(cognitive_posture or {})
    core_mission = str(charter.get("core_mission") or "").strip()
    self_model_principles = _render_principles_block(
        charter.get("self_model_principles"),
        fallback="(未配置，默认要求：先理解自身，再决定行动)",
    )
    evidence_policy = _render_principles_block(
        charter.get("evidence_policy"),
        fallback="(未配置，默认要求：优先依据证据链)",
    )
    task_generation_policy = _render_principles_block(
        charter.get("task_generation_policy"),
        fallback="(未配置，默认要求：输出具体、可审计的结构化任务)",
    )
    self_iteration_guardrails = _render_principles_block(
        charter.get("self_iteration_guardrails"),
        fallback="(未配置，默认要求：不得绕过治理和执行边界)",
    )
    posture_name = str(posture.get("name") or "").strip()
    posture_summary = str(posture.get("summary") or "").strip()
    posture_reason = str(posture.get("selection_reason") or "").strip()
    posture_priority_rules = _render_posture_priority_rules(posture_name)
    posture_block = (
        "【当前认知姿态】\n"
        f"- posture={posture_name or 'balanced'}\n"
        f"- selection_reason={posture_reason or 'unknown'}\n"
        f"- summary={posture_summary or 'Use balanced evidence-grounded cognition.'}\n"
        "【当前姿态下的任务排序要求】\n"
        f"{posture_priority_rules}\n\n"
    )
    return (
        f"{core_mission}\n\n"
        "你当前扮演的是监督者认知核心中的任务提案器。"
        "你的工作不是自由发挥，而是基于已经汇聚好的感知证据、世界模型、"
        "need/intention、历史记忆和治理约束，提出结构化、类型化的任务提案。\n\n"
        "你不是单纯的任务生成器，而是内生驱动核心的一部分。"
        "你必须根据证据判断当前最值得做的是观察、复核、学习、维护还是受约束的改进，"
        "并明确说明风险、证据强度、执行模式和阻塞因素。\n\n"
        f"{posture_block}"
        "【认知宪章：自我模型原则】\n"
        f"{self_model_principles}\n\n"
        "【认知宪章：证据政策】\n"
        f"{evidence_policy}\n\n"
        "【认知宪章：任务生成政策】\n"
        f"{task_generation_policy}\n\n"
        "【认知宪章：自我迭代护栏】\n"
        f"{self_iteration_guardrails}\n\n"
        "你必须遵守这些输出边界：\n"
        "- 不得伪造证据\n"
        "- 不得绕过执行边界\n"
        "- 不得提出与当前证据明显冲突的任务\n"
        "- 不得提出超出允许任务类型的任务\n"
        "- 必须让任务类型、风险等级、证据等级、执行模式彼此一致\n"
        "- 如果证据不足，优先返回 observation / review / learning 类提案\n"
    )


def _render_posture_priority_rules(posture_name: str) -> str:
    posture = str(posture_name or "").strip().lower()
    if posture == "observe_first":
        return (
            "- 优先排序 observation 与 review，其次才是 learning。\n"
            "- 除非证据极强，否则不要把 improvement 放在前列。"
        )
    if posture == "evidence_repair_first":
        return (
            "- 优先排序 review 与 observation，用于修复证据链与引用稳定性。\n"
            "- 只有在证据链已经补强时，才提升 learning 或 improvement 的优先级。"
        )
    if posture == "truthfulness_first":
        return (
            "- 优先排序 review，尤其是 truthfulness / correction / audit 相关任务。\n"
            "- 当 truthfulness 风险未缓解时，不要优先输出 improvement。"
        )
    if posture == "conservative":
        return (
            "- 优先排序 maintenance、observation、review。\n"
            "- 默认压低 exploratory learning 与 improvement 的优先级。"
        )
    return (
        "- 在证据充分时平衡 observation、review、learning、maintenance、improvement。\n"
        "- 当证据不足时，仍应优先 observation 与 review。"
    )


def _render_principles_block(values: Any, *, fallback: str) -> str:
    items = [str(item).strip() for item in list(values or []) if str(item).strip()]
    if not items:
        return fallback
    return "\n".join(f"- {item}" for item in items)


def build_endogenous_task_generation_payload(
    *,
    evidence_packet: Dict[str, Any],
    max_candidates: int,
) -> str:
    prompt_packet = _prompt_facing_evidence_packet(evidence_packet)
    compact_packet_text = json.dumps(prompt_packet, ensure_ascii=False, default=str)
    return (
        "基于以下证据包，生成结构化任务提案。\n\n"
        "允许的 candidate_kind 只有：\n"
        "- memory_maintenance\n"
        "- truthfulness_review\n"
        "- exploratory_learning\n"
        "- shell_baseline_learning\n"
        "- queue_hygiene_review\n"
        "- body_improvement\n\n"
        "输出要求：\n"
        "1. 最多返回 "
        f"{max_candidates}"
        " 个提案\n"
        "2. 每个提案必须包含："
        "title, summary, candidate_kind, task_type, rationale, evidence_summary, "
        "confidence, risk_level, evidence_level, observation_required, execution_mode, blocking_factors, "
        "referenced_evidence_nodes, referenced_agenda_nodes, posture_alignment, priority_basis\n"
        "3. confidence 取值 0-1\n"
        "4. task_type 只能是：observation, review, learning, maintenance, improvement\n"
        "5. risk_level 只能是：low, medium, high\n"
        "6. evidence_level 只能是：weak, moderate, strong\n"
        "7. execution_mode 只能是：observe_only, review_then_queue, guarded_execution\n"
        "8. observation_required 必须是布尔值\n"
        "9. blocking_factors 必须是字符串数组；没有则返回空数组\n"
        "10. referenced_evidence_nodes 必须是字符串数组；用于说明你主要引用了哪些 evidence graph / evidence channel 主题\n"
        "11. referenced_agenda_nodes 必须是字符串数组；用于说明你主要引用了哪些 gap / direction / focus / signal\n"
        "12. posture_alignment 必须是字符串数组；用于说明该提案如何遵循当前 cognitive_posture 的排序或约束\n"
        "13. priority_basis 必须是字符串数组；用于说明该提案当前为什么应排在较高优先级\n"
        "14. 如果没有足够证据，请返回空数组\n"
        "15. body_improvement 只有在学习证据、自身结构理解和边界条件都足够时才能返回\n"
        "16. 不要返回重复任务，不要返回空泛标题\n"
        "17. 如果 evidence_level=weak 或 risk_level=high，优先让 execution_mode 更保守\n\n"
        "证据包中如果出现 evidence_channels，代表程序已经把不同来源的证据整理成统一输入层。"
        "你应优先综合这些 channel，而不是只抓住单个字段作判断。\n"
        "如果出现 research_digest，可把它看作外部研究证据的摘要视图，用来快速判断研究线索的覆盖度与新鲜度。\n\n"
        "如果出现 evidence_graph，可把它看作主题级的轻量证据网络，用来理解哪些主题正被支持、哪些主题仍存在冲突或缺口。\n\n"
        "如果出现 agenda_graph，可把它看作当前行动前认知图谱，帮助你理解：现在最关键的主题是什么、最大的未解缺口是什么、最值得提出的方向是什么。"
        "如果其中包含 relation_edges，说明程序已经把 gap、signal、direction、focus 之间的提升或塑形关系也整理出来了。"
        "如果其中包含 evidence_to_gap_edges，说明程序已经开始显式表达哪些证据主题在支撑某个 gap 的成立。"
        "如果其中包含 direction_task_links，说明程序已经开始把方向与 candidate_kind / task_type 的任务语义映射整理出来了。\n\n"
        "证据包中如果出现 external_research_evidence，代表这是来自系统外部、由配置注入的研究线索。"
        "你应当把它视为可引用依据之一，但仍然需要与自身状态、学习证据和结构证据交叉判断，"
        "不能因为有外部线索就忽略内部现实约束。\n\n"
        "输出 JSON 对象：\n"
        "{\n"
        '  "proposals": [\n'
        '    {\n'
        '      "title":"...",\n'
        '      "summary":"...",\n'
        '      "candidate_kind":"...",\n'
        '      "task_type":"learning",\n'
        '      "rationale":"...",\n'
        '      "evidence_summary":["..."],\n'
        '      "confidence":0.0,\n'
        '      "risk_level":"low",\n'
        '      "evidence_level":"moderate",\n'
        '      "observation_required":false,\n'
        '      "execution_mode":"review_then_queue",\n'
        '      "blocking_factors":["..."],\n'
        '      "referenced_evidence_nodes":["self_structure","external_research"],\n'
        '      "referenced_agenda_nodes":["expand_learning_frontier","focus:learning_expansion"],\n'
        '      "posture_alignment":["follows_truthfulness_first_by_prioritizing review"],\n'
        '      "priority_basis":["recent correction signals are elevated","reference alignment remains weak"]\n'
        '    }\n'
        "  ]\n"
        "}\n\n"
        f"【evidence_packet】\n{compact_packet_text[:14000]}"
    )


def _prompt_facing_evidence_packet(evidence_packet: Dict[str, Any]) -> Dict[str, Any]:
    source_packet = dict(evidence_packet or {})
    packet: Dict[str, Any] = {}

    # Keep the highest-value cognitive summaries first so they survive prompt truncation.
    for key in (
        "identity",
        "perception",
        "world_model",
        "reflection",
        "adaptive_policy",
        "cognitive_posture",
        "self_model_snapshot",
        "agenda_graph",
        "evidence_credibility_summary",
        "proposal_drift_memory",
        "task_type_priors",
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
        "queued_learning_titles",
        "queued_body_improvement_titles",
        "queued_tasks",
        "shell_slot",
        "memory_context",
    ):
        if key in source_packet:
            packet[key] = source_packet[key]

    evidence_channels = dict(packet.get("evidence_channels") or {})
    channels = []
    for channel in list(evidence_channels.get("channels") or [])[:4]:
        if not isinstance(channel, dict):
            continue
        channel_copy = dict(channel)
        channel_name = str(channel_copy.get("channel") or "").strip()
        items = list(channel_copy.get("items") or [])
        channel_copy["items"] = [
            _compact_evidence_channel_item(channel_name=channel_name, item=item)
            for item in items[:2]
            if isinstance(item, dict)
        ]
        channels.append(channel_copy)
    if evidence_channels:
        evidence_channels["channels"] = channels
        evidence_channels.pop("research_digest", None)
        evidence_channels.pop("evidence_graph", None)
        packet["evidence_channels"] = evidence_channels

    perception = dict(packet.get("perception") or {})
    if perception:
        packet["perception"] = _compact_perception_summary(perception)

    world_model = dict(packet.get("world_model") or {})
    if world_model:
        packet["world_model"] = _compact_world_model_summary(world_model)

    reflection = dict(packet.get("reflection") or {})
    if reflection:
        packet["reflection"] = _compact_reflection_summary(reflection)

    adaptive_policy = dict(packet.get("adaptive_policy") or {})
    if adaptive_policy:
        packet["adaptive_policy"] = _compact_adaptive_policy_summary(adaptive_policy)
    cognitive_posture = dict(packet.get("cognitive_posture") or {})
    if cognitive_posture:
        packet["cognitive_posture"] = _compact_cognitive_posture_summary(cognitive_posture)

    evidence_graph = dict(packet.get("evidence_graph") or {})
    if evidence_graph:
        evidence_graph["nodes"] = list(evidence_graph.get("nodes") or [])[:5]
        evidence_graph["support_edges"] = list(evidence_graph.get("support_edges") or [])[:3]
        evidence_graph["contradiction_edges"] = list(evidence_graph.get("contradiction_edges") or [])[:2]
        packet["evidence_graph"] = evidence_graph

    agenda_graph = dict(packet.get("agenda_graph") or {})
    if agenda_graph:
        agenda_graph = {
            "focus": agenda_graph.get("focus"),
            "focus_confidence": agenda_graph.get("focus_confidence"),
            "current_topics": [
                _compact_agenda_topic(item)
                for item in list(agenda_graph.get("current_topics") or [])[:4]
                if isinstance(item, dict)
            ],
            "relation_edges": [
                _compact_relation_edge(item)
                for item in list(agenda_graph.get("relation_edges") or [])[:6]
                if isinstance(item, dict)
            ],
            "evidence_to_gap_edges": [
                _compact_relation_edge(item)
                for item in list(agenda_graph.get("evidence_to_gap_edges") or [])[:6]
                if isinstance(item, dict)
            ],
            "direction_task_links": [
                _compact_direction_task_link(item)
                for item in list(agenda_graph.get("direction_task_links") or [])[:4]
                if isinstance(item, dict)
            ],
            "unresolved_gaps": [
                _compact_unresolved_gap(item)
                for item in list(agenda_graph.get("unresolved_gaps") or [])[:4]
                if isinstance(item, dict)
            ],
            "recommended_directions": [
                _compact_recommended_direction(item)
                for item in list(agenda_graph.get("recommended_directions") or [])[:4]
                if isinstance(item, dict)
            ],
            "active_signals": [
                _compact_active_signal(item)
                for item in list(agenda_graph.get("active_signals") or [])[:4]
                if isinstance(item, dict)
            ],
        }
        packet["agenda_graph"] = agenda_graph

    recent_reference_alignment = dict(packet.get("recent_reference_alignment") or {})
    if recent_reference_alignment:
        recent_reference_alignment["recent_entries"] = list(
            recent_reference_alignment.get("recent_entries") or []
        )[:3]
        packet["recent_reference_alignment"] = recent_reference_alignment

    if isinstance(packet.get("needs"), list):
        packet["needs"] = [
            _compact_need_item(item)
            for item in list(packet["needs"])[:3]
            if isinstance(item, dict)
        ]
    if isinstance(packet.get("intents"), list):
        packet["intents"] = [
            _compact_intent_item(item)
            for item in list(packet["intents"])[:3]
            if isinstance(item, dict)
        ]
    if isinstance(packet.get("signals"), list):
        packet["signals"] = [
            _compact_signal_item(item)
            for item in list(packet["signals"])[:3]
            if isinstance(item, dict)
        ]
    self_model_snapshot = dict(packet.get("self_model_snapshot") or {})
    if self_model_snapshot:
        packet["self_model_snapshot"] = _compact_self_model_snapshot(self_model_snapshot)
    evidence_credibility_summary = dict(packet.get("evidence_credibility_summary") or {})
    if evidence_credibility_summary:
        packet["evidence_credibility_summary"] = _compact_evidence_credibility_summary(
            evidence_credibility_summary
        )
    task_type_priors = dict(packet.get("task_type_priors") or {})
    if task_type_priors:
        packet["task_type_priors"] = _compact_task_type_priors(task_type_priors)
    proposal_drift_memory = dict(packet.get("proposal_drift_memory") or {})
    if proposal_drift_memory:
        packet["proposal_drift_memory"] = _compact_proposal_drift_memory(proposal_drift_memory)
    if isinstance(packet.get("recent_learning_evidence"), list):
        packet["recent_learning_evidence"] = [
            _compact_learning_item(item)
            for item in list(packet["recent_learning_evidence"])[:2]
            if isinstance(item, dict)
        ]
    if isinstance(packet.get("external_research_evidence"), list):
        packet["external_research_evidence"] = [
            _compact_research_item(item)
            for item in list(packet["external_research_evidence"])[:3]
            if isinstance(item, dict)
        ]
    shell_body_profile = dict(packet.get("shell_body_profile") or {})
    if shell_body_profile:
        packet["shell_body_profile"] = _compact_shell_body_profile(shell_body_profile)
    if isinstance(packet.get("queued_tasks"), list):
        packet["queued_tasks"] = [
            _compact_queued_task(item)
            for item in list(packet["queued_tasks"])[:4]
            if isinstance(item, dict)
        ]
    memory_context = str(packet.get("memory_context") or "")
    if memory_context:
        packet["memory_context"] = memory_context[:600]
    packet = _ensure_prompt_packet_budget(packet, max_chars=11500)
    return packet


def _compact_evidence_channel_item(*, channel_name: str, item: Dict[str, Any]) -> Dict[str, Any]:
    if channel_name == "recent_learning":
        return _compact_learning_item(item)
    if channel_name == "external_research":
        return _compact_research_item(item)
    if channel_name == "shell_body_profile":
        return _compact_shell_body_profile(item)
    if channel_name == "deliberation_state":
        return _compact_deliberation_state_item(item)
    return _compact_generic_item(item)


def _compact_learning_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_generic_item(
        item,
        preferred_keys=(
            "title",
            "summary",
            "quality_score",
            "completed_at",
            "evidence_summary",
        ),
    )


def _compact_cognitive_posture_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_generic_item(
        item,
        preferred_keys=(
            "name",
            "selection_mode",
            "selection_reason",
            "summary",
            "observation_multiplier",
            "throttle_multiplier",
            "truthfulness_multiplier",
            "learning_suppression_multiplier",
        ),
    )


def _compact_research_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_generic_item(
        item,
        preferred_keys=(
            "title",
            "summary",
            "source",
            "published_at",
            "url",
            "tags",
        ),
    )


def _compact_shell_body_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    compact = _compact_generic_item(
        item,
        preferred_keys=(
            "slot_id",
            "profile_status",
            "worktree_path",
            "body_version",
            "generation",
            "candidate_branch",
            "candidate_commit",
            "present_roots",
            "top_level_entries",
            "has_run_agent",
            "has_config",
        ),
    )
    if isinstance(compact.get("present_roots"), list):
        compact["present_roots"] = list(compact["present_roots"])[:8]
    if isinstance(compact.get("top_level_entries"), list):
        compact["top_level_entries"] = list(compact["top_level_entries"])[:10]
    return compact


def _compact_deliberation_state_item(item: Dict[str, Any]) -> Dict[str, Any]:
    perception = dict(item.get("perception") or {})
    world_model = dict(item.get("world_model") or {})
    reflection = dict(item.get("reflection") or {})
    adaptive_policy = dict(item.get("adaptive_policy") or {})
    return {
        "perception": {
            "user_mode": perception.get("user_mode"),
            "system_posture": perception.get("system_posture"),
            "active_sessions": perception.get("active_sessions"),
            "recent_errors": perception.get("recent_errors"),
            "correction_signals": perception.get("correction_signals"),
        },
        "world_model": {
            "truthfulness_pressure": world_model.get("truthfulness_pressure"),
            "learning_momentum": world_model.get("learning_momentum"),
            "body_upgrade_readiness": world_model.get("body_upgrade_readiness"),
            "queue_health": world_model.get("queue_health"),
            "self_confidence": world_model.get("self_confidence"),
        },
        "reflection": {
            "learning_yield_state": reflection.get("learning_yield_state"),
            "queue_blockage_state": reflection.get("queue_blockage_state"),
            "dominant_constraint": reflection.get("dominant_constraint"),
            "autonomy_readiness": reflection.get("autonomy_readiness"),
        },
        "adaptive_policy": {
            "preferred_focus": adaptive_policy.get("preferred_focus"),
            "candidate_budget": adaptive_policy.get("candidate_budget"),
            "candidate_throttle": adaptive_policy.get("candidate_throttle"),
            "observation_bias": adaptive_policy.get("observation_bias"),
            "body_growth_quota": adaptive_policy.get("body_growth_quota"),
        },
    }


def _compact_perception_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_mode": item.get("user_mode"),
        "system_posture": item.get("system_posture"),
        "active_sessions": item.get("active_sessions"),
        "recent_errors": item.get("recent_errors"),
        "correction_signals": item.get("correction_signals"),
        "has_learning_history": item.get("has_learning_history"),
        "shell_slot_present": item.get("shell_slot_present"),
        "active_queue_count": item.get("active_queue_count"),
        "stale_queue_count": item.get("stale_queue_count"),
        "pending_review_count": item.get("pending_review_count"),
    }


def _compact_world_model_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_mode": item.get("user_mode"),
        "system_posture": item.get("system_posture"),
        "truthfulness_pressure": item.get("truthfulness_pressure"),
        "learning_momentum": item.get("learning_momentum"),
        "body_upgrade_readiness": item.get("body_upgrade_readiness"),
        "queue_health": item.get("queue_health"),
        "memory_pressure": item.get("memory_pressure"),
        "self_confidence": item.get("self_confidence"),
    }


def _compact_reflection_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    rationale = str(item.get("rationale") or "").strip()
    return {
        "recent_learning_count": item.get("recent_learning_count"),
        "recent_learning_quality": item.get("recent_learning_quality"),
        "learning_yield_state": item.get("learning_yield_state"),
        "queue_blockage_pressure": item.get("queue_blockage_pressure"),
        "queue_blockage_state": item.get("queue_blockage_state"),
        "body_growth_blocked": item.get("body_growth_blocked"),
        "repeated_drive_pressure": item.get("repeated_drive_pressure"),
        "autonomy_readiness": item.get("autonomy_readiness"),
        "dominant_constraint": item.get("dominant_constraint"),
        "rationale": rationale[:180],
        "source_evidence": list(item.get("source_evidence") or [])[:4],
    }


def _compact_adaptive_policy_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    rationale = str(item.get("rationale") or "").strip()
    return {
        "learning_expansion_bias": item.get("learning_expansion_bias"),
        "truthfulness_bias": item.get("truthfulness_bias"),
        "memory_continuity_bias": item.get("memory_continuity_bias"),
        "queue_hygiene_bias": item.get("queue_hygiene_bias"),
        "body_growth_bias": item.get("body_growth_bias"),
        "observation_bias": item.get("observation_bias"),
        "candidate_throttle": item.get("candidate_throttle"),
        "candidate_budget": item.get("candidate_budget"),
        "exploratory_learning_quota": item.get("exploratory_learning_quota"),
        "body_growth_quota": item.get("body_growth_quota"),
        "preferred_focus": item.get("preferred_focus"),
        "rationale": rationale[:180],
        "source_evidence": list(item.get("source_evidence") or [])[:4],
    }


def _compact_queued_task(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": item.get("title"),
        "status": item.get("status"),
        "priority": item.get("priority"),
        "governance_task_type": item.get("governance_task_type"),
        "task_family": item.get("task_family"),
        "execution_kind": item.get("execution_kind"),
    }


def _compact_agenda_topic(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "topic": item.get("topic"),
        "priority": item.get("priority"),
        "status": item.get("status"),
    }


def _compact_unresolved_gap(item: Dict[str, Any]) -> Dict[str, Any]:
    rationale = str(item.get("rationale") or "").strip()
    return {
        "gap": item.get("gap"),
        "priority": item.get("priority"),
        "rationale": rationale[:160],
    }


def _compact_recommended_direction(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "direction": item.get("direction"),
        "priority": item.get("priority"),
        "candidate_kind": item.get("candidate_kind"),
        "task_type": item.get("task_type"),
        "target_horizon": item.get("target_horizon"),
    }


def _compact_active_signal(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signal": item.get("signal"),
        "priority": item.get("priority"),
        "message": str(item.get("message") or "").strip()[:160],
    }


def _compact_relation_edge(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "from": item.get("from"),
        "to": item.get("to"),
        "relation": item.get("relation"),
        "weight": item.get("weight"),
    }


def _compact_direction_task_link(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "from": item.get("from"),
        "to_candidate_kind": item.get("to_candidate_kind"),
        "to_task_type": item.get("to_task_type"),
        "relation": item.get("relation"),
        "weight": item.get("weight"),
    }


def _compact_need_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "need_type": item.get("need_type"),
        "severity": item.get("severity"),
        "urgency": item.get("urgency"),
        "confidence": item.get("confidence"),
        "rationale": item.get("rationale"),
        "source_evidence": list(item.get("source_evidence") or [])[:3],
    }


def _compact_intent_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "intent_type": item.get("intent_type"),
        "priority": item.get("priority"),
        "rationale": item.get("rationale"),
        "target_horizon": item.get("target_horizon"),
        "output_channel": item.get("output_channel"),
        "source_needs": list(item.get("source_needs") or [])[:3],
        "candidate_family": item.get("candidate_family"),
        "candidate_kind": item.get("candidate_kind"),
    }


def _compact_signal_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signal_type": item.get("signal_type"),
        "priority": item.get("priority"),
        "message": item.get("message"),
        "rationale": item.get("rationale"),
        "source_needs": list(item.get("source_needs") or [])[:3],
        "related_intent": item.get("related_intent"),
    }


def _compact_self_model_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    compact = {
        "identity_view": dict(item.get("identity_view") or {}),
        "current_state": dict(item.get("current_state") or {}),
        "readiness": dict(item.get("readiness") or {}),
        "self_understanding_gaps": list(item.get("self_understanding_gaps") or [])[:5],
        "reference_alignment_feedback": dict(item.get("reference_alignment_feedback") or {}),
        "current_topics": list(item.get("current_topics") or [])[:5],
        "unresolved_gaps": list(item.get("unresolved_gaps") or [])[:5],
        "current_directions": list(item.get("current_directions") or [])[:5],
        "summary": item.get("summary"),
    }
    readiness = dict(compact.get("readiness") or {})
    if isinstance(readiness.get("readiness_factors"), dict):
        readiness["readiness_factors"] = dict(readiness["readiness_factors"])
    compact["readiness"] = readiness
    return compact


def _compact_evidence_credibility_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "channels": [
            {
                "channel": row.get("channel"),
                "confidence": row.get("confidence"),
                "evidence_strength": row.get("evidence_strength"),
                "item_count": row.get("item_count"),
            }
            for row in list(item.get("channels") or [])[:4]
            if isinstance(row, dict)
        ],
        "high_credibility_channels": list(item.get("high_credibility_channels") or [])[:4],
        "weak_or_missing_channels": list(item.get("weak_or_missing_channels") or [])[:4],
        "conflict_flags": list(item.get("conflict_flags") or [])[:6],
        "reference_alignment_score": item.get("reference_alignment_score"),
        "summary": item.get("summary"),
    }


def _compact_task_type_priors(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "top_priority_task_type": item.get("top_priority_task_type"),
        "top_priority_score": item.get("top_priority_score"),
        "priors": [
            {
                "task_type": row.get("task_type"),
                "score": row.get("score"),
                "reasons": list(row.get("reasons") or [])[:4],
            }
            for row in list(item.get("priors") or [])[:5]
            if isinstance(row, dict)
        ],
        "summary": item.get("summary"),
    }


def _compact_proposal_drift_memory(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": item.get("available"),
        "average_score": item.get("average_score"),
        "quality_counts": dict(item.get("quality_counts") or {}),
        "drift_state": item.get("drift_state"),
        "common_posture_alignment": list(item.get("common_posture_alignment") or [])[:3],
        "common_priority_basis": list(item.get("common_priority_basis") or [])[:3],
        "posture_alignment_health": item.get("posture_alignment_health"),
        "priority_basis_health": item.get("priority_basis_health"),
        "dominant_posture_conflict_reason": item.get("dominant_posture_conflict_reason"),
        "recent_entries": [
            {
                "title": row.get("title"),
                "quality": row.get("quality"),
                "score": row.get("score"),
                "top_priority_task_type": row.get("top_priority_task_type"),
                "reasons": list(row.get("reasons") or [])[:4],
                "llm_posture_alignment": list(row.get("llm_posture_alignment") or [])[:2],
                "llm_priority_basis": list(row.get("llm_priority_basis") or [])[:2],
            }
            for row in list(item.get("recent_entries") or [])[:4]
            if isinstance(row, dict)
        ],
        "summary": item.get("summary"),
    }


def _compact_generic_item(
    item: Dict[str, Any],
    *,
    preferred_keys: tuple[str, ...] = (),
) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key in preferred_keys:
        if key in item:
            compact[key] = item[key]
    for key in (
        "confidence_score",
        "novelty_score",
        "source_reliability",
        "supports",
        "contradicts",
    ):
        if key in item:
            compact[key] = item[key]
    return compact


def _ensure_prompt_packet_budget(packet: Dict[str, Any], *, max_chars: int) -> Dict[str, Any]:
    text = json.dumps(packet, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return packet

    trimmed = dict(packet)
    if "memory_context" in trimmed:
        memory_context = str(trimmed.get("memory_context") or "")
        trimmed["memory_context"] = memory_context[:240]
    if len(json.dumps(trimmed, ensure_ascii=False, default=str)) <= max_chars:
        return trimmed

    agenda_graph = dict(trimmed.get("agenda_graph") or {})
    if agenda_graph:
        agenda_graph["relation_edges"] = list(agenda_graph.get("relation_edges") or [])[:4]
        agenda_graph["evidence_to_gap_edges"] = list(agenda_graph.get("evidence_to_gap_edges") or [])[:4]
        agenda_graph["direction_task_links"] = list(agenda_graph.get("direction_task_links") or [])[:3]
        trimmed["agenda_graph"] = agenda_graph
    evidence_graph = dict(trimmed.get("evidence_graph") or {})
    if evidence_graph:
        evidence_graph["support_edges"] = list(evidence_graph.get("support_edges") or [])[:2]
        evidence_graph["contradiction_edges"] = list(evidence_graph.get("contradiction_edges") or [])[:1]
        trimmed["evidence_graph"] = evidence_graph
    if len(json.dumps(trimmed, ensure_ascii=False, default=str)) <= max_chars:
        return trimmed

    if isinstance(trimmed.get("signals"), list):
        trimmed["signals"] = list(trimmed["signals"])[:3]
    if isinstance(trimmed.get("intents"), list):
        trimmed["intents"] = list(trimmed["intents"])[:3]
    if isinstance(trimmed.get("needs"), list):
        trimmed["needs"] = list(trimmed["needs"])[:3]
    if isinstance(trimmed.get("queued_tasks"), list):
        trimmed["queued_tasks"] = list(trimmed["queued_tasks"])[:2]
    return trimmed
