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
    task_generation_focus = _render_principles_block(
        charter.get("task_generation_focus"),
        fallback="(未配置，默认要求：先综合证据与认知记忆，再决定任务类型)",
    )
    prompt_output_requirements = _render_principles_block(
        charter.get("prompt_output_requirements"),
        fallback="(未配置，默认要求：提案需绑定证据节点并允许空 proposals)",
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
        "【认知宪章：任务生成焦点】\n"
        f"{task_generation_focus}\n\n"
        "【认知宪章：输出要求】\n"
        f"{prompt_output_requirements}\n\n"
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
    cognition_charter: Dict[str, Any],
    max_candidates: int,
) -> str:
    charter = dict(cognition_charter or {})
    prompt_packet = _prompt_facing_evidence_packet(
        evidence_packet,
        cognition_charter=charter,
    )
    cognitive_briefing = _render_cognitive_briefing(prompt_packet)
    decision_core = dict(prompt_packet.get("decision_core") or {})
    supporting_detail = dict(prompt_packet.get("supporting_detail") or {})
    long_tail_context = dict(prompt_packet.get("long_tail_context") or {})
    graph_brief = {
        "evidence_graph": dict(prompt_packet.get("evidence_graph") or {}),
        "agenda_graph": dict(prompt_packet.get("agenda_graph") or {}),
    }
    memory_brief = _build_prompt_memory_brief(prompt_packet)
    evidence_brief = _build_prompt_evidence_brief(prompt_packet)
    reasoning_focus_block = _render_principles_block(
        charter.get("task_generation_focus"),
        fallback=(
            "- 先综合主证据主题、主议程主题、grounding 缺口与认知记忆，再决定任务类型。\n"
            "- 把 cognitive_assessment 当作真实认知中间层，而不是装饰说明。"
        ),
    )
    output_requirements_block = _render_principles_block(
        charter.get("prompt_output_requirements"),
        fallback=(
            "- 提案必须显式绑定 evidence graph / agenda graph 节点。\n"
            "- 证据不足时允许返回空 proposals。"
        ),
    )
    brief_sections = ""
    for title, value, limit in (
        ("graph_brief", graph_brief, 3200),
        ("memory_brief", memory_brief, 2600),
        ("evidence_brief", evidence_brief, 2600),
    ):
        if _has_prompt_brief_content(value):
            brief_sections += (
                f"【{title}】\n"
                f"{json.dumps(value, ensure_ascii=False, default=str)[:limit]}\n\n"
            )
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
        "【本轮任务生成焦点】\n"
        f"{reasoning_focus_block}\n\n"
        "【decision_core】\n"
        f"{json.dumps(decision_core, ensure_ascii=False, default=str)[:3200]}\n\n"
        "【supporting_detail】\n"
        f"{json.dumps(supporting_detail, ensure_ascii=False, default=str)[:2200]}\n\n"
        "【long_tail_context】\n"
        f"{json.dumps(long_tail_context, ensure_ascii=False, default=str)[:1600]}\n\n"
        f"{brief_sections}"
        "【本轮输出附加要求】\n"
        f"{output_requirements_block}\n\n"
        "在生成 proposals 之前，你必须先给出一层 `cognitive_assessment`，"
        "用来显式说明你基于哪些主证据、当前核心约束与 grounding 缺口做判断。"
        "这层 assessment 是认知可见性，不是官样文章；它应直接服务于治理判断与是否需要行动投影。"
        "如果 evidence_packet 给出了 self_iteration_hypotheses，你应显式判断当前最应该被迭代的认知缺陷域。\n\n"
        "如果 evidence_packet 给出了 self_iteration_trend_memory，你还应判断应该延续当前自我迭代方向还是切换方向，"
        "并给出切换或维持的依据。\n\n"
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
        "【认知简报】\n"
        f"{cognitive_briefing}\n\n"
        "输出 JSON 对象：\n"
        "{\n"
        '  "cognitive_assessment": {\n'
        '    "current_judgement":"...",\n'
        '    "dominant_constraint":"...",\n'
        '    "primary_grounding_gaps":["..."],\n'
        '    "why_this_task_type_now":["..."],\n'
        '    "why_not_improvement_now":["..."],\n'
        '    "self_iteration_target":"...",\n'
        '    "self_iteration_hypothesis":"...",\n'
        '    "stay_or_switch":"stay",\n'
        '    "switch_reason":"..."\n'
        "  },\n"
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


def _render_cognitive_briefing(packet: Dict[str, Any]) -> str:
    decision_core = dict(packet.get("decision_core") or {})
    supporting_detail = dict(packet.get("supporting_detail") or {})
    queue_state_snapshot = dict(packet.get("queue_state_snapshot") or {})
    grounding_focus = dict(packet.get("grounding_focus") or {})
    meta_cognition_profile = dict(packet.get("meta_cognition_profile") or {})
    cognitive_posture = dict(packet.get("cognitive_posture") or {})
    self_model_snapshot = dict(packet.get("self_model_snapshot") or {})
    evidence_credibility_summary = dict(packet.get("evidence_credibility_summary") or {})
    self_iteration_hypotheses = dict(packet.get("self_iteration_hypotheses") or {})

    posture_source = dict(decision_core.get("cognitive_posture") or {})
    posture_name = str(
        posture_source.get("name") or cognitive_posture.get("name") or "balanced"
    ).strip()
    posture_reason = str(
        posture_source.get("selection_reason")
        or cognitive_posture.get("selection_reason")
        or "unknown"
    ).strip()
    current_judgement = str(
        decision_core.get("current_judgement")
        or meta_cognition_profile.get("current_judgement")
        or meta_cognition_profile.get("summary")
        or "unknown"
    ).strip()
    dominant_constraint = str(
        decision_core.get("dominant_constraint")
        or meta_cognition_profile.get("dominant_constraint")
        or "unknown"
    ).strip()
    secondary_task_shape_hint = str(
        decision_core.get("secondary_task_shape_hint")
        or "unknown"
    ).strip()
    governance_posture = str(
        decision_core.get("governance_posture")
        or meta_cognition_profile.get("governance_posture")
        or secondary_task_shape_hint
        or "unknown"
    ).strip()
    secondary_task_shape_score = decision_core.get("secondary_task_shape_score")
    self_gaps = [
        str(item).strip()
        for item in list(
            supporting_detail.get("self_understanding_gaps")
            or self_model_snapshot.get("self_understanding_gaps")
            or []
        )[:4]
        if str(item).strip()
    ]
    primary_evidence_nodes = [
        str(item).strip()
        for item in list(
            decision_core.get("primary_evidence_nodes")
            or grounding_focus.get("primary_evidence_nodes")
            or []
        )[:3]
        if str(item).strip()
    ]
    primary_agenda_nodes = [
        str(item).strip()
        for item in list(
            decision_core.get("primary_agenda_nodes")
            or grounding_focus.get("primary_agenda_nodes")
            or []
        )[:3]
        if str(item).strip()
    ]
    grounding_gaps = [
        str(item).strip()
        for item in list(
            supporting_detail.get("grounding_gaps")
            or grounding_focus.get("grounding_gaps")
            or []
        )[:4]
        if str(item).strip()
    ]
    contradictory_topics = [
        str(item).strip()
        for item in list(
            supporting_detail.get("contradictory_topics")
            or grounding_focus.get("contradictory_topics")
            or []
        )[:3]
        if str(item).strip()
    ]
    weak_channels = [
        str(item).strip()
        for item in list(
            supporting_detail.get("weak_or_missing_channels")
            or evidence_credibility_summary.get("weak_or_missing_channels")
            or []
        )[:4]
        if str(item).strip()
    ]
    top_iteration_domain = str(
        decision_core.get("top_self_iteration_domain")
        or self_iteration_hypotheses.get("top_target_domain")
        or "unknown"
    ).strip()
    dominant_iteration_hypothesis = str(
        decision_core.get("top_self_iteration_hypothesis")
        or self_iteration_hypotheses.get("dominant_hypothesis")
        or "unknown"
    ).strip()
    meta_summary = str(
        decision_core.get("summary")
        or meta_cognition_profile.get("summary")
        or current_judgement
        or "unknown"
    ).strip()
    dominant_failure_mode = str(
        meta_cognition_profile.get("dominant_failure_mode") or "unknown"
    ).strip()
    grounding_pressure = str(
        decision_core.get("grounding_pressure")
        or meta_cognition_profile.get("grounding_pressure")
        or "unknown"
    ).strip()
    why_not_improvement_now = [
        str(item).strip()
        for item in list(supporting_detail.get("why_not_improvement_now") or [])[:3]
        if str(item).strip()
    ]
    queue_state_summary = str(
        decision_core.get("queue_state_summary")
        or queue_state_snapshot.get("summary")
        or ""
    ).strip()

    lines = [
        f"- 元认知画像: {meta_summary}",
        f"- 当前判断: {current_judgement}",
        f"- 当前主约束: {dominant_constraint}",
        f"- 当前主失败模式: {dominant_failure_mode}",
        f"- 当前 grounding 压力: {grounding_pressure}",
        f"- 当前建议治理姿态: {governance_posture}",
        f"- 当前姿态: {posture_name} ({posture_reason})",
        f"- 当前主证据主题: {', '.join(primary_evidence_nodes) or 'none'}",
        f"- 当前主议程主题: {', '.join(primary_agenda_nodes) or 'none'}",
        f"- 当前 grounding 缺口: {', '.join(grounding_gaps) or 'none'}",
        f"- 当前证据冲突: {', '.join(contradictory_topics) or 'none'}",
        f"- 当前弱通道: {', '.join(weak_channels) or 'none'}",
        f"- 当前自我理解缺口: {', '.join(self_gaps) or 'none'}",
        f"- 当前首要自我迭代域: {top_iteration_domain or 'none'}",
        f"- 当前首要自我迭代假设: {dominant_iteration_hypothesis or 'none'}",
        f"- 当前排队上下文: {queue_state_summary or 'none'}",
        f"- 当前不宜直接 improvement 的原因: {', '.join(why_not_improvement_now) or 'none'}",
        "- 先输出一个 cognitive_assessment，明确写出当前判断、主约束、grounding 缺口，以及为什么当前治理姿态成立。",
        "- 如果 evidence_packet 提供了 self_iteration_hypotheses，请在 cognitive_assessment 中写出 self_iteration_target 与 self_iteration_hypothesis。",
        "- 如果 evidence_packet 提供了 self_iteration_trend_memory，请在 cognitive_assessment 中写出 stay_or_switch 与 switch_reason。",
        "- 先基于主证据主题与主议程主题做判断，再决定是否需要形成任务投影以及投影强度。",
        "- 如果 grounding 缺口或证据冲突明显，优先提出 observation / review / learning，而不是直接 improvement。",
    ]
    if secondary_task_shape_hint and secondary_task_shape_hint != "unknown":
        task_shape_line = (
            f"- 任务形态辅助提示: {secondary_task_shape_hint}"
            + (
                f" ({float(secondary_task_shape_score):.2f})"
                if isinstance(secondary_task_shape_score, (int, float))
                else ""
            )
            + "。仅作辅助参考，不得覆盖当前判断与治理姿态。"
        )
        lines.insert(7, task_shape_line)
    return "\n".join(lines)


def _build_prompt_memory_brief(packet: Dict[str, Any]) -> Dict[str, Any]:
    brief: Dict[str, Any] = {}
    for key in (
        "recent_reference_alignment",
        "proposal_drift_memory",
        "self_iteration_trend_memory",
        "switch_self_regulation_memory",
        "post_task_effect_memory",
        "cognitive_assessment_memory",
        "evidence_credibility_summary",
    ):
        value = packet.get(key)
        if value:
            brief[key] = value
    return brief


def _build_prompt_evidence_brief(packet: Dict[str, Any]) -> Dict[str, Any]:
    brief: Dict[str, Any] = {}
    for key in (
        "recent_learning_evidence",
        "external_research_evidence",
        "shell_body_profile",
        "research_digest",
    ):
        value = packet.get(key)
        if value:
            brief[key] = value
    return brief


def _has_prompt_brief_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_prompt_brief_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_prompt_brief_content(item) for item in value)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return value
    return True


def _prompt_facing_evidence_packet(
    evidence_packet: Dict[str, Any],
    *,
    cognition_charter: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    source_packet = dict(evidence_packet or {})
    packet: Dict[str, Any] = {}
    charter = dict(cognition_charter or {})
    prompt_attention_policy = _resolve_prompt_attention_policy(charter)

    # Keep the highest-value cognitive summaries first so they survive prompt truncation.
    for key in list(prompt_attention_policy.get("priority_order") or []):
        if key in source_packet:
            packet[key] = source_packet[key]

    decision_core = dict(packet.get("decision_core") or {})
    if decision_core:
        packet["decision_core"] = {
            "current_judgement": str(
                decision_core.get("current_judgement") or ""
            ).strip(),
            "dominant_constraint": str(
                decision_core.get("dominant_constraint") or ""
            ).strip(),
            "grounding_pressure": str(
                decision_core.get("grounding_pressure") or ""
            ).strip(),
            "governance_posture": str(
                decision_core.get("governance_posture")
                or ""
            ).strip(),
            "secondary_task_shape_hint": str(
                decision_core.get("secondary_task_shape_hint")
                or ""
            ).strip(),
            "secondary_task_shape_score": decision_core.get("secondary_task_shape_score"),
            "top_self_iteration_domain": str(
                decision_core.get("top_self_iteration_domain") or ""
            ).strip(),
            "top_self_iteration_hypothesis": str(
                decision_core.get("top_self_iteration_hypothesis") or ""
            ).strip(),
            "primary_evidence_nodes": [
                str(item).strip()
                for item in list(decision_core.get("primary_evidence_nodes") or [])[:3]
                if str(item).strip()
            ],
            "primary_agenda_nodes": [
                str(item).strip()
                for item in list(decision_core.get("primary_agenda_nodes") or [])[:3]
                if str(item).strip()
            ],
            "queue_state_summary": str(
                decision_core.get("queue_state_summary") or ""
            ).strip(),
            "summary": str(decision_core.get("summary") or "").strip()[:220],
        }
    supporting_detail = dict(packet.get("supporting_detail") or {})
    if supporting_detail:
        packet["supporting_detail"] = {
            "grounding_gaps": [
                str(item).strip()
                for item in list(supporting_detail.get("grounding_gaps") or [])[:4]
                if str(item).strip()
            ],
            "contradictory_topics": [
                str(item).strip()
                for item in list(supporting_detail.get("contradictory_topics") or [])[:3]
                if str(item).strip()
            ],
            "weak_or_missing_channels": [
                str(item).strip()
                for item in list(supporting_detail.get("weak_or_missing_channels") or [])[:4]
                if str(item).strip()
            ],
            "self_understanding_gaps": [
                str(item).strip()
                for item in list(supporting_detail.get("self_understanding_gaps") or [])[:4]
                if str(item).strip()
            ],
            "why_not_improvement_now": [
                str(item).strip()
                for item in list(supporting_detail.get("why_not_improvement_now") or [])[:4]
                if str(item).strip()
            ],
            "trend_state": str(supporting_detail.get("trend_state") or "").strip(),
            "stay_or_switch_bias": str(
                supporting_detail.get("stay_or_switch_bias") or ""
            ).strip(),
            "recent_effect_direction": str(
                supporting_detail.get("recent_effect_direction") or ""
            ).strip(),
            "reference_alignment_score": supporting_detail.get("reference_alignment_score"),
            "self_iteration_readiness_score": supporting_detail.get("self_iteration_readiness_score"),
            "summary": str(supporting_detail.get("summary") or "").strip()[:220],
        }
    long_tail_context = dict(packet.get("long_tail_context") or {})
    if long_tail_context:
        packet["long_tail_context"] = {
            "recent_learning_titles": [
                str(item).strip()
                for item in list(long_tail_context.get("recent_learning_titles") or [])[:5]
                if str(item).strip()
            ],
            "recent_learning_evidence": [
                {
                    "title": str(item.get("title") or "").strip(),
                    "quality_score": item.get("quality_score"),
                }
                for item in list(long_tail_context.get("recent_learning_evidence") or [])[:2]
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ],
            "external_research_titles": [
                str(item).strip()
                for item in list(long_tail_context.get("external_research_titles") or [])[:3]
                if str(item).strip()
            ],
            "evidence_channels": [
                {
                    "channel": str(item.get("channel") or "").strip(),
                    "evidence_strength": str(item.get("evidence_strength") or "").strip(),
                    "item_count": item.get("item_count"),
                }
                for item in list(long_tail_context.get("evidence_channels") or [])[:4]
                if isinstance(item, dict) and str(item.get("channel") or "").strip()
            ],
            "memory_context_preview": str(
                long_tail_context.get("memory_context_preview") or ""
            ).strip()[:220],
            "summary": str(long_tail_context.get("summary") or "").strip()[:220],
        }

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
    meta_cognition_profile = dict(packet.get("meta_cognition_profile") or {})
    if meta_cognition_profile:
        packet["meta_cognition_profile"] = {
            "current_judgement": str(
                meta_cognition_profile.get("current_judgement") or ""
            ).strip(),
            "dominant_constraint": str(
                meta_cognition_profile.get("dominant_constraint") or ""
            ).strip(),
            "grounding_pressure": str(
                meta_cognition_profile.get("grounding_pressure") or ""
            ).strip(),
            "top_self_iteration_domain": str(
                meta_cognition_profile.get("top_self_iteration_domain") or ""
            ).strip(),
            "top_self_iteration_hypothesis": str(
                meta_cognition_profile.get("top_self_iteration_hypothesis") or ""
            ).strip(),
            "stay_or_switch_bias": str(
                meta_cognition_profile.get("stay_or_switch_bias") or ""
            ).strip(),
            "switch_bias_effectiveness": str(
                meta_cognition_profile.get("switch_bias_effectiveness") or ""
            ).strip(),
            "recent_effect_direction": str(
                meta_cognition_profile.get("recent_effect_direction") or ""
            ).strip(),
            "dominant_failure_mode": str(
                meta_cognition_profile.get("dominant_failure_mode") or ""
            ).strip(),
            "governance_posture": str(
                meta_cognition_profile.get("governance_posture")
                or meta_cognition_profile.get("recommended_task_posture")
                or ""
            ).strip(),
            "priority_signals": [
                str(item).strip()
                for item in list(meta_cognition_profile.get("priority_signals") or [])[:6]
                if str(item).strip()
            ],
            "summary": str(meta_cognition_profile.get("summary") or "").strip()[:260],
        }
    cognitive_assessment_memory = dict(packet.get("cognitive_assessment_memory") or {})
    if cognitive_assessment_memory:
        packet["cognitive_assessment_memory"] = _compact_cognitive_assessment_memory(
            cognitive_assessment_memory,
            summary_limit=220,
        )
    self_iteration_trend_memory = dict(packet.get("self_iteration_trend_memory") or {})
    if self_iteration_trend_memory:
        packet["self_iteration_trend_memory"] = _compact_self_iteration_trend_memory(
            self_iteration_trend_memory,
            summary_limit=220,
        )
    switch_self_regulation_memory = dict(packet.get("switch_self_regulation_memory") or {})
    if switch_self_regulation_memory:
        packet["switch_self_regulation_memory"] = {
            "preferred_switch_bias": str(
                switch_self_regulation_memory.get("preferred_switch_bias") or ""
            ).strip(),
            "switch_effectiveness": str(
                switch_self_regulation_memory.get("switch_effectiveness") or ""
            ).strip(),
            "stay_effectiveness": str(
                switch_self_regulation_memory.get("stay_effectiveness") or ""
            ).strip(),
            "average_switch_quality": switch_self_regulation_memory.get("average_switch_quality"),
            "average_stay_quality": switch_self_regulation_memory.get("average_stay_quality"),
            "summary": str(switch_self_regulation_memory.get("summary") or "").strip()[:220],
        }
    post_task_effect_memory = dict(packet.get("post_task_effect_memory") or {})
    if post_task_effect_memory:
        packet["post_task_effect_memory"] = {
            "effect_direction": str(
                post_task_effect_memory.get("effect_direction") or ""
            ).strip(),
            "average_quality_score": post_task_effect_memory.get("average_quality_score"),
            "average_cognitive_alignment_score": post_task_effect_memory.get("average_cognitive_alignment_score"),
            "average_reference_alignment_score": post_task_effect_memory.get("average_reference_alignment_score"),
            "dominant_target_effect": str(
                post_task_effect_memory.get("dominant_target_effect") or ""
            ).strip(),
            "summary": str(post_task_effect_memory.get("summary") or "").strip()[:220],
        }
    self_iteration_hypotheses = dict(packet.get("self_iteration_hypotheses") or {})
    if self_iteration_hypotheses:
        packet["self_iteration_hypotheses"] = _compact_self_iteration_hypotheses(
            self_iteration_hypotheses,
            summary_limit=240,
            guidance_limit=220,
        )
    grounding_focus = _derive_grounding_focus(source_packet)
    if grounding_focus:
        packet["grounding_focus"] = grounding_focus

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
        packet["recent_reference_alignment"] = _compact_recent_reference_alignment(
            recent_reference_alignment,
            summary_limit=220,
        )

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
    packet.pop("task_type_priors", None)
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
    queue_state_snapshot = _derive_queue_state_snapshot(source_packet)
    if queue_state_snapshot:
        packet["queue_state_snapshot"] = queue_state_snapshot
    has_context_layers = any(
        packet.get(key) for key in ("decision_core", "supporting_detail", "long_tail_context")
    )
    if has_context_layers:
        packet.pop("cognitive_evolution_draft", None)
        meta_cognition_profile = dict(packet.get("meta_cognition_profile") or {})
        if meta_cognition_profile:
            packet["meta_cognition_profile"] = {
                "current_judgement": str(
                    meta_cognition_profile.get("current_judgement") or ""
                ).strip()[:140],
                "dominant_constraint": str(
                    meta_cognition_profile.get("dominant_constraint") or ""
                ).strip()[:140],
                "grounding_pressure": str(
                    meta_cognition_profile.get("grounding_pressure") or ""
                ).strip()[:32],
                "top_self_iteration_domain": str(
                    meta_cognition_profile.get("top_self_iteration_domain") or ""
                ).strip()[:80],
                "top_self_iteration_hypothesis": str(
                    meta_cognition_profile.get("top_self_iteration_hypothesis") or ""
                ).strip()[:160],
                "dominant_failure_mode": str(
                    meta_cognition_profile.get("dominant_failure_mode") or ""
                ).strip()[:120],
                "stay_or_switch_bias": str(
                    meta_cognition_profile.get("stay_or_switch_bias") or ""
                ).strip()[:32],
                "recent_effect_direction": str(
                    meta_cognition_profile.get("recent_effect_direction") or ""
                ).strip()[:32],
                "governance_posture": str(
                    meta_cognition_profile.get("governance_posture")
                    or meta_cognition_profile.get("recommended_task_posture")
                    or ""
                ).strip()[:80],
                "priority_signals": list(meta_cognition_profile.get("priority_signals") or [])[:3],
                "summary": str(meta_cognition_profile.get("summary") or "").strip()[:180],
            }
        grounding_focus = dict(packet.get("grounding_focus") or {})
        if grounding_focus:
            packet["grounding_focus"] = {
                "primary_evidence_nodes": list(
                    grounding_focus.get("primary_evidence_nodes") or []
                )[:3],
                "primary_agenda_nodes": list(
                    grounding_focus.get("primary_agenda_nodes") or []
                )[:3],
                "recommended_directions": list(
                    grounding_focus.get("recommended_directions") or []
                )[:3],
                "grounding_gaps": list(grounding_focus.get("grounding_gaps") or [])[:4],
                "weak_or_missing_channels": list(
                    grounding_focus.get("weak_or_missing_channels") or []
                )[:3],
                "summary": str(grounding_focus.get("summary") or "").strip()[:180],
                "guidance": str(grounding_focus.get("guidance") or "").strip()[:160],
            }
        self_iteration_hypotheses = dict(packet.get("self_iteration_hypotheses") or {})
        if self_iteration_hypotheses:
            packet["self_iteration_hypotheses"] = _compact_self_iteration_hypotheses(
                self_iteration_hypotheses,
                summary_limit=180,
                guidance_limit=160,
                text_limit=180,
            )
        recent_reference_alignment = dict(packet.get("recent_reference_alignment") or {})
        if recent_reference_alignment:
            packet["recent_reference_alignment"] = _compact_recent_reference_alignment(
                recent_reference_alignment,
                summary_limit=180,
            )
        cognitive_assessment_memory = dict(packet.get("cognitive_assessment_memory") or {})
        if cognitive_assessment_memory:
            packet["cognitive_assessment_memory"] = _compact_cognitive_assessment_memory(
                cognitive_assessment_memory,
                summary_limit=180,
                text_limit=140,
            )
        self_iteration_trend_memory = dict(packet.get("self_iteration_trend_memory") or {})
        if self_iteration_trend_memory:
            packet["self_iteration_trend_memory"] = _compact_self_iteration_trend_memory(
                self_iteration_trend_memory,
                summary_limit=180,
                text_limit=80,
            )
        switch_self_regulation_memory = dict(packet.get("switch_self_regulation_memory") or {})
        if switch_self_regulation_memory:
            packet["switch_self_regulation_memory"] = {
                "preferred_switch_bias": str(
                    switch_self_regulation_memory.get("preferred_switch_bias") or ""
                ).strip()[:40],
                "switch_effectiveness": str(
                    switch_self_regulation_memory.get("switch_effectiveness") or ""
                ).strip()[:40],
                "stay_effectiveness": str(
                    switch_self_regulation_memory.get("stay_effectiveness") or ""
                ).strip()[:40],
                "average_switch_quality": switch_self_regulation_memory.get(
                    "average_switch_quality"
                ),
                "average_stay_quality": switch_self_regulation_memory.get(
                    "average_stay_quality"
                ),
                "summary": str(switch_self_regulation_memory.get("summary") or "").strip()[:180],
            }
        post_task_effect_memory = dict(packet.get("post_task_effect_memory") or {})
        if post_task_effect_memory:
            packet["post_task_effect_memory"] = {
                "effect_direction": str(
                    post_task_effect_memory.get("effect_direction") or ""
                ).strip()[:40],
                "average_quality_score": post_task_effect_memory.get("average_quality_score"),
                "average_cognitive_alignment_score": post_task_effect_memory.get(
                    "average_cognitive_alignment_score"
                ),
                "average_reference_alignment_score": post_task_effect_memory.get(
                    "average_reference_alignment_score"
                ),
                "dominant_target_effect": str(
                    post_task_effect_memory.get("dominant_target_effect") or ""
                ).strip()[:120],
                "summary": str(post_task_effect_memory.get("summary") or "").strip()[:180],
            }
        proposal_drift_memory = dict(packet.get("proposal_drift_memory") or {})
        if proposal_drift_memory:
            packet["proposal_drift_memory"] = _compact_proposal_drift_memory(
                proposal_drift_memory
            )
    memory_context = str(packet.get("memory_context") or "")
    if memory_context:
        packet["memory_context"] = memory_context[:600]
    packet = _ensure_prompt_packet_budget(
        packet,
        max_chars=max(1000, int(prompt_attention_policy.get("max_chars") or 11500)),
        prompt_attention_policy=prompt_attention_policy,
    )
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


def _derive_queue_state_snapshot(packet: Dict[str, Any]) -> Dict[str, Any]:
    queued_tasks = [
        dict(item)
        for item in list(packet.get("queued_tasks") or [])[:8]
        if isinstance(item, dict)
    ]
    queued_learning_titles = [
        str(item).strip()
        for item in list(packet.get("queued_learning_titles") or [])[:5]
        if str(item).strip()
    ]
    queued_body_titles = [
        str(item).strip()
        for item in list(packet.get("queued_body_improvement_titles") or [])[:4]
        if str(item).strip()
    ]
    if not queued_tasks and not queued_learning_titles and not queued_body_titles:
        return {}

    queue_count = len(queued_tasks)
    learning_count = len(queued_learning_titles)
    body_count = len(queued_body_titles)
    recent_titles = [
        str(item.get("title") or "").strip()
        for item in queued_tasks[:4]
        if str(item.get("title") or "").strip()
    ]
    statuses = [
        str(item.get("status") or "").strip()
        for item in queued_tasks[:4]
        if str(item.get("status") or "").strip()
    ]
    return {
        "queued_task_count": queue_count,
        "queued_learning_count": learning_count,
        "queued_body_improvement_count": body_count,
        "recent_titles": recent_titles,
        "recent_statuses": statuses,
        "summary": (
            f"queued_tasks={queue_count}; "
            f"queued_learning={learning_count}; "
            f"queued_body_improvement={body_count}; "
            f"recent_titles={', '.join(recent_titles[:3]) or 'none'}."
        ),
        "guidance": (
            "Avoid proposing tasks that duplicate existing queued work unless the evidence clearly justifies a stronger replacement."
        ),
    }


def _derive_grounding_focus(packet: Dict[str, Any]) -> Dict[str, Any]:
    evidence_graph = dict(packet.get("evidence_graph") or {})
    agenda_graph = dict(packet.get("agenda_graph") or {})
    recent_reference_alignment = dict(packet.get("recent_reference_alignment") or {})
    evidence_credibility_summary = dict(packet.get("evidence_credibility_summary") or {})

    primary_evidence_nodes = [
        str(item.get("topic") or "").strip()
        for item in sorted(
            [
                dict(row)
                for row in list(evidence_graph.get("nodes") or [])
                if isinstance(row, dict) and str(row.get("topic") or "").strip()
            ],
            key=lambda row: (
                -float(row.get("priority") or row.get("avg_confidence") or 0.0),
                str(row.get("topic") or "").strip(),
            ),
        )[:3]
        if str(item.get("topic") or "").strip()
    ]
    contradictory_topics = []
    for edge in list(evidence_graph.get("contradiction_edges") or [])[:3]:
        if not isinstance(edge, dict):
            continue
        frm = str(edge.get("from") or "").strip()
        to = str(edge.get("to") or "").strip()
        relation = str(edge.get("relation") or "").strip()
        if frm or to:
            contradictory_topics.append(f"{frm}->{to}:{relation or 'contradicts'}")

    primary_agenda_nodes: list[str] = []
    focus = str(agenda_graph.get("focus") or "").strip()
    if focus:
        primary_agenda_nodes.append(f"focus:{focus}")
    primary_agenda_nodes.extend(
        str(item.get("gap") or "").strip()
        for item in sorted(
            [
                dict(row)
                for row in list(agenda_graph.get("unresolved_gaps") or [])
                if isinstance(row, dict) and str(row.get("gap") or "").strip()
            ],
            key=lambda row: (-float(row.get("priority") or 0.0), str(row.get("gap") or "").strip()),
        )[:3]
        if str(item.get("gap") or "").strip()
    )
    recommended_directions = [
        str(item.get("direction") or "").strip()
        for item in list(agenda_graph.get("recommended_directions") or [])[:3]
        if isinstance(item, dict) and str(item.get("direction") or "").strip()
    ]

    grounding_gaps = []
    primary_missing_evidence_node = str(
        recent_reference_alignment.get("primary_missing_evidence_node") or ""
    ).strip()
    primary_missing_agenda_node = str(
        recent_reference_alignment.get("primary_missing_agenda_node") or ""
    ).strip()
    if primary_missing_evidence_node:
        grounding_gaps.append(f"missing_evidence:{primary_missing_evidence_node}")
    if primary_missing_agenda_node:
        grounding_gaps.append(f"missing_agenda:{primary_missing_agenda_node}")
    recent_entries = _legacy_reference_alignment_entries(
        recent_reference_alignment,
        limit=3,
    )
    for entry in recent_entries:
        for node in list(entry.get("missing_evidence_nodes") or [])[:2]:
            text = str(node).strip()
            label = f"missing_evidence:{text}" if text else ""
            if label and label not in grounding_gaps:
                grounding_gaps.append(label)
        for node in list(entry.get("missing_agenda_nodes") or [])[:2]:
            text = str(node).strip()
            label = f"missing_agenda:{text}" if text else ""
            if label and label not in grounding_gaps:
                grounding_gaps.append(label)
    weak_or_missing_channels = [
        str(item).strip()
        for item in list(evidence_credibility_summary.get("weak_or_missing_channels") or [])[:4]
        if str(item).strip()
    ]

    if not (
        primary_evidence_nodes
        or contradictory_topics
        or primary_agenda_nodes
        or recommended_directions
        or grounding_gaps
        or weak_or_missing_channels
    ):
        return {}

    return {
        "primary_evidence_nodes": primary_evidence_nodes,
        "primary_agenda_nodes": primary_agenda_nodes,
        "recommended_directions": recommended_directions,
        "contradictory_topics": contradictory_topics,
        "grounding_gaps": grounding_gaps[:6],
        "weak_or_missing_channels": weak_or_missing_channels,
        "summary": (
            f"Primary evidence={', '.join(primary_evidence_nodes[:3]) or 'none'}; "
            f"primary agenda={', '.join(primary_agenda_nodes[:3]) or 'none'}; "
            f"gaps={', '.join(grounding_gaps[:4]) or 'none'}."
        ),
        "guidance": (
            "Bind proposals to primary evidence and agenda nodes first; "
            "treat contradictions and grounding gaps as priority repair signals."
        ),
    }


def _first_text(values: Any, *, limit: int) -> str:
    raw_values = [values] if isinstance(values, str) else list(values or [])
    for item in raw_values:
        text = str(item).strip()
        if text:
            return text[:limit]
    return ""


def _text_count(values: Any) -> int:
    raw_values = [values] if isinstance(values, str) else list(values or [])
    return sum(1 for item in raw_values if str(item).strip())


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _stored_count(item: Dict[str, Any], *keys: str) -> int:
    return max(_safe_count(item.get(key)) for key in keys)


def _legacy_reference_alignment_entries(item: Dict[str, Any], *, limit: int) -> list[Dict[str, Any]]:
    return [
        dict(row)
        for row in list(item.get("recent_entries") or [])[:limit]
        if isinstance(row, dict)
    ]


def _compact_cognitive_assessment_memory(
    item: Dict[str, Any],
    *,
    summary_limit: int,
    text_limit: int = 180,
) -> Dict[str, Any]:
    legacy_current_judgements = item.get("common_current_judgements") or []
    legacy_why_not_improvement = item.get("common_why_not_improvement_now") or []
    legacy_grounding_gaps = item.get("common_grounding_gaps") or []
    current_judgement = str(item.get("current_judgement") or "").strip()[
        :text_limit
    ] or _first_text(legacy_current_judgements, limit=text_limit)
    why_not_improvement_now = str(
        item.get("why_not_improvement_now") or ""
    ).strip()[:text_limit] or _first_text(legacy_why_not_improvement, limit=text_limit)
    primary_grounding_gap = str(item.get("primary_grounding_gap") or "").strip()[
        :text_limit
    ] or _first_text(legacy_grounding_gaps, limit=text_limit)
    return {
        "dominant_constraint": str(item.get("dominant_constraint") or "").strip()[:text_limit],
        "current_judgement": current_judgement,
        "why_not_improvement_now": why_not_improvement_now,
        "primary_grounding_gap": primary_grounding_gap,
        "current_judgement_count": max(
            _safe_count(item.get("current_judgement_count")),
            _text_count(legacy_current_judgements),
        ),
        "why_not_improvement_now_count": max(
            _safe_count(item.get("why_not_improvement_now_count")),
            _text_count(legacy_why_not_improvement),
        ),
        "grounding_gap_count": max(
            _safe_count(item.get("grounding_gap_count")),
            _text_count(legacy_grounding_gaps),
        ),
        "summary": str(item.get("summary") or "").strip()[:summary_limit],
    }


def _compact_self_iteration_trend_memory(
    item: Dict[str, Any],
    *,
    summary_limit: int,
    text_limit: int = 120,
) -> Dict[str, Any]:
    legacy_targets = item.get("common_targets") or []
    legacy_hypotheses = item.get("common_hypotheses") or []
    legacy_stay_or_switch = item.get("common_stay_or_switch") or []
    legacy_switch_reasons = item.get("common_switch_reasons") or []
    dominant_hypothesis = str(item.get("dominant_hypothesis") or "").strip()[
        : max(text_limit, 160)
    ] or _first_text(legacy_hypotheses, limit=max(text_limit, 160))
    stay_or_switch_value = (
        str(item.get("dominant_stay_or_switch") or "").strip()[:40]
        or str(item.get("stay_or_switch") or "").strip()[:40]
        or _first_text(legacy_stay_or_switch, limit=40)
    )
    switch_reason = str(
        item.get("dominant_switch_reason") or item.get("switch_reason") or ""
    ).strip()[
        : max(text_limit, 160)
    ] or _first_text(legacy_switch_reasons, limit=max(text_limit, 160))
    return {
        "dominant_target": str(item.get("dominant_target") or "").strip()[:text_limit],
        "trend_state": str(item.get("trend_state") or "").strip()[:40],
        "target_stability": str(item.get("target_stability") or "").strip()[:40],
        "dominant_hypothesis": dominant_hypothesis,
        "stay_or_switch": stay_or_switch_value,
        "switch_reason": switch_reason,
        "target_signal_count": max(
            _stored_count(item, "target_count", "target_signal_count"),
            _text_count(legacy_targets),
        ),
        "hypothesis_signal_count": max(
            _stored_count(item, "hypothesis_count", "hypothesis_signal_count"),
            _text_count(legacy_hypotheses),
        ),
        "stay_or_switch_signal_count": max(
            _stored_count(item, "stay_or_switch_count", "stay_or_switch_signal_count"),
            _text_count(legacy_stay_or_switch),
        ),
        "switch_reason_signal_count": max(
            _stored_count(item, "switch_reason_count", "switch_reason_signal_count"),
            _text_count(legacy_switch_reasons),
        ),
        "summary": str(item.get("summary") or "").strip()[:summary_limit],
    }


def _compact_self_iteration_hypotheses(
    item: Dict[str, Any],
    *,
    summary_limit: int,
    guidance_limit: int,
    text_limit: int = 180,
) -> Dict[str, Any]:
    hypotheses = [
        dict(row)
        for row in list(item.get("hypotheses") or [])
        if isinstance(row, dict) and str(row.get("hypothesis") or "").strip()
    ]
    dominant_row = hypotheses[0] if hypotheses else {}
    dominant_hypothesis = str(item.get("dominant_hypothesis") or "").strip()[
        :text_limit
    ] or str(dominant_row.get("hypothesis") or "").strip()[:text_limit]
    top_target_domain = str(item.get("top_target_domain") or "").strip()[
        :80
    ] or str(dominant_row.get("target_domain") or "").strip()[:80]
    stored_hypothesis_count = _safe_count(item.get("hypothesis_count"))
    hypothesis_count = (
        stored_hypothesis_count
        if item.get("hypothesis_count") is not None
        else len(hypotheses)
    )
    suggested_task_types = [
        str(row).strip()
        for row in list(
            item.get("suggested_task_types")
            or dominant_row.get("suggested_task_types")
            or []
        )[:3]
        if str(row).strip()
    ]
    top_evidence = str(item.get("top_evidence") or "").strip()[
        :text_limit
    ] or _first_text(dominant_row.get("evidence") or [], limit=text_limit)
    return {
        "available": bool(item.get("available")) or bool(dominant_hypothesis),
        "top_target_domain": top_target_domain,
        "dominant_hypothesis": dominant_hypothesis,
        "hypothesis_count": max(hypothesis_count, 1 if dominant_hypothesis else 0),
        "top_priority": (
            item.get("top_priority")
            if item.get("top_priority") is not None
            else dominant_row.get("priority")
        ),
        "top_evidence": top_evidence,
        "suggested_task_types": suggested_task_types,
        "summary": str(item.get("summary") or "").strip()[:summary_limit],
        "guidance": str(item.get("guidance") or "").strip()[:guidance_limit],
    }


def _compact_recent_reference_alignment(
    item: Dict[str, Any],
    *,
    summary_limit: int,
    text_limit: int = 140,
) -> Dict[str, Any]:
    entries = _legacy_reference_alignment_entries(item, limit=12)
    missing_evidence_nodes: list[str] = []
    missing_agenda_nodes: list[str] = []
    legacy_weak_or_partial_count = 0
    for entry in entries:
        quality = str(entry.get("quality") or "").strip().lower()
        if quality in {"weak", "partial", "drifted"}:
            legacy_weak_or_partial_count += 1
        missing_evidence_nodes.extend(
            str(node).strip()
            for node in list(entry.get("missing_evidence_nodes") or [])
            if str(node).strip()
        )
        missing_agenda_nodes.extend(
            str(node).strip()
            for node in list(entry.get("missing_agenda_nodes") or [])
            if str(node).strip()
        )
    return {
        "available": bool(item.get("available")) or bool(entries),
        "average_alignment_score": item.get("average_alignment_score"),
        "weak_or_partial_count": max(
            _safe_count(item.get("weak_or_partial_count")),
            legacy_weak_or_partial_count,
        ),
        "entry_count": max(len(entries), _safe_count(item.get("entry_count"))),
        "primary_missing_evidence_node": str(
            item.get("primary_missing_evidence_node") or ""
        ).strip()[:text_limit]
        or _first_text(missing_evidence_nodes, limit=text_limit),
        "primary_missing_agenda_node": str(
            item.get("primary_missing_agenda_node") or ""
        ).strip()[:text_limit]
        or _first_text(missing_agenda_nodes, limit=text_limit),
        "missing_evidence_node_count": max(
            _text_count(missing_evidence_nodes),
            _safe_count(item.get("missing_evidence_node_count")),
        ),
        "missing_agenda_node_count": max(
            _text_count(missing_agenda_nodes),
            _safe_count(item.get("missing_agenda_node_count")),
        ),
        "summary": str(item.get("summary") or "").strip()[:summary_limit],
    }


def _compact_proposal_drift_memory(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": item.get("available"),
        "average_score": item.get("average_score"),
        "quality_counts": dict(item.get("quality_counts") or {}),
        "drift_state": item.get("drift_state"),
        "posture_alignment_signal_count": item.get("posture_alignment_signal_count"),
        "priority_basis_signal_count": item.get("priority_basis_signal_count"),
        "missing_posture_alignment_count": item.get("missing_posture_alignment_count"),
        "missing_priority_basis_count": item.get("missing_priority_basis_count"),
        "posture_alignment_health": item.get("posture_alignment_health"),
        "priority_basis_health": item.get("priority_basis_health"),
        "dominant_posture_conflict_reason": item.get("dominant_posture_conflict_reason"),
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


def _ensure_prompt_packet_budget(
    packet: Dict[str, Any],
    *,
    max_chars: int,
    prompt_attention_policy: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    text = json.dumps(packet, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return packet

    trimmed = dict(packet)
    policy = dict(prompt_attention_policy or {})
    for stage_name in list(policy.get("trim_stage_order") or []):
        trimmed = _apply_prompt_trim_stage(trimmed, stage_name=stage_name)
        if len(json.dumps(trimmed, ensure_ascii=False, default=str)) <= max_chars:
            return trimmed
    return trimmed


def _resolve_prompt_attention_policy(
    cognition_charter: Dict[str, Any],
) -> Dict[str, Any]:
    raw_policy = dict(cognition_charter.get("prompt_attention_policy") or {})
    return {
        "max_chars": max(1000, int(raw_policy.get("max_chars") or 11500)),
        "priority_order": [
            str(item).strip()
            for item in list(raw_policy.get("priority_order") or [])[:64]
            if str(item).strip()
        ]
        or [
            "identity",
            "decision_core",
            "supporting_detail",
            "long_tail_context",
            "queue_state_snapshot",
            "agenda_graph",
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
            "evidence_credibility_summary",
            "cognitive_assessment_memory",
            "proposal_drift_memory",
            "recent_reference_alignment",
            "evidence_channels",
            "recent_learning_evidence",
            "external_research_evidence",
            "research_digest",
            "shell_body_profile",
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
        ],
        "structure_keys": [
            str(item).strip()
            for item in list(raw_policy.get("structure_keys") or [])[:16]
            if str(item).strip()
        ]
        or ["decision_core", "supporting_detail", "long_tail_context", "queue_state_snapshot"],
        "trim_stage_order": [
            str(item).strip()
            for item in list(raw_policy.get("trim_stage_order") or [])[:16]
            if str(item).strip()
        ]
        or [
            "primary_context_compaction",
            "graph_compaction",
            "grounding_focus_compaction",
            "evidence_tail_compaction",
            "activity_tail_compaction",
        ],
    }


def _apply_prompt_trim_stage(packet: Dict[str, Any], *, stage_name: str) -> Dict[str, Any]:
    trimmed = dict(packet)
    stage = str(stage_name or "").strip().lower()
    if stage == "primary_context_compaction":
        decision_core = dict(trimmed.get("decision_core") or {})
        if decision_core:
            trimmed["decision_core"] = {
                "current_judgement": str(decision_core.get("current_judgement") or "")[:140],
                "dominant_constraint": str(decision_core.get("dominant_constraint") or "")[:140],
                "grounding_pressure": str(decision_core.get("grounding_pressure") or "")[:32],
                "governance_posture": str(
                    decision_core.get("governance_posture")
                    or ""
                )[:80],
                "secondary_task_shape_hint": str(
                    decision_core.get("secondary_task_shape_hint")
                    or ""
                )[:60],
                "secondary_task_shape_score": decision_core.get("secondary_task_shape_score"),
                "top_self_iteration_domain": str(decision_core.get("top_self_iteration_domain") or "")[:80],
                "primary_evidence_nodes": list(decision_core.get("primary_evidence_nodes") or [])[:3],
                "primary_agenda_nodes": list(decision_core.get("primary_agenda_nodes") or [])[:3],
                "queue_state_summary": str(decision_core.get("queue_state_summary") or "")[:180],
                "summary": str(decision_core.get("summary") or "")[:220],
            }
        supporting_detail = dict(trimmed.get("supporting_detail") or {})
        if supporting_detail:
            trimmed["supporting_detail"] = {
                "grounding_gaps": list(supporting_detail.get("grounding_gaps") or [])[:4],
                "contradictory_topics": list(supporting_detail.get("contradictory_topics") or [])[:2],
                "weak_or_missing_channels": list(supporting_detail.get("weak_or_missing_channels") or [])[:3],
                "self_understanding_gaps": list(supporting_detail.get("self_understanding_gaps") or [])[:3],
                "why_not_improvement_now": list(supporting_detail.get("why_not_improvement_now") or [])[:3],
                "trend_state": str(supporting_detail.get("trend_state") or "")[:40],
                "recent_effect_direction": str(supporting_detail.get("recent_effect_direction") or "")[:40],
                "summary": str(supporting_detail.get("summary") or "")[:220],
            }
        long_tail_context = dict(trimmed.get("long_tail_context") or {})
        if long_tail_context:
            trimmed["long_tail_context"] = {
                "recent_learning_titles": list(long_tail_context.get("recent_learning_titles") or [])[:4],
                "external_research_titles": list(long_tail_context.get("external_research_titles") or [])[:3],
                "evidence_channels": list(long_tail_context.get("evidence_channels") or [])[:3],
                "memory_context_preview": str(long_tail_context.get("memory_context_preview") or "")[:180],
                "summary": str(long_tail_context.get("summary") or "")[:220],
            }
        queue_state_snapshot = dict(trimmed.get("queue_state_snapshot") or {})
        if queue_state_snapshot:
            trimmed["queue_state_snapshot"] = {
                "queued_task_count": queue_state_snapshot.get("queued_task_count"),
                "queued_learning_count": queue_state_snapshot.get("queued_learning_count"),
                "queued_body_improvement_count": queue_state_snapshot.get("queued_body_improvement_count"),
                "recent_titles": list(queue_state_snapshot.get("recent_titles") or [])[:3],
                "recent_statuses": list(queue_state_snapshot.get("recent_statuses") or [])[:3],
                "summary": str(queue_state_snapshot.get("summary") or "")[:220],
                "guidance": str(queue_state_snapshot.get("guidance") or "")[:180],
            }
        meta_cognition_profile = dict(trimmed.get("meta_cognition_profile") or {})
        if meta_cognition_profile:
            trimmed["meta_cognition_profile"] = {
                "current_judgement": str(meta_cognition_profile.get("current_judgement") or "")[:140],
                "dominant_constraint": str(meta_cognition_profile.get("dominant_constraint") or "")[:140],
                "grounding_pressure": str(meta_cognition_profile.get("grounding_pressure") or "")[:32],
                "top_self_iteration_domain": str(meta_cognition_profile.get("top_self_iteration_domain") or "")[:80],
                "top_self_iteration_hypothesis": str(
                    meta_cognition_profile.get("top_self_iteration_hypothesis") or ""
                )[:160],
                "stay_or_switch_bias": str(meta_cognition_profile.get("stay_or_switch_bias") or "")[:32],
                "recent_effect_direction": str(meta_cognition_profile.get("recent_effect_direction") or "")[:32],
                "dominant_failure_mode": str(meta_cognition_profile.get("dominant_failure_mode") or "")[:120],
                "governance_posture": str(
                    meta_cognition_profile.get("governance_posture")
                    or meta_cognition_profile.get("recommended_task_posture")
                    or ""
                )[:80],
                "priority_signals": list(meta_cognition_profile.get("priority_signals") or [])[:4],
                "summary": str(meta_cognition_profile.get("summary") or "")[:220],
            }
        if "memory_context" in trimmed:
            memory_context = str(trimmed.get("memory_context") or "")
            trimmed["memory_context"] = memory_context[:240]
        return trimmed
    if stage == "graph_compaction":
        agenda_graph = dict(trimmed.get("agenda_graph") or {})
        if agenda_graph:
            agenda_graph["relation_edges"] = list(agenda_graph.get("relation_edges") or [])[:4]
            agenda_graph["evidence_to_gap_edges"] = list(agenda_graph.get("evidence_to_gap_edges") or [])[:4]
            agenda_graph["direction_task_links"] = list(agenda_graph.get("direction_task_links") or [])[:3]
            trimmed["agenda_graph"] = agenda_graph
        evidence_graph = dict(trimmed.get("evidence_graph") or {})
        if evidence_graph:
            evidence_graph["nodes"] = list(evidence_graph.get("nodes") or [])[:3]
            evidence_graph["support_edges"] = list(evidence_graph.get("support_edges") or [])[:2]
            evidence_graph["contradiction_edges"] = list(evidence_graph.get("contradiction_edges") or [])[:1]
            trimmed["evidence_graph"] = evidence_graph
        return trimmed
    if stage == "grounding_focus_compaction":
        if "grounding_focus" in trimmed:
            grounding_focus = dict(trimmed.get("grounding_focus") or {})
            grounding_focus["contradictory_topics"] = list(grounding_focus.get("contradictory_topics") or [])[:1]
            grounding_focus["grounding_gaps"] = list(grounding_focus.get("grounding_gaps") or [])[:3]
            grounding_focus["weak_or_missing_channels"] = list(grounding_focus.get("weak_or_missing_channels") or [])[:2]
            trimmed["grounding_focus"] = grounding_focus
        return trimmed
    if stage == "evidence_tail_compaction":
        if isinstance(trimmed.get("recent_learning_evidence"), list):
            trimmed["recent_learning_evidence"] = list(trimmed["recent_learning_evidence"])[:1]
        if isinstance(trimmed.get("external_research_evidence"), list):
            trimmed["external_research_evidence"] = list(trimmed["external_research_evidence"])[:2]
        if isinstance(trimmed.get("evidence_channels"), dict):
            channels = dict(trimmed["evidence_channels"])
            channels["channels"] = list(channels.get("channels") or [])[:3]
            trimmed["evidence_channels"] = channels
        return trimmed
    if stage == "activity_tail_compaction":
        if isinstance(trimmed.get("signals"), list):
            trimmed["signals"] = list(trimmed["signals"])[:3]
        if isinstance(trimmed.get("intents"), list):
            trimmed["intents"] = list(trimmed["intents"])[:3]
        if isinstance(trimmed.get("needs"), list):
            trimmed["needs"] = list(trimmed["needs"])[:3]
        if isinstance(trimmed.get("queued_tasks"), list):
            trimmed["queued_tasks"] = list(trimmed["queued_tasks"])[:2]
        return trimmed
    return trimmed
