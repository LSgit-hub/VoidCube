"""Governed memory-promotion proposal boundary for autonomous tasks."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from .autonomous_chain_store import AutonomousChainTask


logger = logging.getLogger("supervisor")
GatewayMemoryHeaders = Callable[..., Dict[str, str]]


class AutonomousTaskMemoryPromotionService:
    """Create governed promotion candidates without owning task policy."""

    def __init__(
        self,
        *,
        task_state: Any,
        gateway_address: str,
        gateway_memory_headers: GatewayMemoryHeaders,
    ) -> None:
        self._task_state = task_state
        self._gateway_address = gateway_address.rstrip("/")
        self._gateway_memory_headers = gateway_memory_headers

    async def propose(
        self,
        task: AutonomousChainTask,
    ) -> Optional[Dict[str, Any]]:
        metadata = dict(task.metadata or {})
        if str(task.source or "").strip() != "self_learning":
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

        headers_auto = self._gateway_memory_headers(memory_actor="stellar_auto")
        headers_governor = (
            self._gateway_memory_headers(memory_actor="governor") if verified else {}
        )
        if not headers_auto or (verified and not headers_governor):
            return {"status": "deferred", "reason": "gateway_identity_unavailable"}

        execution_request = task.execution_request
        decision_id = str(
            getattr(execution_request, "decision_id", None)
            or (task.decision_history[-1].decision_id if task.decision_history else "")
            or task.task_id
        )
        governance_ref = f"autonomous-chain:{task.task_id}:decision:{decision_id}"
        conclusion = ""
        for decision in reversed(task.decision_history):
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
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._gateway_address}/api/mem/remember",
                    json={
                        "title": f"Auto 结论：{str(task.title)[:280]}",
                        "summary": conclusion,
                        "topics": [
                            "self_learning",
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
                    headers=headers_auto,
                ) as response:
                    memory_payload = await response.json()
                    if response.status != 200:
                        return {
                            "status": "deferred",
                            "reason": "evolution_memory_write_failed",
                            "http_status": response.status,
                        }

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
                    async with session.post(
                        f"{self._gateway_address}/api/mem/promotion-candidates",
                        json={
                            "source_memory_id": source_memory_id,
                            "source_type": "compressed",
                            "source_domain": "evolution",
                            "target_domain": "companion",
                            "reason": (
                                "Governor 已确认该 Auto 结论，可由本机所有者决定是否供日常陪伴召回。"
                            ),
                            "governance_ref": governance_ref,
                        },
                        headers=headers_governor,
                    ) as response:
                        candidate_payload = await response.json()
                        if response.status == 409:
                            result = {
                                "status": "already_governed",
                                "source_memory_id": source_memory_id,
                            }
                        elif response.status != 200:
                            return {
                                "status": "deferred",
                                "reason": "promotion_candidate_write_failed",
                                "http_status": response.status,
                            }
                        else:
                            candidate = (
                                candidate_payload.get("candidate")
                                if isinstance(candidate_payload, dict)
                                else None
                            )
                            result = {
                                "status": "awaiting_user_consent",
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
