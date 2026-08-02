"""Compact API-B backlog snapshot projection for endogenous prompts."""

from __future__ import annotations

from typing import Any, Dict


def build_api_b_judgement_snapshot(drive_context: Dict[str, Any]) -> Dict[str, Any]:
    api_b_judgement_tasks = [
        dict(item)
        for item in list(drive_context.get("api_b_judgement_tasks") or [])[:8]
        if isinstance(item, dict)
    ]
    learning_backlog_titles = [
        str(item).strip()
        for item in list(drive_context.get("learning_backlog_titles") or [])[:5]
        if str(item).strip()
    ]
    body_improvement_backlog_titles = [
        str(item).strip()
        for item in list(drive_context.get("body_improvement_backlog_titles") or [])[:4]
        if str(item).strip()
    ]
    if (
        not api_b_judgement_tasks
        and not learning_backlog_titles
        and not body_improvement_backlog_titles
    ):
        return {}

    recent_titles = [
        str(item.get("title") or "").strip()
        for item in api_b_judgement_tasks[:4]
        if str(item.get("title") or "").strip()
    ]
    recent_statuses = [
        str(item.get("status") or "").strip()
        for item in api_b_judgement_tasks[:4]
        if str(item.get("status") or "").strip()
    ]
    return {
        "api_b_judgement_task_count": len(api_b_judgement_tasks),
        "learning_backlog_count": len(learning_backlog_titles),
        "body_improvement_backlog_count": len(body_improvement_backlog_titles),
        "recent_titles": recent_titles,
        "recent_statuses": recent_statuses,
        "summary": (
            f"API-B 判断在途 {len(api_b_judgement_tasks)} 项，"
            f"学习 {len(learning_backlog_titles)} 项，"
            f"替身改进 {len(body_improvement_backlog_titles)} 项；"
            f"最近：{', '.join(recent_titles[:3]) or '无'}。"
        ),
        "guidance": (
            "除非新证据明显更强，否则不要重复提出与现有 API-B 判断在途等价的工作。"
        ),
    }
