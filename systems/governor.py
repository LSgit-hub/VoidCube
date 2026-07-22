from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, Field

from systems.body_registry import BodySlotMeta
from systems.runtime_task_profile import derive_runtime_task_profile

GovernorMode = Literal["governor"]
GovernorEventType = Literal[
    "body_upgrade_request",
    "health_review_request",
    "switch_request",
    "rollback_request",
    "improvement_rollback_request",
    "post_switch_review",
    "switch_suggestion",
    "switch_consent_rejection",
]
GovernorDecisionType = Literal[
    "approve",
    "reject",
    "approve_with_watch",
    "awaiting_user_consent",
    "rollback_required",
    "request_more_evidence",
]
GovernorRiskLevel = Literal["low", "medium", "high", "critical"]
GovernorActionType = Literal[
    "issue_probe_lease",
    "await_user_consent",
    "activate_slot",
    "restore_retired_slot",
    "restore_healthy_commit",
    "recycle_retired_slot",
    "abandon_candidate",
    "record_evolution_event",
]


class GovernorAction(BaseModel):
    action_type: GovernorActionType
    slot_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = None


class GovernorWritebackEvent(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class GovernorRequest(BaseModel):
    request_id: str
    trace_id: Optional[str] = None
    task_type: str = "self_evolution"
    decision_id: Optional[str] = None
    mode: GovernorMode = "governor"
    event_type: GovernorEventType
    body_id: str
    source_actor: str
    summary: str
    evidence: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class GovernorResponse(BaseModel):
    decision: GovernorDecisionType
    confidence: float = 0.5
    risk_level: GovernorRiskLevel = "medium"
    reasoning_summary: str
    required_actions: list[GovernorAction] = Field(default_factory=list)
    watch_window_hint: Optional[int] = None
    writeback_events: list[GovernorWritebackEvent] = Field(default_factory=list)


class GovernorDecisionEngine:
    """Deterministic evaluator for supervisor governance requests + optional LLM advisor.

    This engine is intentionally conservative and protocol-safe.  It provides the
    primary authority for switch, rollback, and execution-governance decisions.

    For ambiguous cases (low confidence, request_more_evidence, medium+ risk),
    the optional ``LLMGovernorReasoner`` can provide additional analysis without
    overriding the deterministic decision.
    """

    def __init__(self, llm_reasoner: "LLMGovernorReasoner | None" = None) -> None:
        self._llm_reasoner = llm_reasoner

    def evaluate(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta] = None,
    ) -> GovernorResponse:
        response: GovernorResponse
        if request.event_type == "body_upgrade_request":
            response = self._evaluate_body_upgrade(request)
        elif request.event_type == "health_review_request":
            response = self._evaluate_health_review(request, slot_meta=slot_meta)
        elif request.event_type == "switch_request":
            response = self._evaluate_switch_request(request, slot_meta=slot_meta)
        elif request.event_type == "rollback_request":
            response = self._evaluate_rollback_request(request)
        elif request.event_type == "improvement_rollback_request":
            response = self._evaluate_improvement_rollback_request(
                request,
                slot_meta=slot_meta,
            )
        elif request.event_type == "post_switch_review":
            response = self._evaluate_post_switch_review(request, slot_meta=slot_meta)
        elif request.event_type == "switch_suggestion":
            response = self._evaluate_switch_suggestion(request, slot_meta=slot_meta)
        elif request.event_type == "switch_consent_rejection":
            response = self._evaluate_switch_consent_rejection(
                request,
                slot_meta=slot_meta,
            )
        else:
            response = GovernorResponse(
                decision="request_more_evidence",
                confidence=0.2,
                risk_level="medium",
                reasoning_summary=f"Unsupported governor event type: {request.event_type}",
            )

        # Consult LLM reasoner for ambiguous decisions (advisory only)
        if (
            self._llm_reasoner is not None
            and self._llm_reasoner.available
            and (
                response.decision == "request_more_evidence"
                or response.confidence < 0.6
                or response.risk_level in ("medium", "high", "critical")
            )
        ):
            llm_analysis = self._llm_reasoner.analyze(
                request, response, slot_meta=slot_meta
            )
            if llm_analysis.get("llm_available"):
                response.writeback_events.append(
                    GovernorWritebackEvent(
                        event_type="llm_governance_analysis",
                        payload={"analysis": llm_analysis},
                    )
                )

        return response

    def _evaluate_body_upgrade(self, request: GovernorRequest) -> GovernorResponse:
        return GovernorResponse(
            decision="request_more_evidence",
            confidence=0.4,
            risk_level="low",
            reasoning_summary=(
                "Upgrade proposal recorded. More build evidence is needed before "
                "the soul layer can approve a probe or switch decision."
            ),
            required_actions=[
                GovernorAction(
                    action_type="record_evolution_event",
                    slot_id=request.body_id,
                    notes="Record the proposed body upgrade in the soul layer.",
                )
            ],
            writeback_events=[
                GovernorWritebackEvent(
                    event_type="evolution_event",
                    payload=self._with_runtime_task_profile(
                        request,
                        {
                            "body_id": request.body_id,
                            "summary": request.summary,
                            "source_actor": request.source_actor,
                        },
                    ),
                )
            ],
        )

    def _evaluate_health_review(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta],
    ) -> GovernorResponse:
        target_transition = str(request.constraints.get("target_transition", "")).strip().lower()

        if target_transition == "candidate_to_probe":
            build_ready = bool(
                request.evidence.get("build_ready")
                or request.evidence.get("build_complete")
            )
            if slot_meta and slot_meta.body_state != "candidate":
                return self._reject(
                    f"Slot {slot_meta.slot_id} must be in candidate state before probe approval."
                )
            if not build_ready:
                return self._request_more_evidence(
                    "Candidate body does not yet provide enough build evidence for probe approval."
                )
            return GovernorResponse(
                decision="approve",
                confidence=0.82,
                risk_level="low",
                reasoning_summary=(
                    "Candidate body appears build-complete and may receive a probe lease "
                    "for controlled health verification."
                ),
                required_actions=[
                    GovernorAction(
                        action_type="issue_probe_lease",
                        slot_id=request.body_id,
                        payload={
                            "lease": "probe",
                            "runtime_task_profile": self._runtime_task_profile(request),
                        },
                    )
                ],
                writeback_events=[
                    GovernorWritebackEvent(
                        event_type="governor_decision",
                        payload=self._with_runtime_task_profile(
                            request,
                            {
                                "decision": "approve",
                                "target_transition": target_transition,
                                "body_id": request.body_id,
                            },
                        ),
                    )
                ],
            )

        if target_transition == "probe_to_shell":
            report = request.evidence.get("probe_report")
            probe_failed = request.evidence.get("probe_passed") is False or (
                isinstance(report, dict) and report.get("overall_passed") is False
            )
            if slot_meta and slot_meta.body_state != "probe":
                return self._reject(
                    f"Slot {slot_meta.slot_id} must be in probe state before abandonment."
                )
            if not probe_failed:
                return self._request_more_evidence(
                    "Candidate abandonment requires an explicit failed probe report."
                )
            return GovernorResponse(
                decision="approve",
                confidence=0.94,
                risk_level="low",
                reasoning_summary=(
                    "The candidate failed controlled probe verification and must be "
                    "discarded before the shell slot can accept more work."
                ),
                required_actions=[
                    GovernorAction(
                        action_type="abandon_candidate",
                        slot_id=request.body_id,
                        payload={
                            "reason": "required_probe_failed",
                            "runtime_task_profile": self._runtime_task_profile(request),
                        },
                    )
                ],
                writeback_events=[
                    GovernorWritebackEvent(
                        event_type="governor_decision",
                        payload=self._with_runtime_task_profile(
                            request,
                            {
                                "decision": "approve",
                                "target_transition": target_transition,
                                "body_id": request.body_id,
                                "reason": "required_probe_failed",
                            },
                        ),
                    )
                ],
            )

        if target_transition == "probe_to_active":
            probe_passed = self._probe_passed(request.evidence)
            user_consent_approved = bool(request.evidence.get("user_consent_approved"))
            if slot_meta and slot_meta.body_state != "awaiting_user_consent":
                return self._reject(
                    f"Slot {slot_meta.slot_id} must be awaiting user consent before activation approval."
                )
            if not probe_passed:
                return self._reject("Probe report indicates the candidate is not yet safe to activate.")
            if not user_consent_approved:
                return self._request_more_evidence(
                    "Activation requires explicit user consent after probe and governor review."
                )
            watch_window_hint = int(request.constraints.get("watch_window_seconds", 300))
            return GovernorResponse(
                decision="approve_with_watch",
                confidence=0.88,
                risk_level="medium",
                reasoning_summary=(
                    "Probe checks passed. The body may be promoted to active, but a "
                    "watch window is still required before the previous body is recycled."
                ),
                required_actions=[
                    GovernorAction(
                        action_type="activate_slot",
                        slot_id=request.body_id,
                        payload={
                            "watch_window_seconds": watch_window_hint,
                            "reason": "user_consented_switch",
                            "runtime_task_profile": self._runtime_task_profile(request),
                        },
                    )
                ],
                watch_window_hint=watch_window_hint,
                writeback_events=[
                    GovernorWritebackEvent(
                        event_type="governor_decision",
                        payload=self._with_runtime_task_profile(
                            request,
                            {
                                "decision": "approve_with_watch",
                                "target_transition": target_transition,
                                "body_id": request.body_id,
                            },
                        ),
                    )
                ],
            )

        return self._request_more_evidence(
            "Health review requests must specify a supported target_transition."
        )

    def _evaluate_switch_request(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta],
    ) -> GovernorResponse:
        probe_passed = self._probe_passed(request.evidence)
        if slot_meta and slot_meta.body_state != "probe":
            return self._reject(
                f"Slot {slot_meta.slot_id} must be in probe state before switch approval."
            )
        if not probe_passed:
            return self._reject("Switch request denied because the probe evidence is incomplete or failed.")

        watch_window_hint = int(request.constraints.get("watch_window_seconds", 300))
        return GovernorResponse(
            decision="awaiting_user_consent",
            confidence=0.9,
            risk_level="medium",
            reasoning_summary=(
                "The candidate passed probe validation and the governor allows a switch "
                "recommendation, but activation must wait for explicit user consent."
            ),
            required_actions=[
                    GovernorAction(
                        action_type="await_user_consent",
                        slot_id=request.body_id,
                        payload={
                        "watch_window_seconds": watch_window_hint,
                        "requires_user_consent": True,
                        "reason": "governor_approved_pending_user_consent",
                            "runtime_task_profile": self._runtime_task_profile(request),
                            "autonomous_task_link": dict(
                                request.evidence.get("autonomous_task_link") or {}
                            ),
                    },
                )
            ],
            watch_window_hint=watch_window_hint,
            writeback_events=[
                GovernorWritebackEvent(
                    event_type="switch_result",
                    payload=self._with_runtime_task_profile(
                        request,
                        {
                            "body_id": request.body_id,
                            "decision": "approved_pending_user_consent",
                            "requires_user_consent": True,
                        },
                    ),
                )
            ],
        )

    def _evaluate_switch_consent_rejection(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta],
    ) -> GovernorResponse:
        if slot_meta and slot_meta.body_state != "awaiting_user_consent":
            return self._reject(
                f"Slot {slot_meta.slot_id} must be awaiting user consent before rejection."
            )
        return GovernorResponse(
            decision="reject",
            confidence=1.0,
            risk_level="low",
            reasoning_summary="The user explicitly declined the body activation.",
            required_actions=[
                GovernorAction(
                    action_type="abandon_candidate",
                    slot_id=request.body_id,
                    payload={
                        "reason": "user_rejected_switch",
                        "runtime_task_profile": self._runtime_task_profile(request),
                    },
                )
            ],
            writeback_events=[
                GovernorWritebackEvent(
                    event_type="switch_result",
                    payload=self._with_runtime_task_profile(
                        request,
                        {
                            "body_id": request.body_id,
                            "decision": "user_rejected",
                            "requires_user_consent": True,
                        },
                    ),
                )
            ],
        )

    def _evaluate_rollback_request(self, request: GovernorRequest) -> GovernorResponse:
        rollback_signal = bool(
            request.evidence.get("rollback_signal")
            or request.evidence.get("active_body_healthy") is False
            or request.evidence.get("watch_window_failed")
        )
        if not rollback_signal:
            return self._request_more_evidence(
                "Rollback request requires a concrete failure signal or failed health evidence."
            )
        retired_slot = request.constraints.get("retired_slot")
        return GovernorResponse(
            decision="rollback_required",
            confidence=0.93,
            risk_level="high",
            reasoning_summary=(
                "Observed failure during or after activation exceeds the safe threshold. "
                "The retired body should be restored as the active body."
            ),
            required_actions=[
                GovernorAction(
                    action_type="restore_retired_slot",
                    slot_id=retired_slot,
                    payload={
                        "failed_body_id": request.body_id,
                        "runtime_task_profile": self._runtime_task_profile(request),
                    },
                )
            ],
            writeback_events=[
                GovernorWritebackEvent(
                    event_type="rollback_result",
                    payload=self._with_runtime_task_profile(
                        request,
                        {
                            "failed_body_id": request.body_id,
                            "retired_slot": retired_slot,
                            "decision": "rollback_required",
                        },
                    ),
                )
            ],
        )

    def _evaluate_improvement_rollback_request(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta],
    ) -> GovernorResponse:
        failure_signal = bool(
            request.evidence.get("destructive_change_detected")
            or request.evidence.get("probe_failed")
            or request.evidence.get("regression_detected")
        )
        if not failure_signal:
            return self._request_more_evidence(
                "Body improvement rollback requires a concrete regression, destructive-change, "
                "or failed-probe signal."
            )
        if slot_meta is None:
            return self._request_more_evidence(
                "Body improvement rollback requires current slot metadata."
            )
        if slot_meta.body_state in {"active", "retired"}:
            return self._reject(
                "Active and retired bodies must use the switch watch-window rollback protocol."
            )

        previous_healthy_commit = str(
            request.constraints.get("previous_healthy_commit")
            or slot_meta.previous_healthy_commit
            or ""
        ).strip()
        current_commit = str(
            request.evidence.get("current_commit")
            or slot_meta.current_healthy_commit
            or slot_meta.candidate_commit
            or ""
        ).strip()
        if not previous_healthy_commit or not current_commit:
            return self._request_more_evidence(
                "Body improvement rollback requires both current and previous healthy commits."
            )
        if previous_healthy_commit == current_commit:
            return self._reject(
                "The previous healthy commit must differ from the current body commit."
            )

        return GovernorResponse(
            decision="rollback_required",
            confidence=0.95,
            risk_level="high",
            reasoning_summary=(
                "A concrete body-improvement failure signal is present. Restore the recorded "
                "healthy ancestor in the isolated slot and require a fresh probe before reuse."
            ),
            required_actions=[
                GovernorAction(
                    action_type="restore_healthy_commit",
                    slot_id=request.body_id,
                    payload={
                        "expected_current_commit": current_commit,
                        "previous_healthy_commit": previous_healthy_commit,
                        "request_id": request.request_id,
                        "reason": str(
                            request.evidence.get("failure_reason")
                            or "destructive_body_improvement"
                        ),
                        "runtime_task_profile": self._runtime_task_profile(request),
                    },
                )
            ],
            writeback_events=[
                GovernorWritebackEvent(
                    event_type="body_improvement_rollback_approved",
                    payload=self._with_runtime_task_profile(
                        request,
                        {
                            "body_id": request.body_id,
                            "source_commit": current_commit,
                            "target_commit": previous_healthy_commit,
                            "decision": "rollback_required",
                        },
                    ),
                )
            ],
        )

    def _evaluate_post_switch_review(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta],
    ) -> GovernorResponse:
        watch_window_passed = bool(request.evidence.get("watch_window_passed"))
        if slot_meta and slot_meta.body_state != "retired":
            return self._reject(
                f"Post-switch review expects a retired body, got {slot_meta.body_state!r}."
            )
        if not watch_window_passed:
            return self._request_more_evidence(
                "Post-switch review needs an explicit watch_window_passed signal before recycling."
            )
        return GovernorResponse(
            decision="approve",
            confidence=0.87,
            risk_level="low",
            reasoning_summary=(
                "The watch window completed without a rollback signal. The retired body "
                "may be recycled back into shell state."
            ),
            required_actions=[
                GovernorAction(
                    action_type="recycle_retired_slot",
                    slot_id=request.body_id,
                )
            ],
            writeback_events=[
                GovernorWritebackEvent(
                    event_type="body_state_event",
                    payload=self._with_runtime_task_profile(
                        request,
                        {
                            "body_id": request.body_id,
                            "next_state": "shell",
                        },
                    ),
                )
            ],
        )

    def _evaluate_switch_suggestion(
        self,
        request: GovernorRequest,
        *,
        slot_meta: Optional[BodySlotMeta],
    ) -> GovernorResponse:
        slot_id = request.body_id
        if not slot_id and slot_meta:
            slot_id = slot_meta.slot_id

        if slot_meta and slot_meta.body_state not in {"shell", "candidate"}:
            return GovernorResponse(
                decision="request_more_evidence",
                confidence=0.5,
                risk_level="medium",
                reasoning_summary=(
                    f"Switch suggestion received but slot {slot_id} is in state {slot_meta.body_state!r}. "
                    f"Only shell or candidate slots can be promoted. "
                    f"First mark as candidate and run probe."
                ),
                required_actions=[
                    GovernorAction(
                        action_type="record_evolution_event",
                        slot_id=slot_id,
                        notes="Switch suggestion recorded but slot not in valid state.",
                    )
                ],
            )

        health_score = float(request.evidence.get("health_score") or 0)
        improvement_count = int(request.evidence.get("improvement_count") or 0)
        active_health = float(request.evidence.get("active_health_score") or 0)

        # Relative threshold: shell must exceed active by >= 15, OR simply surpass active
        threshold_met = (
            (active_health == 0 and health_score >= 60)  # first switch: lower bar
            or health_score > active_health              # surpass active regardless
            or (active_health > 0 and health_score >= active_health + 15)  # relative threshold
        )

        if threshold_met and improvement_count >= 1:
            return GovernorResponse(
                decision="approve",
                confidence=0.8,
                risk_level="low",
                reasoning_summary=(
                    f"Switch suggestion approved. Slot {slot_id} health score {health_score}/100 "
                    f"(active: {active_health}/100, delta: +{health_score - active_health:.0f}) "
                    f"with {improvement_count} improvements. Ready for probe and switch."
                ),
                required_actions=[
                    GovernorAction(
                        action_type="issue_probe_lease",
                        slot_id=slot_id,
                        notes=f"Health score {health_score} meets threshold for probe.",
                    )
                ],
                writeback_events=[
                    GovernorWritebackEvent(
                        event_type="switch_suggestion_approved",
                        payload={
                            "slot_id": slot_id,
                            "health_score": health_score,
                            "improvement_count": improvement_count,
                        },
                    ),
                ],
            )

        return GovernorResponse(
            decision="request_more_evidence",
            confidence=0.6,
            risk_level="low",
            reasoning_summary=(
                f"Switch suggestion noted. Slot {slot_id} health score {health_score}/100 "
                f"with {improvement_count} improvements. Continue accumulating improvements."
            ),
            required_actions=[
                GovernorAction(
                    action_type="record_evolution_event",
                    slot_id=slot_id,
                    notes=f"Switch suggestion recorded. Health score: {health_score}",
                )
            ],
        )

    def _probe_passed(self, evidence: Dict[str, Any]) -> bool:
        if evidence.get("probe_passed") is True:
            return True
        report = evidence.get("probe_report")
        if isinstance(report, dict):
            if report.get("overall_passed") is True:
                return True
            if str(report.get("overall_status", "")).strip().lower() == "passed":
                return True
        checks = evidence.get("checks")
        if isinstance(checks, Iterable) and not isinstance(checks, (str, bytes, dict)):
            normalized = list(checks)
            return bool(normalized) and all(bool(item) for item in normalized)
        return False

    def _reject(self, summary: str) -> GovernorResponse:
        return GovernorResponse(
            decision="reject",
            confidence=0.85,
            risk_level="high",
            reasoning_summary=summary,
        )

    def _request_more_evidence(self, summary: str) -> GovernorResponse:
        return GovernorResponse(
            decision="request_more_evidence",
            confidence=0.45,
            risk_level="medium",
            reasoning_summary=summary,
        )

    def _runtime_task_profile(self, request: GovernorRequest) -> Dict[str, Any]:
        profile = dict(request.evidence.get("runtime_task_profile") or {})
        runtime_task_profile = derive_runtime_task_profile(
            task_type=request.task_type,
            governance_task_type=profile.get("governance_task_type"),
            task_family=profile.get("task_family"),
            execution_kind=profile.get("execution_kind"),
            default_task_family="general_self_evolution",
        )

        return {
            "task_type": request.task_type,
            "governance_task_type": runtime_task_profile["governance_task_type"],
            "task_family": runtime_task_profile["task_family"],
            "execution_kind": runtime_task_profile["execution_kind"],
        }

    def _with_runtime_task_profile(
        self,
        request: GovernorRequest,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        runtime_task_profile = self._runtime_task_profile(request)
        enriched = dict(payload)
        enriched["runtime_task_profile"] = runtime_task_profile
        for key, value in runtime_task_profile.items():
            if value is not None:
                enriched.setdefault(key, value)
        return enriched


class LLMGovernorReasoner:
    """Optional LLM reasoning layer for ambiguous governance decisions.

    Architecture baseline §3.6 / soul-layer.md §8:
      - Governor Engine: deterministic, protocol-safe, primary authority
      - Governor Reasoner: optional model-assisted analysis for ambiguous cases

    This reasoner is CONSULTATIVE only — it never overrides the deterministic
    engine's decision.  It provides additional analysis that downstream systems
    (or human operators) can use to make more informed choices.
    """

    def __init__(self) -> None:
        self._available = False
        try:
            from memai.model_config import resolve_mem_llm_client
            client, _ = resolve_mem_llm_client(role="governance_reasoner")
            if client is not None:
                self._client = client
                self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def analyze(
        self,
        request: GovernorRequest,
        deterministic_decision: GovernorResponse,
        *,
        slot_meta: Any = None,
    ) -> Dict[str, Any]:
        """Provide LLM analysis of a governance decision.

        Called when the deterministic engine returns ambiguous results
        (request_more_evidence, low confidence, or medium+ risk).
        Returns advisory analysis — does NOT change the decision.
        """
        if not self._available:
            return {"llm_available": False}

        try:
            evidence = dict(request.evidence or {})
            prompt = (
                f"你正在审查 VoidCube 母体系统的一项治理决策。\n\n"
                f"事件类型: {request.event_type}\n"
                f"目标 Body: {request.body_id}\n"
                f"来源: {request.source_actor}\n"
                f"确定性引擎决策: {deterministic_decision.decision}\n"
                f"置信度: {deterministic_decision.confidence}\n"
                f"风险等级: {deterministic_decision.risk_level}\n"
                f"推理摘要: {deterministic_decision.reasoning_summary}\n\n"
                f"证据摘要:\n"
                f"  - build_complete: {evidence.get('build_complete')}\n"
                f"  - probe_passed: {evidence.get('probe_passed')}\n"
                f"  - git_lineage: {json.dumps(dict(evidence.get('git_lineage') or {}), default=str)[:500] if evidence.get('git_lineage') else '无'}\n"
                f"  - slot_state: {slot_meta.body_state if slot_meta else 'unknown'}\n\n"
                f"请提供:\n"
                f"1. 对当前证据质量的评估\n"
                f"2. 决策中可能被忽略的风险因素\n"
                f"3. 建议在做出最终决定前收集哪些额外证据\n"
                f"输出JSON: {{\"evidence_quality\": \"...\", \"hidden_risks\": [\"...\"], \"suggested_evidence\": [\"...\"]}}"
            )

            result = self._client.complete_json(
                system_prompt=(
                    "你是 VoidCube 母体的治理推理顾问。你的分析是咨询性的——"
                    "你不做最终决策，不覆盖确定性引擎的判断。你帮助发现盲点、"
                    "评估证据质量、建议补充信息。保守、审慎、证据驱动。"
                ),
                user_payload={"review": prompt},
                task="scholar.revision",
            )

            if isinstance(result, dict):
                return {
                    "llm_available": True,
                    "evidence_quality": str(result.get("evidence_quality", ""))[:500],
                    "hidden_risks": [
                        str(r) for r in (result.get("hidden_risks") or []) if r
                    ][:5],
                    "suggested_evidence": [
                        str(e) for e in (result.get("suggested_evidence") or []) if e
                    ][:5],
                }
        except Exception:
            pass

        return {"llm_available": True, "error": "LLM analysis failed"}
