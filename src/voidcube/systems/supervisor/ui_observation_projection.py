"""Pure chain-observation card projections for the Supervisor web UI."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .observation_status import (
    normalize_autonomous_status,
    observation_status_label,
)
from .ui_projection import runtime_activity_label


def is_employee_lane_family_task(task: Dict[str, Any]) -> bool:
    governance = str(task.get("governance_task_type") or "").strip().lower()
    execution_kind = str(task.get("execution_kind") or "").strip().lower()
    return governance == "self_learning" or execution_kind == "body_improvement"

def normalize_observation_status(status: Any) -> str:
    return normalize_autonomous_status(status)

def observation_status_value(task: Dict[str, Any]) -> str:
    return normalize_observation_status(task.get("status"))

def is_employee_execution_lane_task(task: Dict[str, Any]) -> bool:
    return is_employee_lane_family_task(task) and observation_status_value(task) in {
        "approved",
        "running",
        "retry",
    }

def chain_projection_phase_rank(task: Dict[str, Any]) -> int:
    status = observation_status_value(task)
    if status in {"active", "running"}:
        return 0
    if status in {"approved", "awaiting_user_consent", "retry"}:
        return 1
    if status == "candidate":
        return 2
    if status in {"planned", "awaiting_review"}:
        return 3
    if status in {"deferred", "paused"}:
        return 4
    if status in {"completed", "failed", "cancelled"}:
        return 5
    return 9

def chain_projection_order_key(task: Dict[str, Any]) -> tuple[int, str, str]:
    updated = str(task.get("updated_at") or task.get("created_at") or "")
    title = str(task.get("title") or task.get("task_id") or "")
    return (chain_projection_phase_rank(task), updated, title)

def memory_maintenance_handoff_status(task: Dict[str, Any]) -> str:
    metadata = dict(task.get("metadata") or {})
    identity = dict(task.get("task_identity") or {})
    task_types = {
        str(value or "").strip().lower()
        for value in (
            task.get("governance_task_type"),
            task.get("task_family"),
            task.get("execution_kind"),
            metadata.get("governance_task_type"),
            metadata.get("task_family"),
            metadata.get("execution_kind"),
            identity.get("governance_task_type"),
            identity.get("task_family"),
            identity.get("execution_kind"),
        )
    }
    if "memory_maintenance" not in task_types:
        return ""

    execution_result = dict(metadata.get("execution_result") or {})
    adapter_result = dict(execution_result.get("result") or {})
    maintenance_result = dict(
        adapter_result.get("memory_service_maintenance")
        or execution_result.get("memory_service_maintenance")
        or {}
    )
    for result in (maintenance_result, adapter_result, execution_result):
        status = str(result.get("status") or "").strip().lower()
        if status in {"accepted", "in_progress"}:
            return status
    return ""

def observation_display_status(task: Dict[str, Any]) -> str:
    memory_status = memory_maintenance_handoff_status(task)
    if memory_status == "accepted":
        return "已受理"
    if memory_status == "in_progress":
        return "维护中"
    return observation_status_label(task.get("status"))

def loop_stage_status_label(status: str) -> str:
    mapping = {
        "active": "当前在途",
        "ready": "已观察到",
        "stale": "执行器失联",
        "idle": "等待中",
    }
    return mapping.get(str(status or "").strip().lower(), "等待中")

def observation_role_tag(task: Dict[str, Any]) -> str:
    return "agent" if is_employee_lane_family_task(task) else "supervisor"

def observation_task_type_label(task: Dict[str, Any]) -> str:
    observation_role = str(task.get("observation_role") or "").strip()
    mapping = {
        "mem_writeback": "Mem 写回",
        "memory_maintenance_receipt": "Memory 受理回执",
        "api_b_reread": "再次判断",
        "api_b_judgement": "API-B 判断",
        "employee_execution": "员工执行回报",
        "candidate": "候选形成",
    }
    if observation_role in mapping:
        return mapping[observation_role]
    identity = dict(task.get("task_identity") or {})
    display_label = str(identity.get("display_label") or "").strip()
    if display_label:
        return display_label
    display_kind = str(
        identity.get("display_kind") or task.get("execution_kind") or ""
    ).strip()
    governance = str(task.get("governance_task_type") or "").strip()
    family = str(task.get("task_family") or "").strip()
    primary = display_kind or governance or family
    labels = {
        "self_learning": "自主学习",
        "body_improvement": "替身改进",
        "memory_maintenance": "记忆维护",
        "self_evolution": "自主改进",
        "general_self_evolution": "通用自主改进",
        "body_switch": "身体切换",
        "body_upgrade": "替身升级",
    }
    return labels.get(primary, primary.replace("_", " ") if primary else "链路项")

def observation_identity_hint(task: Dict[str, Any]) -> str:
    identity = dict(task.get("task_identity") or {})
    family = str(identity.get("task_family") or task.get("task_family") or "").strip()
    display_kind = str(
        identity.get("display_kind") or identity.get("execution_kind") or ""
    ).strip()
    if family and display_kind and family != display_kind:
        return (
            f"链路类型: {runtime_activity_label(family)}"
            f" · 执行动作: {runtime_activity_label(display_kind)}"
        )
    if display_kind:
        return f"执行动作: {runtime_activity_label(display_kind)}"
    if family:
        return f"链路类型: {runtime_activity_label(family)}"
    return ""

def observation_judgement_hint(task: Dict[str, Any]) -> str:
    preview = dict(task.get("judgement_preview") or {})
    summary = str(preview.get("summary") or "").strip()
    if summary:
        return summary[:120]
    direct = dict(preview.get("review_outcome") or {})
    shadow = dict(preview.get("followup_suggestion") or {})
    priority = dict(preview.get("priority_adjustment") or {})
    action_labels = {
        "approve": "转交",
        "defer": "延后",
        "cancel": "清退",
        "pause": "暂停",
        "retire": "退休建议",
        "merge": "合并建议",
        "reprioritize": "重排优先级",
        "reprioritise": "重排优先级",
    }

    def action_label(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return action_labels.get(normalized, str(value or "").strip())

    if direct.get("action"):
        return (
            f"监督者已裁定: {action_label(direct.get('action'))}"
            f" · {str(direct.get('reason') or '').strip()[:80]}"
        ).strip(" ·")
    if priority.get("priority"):
        return (
            f"监督者已重排优先级: "
            f"{str(priority.get('priority_label') or priority.get('priority') or '').strip()[:24]}"
            f" · {str(priority.get('reason') or '').strip()[:80]}"
        ).strip(" ·")
    if shadow.get("action"):
        extra = ""
        if shadow.get("merge_into_title"):
            extra = f" -> {str(shadow.get('merge_into_title') or '').strip()[:24]}"
        elif shadow.get("merge_into"):
            extra = f" -> {str(shadow.get('merge_into') or '').strip()[:16]}"
        elif shadow.get("priority"):
            extra = f" -> {str(shadow.get('priority') or '').strip()}"
        return (
            f"监督者建议: {action_label(shadow.get('action'))}{extra}"
            f" · {str(shadow.get('reason') or '').strip()[:80]}"
        ).strip(" ·")
    return ""

def observation_candidate_hint(task: Dict[str, Any]) -> str:
    metadata = dict(task.get("metadata") or {})
    evidence = dict(task.get("evidence") or {})
    endogenous = dict(evidence.get("endogenous_drive") or {})
    score_breakdown = dict(
        metadata.get("score_breakdown")
        or endogenous.get("score_breakdown")
        or {}
    )
    candidate_kind = str(score_breakdown.get("candidate_kind") or "").strip().lower()
    topic_source = str(
        endogenous.get("topic_source")
        or evidence.get("topic_source")
        or ""
    ).strip().lower()
    learning_branch = str(
        endogenous.get("learning_branch")
        or evidence.get("learning_branch")
        or metadata.get("learning_branch")
        or ""
    ).strip().lower()
    candidate_kind_label = {
        "memory_maintenance": "记忆维护",
        "truthfulness_review": "真实性复核",
        "exploratory_learning": "探索学习",
        "shell_baseline_learning": "替身基线学习",
        "governance_hygiene_review": "判断在途卫生观察",
        "body_improvement": "替身改进",
    }.get(candidate_kind, "")
    topic_source_label = {
        "activity_metadata": "活动信号",
        "cognitive_assessment_memory": "认知评估记忆",
        "shell_codebase_baseline": "替身代码基线",
        "external_research": "外部研究",
    }.get(topic_source, "")
    learning_branch_label = {
        "exploratory": "探索分支",
        "cognitive_assessment_review": "认知评估复核",
        "codebase_baseline": "代码基线",
    }.get(learning_branch, "")
    try:
        utility = float(
            metadata.get("utility")
            if metadata.get("utility") is not None
            else task.get("utility")
        )
    except Exception:
        utility = float("nan")
    hints: List[str] = []
    if candidate_kind_label:
        hints.append(f"候选类型: {candidate_kind_label}")
    if topic_source_label:
        hints.append(f"信号来源: {topic_source_label}")
    if learning_branch_label:
        hints.append(f"学习分支: {learning_branch_label}")
    if utility == utility:
        hints.append(f"价值度 {round(utility * 100)}%")
    return " · ".join(hints)

def observation_card_subtitle(task: Dict[str, Any]) -> str:
    observation_role = str(task.get("observation_role") or "").strip()
    summary = str(task.get("summary") or "").strip()[:100]
    if observation_role == "candidate":
        parts = ["内生驱动候选形成", observation_candidate_hint(task), summary]
        return " · ".join([part for part in parts if part]) or "交给 API-B 判断"
    parts = [
        observation_identity_hint(task),
        observation_judgement_hint(task),
        summary,
    ]
    return " · ".join([part for part in parts if part])[:160] or observation_task_type_label(task)

def observation_stage_subtitle(stage: Dict[str, Any]) -> str:
    parts = [
        str(stage.get("observation_stage_label") or stage.get("label") or "").strip(),
        (
            f"观测来源: {str(stage.get('source_label') or '').strip()}"
            if str(stage.get("source_label") or "").strip()
            else ""
        ),
        str(stage.get("read_rule") or "").strip()[:88],
        (
            f"下一跳: {str(stage.get('transition_hint') or '').strip()[:56]}"
            if str(stage.get("transition_hint") or "").strip()
            else ""
        ),
        str(stage.get("summary") or "").strip()[:100],
    ]
    return " · ".join([part for part in parts if part])[:200] or "自主闭环阶段观察"

def project_observation_stage_card(
    stage: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    row = dict(stage or {})
    focus_task = dict(row.get("focus_task") or {})
    raw_status = observation_status_value(
        {
            **focus_task,
            "status": row.get("status") or focus_task.get("status") or "idle",
        }
    ) or "idle"
    lane = str(row.get("lane") or focus_task.get("lane") or "").strip() or "supervisor"
    observation_role = (
        str(row.get("observation_role") or "").strip()
        or str(row.get("key") or "").strip()
        or "autonomous_observation"
    )
    observation_stage_label = (
        str(row.get("observation_stage_label") or row.get("label") or "").strip()
        or "自主闭环阶段"
    )
    display_payload = {
        **focus_task,
        "status": raw_status,
        "status_label": row.get("status_label"),
        "display_status": row.get("display_status"),
    }
    return {
        **focus_task,
        "title": str(focus_task.get("title") or row.get("label") or "阶段").strip() or "阶段",
        "status": raw_status,
        "status_label": str(row.get("status_label") or "").strip(),
        "display_status": observation_display_status(display_payload),
        "summary": str(
            focus_task.get("summary")
            or row.get("summary")
            or row.get("chain_reason")
            or row.get("activity_text")
            or ""
        ).strip(),
        "chain_reason": str(row.get("chain_reason") or "").strip(),
        "activity_text": str(row.get("activity_text") or "").strip(),
        "reason_style": str(row.get("reason_style") or "").strip(),
        "read_rule": str(row.get("read_rule") or "").strip(),
        "transition_hint": str(row.get("transition_hint") or "").strip(),
        "observation_role": observation_role,
        "observation_stage_label": observation_stage_label,
        "lane": lane,
        "stage_key": str(row.get("key") or "").strip(),
        "source_label": str(row.get("source_label") or "").strip() or "—",
        "card_subtitle": str(row.get("card_subtitle") or "").strip(),
        "focus_task": dict(focus_task) if focus_task else None,
    }

def project_observation_rail_entry(stage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = dict(stage or {})
    return {
        "key": str(row.get("key") or "").strip(),
        "label": str(row.get("label") or "阶段").strip() or "阶段",
        "source_label": str(row.get("source_label") or "—").strip() or "—",
        "status": str(row.get("status") or "idle").strip().lower() or "idle",
        "state": str(row.get("rail_state") or "").strip() or "等待中",
        "note": str(row.get("rail_note") or row.get("summary") or "").strip(),
        "focus": bool(row.get("is_focus")),
    }

def build_observation_card(
    payload: Optional[Dict[str, Any]],
    *,
    lane: str,
    display_status: Optional[str] = None,
    status: Optional[str] = None,
    summary_override: Optional[str] = None,
    observation_role: Optional[str] = None,
    title_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    card = dict(payload)
    card["lane"] = str(lane or card.get("lane") or "supervisor").strip() or "supervisor"
    if title_override is not None:
        card["title"] = str(title_override).strip() or card.get("title") or "未命名"
    else:
        card["title"] = str(card.get("title") or "未命名").strip() or "未命名"
    if summary_override is not None:
        card["summary"] = str(summary_override).strip()[:160]
    elif card.get("summary") is not None:
        card["summary"] = str(card.get("summary") or "").strip()[:160]
    metadata = dict(card.get("metadata") or {})
    card["metadata"] = metadata
    judgement_preview = dict(card.get("judgement_preview") or {})
    if judgement_preview:
        card["judgement_preview"] = judgement_preview
    if observation_role is not None:
        card["observation_role"] = observation_role
    if status is not None:
        card["status"] = normalize_observation_status(status)
    else:
        card["status"] = normalize_observation_status(card.get("status"))
    if display_status is not None:
        card["display_status"] = str(display_status).strip() or "待定"
    elif card.get("display_status") is None:
        card["display_status"] = observation_display_status(card)
    card["identity_hint"] = observation_identity_hint(card)
    card["judgement_hint"] = observation_judgement_hint(card)
    card["candidate_hint"] = observation_candidate_hint(card)
    card["observation_type_label"] = observation_task_type_label(card)
    card["observation_card_subtitle"] = observation_card_subtitle(card)
    return card

def build_observation_group(
    *,
    key: str,
    label: str,
    empty_text: str,
    items: List[Dict[str, Any]],
    emphasis: str = "neutral",
    source_label: str = "",
    stage_label: str = "",
    summary: str = "",
    order: int = 0,
    segment_kind: str = "",
    decor_cls: str = "",
    decor_icon: str = "",
    item_label: str = "",
    event_label: str = "",
    trace_label: str = "",
    footer_label: str = "",
    drill_label: str = "",
    read_rule: str = "",
    next_step: str = "",
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "empty_text": empty_text,
        "emphasis": emphasis,
        "source_label": str(source_label or "").strip() or "自主链路",
        "stage_label": stage_label,
        "summary": summary,
        "order": order,
        "segment_kind": segment_kind,
        "decor_class": str(decor_cls or "").strip() or "supervisor",
        "decor_icon": str(decor_icon or "").strip() or "🧠",
        "item_label": str(item_label or "").strip() or "链路项",
        "event_label": str(event_label or "").strip() or "动作",
        "trace_label": str(trace_label or "").strip() or "回合",
        "footer_label": str(footer_label or "").strip() or "查看最近状态",
        "drill_label": str(drill_label or "").strip() or "查看详情",
        "read_rule": str(read_rule or "").strip(),
        "next_step": str(next_step or "").strip(),
        "count": len(items),
        "items": list(items),
    }

