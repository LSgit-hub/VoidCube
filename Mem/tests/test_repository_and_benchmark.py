from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from memai import (
    BenchmarkRunner,
    MemoryStateRepository,
    PlannerBenchmarkRunner,
    PromptPackBenchmarkMatrixRunner,
    ProviderContractBenchmarkRunner,
)
from memai.cli import main

TEST_DIR = Path(__file__).parent
FIXTURES_DIR = TEST_DIR.parent / "benchmarks" / "fixtures"
PLANNER_FIXTURES_DIR = TEST_DIR.parent / "benchmarks" / "planner_fixtures"
PROVIDER_CONTRACTS_DIR = TEST_DIR.parent / "benchmarks" / "provider_contracts"


def test_repository_initializes_and_updates_state(tmp_path: Path) -> None:
    repository = MemoryStateRepository()
    state_path = tmp_path / "memory-state.json"
    initial_transcript = tmp_path / "initial.json"
    update_transcript = tmp_path / "update.json"

    initial_transcript.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_001",
                        "speaker": "user",
                        "text": "Please use Chinese responses.",
                        "timestamp": "2026-03-22T09:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    update_transcript.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_002",
                        "speaker": "assistant",
                        "text": "The project must preserve evidence traces.",
                        "timestamp": "2026-03-23T10:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = repository.initialize_from_transcript(state_path, initial_transcript)
    assert state.version == 1
    assert len(state.result.turns) == 1
    assert state.result.profile_memories

    updated = repository.update_from_transcript(state_path, update_transcript)
    assert updated.version == 2
    assert len(updated.result.turns) == 2
    reloaded = repository.load(state_path)
    assert len(reloaded.result.turns) == 2
    assert reloaded.result.profile_memories


def test_benchmark_runner_handles_fixture_directory() -> None:
    result = BenchmarkRunner().run_directory(FIXTURES_DIR)
    payload = result.to_dict()
    assert payload["aggregate"]["fixtures"] >= 4
    assert payload["results"]


def test_repository_preserves_profile_conflicts_across_updates(tmp_path: Path) -> None:
    repository = MemoryStateRepository()
    state_path = tmp_path / "memory-state.json"
    initial_transcript = tmp_path / "initial-conflict.json"
    update_transcript = tmp_path / "update-conflict.json"

    initial_transcript.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_001",
                        "speaker": "user",
                        "text": "Please use Chinese responses.",
                        "timestamp": "2026-03-22T09:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    update_transcript.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_002",
                        "speaker": "user",
                        "text": "Please use English responses.",
                        "timestamp": "2026-03-23T10:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    repository.initialize_from_transcript(state_path, initial_transcript)
    updated = repository.update_from_transcript(state_path, update_transcript)

    disputed = [
        item
        for item in updated.result.profile_memories
        if item.certainty_state.value == "disputed"
    ]
    assert len(disputed) == 2
    assert all(item.conflict_refs for item in disputed)


def test_benchmark_directory_includes_new_quality_fixtures() -> None:
    payload = BenchmarkRunner().run_directory(FIXTURES_DIR).to_dict()
    fixture_names = {item["fixture"] for item in payload["results"]}

    assert "temporal_relative_revision_probe" in fixture_names
    assert "interpretation_restraint_project_stress" in fixture_names
    assert all(
        "structure_integrity" in item["metrics"]
        and "revision_precision" in item["metrics"]
        for item in payload["results"]
    )


def test_benchmark_runner_supports_richer_quality_probes(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    expectations_path = tmp_path / "expectations.json"
    transcript_path.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_001",
                        "speaker": "user",
                        "text": "2026-03-22 we decided to build the memory system project.",
                        "timestamp": "2026-03-22T09:00:00Z",
                    },
                    {
                        "turn_id": "turn_002",
                        "speaker": "assistant",
                        "text": "2026-03-23 we implemented the schema and retrieval pipeline.",
                        "timestamp": "2026-03-23T10:00:00Z",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    expectations_path.write_text(
        json.dumps(
            {
                "name": "quality_probe_fixture",
                "expected_min_events": 2,
                "expected_min_scenes": 1,
                "expected_min_arcs": 1,
                "expected_min_epochs": 1,
                "expected_topics": ["memory-system", "schema", "retrieval"],
                "required_theme_queries": ["memory-system"],
                "range_query_checks": [
                    {
                        "start": "2026-03-01",
                        "end": "2026-03-31",
                        "topic": "memory-system",
                        "min_observed": 1,
                        "min_main_arcs": 0,
                        "min_side_arcs": 1,
                    }
                ],
                "chapter_query_checks": [
                    {
                        "start": "2026-03-01",
                        "end": "2026-03-31",
                        "min_epochs": 1,
                        "min_themes": 1,
                    }
                ],
                "revision_probe": {
                    "target_selector": "event:0",
                    "revision_type": "factual_revision",
                    "reason": "Shift into a retrieval blocker",
                    "changes": {
                        "summary": "Retrieval is blocked and needs a revision pass.",
                        "topics": ["retrieval"],
                        "event_kind": "blocker",
                    },
                    "expected_revision_records_min": 4,
                    "expected_arc_topics": ["retrieval"],
                    "expected_epoch_topics": ["retrieval"],
                    "expected_open_question_contains": "retrieval",
                },
                "forbidden_summary_terms": ["personality trait"],
                "passing_threshold": 0.5,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = BenchmarkRunner().run([transcript_path], [expectations_path]).to_dict()
    result = payload["results"][0]

    assert result["passed"] is True
    assert result["metrics"]["structure_integrity"] >= 1.0
    assert result["metrics"]["evidence_integrity"] >= 1.0
    assert result["metrics"]["range_query_quality"] >= 1.0
    assert result["metrics"]["chapter_query_quality"] >= 1.0
    assert result["metrics"]["revision_precision"] >= 1.0
    assert result["metrics"]["interpretation_restraint"] >= 1.0


def test_cli_state_commands_work(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    transcript_path = FIXTURES_DIR / "sample_transcript.json"
    update_path = tmp_path / "update.json"
    update_path.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_004",
                        "speaker": "assistant",
                        "text": "2026-03-24 we refined the revision rules.",
                        "timestamp": "2026-03-24T10:00:00Z",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stream = StringIO()
    with redirect_stdout(stream):
        code = main(["state-init", str(state_path), str(transcript_path)])
    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["version"] == 1

    update_stream = StringIO()
    with redirect_stdout(update_stream):
        code = main(["state-update", str(state_path), str(update_path)])
    update_payload = json.loads(update_stream.getvalue())
    assert code == 0
    assert update_payload["state"]["version"] == 2
    assert "diff" in update_payload
    assert update_payload["diff"]["added_event_ids"]
    assert "mainline_report" in update_payload["diff"]
    assert "summary" in update_payload["diff"]["mainline_report"]

    query_stream = StringIO()
    with redirect_stdout(query_stream):
        code = main(
            [
                "state-query",
                str(state_path),
                "--query-type",
                "theme",
                "--theme",
                "memory-system",
            ]
        )
    query_payload = json.loads(query_stream.getvalue())
    assert code == 0
    assert query_payload["result_type"] == "theme_evolution"


def test_cli_benchmark_directory_outputs_aggregate() -> None:
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(["benchmark", "--fixture", str(FIXTURES_DIR)])
    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["aggregate"]["fixtures"] >= 4


def test_prompt_pack_benchmark_matrix_runner_outputs_ranking() -> None:
    runner = PromptPackBenchmarkMatrixRunner(
        lambda prompt_pack: BenchmarkRunner().pipeline
    )
    payload = runner.run_directory(
        FIXTURES_DIR,
        ["default", "conservative"],
    ).to_dict()

    assert payload["summary"]["packs"] == 2
    assert len(payload["runs"]) == 2


def test_planner_benchmark_runner_outputs_fixture_results() -> None:
    payload = (
        PlannerBenchmarkRunner()
        .run_directory(PLANNER_FIXTURES_DIR)
        .to_dict()
    )

    fixture_names = {item["fixture"] for item in payload["results"]}
    assert payload["aggregate"]["fixtures"] >= 5
    assert payload["aggregate"]["failed"] == 0
    assert "recent_changes_request" in fixture_names
    assert "theme_evolution_request" in fixture_names
    assert "stable_context_request" in fixture_names
    assert "mixed_intent_recent_and_state_request" in fixture_names
    assert "audit_trace_request" in fixture_names


def test_provider_contract_benchmark_runner_outputs_fixture_results() -> None:
    payload = (
        ProviderContractBenchmarkRunner()
        .run_directory(PROVIDER_CONTRACTS_DIR)
        .to_dict()
    )

    fixture_names = {item["fixture"] for item in payload["results"]}
    fixture_details = {item["fixture"]: item["details"] for item in payload["results"]}

    assert payload["aggregate"]["fixtures"] >= 4
    assert payload["aggregate"]["failed"] == 0
    assert "developer_role_transport_contract" in fixture_names
    assert "legacy_text_choice_transport_contract" in fixture_names
    assert "user_only_output_text_transport_contract" in fixture_names
    assert "custom_profile_file_transport_contract" in fixture_names
    assert (
        fixture_details["custom_profile_file_transport_contract"]["provider_profile"]
        == "vendor-file-profile"
    )


def test_cli_benchmark_prompt_packs_outputs_matrix() -> None:
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "benchmark-prompt-packs",
                "--fixture",
                "benchmarks/fixtures",
                "--prompt-packs",
                "default,conservative",
            ]
        )
    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["summary"]["packs"] == 2


def test_cli_benchmark_provider_contracts_outputs_aggregate() -> None:
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "benchmark-provider-contracts",
                "--fixture",
                str(PROVIDER_CONTRACTS_DIR),
            ]
        )
    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["aggregate"]["fixtures"] >= 4
    assert payload["aggregate"]["failed"] == 0


def test_cli_benchmark_query_planner_outputs_aggregate() -> None:
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "benchmark-query-planner",
                "--fixture",
                str(PLANNER_FIXTURES_DIR),
            ]
        )
    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["aggregate"]["fixtures"] >= 5
    assert payload["aggregate"]["failed"] == 0


def test_provider_contract_benchmark_runner_supports_relative_profile_files(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "profiles.json"
    contract_path = tmp_path / "custom_contract.json"
    profile_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "tmp-provider": {
                        "extends": "legacy-compatible",
                        "chat_completions_path": "/tmp/vendor/chat",
                        "system_prompt_style": "developer",
                        "response_format_style": "json_object_string",
                        "response_content_style": "choices_text",
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    contract_path.write_text(
        json.dumps(
            {
                "name": "tmp_relative_profile_contract",
                "provider_profile": "tmp-provider",
                "provider_profile_file": "profiles.json",
                "invocation": {
                    "method": "extract_events",
                    "turns": [
                        {
                            "turn_id": "turn_tmp",
                            "speaker": "user",
                            "text": "2026-03-26 we verified relative provider profile paths.",
                            "timestamp": "2026-03-26T09:00:00Z",
                        }
                    ],
                },
                "response": {
                    "choices": [
                        {
                            "text": '{"events": [{"summary": "Relative profile path verified."}]}'
                        }
                    ]
                },
                "expectations": {
                    "url_suffix": "/tmp/vendor/chat",
                    "message_roles": ["developer", "user"],
                    "response_format": "json_object_string",
                    "expected_min_events": 1,
                    "expected_summary_contains": "relative profile path",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = ProviderContractBenchmarkRunner().run([contract_path]).to_dict()
    result = payload["results"][0]

    assert payload["aggregate"]["failed"] == 0
    assert result["fixture"] == "tmp_relative_profile_contract"
    assert result["details"]["provider_profile"] == "tmp-provider"
