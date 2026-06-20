from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

from .extraction import TOPIC_LEXICON
from .query import MemoryQueryEngine
from .schema import CertaintyState, MemoryKind, Status, UTC


@dataclass(slots=True)
class QueryPlanStep:
    step_type: str
    arguments: dict[str, Any]
    reason: str
    required: bool = True
    optional: bool = False
    consumes: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_type": self.step_type,
            "arguments": self.arguments,
            "reason": self.reason,
            "required": self.required,
            "optional": self.optional,
            "consumes": self.consumes,
            "produces": self.produces,
        }


@dataclass(slots=True)
class QueryPlan:
    request: str
    plan_type: str
    intent: str
    steps: list[QueryPlanStep]
    answer_strategy: str
    requires_audit_note: bool = False
    uncertainty_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "plan_type": self.plan_type,
            "intent": self.intent,
            "steps": [step.to_dict() for step in self.steps],
            "answer_strategy": self.answer_strategy,
            "requires_audit_note": self.requires_audit_note,
            "uncertainty_flags": self.uncertainty_flags,
        }


@dataclass(slots=True)
class QueryExecutionResult:
    plan: QueryPlan
    artifacts: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "artifacts": self.artifacts,
        }


class QueryPlanner:
    def __init__(self, query_engine: MemoryQueryEngine) -> None:
        self.query_engine = query_engine

    def plan(
        self,
        request: str,
        *,
        reference_time: datetime | None = None,
        detail_level: str = "standard",
        include_evidence: bool = True,
        max_results: int = 8,
        mode: str = "default",
        target_id: str | None = None,
    ) -> QueryPlan:
        anchor = (reference_time or datetime.now(tz=UTC)).astimezone(UTC)
        normalized_request = request.strip()
        request_lower = normalized_request.lower()
        topic = self._extract_topic(normalized_request)
        entity = self._extract_entity(normalized_request)
        resolved_scope, scope_flags = self._resolve_temporal_scope(
            request_lower, anchor
        )

        intent = self._classify_intent(request_lower, target_id)
        uncertainty_flags = list(scope_flags)
        if self._has_mixed_intent(request_lower):
            uncertainty_flags.append("request_has_multiple_possible_intents")

        steps: list[QueryPlanStep] = []
        plan_type = "timeline_summary"
        answer_strategy = "timeline_first"
        requires_audit_note = mode == "audit"

        if intent == "explain_memory":
            plan_type = "memory_audit"
            answer_strategy = "audit_first"
            requires_audit_note = True
            arguments = {"include_superseded": mode == "audit"}
            if target_id:
                arguments["target_id"] = target_id
            steps.append(
                QueryPlanStep(
                    step_type="evidence_trace",
                    arguments=arguments,
                    reason="The request asks for provenance or support behind a memory.",
                    produces=["evidence_trace"],
                )
            )
        elif intent == "retrieve_stable_context":
            plan_type = "stable_context_summary"
            answer_strategy = "stable_context_first"
            steps.append(
                QueryPlanStep(
                    step_type="profile_lookup",
                    arguments={
                        "subject": entity,
                        "memory_kind": self._preferred_memory_kind(request_lower),
                        "certainty_states": None
                        if mode != "conservative"
                        else [
                            CertaintyState.OBSERVED.value,
                            CertaintyState.CONFIRMED.value,
                        ],
                        "include_superseded": mode == "audit",
                        "max_results": max_results,
                    },
                    reason="The request asks for stable preferences, constraints, or facts.",
                    produces=["profile_lookup"],
                )
            )
        elif intent == "trace_theme":
            plan_type = "theme_summary"
            answer_strategy = "theme_first"
            steps.append(
                QueryPlanStep(
                    step_type="theme_evolution",
                    arguments={
                        "theme": topic or entity or "general",
                        "include_evidence": include_evidence,
                        "include_superseded": mode == "audit",
                        "detail_level": detail_level,
                        "max_results": max_results,
                        "statuses": None,
                    },
                    reason="The request asks how one theme evolved over time.",
                    produces=["theme_evolution"],
                )
            )
            if resolved_scope is not None:
                start, end = resolved_scope
                steps.append(
                    QueryPlanStep(
                        step_type="range_query",
                        arguments={
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "topic": topic,
                            "entity": entity,
                            "include_evidence": include_evidence,
                            "include_superseded": mode == "audit",
                            "detail_level": detail_level,
                            "max_results": max_results,
                            "statuses": None,
                        },
                        reason="A bounded range can provide supporting developments for the theme.",
                        required=False,
                        optional=True,
                        produces=["range_summary"],
                    )
                )
        elif intent == "inspect_current_state":
            plan_type = "current_state_summary"
            answer_strategy = "state_first"
            steps.append(
                QueryPlanStep(
                    step_type="active_arcs",
                    arguments={
                        "statuses": [Status.ACTIVE.value, Status.DORMANT.value],
                        "include_superseded": mode == "audit",
                        "max_results": max_results,
                    },
                    reason="The request asks for active, stalled, or unresolved lines.",
                    produces=["active_arcs"],
                )
            )
            if resolved_scope is not None:
                start, end = resolved_scope
                steps.append(
                    QueryPlanStep(
                        step_type="range_query",
                        arguments={
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "topic": topic,
                            "entity": entity,
                            "include_evidence": include_evidence,
                            "include_superseded": mode == "audit",
                            "detail_level": detail_level,
                            "max_results": max_results,
                            "statuses": None,
                        },
                        reason="Recent range context can support current-state inspection.",
                        required=False,
                        optional=True,
                        produces=["range_summary"],
                    )
                )
        else:
            if resolved_scope is None:
                uncertainty_flags.append("time_window_is_implicit")
                resolved_scope = self._default_recent_scope(anchor)
            start, end = resolved_scope
            steps.append(
                QueryPlanStep(
                    step_type="range_query",
                    arguments={
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "topic": topic,
                        "entity": entity,
                        "include_evidence": include_evidence,
                        "include_superseded": mode == "audit",
                        "detail_level": detail_level,
                        "max_results": max_results,
                        "statuses": None,
                    },
                    reason="The request asks what changed during a bounded or recent period.",
                    produces=["range_summary"],
                )
            )
            if include_evidence or mode == "audit":
                steps.append(
                    QueryPlanStep(
                        step_type="evidence_trace",
                        arguments={
                            "target_source": "top_main_arc",
                            "include_superseded": mode == "audit",
                        },
                        reason="The response should stay evidence-aware for the strongest retrieved line.",
                        required=False,
                        optional=True,
                        consumes=["range_summary"],
                        produces=["evidence_trace"],
                    )
                )

        return QueryPlan(
            request=normalized_request,
            plan_type=plan_type,
            intent=intent,
            steps=steps,
            answer_strategy=answer_strategy,
            requires_audit_note=requires_audit_note,
            uncertainty_flags=sorted(set(uncertainty_flags)),
        )

    def execute(self, plan: QueryPlan) -> QueryExecutionResult:
        artifacts: dict[str, Any] = {}
        for step in plan.steps:
            payload = self._execute_step(step, artifacts)
            for produced in step.produces or [step.step_type]:
                artifacts[produced] = payload
        return QueryExecutionResult(plan=plan, artifacts=artifacts)

    def plan_and_execute(self, request: str, **kwargs: Any) -> QueryExecutionResult:
        plan = self.plan(request, **kwargs)
        return self.execute(plan)

    def _execute_step(
        self,
        step: QueryPlanStep,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        arguments = dict(step.arguments)
        if step.step_type == "range_query":
            return self.query_engine.range_query(
                self._parse_datetime(arguments.pop("start")),
                self._parse_datetime(arguments.pop("end")),
                **arguments,
            )
        if step.step_type == "theme_evolution":
            return self.query_engine.theme_evolution(**arguments)
        if step.step_type == "active_arcs":
            statuses = arguments.pop("statuses", None)
            parsed_statuses = [Status(item) for item in statuses] if statuses else None
            return self.query_engine.active_arcs(statuses=parsed_statuses, **arguments)
        if step.step_type == "profile_lookup":
            certainty_states = arguments.pop("certainty_states", None)
            memory_kind = arguments.pop("memory_kind", None)
            parsed_certainty = (
                [CertaintyState(item) for item in certainty_states]
                if certainty_states
                else None
            )
            parsed_kind = MemoryKind(memory_kind) if memory_kind else None
            return self.query_engine.profile_lookup(
                memory_kind=parsed_kind,
                certainty_states=parsed_certainty,
                **arguments,
            )
        if step.step_type == "evidence_trace":
            target_id = arguments.pop("target_id", None)
            if target_id is None:
                target_source = arguments.pop("target_source", None)
                target_id = self._resolve_target_source(target_source, artifacts)
            if target_id is None:
                return {
                    "result_type": "evidence_trace",
                    "target_id": None,
                    "summary": None,
                    "support_chain": [],
                    "uncertainty": "No target id was available for evidence tracing.",
                }
            return self.query_engine.evidence_trace(target_id=target_id, **arguments)
        raise ValueError(f"Unsupported planner step type: {step.step_type}")

    def _resolve_target_source(
        self,
        target_source: str | None,
        artifacts: dict[str, Any],
    ) -> str | None:
        if target_source == "top_main_arc":
            range_summary = artifacts.get("range_summary")
            if isinstance(range_summary, dict):
                for ref in range_summary.get("evidence_refs", []):
                    if isinstance(ref, str) and ref.startswith("arc_"):
                        return ref
                refs = range_summary.get("evidence_refs", [])
                return refs[0] if refs else None
        if target_source == "top_active_arc":
            active = artifacts.get("active_arcs")
            if isinstance(active, dict) and active.get("arcs"):
                return active["arcs"][0].get("id")
        return None

    def _classify_intent(self, request_lower: str, target_id: str | None) -> str:
        if target_id or any(
            phrase in request_lower
            for phrase in ("where did", "came from", "evidence", "support", "why")
        ):
            return "explain_memory"
        if any(
            phrase in request_lower
            for phrase in (
                "preference",
                "preferences",
                "constraint",
                "constraints",
                "stable",
                "remember before answering",
                "language preference",
            )
        ):
            return "retrieve_stable_context"
        if any(
            phrase in request_lower
            for phrase in ("how has", "evolved", "evolution", "so far")
        ):
            return "trace_theme"
        if any(
            phrase in request_lower
            for phrase in (
                "current",
                "active",
                "unresolved",
                "blocker",
                "blocked",
                "stalled",
            )
        ):
            return "inspect_current_state"
        return "summarize_recent_changes"

    def _resolve_temporal_scope(
        self,
        request_lower: str,
        anchor: datetime,
    ) -> tuple[tuple[datetime, datetime] | None, list[str]]:
        flags: list[str] = []
        if "today" in request_lower:
            start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
            return (start, start + timedelta(days=1) - timedelta(seconds=1)), flags
        if "this week" in request_lower:
            start = (anchor - timedelta(days=anchor.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return (start, start + timedelta(days=7) - timedelta(seconds=1)), flags
        if "this month" in request_lower:
            start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
            return (start, end - timedelta(seconds=1)), flags
        if "recently" in request_lower or "recent" in request_lower:
            flags.append("time_window_is_implicit")
            return self._default_recent_scope(anchor), flags
        if "historically" in request_lower or "history" in request_lower:
            flags.append("time_window_is_broad")
            return None, flags
        return None, flags

    def _default_recent_scope(self, anchor: datetime) -> tuple[datetime, datetime]:
        end = anchor
        start = end - timedelta(days=30)
        return start, end

    def _extract_topic(self, request: str) -> str | None:
        lowered = request.lower()
        for topic, keywords in TOPIC_LEXICON.items():
            if topic in lowered or any(
                keyword.lower() in lowered for keyword in keywords
            ):
                return topic
        return None

    def _extract_entity(self, request: str) -> str | None:
        lowered = request.lower()
        if "project" in lowered:
            return "project"
        if "user" in lowered:
            return "user"
        if "assistant" in lowered:
            return "assistant"
        return None

    def _preferred_memory_kind(self, request_lower: str) -> str | None:
        if (
            "before answering" in request_lower
            or "remember before answering" in request_lower
        ):
            return None
        if "preference" in request_lower:
            return MemoryKind.PREFERENCE.value
        if "constraint" in request_lower:
            return MemoryKind.CONSTRAINT.value
        if "definition" in request_lower:
            return MemoryKind.DEFINITION.value
        if "fact" in request_lower:
            return MemoryKind.FACT.value
        return None

    def _has_mixed_intent(self, request_lower: str) -> bool:
        hits = sum(
            1
            for phrases in (
                ("changed", "progress", "this month", "this week", "recently"),
                ("evolved", "how has", "so far"),
                ("current", "active", "blocker", "unresolved"),
                ("evidence", "where did", "came from", "why"),
                ("preference", "constraint", "stable"),
            )
            if any(phrase in request_lower for phrase in phrases)
        )
        return hits > 1

    def _parse_datetime(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
