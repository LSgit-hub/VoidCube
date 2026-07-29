"""Pure cognitive state projections for the Supervisor web UI."""

from __future__ import annotations

from typing import Any, Dict, List

from systems.supervisor.ui_projection import observation_count

def cognition_label(kind: str, value: Any) -> str:
    normalized = str(value or "").strip().lower()
    maps: Dict[str, Dict[str, str]] = {
        "system_posture": {
            "balanced": "平衡观察",
            "truth_guarded": "真实性优先",
            "exploratory": "探索扩张",
            "continuity_guarded": "连续性守护",
        },
        "user_mode": {
            "quiet": "安静",
            "active": "活跃",
            "interrupted": "被打断",
            "unknown": "未识别",
            "unrecognized": "未识别",
            "未识别": "未识别",
        },
        "governance_load_state": {
            "calm": "平稳",
            "stable": "稳定",
            "busy": "繁忙",
            "strained": "紧张",
            "overloaded": "过载",
            "unknown": "未识别",
            "未识别": "未识别",
        },
        "need_type": {
            "review_api_b_judgement": "观察 API-B 判断在途",
            "truthfulness_repair": "修补真实性风险",
            "exploratory_learning": "发起自主学习",
            "shell_baseline_learning": "替身基线学习",
            "governance_hygiene_review": "判断在途卫生观察",
            "body_improvement": "推进替身改进",
            "memory_continuity": "维护记忆连续性",
            "memory_maintenance": "记忆维护",
            "observation_expansion": "扩展观察覆盖",
            "observe_before_acting": "先观察再行动",
            "未分类需求": "未分类需求",
        },
        "intent_type": {
            "review_governance_hygiene": "观察判断卫生",
            "expand_learning": "扩展学习",
            "protect_truthfulness": "保护真实性",
            "preserve_memory_continuity": "维持记忆连续性",
            "improve_body": "推动替身改进",
            "observe_only": "只观察",
            "未命名意图": "未命名意图",
        },
        "output_channel": {
            "task_candidates": "候选形成段",
            "governance_review": "API-B 判断观察",
            "observation_only": "只读观察",
            "memory_maintenance": "记忆维护",
            "body_improvement": "替身改进",
        },
        "target_horizon": {
            "immediate": "当前轮",
            "near_term": "短时段",
            "next_cycle": "下一轮",
            "medium_term": "中期",
            "current_round": "当前轮",
            "当前轮": "当前轮",
        },
        "preferred_focus": {
            "balanced": "平衡",
            "truthfulness": "真实性",
            "creativity": "创造学习",
            "learning_expansion": "学习扩张",
            "continuity": "连续性",
            "memory_continuity": "记忆连续性",
            "governance_hygiene": "判断卫生",
            "body_growth": "替身成长",
            "observation": "观察覆盖",
        },
        "constraint_type": {
            "user_service_priority": "用户链路优先",
            "historical_underdelivery": "历史兑现偏弱",
            "api_b_judgement_blockage": "API-B 判断阻塞",
            "weak_learning_yield": "学习收益偏弱",
            "weak self structure grounding": "替身结构地基偏弱",
            "weak_self_structure_grounding": "替身结构地基偏弱",
            "none": "暂无主约束",
        },
        "uncertainty_domain": {
            "truthfulness": "真实性侧",
            "api_b_judgement": "API-B 判断侧",
            "learning_yield": "学习收益侧",
            "autonomy_alignment": "自主对齐侧",
            "self_regulation": "自调节侧",
        },
        "observation_target": {
            "truthfulness": "真实性侧",
            "api_b_judgement_blockage": "API-B 判断阻塞侧",
            "learning_yield": "学习收益侧",
            "autonomy_alignment": "自主对齐侧",
            "self_regulation": "自调节侧",
            "grounding": "结构地基侧",
            "learning_frontier": "学习前沿侧",
            "memory_continuity": "记忆连续性侧",
            "body_growth": "替身成长侧",
            "api_b_judgement": "API-B 判断侧",
        },
        "observation_next_step": {
            "collect_observation": "补观察证据",
            "monitor": "继续观察",
        },
        "observation_persistence": {
            "persistent": "持续反复出现",
            "stalled": "长期未化解",
            "stabilizing": "正在稳定",
            "cooling": "开始降温",
            "emerging": "刚浮现",
        },
    }
    if normalized in maps.get(kind, {}):
        return maps[kind][normalized]
    text = str(value or "").strip()
    return text or "未命名"

def cognition_probe_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    mapping = {
        "review recent uncertain answers and correction signals": "复核近期不确定回答与修正信号",
        "inspect stale, deferred, and pending-review endogenous tasks": "检查陈旧、推迟和待复核的自主链路项",
        "compare recent learning quality against downstream task completion and review outcomes": "对照近期学习质量与后续完成/复核结果",
        "inspect whether current posture should remain guarded or corrective on the next endogenous cycle": "下一轮先确认当前姿态是否仍应保持谨慎或纠偏",
        "re-evaluate whether corrective boosts are still justified after the next endogenous cycle": "下一轮后重新评估纠偏增益是否还成立",
        "inspect which observation requests escalated into truthfulness alerts": "回查哪些观察请求升级成了真实性告警",
    }
    if normalized in mapping:
        return mapping[normalized]
    return str(value or "").strip()

def cognition_reason_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    mapping = {
        "delay direct body improvement while user_service_priority remains dominant.": "当前先让路给用户链路，暂不做直接替身改进",
        "delay direct body improvement while historical_underdelivery remains dominant.": "近期自主兑现偏弱，先补兑现再考虑直接替身改进",
        "prioritize truthfulness governance before direct body improvement.": "先处理真实性风险，再考虑直接替身改进",
        "在直接进行身体改进前，应优先处理 truthfulness 治理。": "先处理真实性风险，再考虑直接替身改进",
        "prioritize observation governance before direct body improvement.": "先补观察证据，再考虑直接替身改进",
        "prioritize governance_hygiene governance before direct body improvement.": "先观察 API-B 判断在途，再考虑直接替身改进",
        "prioritize memory_continuity governance before direct body improvement.": "先稳住记忆连续性，再考虑直接替身改进",
    }
    if normalized in mapping:
        return mapping[normalized]
    if normalized.startswith("recent outcome status ") and " requires review before broader self-improvement." in normalized:
        status = normalized.removeprefix("recent outcome status ").replace(
            " requires review before broader self-improvement.",
            "",
        ).strip()
        status_label = {
            "failed": "失败",
            "deferred": "推迟",
            "awaiting_review": "待复核",
            "awaiting_user_consent": "待用户同意",
        }.get(status, status or "未知")
        return f"近期结果为{status_label}，先复核再扩大自我改进"
    return str(value or "").strip()

def cognition_percentage(value: Any) -> str:
    try:
        return f"{round(max(0.0, min(1.0, float(value))) * 100)}%"
    except Exception:
        return "0%"

def project_cognition_judgement(
    cog_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    judgement_core = dict(cog_snapshot.get("judgement_core") or {})
    governance = dict(cog_snapshot.get("governance") or {})
    proposal_cognition = dict(cog_snapshot.get("proposal_cognition") or {})
    assessment_trace = dict(proposal_cognition.get("assessment_trace") or {})
    meta_profile = dict(proposal_cognition.get("meta_cognition_profile") or {})
    observation_program = dict(cog_snapshot.get("observation_program") or {})
    perception = dict(cog_snapshot.get("perception") or {})

    primary_need = dict(judgement_core.get("primary_need") or {})
    primary_intent = dict(judgement_core.get("primary_intent") or {})
    self_iteration_focus = dict(meta_profile.get("self_iteration_focus") or {})

    focus = str(
        governance.get("preferred_focus")
        or meta_profile.get("governance_posture")
        or ""
    ).strip()
    constraint = str(
        assessment_trace.get("dominant_constraint")
        or governance.get("dominant_constraint")
        or meta_profile.get("dominant_constraint")
        or ""
    ).strip()
    current_judgement = str(
        assessment_trace.get("current_judgement")
        or meta_profile.get("current_judgement")
        or ""
    ).strip()
    observation_target = str(
        observation_program.get("highest_priority_target")
        or assessment_trace.get("self_iteration_target")
        or self_iteration_focus.get("domain")
        or ""
    ).strip()
    hypothesis = str(
        assessment_trace.get("self_iteration_hypothesis")
        or self_iteration_focus.get("hypothesis")
        or ""
    ).strip()

    focus_label = cognition_label("preferred_focus", focus)
    constraint_label = (
        cognition_label("constraint_type", constraint)
        if constraint
        else "暂无主约束"
    )
    primary_need_label = cognition_label(
        "need_type", primary_need.get("need_type")
    ) if primary_need else ""
    primary_intent_label = cognition_label(
        "intent_type", primary_intent.get("intent_type")
    ) if primary_intent else ""
    observation_target_label = (
        cognition_label("observation_target", observation_target)
        if observation_target
        else ""
    )
    api_a_handoff_count = observation_count(
        perception.get("api_a_handoff_count")
    )
    api_a_running_count = observation_count(
        perception.get("api_a_running_count")
    )
    api_a_lane_summary = ""
    if api_a_running_count > 0:
        api_a_lane_summary = f"API-A 执行中 {api_a_running_count} 个链路项。"
    elif api_a_handoff_count > 0:
        api_a_lane_summary = f"API-B 已转交 {api_a_handoff_count} 个链路项，等待 API-A 接手。"

    reasons: List[str] = []
    explicit_reason = cognition_reason_label(
        assessment_trace.get("why_not_improvement_now")
    )
    if explicit_reason:
        reasons.append(explicit_reason)
    if constraint in {"user_service_priority", "historical_underdelivery"}:
        derived = (
            "当前先让路给用户链路"
            if constraint == "user_service_priority"
            else "近期自主兑现偏弱，先补兑现"
        )
        if derived not in reasons:
            reasons.append(derived)
    if constraint == "api_b_judgement_blockage":
        reasons.append("API-B 判断在途仍未消化完")
    if constraint == "weak_learning_yield":
        reasons.append("近期学习收益偏弱，先补证据")
    if focus in {
        "truthfulness",
        "observation",
        "governance_hygiene",
        "memory_continuity",
    }:
        focus_reason = {
            "truthfulness": "当前优先处理真实性风险",
            "observation": "当前优先补观察覆盖",
            "governance_hygiene": "当前优先处理判断卫生",
            "memory_continuity": "当前优先稳住记忆连续性",
        }[focus]
        if focus_reason not in reasons:
            reasons.append(focus_reason)
    if api_a_lane_summary and api_a_lane_summary not in reasons:
        reasons.append(api_a_lane_summary)

    summary_parts = []
    if focus_label and focus_label != "未命名":
        summary_parts.append(f"当前焦点在{focus_label}")
    if primary_need_label and primary_need_label != "未命名":
        summary_parts.append(f"先响应{primary_need_label}")
    if constraint_label:
        summary_parts.append(f"主要约束是{constraint_label}")
    if api_a_running_count > 0:
        summary_parts.append(f"API-A 执行中 {api_a_running_count} 个链路项")
    elif api_a_handoff_count > 0:
        summary_parts.append(f"API-B 已转交 {api_a_handoff_count} 个链路项")
    summary = "，".join(summary_parts) or "当前认知判断尚未稳定。"

    return {
        "summary": summary,
        "current_judgement": current_judgement,
        "focus": focus or None,
        "focus_label": focus_label,
        "dominant_constraint": constraint or None,
        "dominant_constraint_label": constraint_label,
        "primary_need": primary_need.get("need_type") if primary_need else None,
        "primary_need_label": primary_need_label or None,
        "primary_intent": primary_intent.get("intent_type") if primary_intent else None,
        "primary_intent_label": primary_intent_label or None,
        "observation_target": observation_target or None,
        "observation_target_label": observation_target_label or None,
        "self_iteration_hypothesis": hypothesis or None,
        "api_a_handoff_count": api_a_handoff_count,
        "api_a_running_count": api_a_running_count,
        "api_a_lane_summary": api_a_lane_summary or None,
        "why_not_direct_improvement": reasons[:4],
    }

def project_cognition_uncertainty(
    cog_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    ledger = dict(cog_snapshot.get("uncertainty_ledger") or {})
    observation_program = dict(cog_snapshot.get("observation_program") or {})
    program_entries = [
        dict(item)
        for item in list(observation_program.get("entries") or [])
        if isinstance(item, dict)
    ]
    program_by_target = {
        str(item.get("target") or "").strip().lower(): item
        for item in program_entries
        if str(item.get("target") or "").strip()
    }

    top_items: List[Dict[str, Any]] = []
    for entry in list(ledger.get("entries") or [])[:3]:
        if not isinstance(entry, dict):
            continue
        domain = str(entry.get("domain") or "").strip().lower()
        target = str(
            entry.get("observation_target")
            or domain
            or ""
        ).strip().lower()
        program = dict(program_by_target.get(target) or {})
        risk = float(entry.get("risk") or 0.0)
        confidence = float(entry.get("confidence") or 0.0)
        domain_label = cognition_label("uncertainty_domain", domain)
        target_label = cognition_label("observation_target", target)
        probe = str(
            entry.get("recommended_probe")
            or program.get("recommended_probe")
            or ""
        ).strip()
        probe_label = cognition_probe_label(probe)
        persistence_state = str(program.get("persistence_state") or "").strip().lower()
        next_step = str(program.get("recommended_next_step") or "").strip().lower()
        top_items.append(
            {
                "domain": domain or None,
                "domain_label": domain_label,
                "risk": round(risk, 4),
                "risk_label": cognition_percentage(risk),
                "confidence": round(confidence, 4),
                "confidence_label": cognition_percentage(confidence),
                "summary": (
                    f"{domain_label}风险较高，建议先{probe_label}。"
                    if probe_label
                    else f"{domain_label}风险较高，建议继续观察。"
                ),
                "why_uncertain": str(entry.get("why_uncertain") or "").strip() or None,
                "observation_target": target or None,
                "observation_target_label": target_label,
                "recommended_probe": probe or None,
                "recommended_probe_label": probe_label or None,
                "recommended_next_step": next_step or None,
                "recommended_next_step_label": (
                    cognition_label("observation_next_step", next_step)
                    if next_step
                    else None
                ),
                "persistence_state": persistence_state or None,
                "persistence_label": (
                    cognition_label("observation_persistence", persistence_state)
                    if persistence_state
                    else None
                ),
            }
        )

    highest_risk_domain = str(ledger.get("highest_risk_domain") or "").strip().lower()
    highest_risk_label = (
        cognition_label("uncertainty_domain", highest_risk_domain)
        if highest_risk_domain
        else "暂无显著不确定性"
    )
    summary = (
        f"当前最需要补证据的是{highest_risk_label}。"
        if top_items
        else "当前没有明显风险点"
    )

    return {
        "summary": summary,
        "active_count": max(0, int(ledger.get("active_count") or 0)),
        "highest_risk_domain": highest_risk_domain or None,
        "highest_risk_label": highest_risk_label,
        "top_items": top_items,
    }

