"""Governed memory-promotion proposal boundary for autonomous tasks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Protocol

from .autonomous_chain_store import AutonomousChainTask
from memai.domain.scope import CLI_WORKSPACE_ID, DEFAULT_OWNER_ID


logger = logging.getLogger("supervisor")


class MemoryClientPort(Protocol):
    async def request_json(
        self,
        method: str,
        path: str,
        payload: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]: ...


MemoryClientFactory = Callable[..., MemoryClientPort]


class AutonomousTaskMemoryPromotionService:
    """Create governed promotion candidates without owning task policy."""

    def __init__(
        self,
        *,
        task_state: Any,
        memory_client_factory: MemoryClientFactory,
    ) -> None:
        self._task_state = task_state
        self._memory_client_factory = memory_client_factory

    async def propose(
        self,
        task: AutonomousChainTask,
    ) -> Optional[Dict[str, Any]]:
        metadata = dict(task.metadata or {})
        source = str(task.source or "").strip().lower()
        if source not in {"self_learning", "endogenous_drive"}:
            return None
        verified = bool(metadata.get("verified"))
        completed = str(task.status or "").strip().lower() == "completed"
        if not verified and not completed:
            return None

        existing_status = str(
            metadata.get("memory_promotion_candidate_status") or ""
        ).strip()
        if existing_status in {
            "recorded_only",
            "awaiting_user_consent",
            "already_governed",
        }:
            return {
                "status": existing_status,
                "candidate_id": metadata.get("memory_promotion_candidate_id"),
                "source_memory_id": metadata.get("evolution_memory_id"),
            }

        execution_request = task.execution_request
        decision_id = str(
            getattr(execution_request, "decision_id", None)
            or (task.decision_history[-1].decision_id if task.decision_history else "")
            or task.task_id
        )
        governance_ref = f"autonomous-chain:{task.task_id}:decision:{decision_id}"
        conclusion = ""
        employee_result = dict(metadata.get("employee_execution_result") or {})
        conclusion = str(
            employee_result.get("employee_final_response")
            or employee_result.get("result_summary")
            or employee_result.get("summary")
            or ""
        ).strip()
        for decision in reversed(task.decision_history):
            if conclusion:
                break
            context = dict(decision.context or {})
            conclusion = str(
                context.get("employee_final_response") or ""
            ).strip()
            if conclusion:
                break
        conclusion = (
            conclusion
            or str(task.evidence.get("summary") or task.summary or task.title)
        ).strip()[:4000]
        if not conclusion:
            return {"status": "deferred", "reason": "conclusion_is_empty"}

        try:
            topics = ["self_learning"]
            if source == "endogenous_drive":
                topics.append("endogenous_drive")
            memory_payload = await self._memory_client_factory(
                memory_actor="stellar_auto",
                memory_domain="evolution",
                owner_id=DEFAULT_OWNER_ID,
                workspace_id=CLI_WORKSPACE_ID,
                timeout_seconds=8,
            ).request_json(
                "POST",
                "/remember",
                {
                    "title": f"Auto 结论：{str(task.title)[:280]}",
                    "summary": conclusion,
                    "topics": [
                        *topics,
                        *(["companion_candidate"] if verified else []),
                    ],
                    "evidence_refs": [governance_ref],
                    "event_kind": "decision",
                    "importance": 0.85,
                    "source_actor": (
                        "stellar_auto_governed_conclusion"
                        if verified
                        else "stellar_auto_learning_conclusion"
                    ),
                    "memory_domain": "evolution",
                },
                idempotency_key=f"auto-memory:{task.task_id}:{decision_id}",
            )

            memory_record = (
                memory_payload.get("memory")
                if isinstance(memory_payload, dict)
                else None
            )
            source_memory_id = str(
                (memory_record or {}).get("memory_id") or ""
            ).strip()
            if not source_memory_id:
                return {
                    "status": "deferred",
                    "reason": "evolution_memory_id_missing",
                }

            if not verified:
                result = {
                    "status": "recorded_only",
                    "source_memory_id": source_memory_id,
                }
            else:
                candidate_payload = await self._memory_client_factory(
                    memory_actor="governor",
                    memory_domain="evolution",
                    owner_id=DEFAULT_OWNER_ID,
                    workspace_id=CLI_WORKSPACE_ID,
                    timeout_seconds=8,
                ).request_json(
                    "POST",
                    "/promotion-candidates",
                    {
                        "source_memory_id": source_memory_id,
                        "source_type": "compressed",
                        "source_domain": "evolution",
                        "target_domain": "companion",
                        "reason": (
                            "Governor 已确认该 Auto 结论，可由本机所有者决定是否供日常陪伴召回。"
                        ),
                        "governance_ref": governance_ref,
                    },
                    idempotency_key=f"auto-promotion:{task.task_id}:{decision_id}",
                )
                candidate = (
                    candidate_payload.get("candidate")
                    if isinstance(candidate_payload, dict)
                    else None
                )
                result = {
                    "status": candidate_payload.get(
                        "status",
                        "awaiting_user_consent",
                    ),
                    "candidate_id": (candidate or {}).get("candidate_id"),
                    "source_memory_id": source_memory_id,
                }
        except Exception as exc:
            logger.warning(
                "Verified conclusion promotion proposal failed for task %s: %s",
                task.task_id,
                exc,
            )
            return {
                "status": "deferred",
                "reason": "memory_promotion_service_unavailable",
            }

        self._task_state.update_metadata(
            task.task_id,
            metadata={
                "evolution_memory_id": result.get("source_memory_id"),
                "memory_promotion_candidate_id": result.get("candidate_id"),
                "memory_promotion_candidate_status": result["status"],
                "memory_promotion_governance_ref": governance_ref,
            },
        )
        return result


__all__ = ["AutonomousTaskMemoryPromotionService"]
