from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from .answer_assembler import AnswerAssembler
from .llm_client import OpenAICompatibleLLMClient, resolve_provider_capabilities
from .llm_protocol import PROTOCOL_VERSION
from .pipeline import ChroniclePipeline
from .query_planner import QueryPlanner
from .schema import (
    Arc,
    ArcState,
    Epoch,
    Event,
    EventKind,
    ImpactScope,
    MainOrSide,
    Scene,
    Status,
    TranscriptTurn,
)


@dataclass(slots=True)
class BenchmarkCaseResult:
    fixture: str
    metrics: dict[str, float]
    passed: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "metrics": self.metrics,
            "passed": self.passed,
            "details": self.details,
        }


@dataclass(slots=True)
class BenchmarkRunResult:
    results: list[BenchmarkCaseResult]

    def to_dict(self) -> dict[str, Any]:
        aggregate = {
            "fixtures": len(self.results),
            "passed": sum(1 for item in self.results if item.passed),
            "failed": sum(1 for item in self.results if not item.passed),
            "average_score": round(
                sum(
                    sum(item.metrics.values()) / max(len(item.metrics), 1)
                    for item in self.results
                )
                / max(len(self.results), 1),
                4,
            ),
        }
        return {
            "aggregate": aggregate,
            "results": [item.to_dict() for item in self.results],
        }


@dataclass(slots=True)
class PromptPackRunResult:
    prompt_pack: str
    run: BenchmarkRunResult

    def to_dict(self) -> dict[str, Any]:
        payload = self.run.to_dict()
        payload["prompt_pack"] = self.prompt_pack
        return payload


@dataclass(slots=True)
class PromptPackMatrixResult:
    runs: list[PromptPackRunResult]

    def to_dict(self) -> dict[str, Any]:
        ranked = sorted(
            self.runs,
            key=lambda item: item.run.to_dict()["aggregate"]["average_score"],
            reverse=True,
        )
        return {
            "summary": {
                "packs": len(self.runs),
                "best_pack": ranked[0].prompt_pack if ranked else None,
                "ranking": [
                    {
                        "prompt_pack": item.prompt_pack,
                        "average_score": item.run.to_dict()["aggregate"][
                            "average_score"
                        ],
                    }
                    for item in ranked
                ],
            },
            "runs": [item.to_dict() for item in self.runs],
        }


@dataclass(slots=True)
class ProviderContractCaseResult:
    fixture: str
    metrics: dict[str, float]
    passed: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "metrics": self.metrics,
            "passed": self.passed,
            "details": self.details,
        }


@dataclass(slots=True)
class ProviderContractRunResult:
    results: list[ProviderContractCaseResult]

    def to_dict(self) -> dict[str, Any]:
        aggregate = {
            "fixtures": len(self.results),
            "passed": sum(1 for item in self.results if item.passed),
            "failed": sum(1 for item in self.results if not item.passed),
            "average_score": round(
                sum(
                    sum(item.metrics.values()) / max(len(item.metrics), 1)
                    for item in self.results
                )
                / max(len(self.results), 1),
                4,
            ),
        }
        return {
            "aggregate": aggregate,
            "results": [item.to_dict() for item in self.results],
        }


@dataclass(slots=True)
class PlannerBenchmarkCaseResult:
    fixture: str
    metrics: dict[str, float]
    passed: bool
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "metrics": self.metrics,
            "passed": self.passed,
            "details": self.details,
        }


@dataclass(slots=True)
class PlannerBenchmarkRunResult:
    results: list[PlannerBenchmarkCaseResult]

    def to_dict(self) -> dict[str, Any]:
        aggregate = {
            "fixtures": len(self.results),
            "passed": sum(1 for item in self.results if item.passed),
            "failed": sum(1 for item in self.results if not item.passed),
            "average_score": round(
                sum(
                    sum(item.metrics.values()) / max(len(item.metrics), 1)
                    for item in self.results
                )
                / max(len(self.results), 1),
                4,
            ),
        }
        return {
            "aggregate": aggregate,
            "results": [item.to_dict() for item in self.results],
        }


class BenchmarkRunner:
    def __init__(self, pipeline: ChroniclePipeline | None = None) -> None:
        self.pipeline = pipeline or ChroniclePipeline()

    def run(
        self,
        transcript_paths: Sequence[str | Path],
        expectation_paths: Sequence[str | Path],
    ) -> BenchmarkRunResult:
        results = [
            self._run_case(Path(transcript_path), Path(expectation_path))
            for transcript_path, expectation_path in zip(
                transcript_paths, expectation_paths, strict=True
            )
        ]
        return BenchmarkRunResult(results=results)

    def run_directory(self, fixtures_dir: str | Path) -> BenchmarkRunResult:
        root = Path(fixtures_dir)
        transcripts = sorted(root.glob("*_transcript.json"))
        expectations = [
            path.with_name(path.name.replace("_transcript.json", "_expectations.json"))
            for path in transcripts
        ]
        return self.run(transcripts, expectations)

    def _run_case(
        self, transcript_path: Path, expectation_path: Path
    ) -> BenchmarkCaseResult:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        expectations = json.loads(expectation_path.read_text(encoding="utf-8"))
        result = self.pipeline.ingest_dicts(transcript["turns"])
        observed_topics = sorted(
            {topic for event in result.events for topic in event.topics}
        )
        query_engine = result.create_query_engine()

        topic_coverage = self._topic_coverage(
            observed_topics, expectations.get("expected_topics", [])
        )
        event_floor = (
            1.0
            if len(result.events) >= expectations.get("expected_min_events", 0)
            else 0.0
        )
        scene_floor = (
            1.0
            if len(result.scenes) >= expectations.get("expected_min_scenes", 0)
            else 0.0
        )
        arc_floor = (
            1.0 if len(result.arcs) >= expectations.get("expected_min_arcs", 0) else 0.0
        )
        epoch_floor = (
            1.0
            if len(result.epochs) >= expectations.get("expected_min_epochs", 0)
            else 0.0
        )
        structure_integrity = self._structure_integrity(result)
        evidence_integrity = self._evidence_integrity(result)
        theme_quality = self._theme_quality(
            query_engine, expectations.get("required_theme_queries", [])
        )
        range_query_quality = self._range_query_quality(
            query_engine, expectations.get("range_query_checks", [])
        )
        chapter_query_quality = self._chapter_query_quality(
            query_engine, expectations.get("chapter_query_checks", [])
        )
        revision_precision = self._revision_precision(
            result, expectations.get("revision_probe")
        )
        interpretation_restraint = self._interpretation_restraint(
            result,
            expectations.get("forbidden_topics", []),
            expectations.get("forbidden_summary_terms", []),
        )

        metrics = {
            "topic_coverage": topic_coverage,
            "event_floor": event_floor,
            "scene_floor": scene_floor,
            "arc_floor": arc_floor,
            "epoch_floor": epoch_floor,
            "structure_integrity": structure_integrity,
            "evidence_integrity": evidence_integrity,
            "theme_quality": theme_quality,
            "range_query_quality": range_query_quality,
            "chapter_query_quality": chapter_query_quality,
            "revision_precision": revision_precision,
            "interpretation_restraint": interpretation_restraint,
        }
        passed = all(
            value >= expectations.get("passing_threshold", 0.6)
            for value in metrics.values()
        )
        details = {
            "events": len(result.events),
            "scenes": len(result.scenes),
            "arcs": len(result.arcs),
            "epochs": len(result.epochs),
            "topics": observed_topics,
            "active_arcs": len(
                [item for item in result.arcs if item.status != Status.SUPERSEDED]
            ),
        }
        return BenchmarkCaseResult(
            fixture=expectations["name"],
            metrics=metrics,
            passed=passed,
            details=details,
        )

    def _topic_coverage(
        self, observed_topics: list[str], expected_topics: list[str]
    ) -> float:
        if not expected_topics:
            return 1.0
        hits = sum(1 for topic in expected_topics if topic in observed_topics)
        return round(hits / len(expected_topics), 4)

    def _theme_quality(self, query_engine, themes: list[str]) -> float:
        if not themes:
            return 1.0
        scores: list[float] = []
        for theme in themes:
            payload = query_engine.theme_evolution(theme)
            checks = [
                1.0 if payload["timeline"] else 0.0,
                1.0 if payload["confidence"] >= 0.4 else 0.0,
                1.0 if payload["evidence_refs"] else 0.0,
            ]
            scores.append(sum(checks) / len(checks))
        return round(sum(scores) / len(scores), 4)

    def _structure_integrity(self, result) -> float:
        events = self._active_index(result.events)
        scenes = self._active_index(result.scenes)
        arcs = self._active_index(result.arcs)
        epochs = self._active_index(result.epochs)
        checks: list[float] = []

        for scene in scenes.values():
            checks.extend(
                self._link_checks(
                    parent=scene,
                    children=events,
                )
            )
        for arc in arcs.values():
            checks.extend(
                self._link_checks(
                    parent=arc,
                    children=scenes,
                )
            )
        for epoch in epochs.values():
            checks.extend(
                self._link_checks(
                    parent=epoch,
                    children=arcs,
                )
            )
        if not checks:
            return 1.0
        return round(sum(checks) / len(checks), 4)

    def _link_checks(self, *, parent, children: dict[str, Any]) -> list[float]:
        checks: list[float] = []
        for child_id in parent.child_ids:
            child = children.get(child_id)
            if child is None:
                checks.append(0.0)
                continue
            checks.append(1.0 if parent.id in child.parent_ids else 0.0)
        return checks or [1.0]

    def _evidence_integrity(self, result) -> float:
        query_engine = result.create_query_engine()
        units = [
            *self._active_index(result.scenes).values(),
            *self._active_index(result.arcs).values(),
            *self._active_index(result.epochs).values(),
        ]
        if not units:
            return 1.0
        scores: list[float] = []
        for unit in units:
            payload = query_engine.evidence_trace(unit.id)
            checks = [
                1.0 if unit.evidence_refs else 0.0,
                1.0 if len(payload["support_chain"]) > 1 else 0.0,
            ]
            scores.append(sum(checks) / len(checks))
        return round(sum(scores) / len(scores), 4)

    def _range_query_quality(self, query_engine, checks: list[dict[str, Any]]) -> float:
        if not checks:
            return 1.0
        scores: list[float] = []
        for item in checks:
            payload = query_engine.range_query(
                self._parse_when(item["start"]),
                self._parse_when(item["end"]),
                topic=item.get("topic"),
                entity=item.get("entity"),
                include_evidence=item.get("include_evidence", True),
            )
            sub_scores = [
                1.0 if len(payload["observed"]) >= item.get("min_observed", 0) else 0.0,
                1.0
                if len(payload["main_arcs"]) >= item.get("min_main_arcs", 0)
                else 0.0,
                1.0
                if len(payload["side_arcs"]) >= item.get("min_side_arcs", 0)
                else 0.0,
                1.0
                if payload["confidence"] >= item.get("min_confidence", 0.4)
                else 0.0,
            ]
            if item.get("require_evidence", True):
                sub_scores.append(1.0 if payload["evidence_refs"] else 0.0)
            scores.append(sum(sub_scores) / len(sub_scores))
        return round(sum(scores) / len(scores), 4)

    def _chapter_query_quality(
        self, query_engine, checks: list[dict[str, Any]]
    ) -> float:
        if not checks:
            return 1.0
        scores: list[float] = []
        for item in checks:
            payload = query_engine.chapter_summary(
                self._parse_when(item["start"]),
                self._parse_when(item["end"]),
                include_evidence=item.get("include_evidence", True),
            )
            sub_scores = [
                1.0 if len(payload["epochs"]) >= item.get("min_epochs", 0) else 0.0,
                1.0 if len(payload["themes"]) >= item.get("min_themes", 0) else 0.0,
                1.0
                if payload["confidence"] >= item.get("min_confidence", 0.4)
                else 0.0,
            ]
            if item.get("require_evidence", True):
                sub_scores.append(1.0 if payload["evidence_refs"] else 0.0)
            scores.append(sum(sub_scores) / len(sub_scores))
        return round(sum(scores) / len(scores), 4)

    def _revision_precision(self, result, probe: dict[str, Any] | None) -> float:
        if not probe:
            return 1.0
        target_id = self._resolve_target_id(result, probe["target_selector"])
        execution = result.revise_memory(
            target_id=target_id,
            revision_type=probe.get("revision_type", "factual_revision"),
            reason=probe.get("reason", "benchmark revision probe"),
            changes=self._normalize_revision_changes(
                probe.get("changes", {}),
                target_id=target_id,
                result=result,
            ),
        )
        scores: list[float] = []
        scores.append(
            1.0
            if len(execution.revision_records)
            >= probe.get("expected_revision_records_min", 1)
            else 0.0
        )
        scores.append(
            1.0
            if any(
                target_id in item.supersedes
                for item in execution.events
                + execution.scenes
                + execution.arcs
                + execution.epochs
            )
            else 0.0
        )
        for key, collection in (
            ("expected_scene_topics", execution.scenes),
            ("expected_arc_topics", execution.arcs),
            ("expected_epoch_topics", execution.epochs),
        ):
            expected_topics = probe.get(key, [])
            if not expected_topics:
                continue
            scores.append(
                1.0
                if self._collection_contains_topics(collection, expected_topics)
                else 0.0
            )
        expected_question = probe.get("expected_open_question_contains")
        if expected_question:
            scores.append(
                1.0
                if any(
                    expected_question.lower() in question.lower()
                    for scene in execution.scenes
                    if scene.status != Status.SUPERSEDED
                    for question in scene.open_questions
                )
                else 0.0
            )
        return round(sum(scores) / len(scores), 4) if scores else 1.0

    def _collection_contains_topics(
        self,
        collection: Sequence[Event | Scene | Arc | Epoch],
        expected_topics: list[str],
    ) -> bool:
        active_items = [item for item in collection if item.status != Status.SUPERSEDED]
        return any(
            all(topic in item.topics for topic in expected_topics)
            for item in active_items
        )

    def _interpretation_restraint(
        self,
        result,
        forbidden_topics: list[str],
        forbidden_summary_terms: list[str],
    ) -> float:
        checks: list[float] = []
        observed_topics = {
            topic
            for item in [
                *result.events,
                *result.scenes,
                *result.arcs,
                *result.epochs,
            ]
            if item.status != Status.SUPERSEDED
            for topic in item.topics
        }
        for topic in forbidden_topics:
            checks.append(0.0 if topic in observed_topics else 1.0)
        searchable_text = " ".join(
            [
                item.title + " " + item.summary
                for item in [
                    *result.events,
                    *result.scenes,
                    *result.arcs,
                    *result.epochs,
                ]
                if item.status != Status.SUPERSEDED
            ]
        ).lower()
        for term in forbidden_summary_terms:
            checks.append(0.0 if term.lower() in searchable_text else 1.0)
        return round(sum(checks) / len(checks), 4) if checks else 1.0

    def _parse_when(self, value: str):
        if len(value) == 10:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )

    def _resolve_target_id(self, result, target: str) -> str:
        if ":" not in target:
            return target
        unit_type, index_text = target.split(":", 1)
        if not index_text.isdigit():
            return target
        index = int(index_text)
        mapping = {
            "event": result.events,
            "scene": result.scenes,
            "arc": result.arcs,
            "epoch": result.epochs,
        }
        collection = mapping.get(unit_type)
        if collection is None or not (0 <= index < len(collection)):
            return target
        return collection[index].id

    def _normalize_revision_changes(
        self,
        changes: dict[str, Any],
        *,
        target_id: str,
        result,
    ) -> dict[str, Any]:
        normalized = dict(changes)
        target = next(
            (
                item
                for item in [
                    *result.events,
                    *result.scenes,
                    *result.arcs,
                    *result.epochs,
                ]
                if item.id == target_id
            ),
            None,
        )
        if target is None:
            return normalized
        if isinstance(target, Event) and isinstance(normalized.get("event_kind"), str):
            normalized["event_kind"] = EventKind(normalized["event_kind"])
        if isinstance(normalized.get("impact_scope"), str):
            normalized["impact_scope"] = ImpactScope(normalized["impact_scope"])
        if isinstance(normalized.get("main_or_side"), str):
            normalized["main_or_side"] = MainOrSide(normalized["main_or_side"])
        if isinstance(normalized.get("status"), str):
            normalized["status"] = Status(normalized["status"])
        if isinstance(target, Arc) and isinstance(normalized.get("arc_state"), str):
            normalized["arc_state"] = ArcState(normalized["arc_state"])
        return normalized

    def _active_index(
        self, items: Sequence[Event | Scene | Arc | Epoch]
    ) -> dict[str, Any]:
        return {item.id: item for item in items if item.status != Status.SUPERSEDED}


class PromptPackBenchmarkMatrixRunner:
    def __init__(
        self,
        pipeline_factory: Callable[[str], ChroniclePipeline],
    ) -> None:
        self.pipeline_factory = pipeline_factory

    def run_directory(
        self,
        fixtures_dir: str | Path,
        prompt_packs: Sequence[str],
    ) -> PromptPackMatrixResult:
        runs = [
            PromptPackRunResult(
                prompt_pack=prompt_pack,
                run=BenchmarkRunner(self.pipeline_factory(prompt_pack)).run_directory(
                    fixtures_dir
                ),
            )
            for prompt_pack in prompt_packs
        ]
        return PromptPackMatrixResult(runs=runs)


class ProviderContractBenchmarkRunner:
    def __init__(
        self,
        *,
        model: str = "contract-model",
        api_key: str = "contract-key",
        base_url: str = "https://example.com/v1",
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def run(
        self,
        contract_paths: Sequence[str | Path],
    ) -> ProviderContractRunResult:
        return ProviderContractRunResult(
            results=[self._run_case(Path(path)) for path in contract_paths]
        )

    def run_directory(self, fixtures_dir: str | Path) -> ProviderContractRunResult:
        root = Path(fixtures_dir)
        contracts = sorted(root.glob("*_contract.json"))
        return self.run(contracts)

    def _run_case(self, contract_path: Path) -> ProviderContractCaseResult:
        fixture = json.loads(contract_path.read_text(encoding="utf-8"))
        invocation = fixture.get("invocation", {})
        expectations = fixture.get("expectations", {})
        captured: dict[str, Any] = {}

        def fake_transport(url, headers, payload):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return fixture["response"]

        capabilities = resolve_provider_capabilities(
            fixture.get("provider_profile"),
            profile_path=self._resolve_profile_path(
                contract_path,
                fixture.get("provider_profile_file"),
            ),
            chat_completions_path=fixture.get("chat_completions_path"),
            system_prompt_style=fixture.get("system_prompt_style"),
            response_format_style=fixture.get("response_format_style"),
            response_content_style=fixture.get("response_content_style"),
        )
        client = OpenAICompatibleLLMClient(
            model=fixture.get("model", self.model),
            api_key=self.api_key,
            base_url=fixture.get("base_url", self.base_url),
            provider_capabilities=capabilities,
            transport=fake_transport,
        )

        method = invocation.get("method", "extract_events")
        if method == "extract_events":
            parsed_payload = {
                "events": client.extract_events(
                    [
                        TranscriptTurn(
                            turn_id=turn["turn_id"],
                            speaker=turn["speaker"],
                            text=turn["text"],
                            timestamp=self._parse_when(turn["timestamp"]),
                        )
                        for turn in invocation.get("turns", [])
                    ]
                )
            }
        else:
            parsed_payload = client.safe_complete_json(
                system_prompt=invocation.get("system_prompt", "Return JSON"),
                user_payload=invocation.get("user_payload", {}),
                task=invocation.get("task"),
                response_schema=invocation.get("response_schema"),
            )

        payload = captured.get("payload", {})
        message_roles = [
            item.get("role")
            for item in payload.get("messages", [])
            if isinstance(item, dict)
        ]
        request_delivery = self._score_checks(
            [
                self._matches_url_suffix(
                    captured.get("url", ""),
                    expectations.get("url_suffix"),
                ),
                self._matches_expected_roles(
                    message_roles,
                    expectations.get("message_roles"),
                ),
                self._matches_response_format(
                    payload,
                    expectations.get("response_format"),
                ),
            ]
        )
        protocol_integrity = self._score_checks(
            [
                self._request_contains_protocol(payload),
                self._matches_expected_task(
                    payload,
                    invocation.get("task", "extractor.events"),
                ),
                *[
                    self._request_contains_fragment(payload, fragment)
                    for fragment in expectations.get("request_contains", [])
                ],
            ]
        )
        response_parsing = self._score_checks(
            [
                self._matches_min_event_count(
                    parsed_payload,
                    expectations.get("expected_min_events"),
                ),
                self._matches_summary_fragment(
                    parsed_payload,
                    expectations.get("expected_summary_contains"),
                ),
            ]
        )
        metrics = {
            "request_delivery": request_delivery,
            "protocol_integrity": protocol_integrity,
            "response_parsing": response_parsing,
        }
        passing_threshold = fixture.get("passing_threshold", 0.8)
        return ProviderContractCaseResult(
            fixture=fixture["name"],
            metrics=metrics,
            passed=all(value >= passing_threshold for value in metrics.values()),
            details={
                "url": captured.get("url"),
                "provider_profile": capabilities.profile_name,
                "message_roles": message_roles,
                "response_format": payload.get("response_format", "<absent>"),
                "parsed_events": len(parsed_payload.get("events", []))
                if isinstance(parsed_payload.get("events"), list)
                else 0,
            },
        )

    def _score_checks(self, checks: list[bool | None]) -> float:
        filtered = [1.0 if item else 0.0 for item in checks if item is not None]
        if not filtered:
            return 1.0
        return round(sum(filtered) / len(filtered), 4)

    def _matches_url_suffix(
        self, observed_url: str, expected_suffix: str | None
    ) -> bool | None:
        if expected_suffix is None:
            return None
        return observed_url.endswith(expected_suffix)

    def _matches_expected_roles(
        self,
        observed_roles: list[str | None],
        expected_roles: list[str] | None,
    ) -> bool | None:
        if expected_roles is None:
            return None
        return observed_roles == expected_roles

    def _matches_response_format(
        self,
        payload: dict[str, Any],
        expected_style: str | None,
    ) -> bool | None:
        if expected_style is None:
            return None
        if expected_style == "absent":
            return "response_format" not in payload
        if expected_style == "json_object_object":
            return payload.get("response_format") == {"type": "json_object"}
        if expected_style == "json_object_string":
            return payload.get("response_format") == "json_object"
        return False

    def _request_contains_protocol(self, payload: dict[str, Any]) -> bool:
        return f'"version": "{PROTOCOL_VERSION}"' in self._request_text(payload)

    def _matches_expected_task(
        self,
        payload: dict[str, Any],
        expected_task: str | None,
    ) -> bool | None:
        if expected_task is None:
            return None
        return f'"task": "{expected_task}"' in self._request_text(payload)

    def _request_contains_fragment(
        self,
        payload: dict[str, Any],
        fragment: str,
    ) -> bool:
        return fragment in self._request_text(payload)

    def _request_text(self, payload: dict[str, Any]) -> str:
        return "\n".join(
            item.get("content", "")
            for item in payload.get("messages", [])
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        )

    def _matches_min_event_count(
        self,
        parsed_payload: dict[str, Any],
        expected_min_events: int | None,
    ) -> bool | None:
        if expected_min_events is None:
            return None
        events = parsed_payload.get("events", [])
        if not isinstance(events, list):
            return False
        return len(events) >= expected_min_events

    def _matches_summary_fragment(
        self,
        parsed_payload: dict[str, Any],
        summary_fragment: str | None,
    ) -> bool | None:
        if summary_fragment is None:
            return None
        events = parsed_payload.get("events", [])
        if not isinstance(events, list):
            return False
        return any(
            summary_fragment.lower() in str(item.get("summary", "")).lower()
            for item in events
            if isinstance(item, dict)
        )

    def _parse_when(self, value: str) -> datetime:
        if len(value) == 10:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )

    def _resolve_profile_path(
        self,
        contract_path: Path,
        profile_path: str | None,
    ) -> Path | None:
        if profile_path is None:
            return None
        candidate = Path(profile_path)
        if candidate.is_absolute():
            return candidate
        return contract_path.parent / candidate


class PlannerBenchmarkRunner:
    def __init__(self, pipeline: ChroniclePipeline | None = None) -> None:
        self.pipeline = pipeline or ChroniclePipeline()
        self.assembler = AnswerAssembler()

    def run(
        self,
        transcript_paths: Sequence[str | Path],
        expectation_paths: Sequence[str | Path],
    ) -> PlannerBenchmarkRunResult:
        results = [
            self._run_case(Path(transcript_path), Path(expectation_path))
            for transcript_path, expectation_path in zip(
                transcript_paths, expectation_paths, strict=True
            )
        ]
        return PlannerBenchmarkRunResult(results=results)

    def run_directory(self, fixtures_dir: str | Path) -> PlannerBenchmarkRunResult:
        root = Path(fixtures_dir)
        transcripts = sorted(root.glob("*_transcript.json"))
        expectations = [
            path.with_name(path.name.replace("_transcript.json", "_expectations.json"))
            for path in transcripts
        ]
        return self.run(transcripts, expectations)

    def _run_case(
        self,
        transcript_path: Path,
        expectation_path: Path,
    ) -> PlannerBenchmarkCaseResult:
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        expectations = json.loads(expectation_path.read_text(encoding="utf-8"))
        result = self.pipeline.ingest_dicts(transcript["turns"])
        planner = result.create_query_planner()
        target_id = expectations.get("target_id")
        if isinstance(target_id, str) and ":" in target_id:
            target_id = BenchmarkRunner(self.pipeline)._resolve_target_id(
                result, target_id
            )
        execution = planner.plan_and_execute(
            expectations["request"],
            reference_time=self._parse_when(
                expectations.get("reference_time", "2026-03-31T00:00:00Z")
            ),
            mode=expectations.get("mode", "default"),
            include_evidence=expectations.get("include_evidence", True),
            detail_level=expectations.get("detail_level", "standard"),
            max_results=expectations.get("max_results", 8),
            target_id=target_id,
        )
        answer = self.assembler.assemble(execution)
        metrics = {
            "intent_classification_accuracy": self._binary(
                execution.plan.intent == expectations.get("expected_intent")
            ),
            "query_step_selection_accuracy": self._step_selection_score(
                execution.plan,
                expectations.get("required_step_types", []),
            ),
            "answer_strategy_accuracy": self._binary(
                execution.plan.answer_strategy == expectations.get("expected_strategy")
            ),
            "evidence_step_recall": self._evidence_step_score(
                execution.plan,
                expectations.get("requires_evidence_step", False),
            ),
            "uncertainty_restraint": self._uncertainty_score(
                execution.plan,
                expectations.get("expected_uncertainty_flags", []),
            ),
            "answer_shape_quality": self._answer_shape_score(
                answer.to_dict(),
                expectations.get("expected_answer_sections", []),
            ),
        }
        passed = all(
            value >= expectations.get("passing_threshold", 0.8)
            for value in metrics.values()
        )
        details = {
            "request": expectations["request"],
            "intent": execution.plan.intent,
            "step_types": [step.step_type for step in execution.plan.steps],
            "answer_strategy": execution.plan.answer_strategy,
            "uncertainty_flags": execution.plan.uncertainty_flags,
            "artifacts": sorted(execution.artifacts.keys()),
        }
        return PlannerBenchmarkCaseResult(
            fixture=expectations["name"],
            metrics=metrics,
            passed=passed,
            details=details,
        )

    def _binary(self, value: bool) -> float:
        return 1.0 if value else 0.0

    def _step_selection_score(self, plan, required_step_types: list[str]) -> float:
        if not required_step_types:
            return 1.0
        selected = {step.step_type for step in plan.steps}
        hits = sum(1 for item in required_step_types if item in selected)
        return round(hits / len(required_step_types), 4)

    def _evidence_step_score(self, plan, required: bool) -> float:
        selected = {step.step_type for step in plan.steps}
        has_evidence = "evidence_trace" in selected
        return 1.0 if has_evidence == required else 0.0

    def _uncertainty_score(self, plan, expected_flags: list[str]) -> float:
        if not expected_flags:
            return 1.0 if not plan.uncertainty_flags else 0.75
        selected = set(plan.uncertainty_flags)
        hits = sum(1 for item in expected_flags if item in selected)
        return round(hits / len(expected_flags), 4)

    def _answer_shape_score(
        self,
        answer: dict[str, Any],
        expected_sections: list[str],
    ) -> float:
        if not expected_sections:
            return 1.0
        hits = sum(1 for section in expected_sections if answer.get(section))
        return round(hits / len(expected_sections), 4)

    def _parse_when(self, value: str) -> datetime:
        if len(value) == 10:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
