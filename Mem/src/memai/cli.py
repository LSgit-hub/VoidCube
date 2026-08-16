from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from .benchmarking import (
    BenchmarkRunner,
    PlannerBenchmarkRunner,
    PromptPackBenchmarkMatrixRunner,
    ProviderContractBenchmarkRunner,
)
from .answer_assembler import AnswerAssembler
from .extraction import (
    EventExtractor,
    HeuristicEventExtractionBackend,
    LLMEventExtractionBackend,
)
from .llm_client import OpenAICompatibleLLMClient
from .model_config import load_mem_model_config_set
from .pipeline import ChroniclePipeline
from .prompt_registry import PromptRegistry
from .repository import MemoryStateRepository
from .scholar import HeuristicScholarBackend, LLMScholarBackend
from .schema import CertaintyState, MemoryKind


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_datetime(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _resolve_target_id(result, target: str) -> str:
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


def _parse_status_filter(value: str | None) -> list[Any] | None:
    if not value:
        return None
    from .schema import Status

    return [Status(item.strip()) for item in value.split(",") if item.strip()]


def _parse_certainty_states(value: str | None) -> list[CertaintyState] | None:
    if not value:
        return None
    return [CertaintyState(item.strip()) for item in value.split(",") if item.strip()]


def _add_backend_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=["heuristic", "llm"],
        default="heuristic",
        help="Extraction backend to use when building memory from transcripts",
    )
    parser.add_argument("--model", help="Model name for the llm backend")
    parser.add_argument(
        "--base-url", help="OpenAI-compatible base URL for the llm backend"
    )
    parser.add_argument(
        "--provider-profile",
        default="openai",
        help="Provider capability profile for the llm backend, including custom names loaded from --provider-profile-file",
    )
    parser.add_argument(
        "--chat-completions-path",
        help="Optional override for the provider chat completions endpoint path",
    )
    parser.add_argument(
        "--provider-profile-file",
        help="Optional JSON file describing one or more custom provider capability profiles",
    )
    parser.add_argument(
        "--system-prompt-style",
        choices=["system", "developer", "inline_user"],
        help="Optional override for how the system prompt is sent to the provider",
    )
    parser.add_argument(
        "--response-format-style",
        choices=["json_object", "json_object_string", "none"],
        help="Optional override for how strict JSON mode is requested",
    )
    parser.add_argument(
        "--response-content-style",
        choices=["auto", "openai_message", "choices_text", "output_text"],
        help="Optional override for how response text is read from provider payloads",
    )
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable that holds the llm backend API key",
    )
    parser.add_argument(
        "--system-prompt-file",
        help="Optional file containing the extraction system prompt for the llm backend",
    )
    parser.add_argument(
        "--prompt-pack-dir",
        help="Optional directory containing prompt pack files such as events.txt, scene.txt, arc.txt, revision.txt",
    )
    parser.add_argument(
        "--prompt-pack",
        choices=["default", "conservative", "high-recall", "scholar-heavy"],
        default="default",
        help="Builtin prompt pack name for the llm backend",
    )


def _add_query_behavior_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--status-filter",
        help="Optional comma-separated lifecycle statuses such as active,dormant",
    )
    parser.add_argument(
        "--detail-level",
        choices=["brief", "standard", "deep"],
        default="standard",
        help="Control output density for supported query types",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Maximum number of ranked items to include in the response",
    )
    parser.add_argument(
        "--include-superseded",
        action="store_true",
        help="Include superseded historical records in query results",
    )
    parser.add_argument(
        "--no-evidence",
        action="store_true",
        help="Suppress evidence reference ids from query responses",
    )
    parser.add_argument("--subject", help="Subject filter for profile query")
    parser.add_argument(
        "--memory-kind",
        choices=[item.value for item in MemoryKind],
        help="Profile memory kind filter",
    )
    parser.add_argument(
        "--certainty-state",
        help="Optional comma-separated certainty states such as observed,confirmed",
    )


def _build_pipeline_from_args(args) -> ChroniclePipeline:
    return _build_pipeline_with_prompt_pack(
        args, getattr(args, "prompt_pack", "default")
    )


def _build_pipeline_with_prompt_pack(args, prompt_pack: str) -> ChroniclePipeline:
    if getattr(args, "backend", "heuristic") == "llm":
        model_config = (
            load_mem_model_config_set()
            .for_role("extraction")
            .with_cli_overrides(args)
        )
        prompt_registry = PromptRegistry.from_path(
            args.prompt_pack_dir,
            builtin_name=prompt_pack,
        )
        if args.system_prompt_file:
            prompt_registry = prompt_registry.with_override(
                "extractor.events",
                OpenAICompatibleLLMClient.load_system_prompt(args.system_prompt_file),
            )
        client = OpenAICompatibleLLMClient.from_env(
            model=model_config.model,
            api_key_env=model_config.api_key_env,
            base_url=model_config.base_url,
            provider_profile=model_config.provider_profile,
            provider_profile_path=model_config.provider_profile_file,
            chat_completions_path=model_config.chat_completions_path,
            system_prompt_style=model_config.system_prompt_style,
            response_format_style=model_config.response_format_style,
            response_content_style=model_config.response_content_style,
            system_prompt=None,
            prompt_registry=prompt_registry,
        )
        extractor = EventExtractor(backend=LLMEventExtractionBackend(client))
        scholar_backend = LLMScholarBackend(client)
    else:
        extractor = EventExtractor(backend=HeuristicEventExtractionBackend())
        scholar_backend = HeuristicScholarBackend()
    return ChroniclePipeline(
        event_extractor=extractor,
        scholar_backend=scholar_backend,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memai", description="Chronicle Scholar memory toolkit"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="Run the pipeline on a transcript JSON file"
    )
    ingest.add_argument("input", help="Path to a transcript JSON file")
    ingest.add_argument(
        "--output", help="Optional path to write the pipeline result JSON"
    )
    _add_backend_arguments(ingest)

    query = subparsers.add_parser(
        "query", help="Run a query against a transcript-derived memory view"
    )
    query.add_argument("input", help="Path to a transcript JSON file")
    query.add_argument(
        "--query-type",
        required=True,
        choices=["point", "range", "theme", "active", "chapter", "evidence", "profile"],
    )
    query.add_argument("--when", help="Datetime for point query")
    query.add_argument("--start", help="Range start datetime")
    query.add_argument("--end", help="Range end datetime")
    query.add_argument("--theme", help="Theme for theme evolution or topic filter")
    query.add_argument("--entity", help="Entity filter for range query")
    query.add_argument("--target-id", help="Target memory id for evidence query")
    _add_query_behavior_arguments(query)
    _add_backend_arguments(query)

    plan_query = subparsers.add_parser(
        "plan-query",
        help="Plan a natural-language retrieval request against a transcript-derived memory view",
    )
    plan_query.add_argument("input", help="Path to a transcript JSON file")
    plan_query.add_argument("--request", required=True, help="Natural-language request")
    plan_query.add_argument("--reference-time", help="Optional reference datetime")
    plan_query.add_argument(
        "--mode",
        choices=["default", "conservative", "audit"],
        default="default",
        help="Planner posture",
    )
    plan_query.add_argument("--target-id", help="Optional explicit target memory id")
    _add_query_behavior_arguments(plan_query)
    _add_backend_arguments(plan_query)

    ask = subparsers.add_parser(
        "ask",
        help="Plan and execute a natural-language retrieval request against a transcript-derived memory view",
    )
    ask.add_argument("input", help="Path to a transcript JSON file")
    ask.add_argument("--request", required=True, help="Natural-language request")
    ask.add_argument("--reference-time", help="Optional reference datetime")
    ask.add_argument(
        "--mode",
        choices=["default", "conservative", "audit"],
        default="default",
        help="Planner posture",
    )
    ask.add_argument("--target-id", help="Optional explicit target memory id")
    _add_query_behavior_arguments(ask)
    _add_backend_arguments(ask)

    maintain = subparsers.add_parser(
        "maintain",
        help="Apply compression and dormancy maintenance to a transcript-derived memory view",
    )
    maintain.add_argument("input", help="Path to a transcript JSON file")
    maintain.add_argument(
        "--reference-time", help="Optional reference datetime for aging decisions"
    )
    _add_backend_arguments(maintain)

    revise = subparsers.add_parser(
        "revise", help="Create a superseding revision for a memory object"
    )
    revise.add_argument("input", help="Path to a transcript JSON file")
    revise.add_argument(
        "--target-id", required=True, help="Existing memory id to revise"
    )
    revise.add_argument("--revision-type", required=True, help="Revision type label")
    revise.add_argument("--reason", required=True, help="Reason for the revision")
    revise.add_argument("--summary", help="Replacement summary")
    revise.add_argument("--title", help="Replacement title")
    revise.add_argument("--importance", type=float, help="Replacement importance")
    revise.add_argument("--confidence", type=float, help="Replacement confidence")
    _add_backend_arguments(revise)

    benchmark = subparsers.add_parser(
        "benchmark", help="Run the sample benchmark fixture"
    )
    benchmark.add_argument(
        "--fixture",
        default=str(Path("benchmarks") / "fixtures"),
    )
    benchmark.add_argument(
        "--expectations",
        default=None,
    )
    _add_backend_arguments(benchmark)

    benchmark_packs = subparsers.add_parser(
        "benchmark-prompt-packs",
        help="Compare multiple built-in prompt packs on the same benchmark fixtures",
    )
    benchmark_packs.add_argument(
        "--fixture",
        default=str(Path("benchmarks") / "fixtures"),
    )
    benchmark_packs.add_argument(
        "--prompt-packs",
        default="default,conservative,high-recall,scholar-heavy",
        help="Comma-separated builtin prompt pack names to compare",
    )
    _add_backend_arguments(benchmark_packs)

    planner_benchmark = subparsers.add_parser(
        "benchmark-query-planner",
        help="Run query planner benchmark fixtures",
    )
    planner_benchmark.add_argument(
        "--fixture",
        default=str(Path("benchmarks") / "planner_fixtures"),
    )
    _add_backend_arguments(planner_benchmark)

    provider_contracts = subparsers.add_parser(
        "benchmark-provider-contracts",
        help="Run provider transport compatibility contract fixtures",
    )
    provider_contracts.add_argument(
        "--fixture",
        default=str(Path("benchmarks") / "provider_contracts"),
    )

    state_init = subparsers.add_parser(
        "state-init", help="Create a persistent memory state from a transcript"
    )
    state_init.add_argument("state_path", help="Path to the state JSON file")
    state_init.add_argument("input", help="Path to the transcript JSON file")
    _add_backend_arguments(state_init)

    state_update = subparsers.add_parser(
        "state-update", help="Append new turns into an existing persistent memory state"
    )
    state_update.add_argument("state_path", help="Path to the state JSON file")
    state_update.add_argument(
        "input", help="Path to the transcript JSON file containing new turns"
    )
    _add_backend_arguments(state_update)

    state_query = subparsers.add_parser(
        "state-query", help="Query a saved memory state"
    )
    state_query.add_argument("state_path", help="Path to the state JSON file")
    state_query.add_argument(
        "--query-type",
        required=True,
        choices=["point", "range", "theme", "active", "chapter", "evidence", "profile"],
    )
    state_query.add_argument("--when", help="Datetime for point query")
    state_query.add_argument("--start", help="Range start datetime")
    state_query.add_argument("--end", help="Range end datetime")
    state_query.add_argument(
        "--theme", help="Theme for theme evolution or topic filter"
    )
    state_query.add_argument("--entity", help="Entity filter for range query")
    state_query.add_argument("--target-id", help="Target memory id for evidence query")
    _add_query_behavior_arguments(state_query)

    state_plan_query = subparsers.add_parser(
        "state-plan-query",
        help="Plan a natural-language retrieval request against a saved memory state",
    )
    state_plan_query.add_argument("state_path", help="Path to the state JSON file")
    state_plan_query.add_argument(
        "--request", required=True, help="Natural-language request"
    )
    state_plan_query.add_argument(
        "--reference-time", help="Optional reference datetime"
    )
    state_plan_query.add_argument(
        "--mode",
        choices=["default", "conservative", "audit"],
        default="default",
        help="Planner posture",
    )
    state_plan_query.add_argument(
        "--target-id", help="Optional explicit target memory id"
    )
    _add_query_behavior_arguments(state_plan_query)

    state_ask = subparsers.add_parser(
        "state-ask",
        help="Plan and execute a natural-language retrieval request against a saved memory state",
    )
    state_ask.add_argument("state_path", help="Path to the state JSON file")
    state_ask.add_argument("--request", required=True, help="Natural-language request")
    state_ask.add_argument("--reference-time", help="Optional reference datetime")
    state_ask.add_argument(
        "--mode",
        choices=["default", "conservative", "audit"],
        default="default",
        help="Planner posture",
    )
    state_ask.add_argument("--target-id", help="Optional explicit target memory id")
    _add_query_behavior_arguments(state_ask)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    query_statuses = _parse_status_filter(getattr(args, "status_filter", None))
    include_evidence = not getattr(args, "no_evidence", False)
    detail_level = getattr(args, "detail_level", "standard")
    max_results = getattr(args, "max_results", 10)
    include_superseded = getattr(args, "include_superseded", False)
    certainty_states = _parse_certainty_states(getattr(args, "certainty_state", None))

    if args.command == "ingest":
        result = _build_pipeline_from_args(args).ingest_json_file(args.input)
        payload = result.to_dict()
        if args.output:
            Path(args.output).write_text(_json_dump(payload), encoding="utf-8")
        print(_json_dump(payload))
        return 0

    if args.command == "query":
        result = _build_pipeline_from_args(args).ingest_json_file(args.input)
        engine = result.create_query_engine()
        if args.query_type == "point":
            if not args.when:
                parser.error("--when is required for point query")
            payload = engine.point_query(
                _parse_datetime(args.when),
                include_evidence=include_evidence,
                include_superseded=include_superseded,
                detail_level=detail_level,
                max_results=max_results,
                statuses=query_statuses,
            )
        elif args.query_type == "range":
            if not args.start or not args.end:
                parser.error("--start and --end are required for range query")
            payload = engine.range_query(
                _parse_datetime(args.start),
                _parse_datetime(args.end),
                topic=args.theme,
                entity=args.entity,
                include_evidence=include_evidence,
                include_superseded=include_superseded,
                detail_level=detail_level,
                max_results=max_results,
                statuses=query_statuses,
            )
        elif args.query_type == "theme":
            if not args.theme:
                parser.error("--theme is required for theme query")
            payload = engine.theme_evolution(
                args.theme,
                include_evidence=include_evidence,
                include_superseded=include_superseded,
                detail_level=detail_level,
                max_results=max_results,
                statuses=query_statuses,
            )
        elif args.query_type == "active":
            payload = engine.active_arcs(
                query_statuses,
                include_superseded=include_superseded,
                max_results=max_results,
            )
        elif args.query_type == "profile":
            payload = engine.profile_lookup(
                subject=args.subject,
                memory_kind=MemoryKind(args.memory_kind) if args.memory_kind else None,
                certainty_states=certainty_states,
                include_superseded=include_superseded,
                max_results=max_results,
            )
        elif args.query_type == "chapter":
            if not args.start or not args.end:
                parser.error("--start and --end are required for chapter query")
            payload = engine.chapter_summary(
                _parse_datetime(args.start),
                _parse_datetime(args.end),
                include_evidence=include_evidence,
                include_superseded=include_superseded,
                detail_level=detail_level,
                max_results=max_results,
                statuses=query_statuses,
            )
        else:
            if not args.target_id:
                parser.error("--target-id is required for evidence query")
            payload = engine.evidence_trace(
                args.target_id,
                include_superseded=include_superseded,
            )

        print(_json_dump(payload))
        return 0

    if args.command == "plan-query":
        result = _build_pipeline_from_args(args).ingest_json_file(args.input)
        planner = result.create_query_planner()
        reference_time = (
            _parse_datetime(args.reference_time) if args.reference_time else None
        )
        payload = planner.plan(
            args.request,
            reference_time=reference_time,
            detail_level=detail_level,
            include_evidence=include_evidence,
            max_results=max_results,
            mode=args.mode,
            target_id=args.target_id,
        ).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "ask":
        result = _build_pipeline_from_args(args).ingest_json_file(args.input)
        planner = result.create_query_planner()
        assembler = AnswerAssembler()
        reference_time = (
            _parse_datetime(args.reference_time) if args.reference_time else None
        )
        execution = planner.plan_and_execute(
            args.request,
            reference_time=reference_time,
            detail_level=detail_level,
            include_evidence=include_evidence,
            max_results=max_results,
            mode=args.mode,
            target_id=args.target_id,
        )
        payload = execution.to_dict()
        payload["answer"] = assembler.assemble(execution).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "maintain":
        result = _build_pipeline_from_args(args).ingest_json_file(args.input)
        reference_time = (
            _parse_datetime(args.reference_time) if args.reference_time else None
        )
        payload = result.apply_maintenance(reference_time=reference_time).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "revise":
        result = _build_pipeline_from_args(args).ingest_json_file(args.input)
        resolved_target_id = _resolve_target_id(result, args.target_id)
        changes: dict[str, Any] = {}
        for field_name in ("summary", "title", "importance", "confidence"):
            value = getattr(args, field_name)
            if value is not None:
                changes[field_name] = value
        payload = result.revise_memory(
            target_id=resolved_target_id,
            revision_type=args.revision_type,
            reason=args.reason,
            changes=changes,
        ).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "benchmark":
        runner = BenchmarkRunner(_build_pipeline_from_args(args))
        fixture_path = Path(args.fixture)
        if fixture_path.is_dir():
            payload = runner.run_directory(fixture_path).to_dict()
        else:
            if args.expectations is None:
                parser.error(
                    "--expectations is required when --fixture points to a single transcript file"
                )
            payload = runner.run([fixture_path], [Path(args.expectations)]).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "benchmark-prompt-packs":
        packs = [item.strip() for item in args.prompt_packs.split(",") if item.strip()]
        runner = PromptPackBenchmarkMatrixRunner(
            lambda pack: _build_pipeline_with_prompt_pack(args, pack)
        )
        payload = runner.run_directory(args.fixture, packs).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "benchmark-query-planner":
        payload = (
            PlannerBenchmarkRunner(_build_pipeline_from_args(args))
            .run_directory(args.fixture)
            .to_dict()
        )
        print(_json_dump(payload))
        return 0

    if args.command == "benchmark-provider-contracts":
        payload = (
            ProviderContractBenchmarkRunner().run_directory(args.fixture).to_dict()
        )
        print(_json_dump(payload))
        return 0

    repository = MemoryStateRepository(_build_pipeline_from_args(args))

    if args.command == "state-init":
        payload = repository.initialize_from_transcript(
            args.state_path, args.input
        ).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "state-update":
        payload = repository.update_with_report(args.state_path, args.input).to_dict()
        print(_json_dump(payload))
        return 0

    state = repository.load(args.state_path)
    if args.command == "state-plan-query":
        planner = state.result.create_query_planner()
        reference_time = (
            _parse_datetime(args.reference_time) if args.reference_time else None
        )
        payload = planner.plan(
            args.request,
            reference_time=reference_time,
            detail_level=detail_level,
            include_evidence=include_evidence,
            max_results=max_results,
            mode=args.mode,
            target_id=args.target_id,
        ).to_dict()
        print(_json_dump(payload))
        return 0

    if args.command == "state-ask":
        planner = state.result.create_query_planner()
        assembler = AnswerAssembler()
        reference_time = (
            _parse_datetime(args.reference_time) if args.reference_time else None
        )
        execution = planner.plan_and_execute(
            args.request,
            reference_time=reference_time,
            detail_level=detail_level,
            include_evidence=include_evidence,
            max_results=max_results,
            mode=args.mode,
            target_id=args.target_id,
        )
        payload = execution.to_dict()
        payload["answer"] = assembler.assemble(execution).to_dict()
        print(_json_dump(payload))
        return 0

    engine = state.result.create_query_engine()
    if args.query_type == "point":
        if not args.when:
            parser.error("--when is required for point query")
        payload = engine.point_query(
            _parse_datetime(args.when),
            include_evidence=include_evidence,
            include_superseded=include_superseded,
            detail_level=detail_level,
            max_results=max_results,
            statuses=query_statuses,
        )
    elif args.query_type == "range":
        if not args.start or not args.end:
            parser.error("--start and --end are required for range query")
        payload = engine.range_query(
            _parse_datetime(args.start),
            _parse_datetime(args.end),
            topic=args.theme,
            entity=args.entity,
            include_evidence=include_evidence,
            include_superseded=include_superseded,
            detail_level=detail_level,
            max_results=max_results,
            statuses=query_statuses,
        )
    elif args.query_type == "theme":
        if not args.theme:
            parser.error("--theme is required for theme query")
        payload = engine.theme_evolution(
            args.theme,
            include_evidence=include_evidence,
            include_superseded=include_superseded,
            detail_level=detail_level,
            max_results=max_results,
            statuses=query_statuses,
        )
    elif args.query_type == "profile":
        payload = engine.profile_lookup(
            subject=args.subject,
            memory_kind=MemoryKind(args.memory_kind) if args.memory_kind else None,
            certainty_states=certainty_states,
            include_superseded=include_superseded,
            max_results=max_results,
        )
    elif args.query_type == "active":
        payload = engine.active_arcs(
            query_statuses,
            include_superseded=include_superseded,
            max_results=max_results,
        )
    elif args.query_type == "chapter":
        if not args.start or not args.end:
            parser.error("--start and --end are required for chapter query")
        payload = engine.chapter_summary(
            _parse_datetime(args.start),
            _parse_datetime(args.end),
            include_evidence=include_evidence,
            include_superseded=include_superseded,
            detail_level=detail_level,
            max_results=max_results,
            statuses=query_statuses,
        )
    else:
        if not args.target_id:
            parser.error("--target-id is required for evidence query")
        payload = engine.evidence_trace(
            args.target_id,
            include_superseded=include_superseded,
        )
    print(_json_dump(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
