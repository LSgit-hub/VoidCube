"""Supervisor governance-adviser boundary for autonomous-chain review."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from systems.supervisor.autonomous_chain_store import (
    AutonomousChainStore,
    AutonomousChainTask,
)
from systems.supervisor.schedule_allocator import ScheduleAllocator
from systems.supervisor.task_profile_policy import TaskProfilePolicy


class AutonomousTaskGovernanceReviewService:
    """Ask the governance model for bounded review suggestions."""

    _ACTION_TO_STATUS = {
        "approve": "approved",
        "approved": "approved",
        "defer": "deferred",
        "deferred": "deferred",
        "cancel": "cancelled",
        "cancelled": "cancelled",
        "pause": "paused",
        "paused": "paused",
    }
    _SHADOW_ACTIONS = frozenset({"retire", "merge"})

    def __init__(
        self,
        *,
        store: AutonomousChainStore,
        task_profile_policy: TaskProfilePolicy,
        schedule_allocator: ScheduleAllocator,
    ) -> None:
        self._store = store
        self._task_profile_policy = task_profile_policy
        self._schedule_allocator = schedule_allocator

    async def review(
        self,
        tasks: list[AutonomousChainTask],
        *,
        drive_input: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        if not tasks:
            return {}

        try:
            from memai.model_config import resolve_mem_llm_client

            llm_client, _ = resolve_mem_llm_client(role="governance_reasoner")
            if llm_client is None:
                return {}
        except Exception:
            return {}

        review_snapshot = self._build_review_snapshot(tasks)
        prompt = (
            "你是 VoidCube 的 API-B 判断层。你的职责不是产出新任务，"
            "而是观察并裁定当前 API-B 判断在途链路项。\n\n"
            "请基于当前 drive_input、API-B 判断在途快照和用户优先级，"
            "为每个链路项给出一个结构化判断动作建议。你可以使用以下动作：\n"
            "- approve: 建议当前任务本轮由 API-B 转交给 API-A 接手\n"
            "- defer: 建议当前任务继续等待\n"
            "- cancel: 建议当前任务清退/取消\n"
            "- pause: 建议当前任务暂停\n"
            "- retire: 建议该任务退休，但先仅记录建议，不直接落状态\n"
            "- merge: 建议该任务与另一任务合并，但先仅记录建议，不直接合并\n"
            "- reprioritize: 建议调整优先级，但先仅记录建议，不直接改优先级\n\n"
            "注意：\n"
            "1. 不要新增任务\n"
            "2. 不要改写 task_id\n"
            "3. 不要为同一任务返回多个动作\n"
            "4. 优先考虑避免重复、无证据、陈旧或与当前系统状态冲突的任务\n"
            "5. body_improvement 只有在学习证据足够时才建议 approve；这里的 approve 只表示转交 API-A 接手，不表示 Web 小屋可控制执行\n\n"
            "6. 同一个 scheduled_for / preset_time 只能保留一个活跃任务；"
            "如果时间重叠，按先后顺序只保留一个，不能与现有自主链计划时段重复，其余建议 defer 或 cancel；"
            "该保留/顺延建议由监督者 LM 判断\n\n"
            "输出 JSON 对象，格式为：\n"
            "{\n"
            '  "actions": [\n'
            '    {"task_id": "...", "action": "approve|defer|cancel|pause|retire|merge|reprioritize", "reason": "...", "merge_into": "...", "priority": "..."}\n'
            "  ]\n"
            "}\n\n"
            f"【drive_input】\n{json.dumps(dict(drive_input or {}), ensure_ascii=False, default=str)[:3000]}\n\n"
            f"【api_b_judgement】\n{json.dumps(review_snapshot, ensure_ascii=False, default=str)[:5000]}"
        )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    llm_client.complete_json,
                    system_prompt=(
                        "你是 VoidCube 的监督者身份。你观察并裁定 API-B 判断在途链路项的生命周期，"
                        "但不能绕过确定性状态机。你的回答必须保守、结构化、可审计。"
                    ),
                    user_payload={"governance_review": prompt},
                    task="scholar.revision",
                ),
                timeout=8.0,
            )
        except Exception:
            return {}

        if not isinstance(result, dict) or not isinstance(result.get("actions"), list):
            return {}

        reviewed: Dict[str, Dict[str, Any]] = {}
        for item in result["actions"]:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            reviewed[task_id] = {
                "action": item.get("action"),
                "reason": str(item.get("reason") or "").strip()[:500],
            }
            followup_suggestion = self._extract_followup_suggestion(item)
            if followup_suggestion is not None:
                reviewed[task_id]["followup_suggestion"] = followup_suggestion
            priority = self._extract_priority_recommendation(item)
            if priority is not None:
                reviewed[task_id]["priority"] = priority
        return reviewed

    def _build_review_snapshot(
        self,
        tasks: list[AutonomousChainTask],
    ) -> list[Dict[str, Any]]:
        snapshot: list[Dict[str, Any]] = []
        for task in tasks:
            metadata = dict(task.metadata or {})
            evidence = dict(task.evidence or {})
            constraints = dict(task.constraints or {})
            snapshot.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "summary": task.summary,
                    "status": task.status,
                    "priority": task.priority,
                    "source": task.source,
                    "governance_task_type": self._task_profile_policy.governance_type(task),
                    "task_family": self._task_profile_policy.runtime_family(task),
                    "execution_kind": self._task_profile_policy.execution_kind(task),
                    "scheduled_for": self._schedule_allocator.task_schedule_token(task),
                    "metadata": {
                        "endogenous_drive_key": metadata.get("endogenous_drive_key"),
                        "utility": metadata.get("utility"),
                        "quality_score": metadata.get("quality_score"),
                        "learning_branch": metadata.get("learning_branch"),
                        "self_learning_mode": metadata.get("self_learning_mode"),
                    },
                    "evidence": {
                        "recent_errors": evidence.get("recent_errors"),
                        "uncertainty_high_count": evidence.get("uncertainty_high_count"),
                        "learning_quality_score": evidence.get("learning_quality_score"),
                        "topic_source": (
                            (evidence.get("endogenous_drive") or {}).get("topic_source")
                            or evidence.get("topic_source")
                        ),
                        "learning_branch": (
                            (evidence.get("endogenous_drive") or {}).get("learning_branch")
                            or evidence.get("learning_branch")
                        ),
                    },
                    "constraints": {
                        "execution_policy": constraints.get("execution_policy"),
                        "target_slot": constraints.get("target_slot"),
                        "must_not_create_new_commit": constraints.get(
                            "must_not_create_new_commit"
                        ),
                        "must_match_evaluated_commit": constraints.get(
                            "must_match_evaluated_commit"
                        ),
                    },
                    "decision_history_count": len(task.decision_history or []),
                }
            )
        return snapshot

    def _extract_followup_suggestion(
        self,
        item: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        action = str(item.get("action") or "").strip().lower()
        if action not in self._SHADOW_ACTIONS:
            return None
        recommendation: Dict[str, Any] = {
            "action": action,
            "reason": str(item.get("reason") or "").strip()[:500],
        }
        if action == "merge":
            recommendation["merge_into"] = str(item.get("merge_into") or "").strip()[:200]
        return recommendation

    @staticmethod
    def _extract_priority_recommendation(item: Dict[str, Any]) -> str | None:
        action = str(item.get("action") or "").strip().lower()
        if action not in {"reprioritize", "reprioritise"}:
            return None
        priority = str(item.get("priority") or "").strip().lower()
        if priority not in {"low", "normal", "high"}:
            return None
        return priority


__all__ = ["AutonomousTaskGovernanceReviewService"]
