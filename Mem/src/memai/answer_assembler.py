from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .query_planner import QueryExecutionResult


@dataclass(slots=True)
class AssembledAnswer:
    strategy: str
    summary: str
    observed: list[str]
    structure: list[str]
    blockers: list[str]
    unknown: list[str]
    evidence: list[str]
    stable_context: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "summary": self.summary,
            "observed": self.observed,
            "structure": self.structure,
            "blockers": self.blockers,
            "unknown": self.unknown,
            "evidence": self.evidence,
            "stable_context": self.stable_context,
        }


class AnswerAssembler:
    def assemble(self, execution: QueryExecutionResult) -> AssembledAnswer:
        strategy = execution.plan.answer_strategy
        if strategy == "theme_first":
            return self._assemble_theme_first(execution)
        if strategy == "state_first":
            return self._assemble_state_first(execution)
        if strategy == "audit_first":
            return self._assemble_audit_first(execution)
        if strategy == "stable_context_first":
            return self._assemble_stable_context_first(execution)
        return self._assemble_timeline_first(execution)

    def _assemble_timeline_first(
        self, execution: QueryExecutionResult
    ) -> AssembledAnswer:
        artifact = execution.artifacts.get("range_summary", {})
        observed = list(artifact.get("observed", []))
        structure = [
            *artifact.get("main_arcs", []),
            *artifact.get("side_arcs", []),
        ]
        evidence = self._collect_evidence(execution, artifact)
        stable_context = list(artifact.get("stable_context", []))
        language = self._select_language(execution, stable_context)
        structure = self._localize_section_entries(structure, language, "structure")
        unknown = self._collect_unknowns(execution, artifact, language=language)
        return AssembledAnswer(
            strategy="timeline_first",
            summary=self._summarize_timeline(observed, structure, unknown, language),
            observed=observed,
            structure=structure,
            blockers=[],
            unknown=unknown,
            evidence=evidence,
            stable_context=stable_context,
        )

    def _assemble_theme_first(self, execution: QueryExecutionResult) -> AssembledAnswer:
        artifact = execution.artifacts.get("theme_evolution", {})
        observed = [
            item.get("shift", "")
            for item in artifact.get("timeline", [])
            if item.get("shift")
        ]
        structure = []
        active_state = artifact.get("active_state")
        if active_state:
            structure.append(f"Current state: {active_state}")
        turning_points = artifact.get("major_turning_points", [])
        if turning_points:
            structure.append(f"Major turning points: {', '.join(turning_points)}")
        evidence = self._collect_evidence(execution, artifact)
        stable_context = list(artifact.get("stable_context", []))
        language = self._select_language(execution, stable_context)
        structure = self._localize_section_entries(structure, language, "structure")
        unknown = self._collect_unknowns(execution, artifact, language=language)
        return AssembledAnswer(
            strategy="theme_first",
            summary=self._summarize_theme(observed, structure, unknown, language),
            observed=observed,
            structure=structure,
            blockers=[],
            unknown=unknown,
            evidence=evidence,
            stable_context=stable_context,
        )

    def _assemble_state_first(self, execution: QueryExecutionResult) -> AssembledAnswer:
        artifact = execution.artifacts.get("active_arcs", {})
        range_artifact = execution.artifacts.get("range_summary", {})
        observed, structure, blockers = self._split_state_signals(
            artifact.get("arcs", []),
            range_artifact.get("open_questions", []),
        )
        evidence = self._collect_evidence(execution, artifact, range_artifact)
        stable_context = list(range_artifact.get("stable_context", []))
        language = self._select_language(execution, stable_context)
        structure = self._localize_section_entries(structure, language, "structure")
        blockers = self._localize_section_entries(blockers, language, "blockers")
        unknown = self._collect_unknowns(execution, artifact, range_artifact, language=language)
        return AssembledAnswer(
            strategy="state_first",
            summary=self._summarize_state(
                observed, structure, blockers, unknown, language
            ),
            observed=observed,
            structure=structure,
            blockers=blockers,
            unknown=unknown,
            evidence=evidence,
            stable_context=stable_context,
        )

    def _split_state_signals(
        self,
        arcs: list[dict[str, Any]],
        open_questions: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        observed: list[str] = []
        structure: list[str] = []
        blockers: list[str] = []
        blocker_terms = (
            "block",
            "blocked",
            "blocker",
            "stalled",
            "unresolved",
            "pending",
        )

        for item in arcs:
            summary = item.get("summary", "")
            if not summary:
                continue
            status = item.get("status", "")
            arc_state = item.get("arc_state", "")
            normalized = summary.lower()
            is_blocked = (
                status == "dormant"
                or arc_state in {"stalled", "dormant"}
                or any(term in normalized for term in blocker_terms)
            )
            if is_blocked:
                blockers.append(summary)
            else:
                observed.append(summary)

        for question in open_questions:
            if question and question not in blockers:
                blockers.append(question)

        if not observed and blockers:
            observed = [blockers[0]]
            blockers = blockers[1:]

        return observed, structure, blockers

    def _assemble_audit_first(self, execution: QueryExecutionResult) -> AssembledAnswer:
        artifact = execution.artifacts.get("evidence_trace", {})
        target_id = artifact.get("target_id")
        summary = artifact.get("summary")
        observed = [summary] if summary else []
        structure = [f"Current claim id: {target_id}"] if target_id else []
        support_chain = artifact.get("support_chain", [])
        if support_chain:
            structure.append(f"Evidence chain: {', '.join(support_chain)}")
        evidence = [item for item in support_chain if isinstance(item, str)]
        language = self._select_language(execution, [])
        structure = self._localize_section_entries(structure, language, "structure")
        unknown = self._collect_unknowns(execution, artifact, language=language)
        return AssembledAnswer(
            strategy="audit_first",
            summary=self._summarize_audit(observed, structure, unknown, language),
            observed=observed,
            structure=structure,
            blockers=[],
            unknown=unknown,
            evidence=evidence,
            stable_context=[],
        )

    def _assemble_stable_context_first(
        self, execution: QueryExecutionResult
    ) -> AssembledAnswer:
        artifact = execution.artifacts.get("profile_lookup", {})
        stable_context = list(artifact.get("items", []))
        observed = [
            item.get("summary", "") for item in stable_context if item.get("summary")
        ]
        structure = [
            f"{item.get('memory_kind')}: {item.get('subject')} -> {item.get('value')}"
            for item in stable_context
        ]
        evidence = [
            ref
            for item in stable_context
            for ref in item.get("evidence_refs", [])
            if isinstance(ref, str)
        ]
        language = self._select_language(execution, stable_context)
        structure = self._localize_section_entries(structure, language, "structure")
        unknown = self._collect_unknowns(execution, artifact, language=language)
        return AssembledAnswer(
            strategy="stable_context_first",
            summary=self._summarize_stable_context(
                observed, structure, unknown, language
            ),
            observed=observed,
            structure=structure,
            blockers=[],
            unknown=unknown,
            evidence=evidence,
            stable_context=stable_context,
        )

    def _summarize_timeline(
        self,
        observed: list[str],
        structure: list[str],
        unknown: list[str],
        language: str,
    ) -> str:
        if language == "zh":
            if observed:
                if structure:
                    return f"最近的进展集中在{self._trim(observed[0])}，主结构线索是{self._trim(structure[0])}。"
                return f"最近的进展集中在{self._trim(observed[0])}。"
            if unknown:
                return f"最近变化仍不够明确：{self._trim(unknown[0])}。"
            return "当前记忆视图还不足以总结最近变化。"
        if observed:
            if structure:
                return f"Recent developments center on {self._trim(observed[0])}, with {self._trim(structure[0])} as the main structure."
            return f"Recent developments center on {self._trim(observed[0])}."
        if unknown:
            return f"Recent changes remain unclear: {self._trim(unknown[0])}."
        return "Recent changes could not be summarized from the current memory view."

    def _summarize_theme(
        self,
        observed: list[str],
        structure: list[str],
        unknown: list[str],
        language: str,
    ) -> str:
        if language == "zh":
            if observed:
                if structure:
                    return f"这个主题的演化主要经过{self._trim(observed[0])}，当前结构表现为{self._trim(structure[0])}。"
                return f"这个主题的演化主要经过{self._trim(observed[0])}。"
            if unknown:
                return f"主题演化仍有不确定性：{self._trim(unknown[0])}。"
            return "当前缺少足够的纵向证据来总结这个主题。"
        if observed:
            if structure:
                return f"The theme evolved through {self._trim(observed[0])}, and {self._trim(structure[0]).lower()}."
            return f"The theme evolved through {self._trim(observed[0])}."
        if unknown:
            return f"Theme evolution is uncertain: {self._trim(unknown[0])}."
        return "The theme lacks enough longitudinal evidence for a concise summary."

    def _summarize_state(
        self,
        observed: list[str],
        structure: list[str],
        blockers: list[str],
        unknown: list[str],
        language: str,
    ) -> str:
        if language == "zh":
            if observed:
                if blockers:
                    return f"当前状态主要由{self._trim(observed[0])}主导，但{self._trim(blockers[0])}仍在阻塞推进。"
                if structure:
                    return f"当前状态主要由{self._trim(observed[0])}主导，同时{self._trim(structure[0])}仍在影响整体结构。"
                return f"当前状态主要由{self._trim(observed[0])}主导。"
            if blockers:
                return f"当前最主要的阻塞项是{self._trim(blockers[0])}。"
            if structure:
                return f"当前状态最适合概括为{self._trim(structure[0])}。"
            if unknown:
                return f"当前状态判断仍受限：{self._trim(unknown[0])}。"
            return "现有检索结果还不足以总结当前状态。"
        if observed:
            if blockers:
                return f"The current state is led by {self._trim(observed[0])}, with {self._trim(blockers[0])} still blocking progress."
            if structure:
                return f"The current state is led by {self._trim(observed[0])}, with {self._trim(structure[0])} still shaping it."
            return f"The current state is led by {self._trim(observed[0])}."
        if blockers:
            return f"The main blocker is {self._trim(blockers[0])}."
        if structure:
            return f"The current state is best described by {self._trim(structure[0])}."
        if unknown:
            return f"Current state assessment is limited: {self._trim(unknown[0])}."
        return "The current state could not be summarized from the available retrieval results."

    def _summarize_audit(
        self,
        observed: list[str],
        structure: list[str],
        unknown: list[str],
        language: str,
    ) -> str:
        if language == "zh":
            if observed:
                return f"当前结论是{self._trim(observed[0])}。"
            if structure:
                return f"已经找到相关审计链路：{self._trim(structure[0])}。"
            if unknown:
                return f"审计链路仍不完整：{self._trim(unknown[0])}。"
            return "当前审计链路还无法生成简明结论。"
        if observed:
            return f"The current claim is {self._trim(observed[0])}."
        if structure:
            return f"Audit trace is available for {self._trim(structure[0])}."
        if unknown:
            return f"Audit trace is incomplete: {self._trim(unknown[0])}."
        return "The audit trace did not produce a concise claim summary."

    def _summarize_stable_context(
        self,
        observed: list[str],
        structure: list[str],
        unknown: list[str],
        language: str,
    ) -> str:
        if language == "zh":
            if observed:
                return f"当前最相关的稳定上下文是{self._trim(observed[0])}。"
            if structure:
                return f"当前最相关的稳定上下文是{self._trim(structure[0])}。"
            if unknown:
                return f"稳定上下文检索仍受限：{self._trim(unknown[0])}。"
            return "当前记忆视图里没有足够的稳定上下文可供总结。"
        if observed:
            return f"The most relevant stable context is {self._trim(observed[0])}."
        if structure:
            return f"The most relevant stable context is {self._trim(structure[0])}."
        if unknown:
            return f"Stable context retrieval is limited: {self._trim(unknown[0])}."
        return "No stable context could be summarized from the current memory view."

    def _trim(self, value: str, limit: int = 120) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) <= limit:
            return cleaned.rstrip(".")
        return cleaned[: limit - 3].rstrip() + "..."

    def _select_language(
        self,
        execution: QueryExecutionResult,
        stable_context: list[dict[str, Any]],
    ) -> str:
        request = execution.plan.request
        if any("\u4e00" <= char <= "\u9fff" for char in request):
            return "zh"
        for item in stable_context:
            text = " ".join(
                str(item.get(key, ""))
                for key in ("summary", "value", "subject", "predicate")
            ).lower()
            if "chinese" in text or "中文" in text:
                return "zh"
        return "en"

    def _localize_section_entries(
        self,
        entries: list[str],
        language: str,
        section: str,
    ) -> list[str]:
        if language != "zh":
            return entries
        localized: list[str] = []
        for entry in entries:
            localized.append(self._localize_entry(entry, section))
        return localized

    def _localize_entry(self, entry: str, section: str) -> str:
        if section == "structure":
            if entry.startswith("Current state: "):
                return f"当前状态：{entry.removeprefix('Current state: ')}"
            if entry.startswith("Major turning points: "):
                return f"关键转折点：{entry.removeprefix('Major turning points: ')}"
            if entry.startswith("Current claim id: "):
                return f"当前结论 ID：{entry.removeprefix('Current claim id: ')}"
            if entry.startswith("Evidence chain: "):
                return f"证据链：{entry.removeprefix('Evidence chain: ')}"
            if ": " in entry and " -> " in entry:
                kind, rest = entry.split(": ", 1)
                kind_map = {
                    "preference": "偏好",
                    "constraint": "约束",
                    "definition": "定义",
                    "fact": "事实",
                }
                return f"{kind_map.get(kind, kind)}：{rest.replace(' -> ', ' -> ')}"
        if section == "blockers":
            return f"阻塞项：{entry}" if not entry.startswith("阻塞项：") else entry
        return entry

    def _collect_unknowns(
        self,
        execution: QueryExecutionResult,
        *artifacts: dict[str, Any],
        language: str,
    ) -> list[str]:
        unknown = []
        unknown.extend(self._localize_uncertainty_flags(execution.plan.uncertainty_flags, language))
        for artifact in artifacts:
            uncertainty = artifact.get("uncertainty")
            if uncertainty:
                unknown.append(self._localize_uncertainty_message(uncertainty, language))
        return unknown

    def _localize_uncertainty_flags(self, flags: list[str], language: str) -> list[str]:
        if language != "zh":
            return flags
        mapping = {
            "request_has_multiple_possible_intents": "请求同时包含多种可能意图",
            "time_window_is_implicit": "时间范围是隐含推断的",
            "time_window_is_broad": "时间范围过宽，结果可能较粗略",
        }
        return [mapping.get(flag, flag) for flag in flags]

    def _localize_uncertainty_message(self, message: str, language: str) -> str:
        if language != "zh":
            return message
        mapping = {
            "No stable profile memory matched the request.": "没有匹配请求的稳定画像记忆。",
            "No structured memory matched the requested range.": "请求范围内没有匹配的结构化记忆。",
            "Theme lacks longitudinal evidence in the current memory view.": "当前记忆视图中缺少这个主题的纵向证据。",
            "No epoch-level history overlaps the requested range.": "请求范围内没有重叠的章节级历史。",
            "No target id was available for evidence tracing.": "当前没有可用于证据追踪的目标 ID。",
        }
        return mapping.get(message, message)

    def _collect_evidence(
        self,
        execution: QueryExecutionResult,
        *artifacts: dict[str, Any],
    ) -> list[str]:
        evidence: list[str] = []
        for artifact in artifacts:
            for ref in artifact.get("evidence_refs", []):
                if isinstance(ref, str) and ref not in evidence:
                    evidence.append(ref)
        trace = execution.artifacts.get("evidence_trace", {})
        for ref in trace.get("support_chain", []):
            if isinstance(ref, str) and ref not in evidence:
                evidence.append(ref)
        return evidence
