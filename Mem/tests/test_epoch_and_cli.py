from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from memai import ChroniclePipeline
from memai.cli import main

TEST_DIR = Path(__file__).parent
FIXTURES_DIR = TEST_DIR.parent / "benchmarks" / "fixtures"


def test_pipeline_builds_epochs() -> None:
    result = ChroniclePipeline().ingest_dicts(
        [
            {
                "turn_id": "turn_001",
                "speaker": "user",
                "text": "2026-01-03 we decided to build the memory system.",
                "timestamp": "2026-01-03T10:00:00Z",
            },
            {
                "turn_id": "turn_002",
                "speaker": "assistant",
                "text": "2026-02-10 we implemented the schema and retrieval pipeline.",
                "timestamp": "2026-02-10T10:00:00Z",
            },
            {
                "turn_id": "turn_003",
                "speaker": "user",
                "text": "2026-03-12 we changed direction and refined the revision rules.",
                "timestamp": "2026-03-12T10:00:00Z",
            },
        ]
    )

    assert result.epochs
    assert result.epochs[0].child_ids


def test_cli_ingest_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(["ingest", str(fixture), "--output", str(output)])

    assert code == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["events"]
    assert payload["epochs"]


def test_cli_theme_query_prints_json() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            ["query", str(fixture), "--query-type", "theme", "--theme", "memory-system"]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["result_type"] == "theme_evolution"


def test_cli_query_supports_detail_level_and_evidence_flags() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "query",
                str(fixture),
                "--query-type",
                "range",
                "--start",
                "2026-03-01",
                "--end",
                "2026-03-31",
                "--theme",
                "memory-system",
                "--detail-level",
                "brief",
                "--max-results",
                "1",
                "--no-evidence",
            ]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["result_type"] == "range_summary"
    assert payload["detail_level"] == "brief"
    assert len(payload["observed"]) <= 1
    assert payload["evidence_refs"] == []


def test_cli_chapter_query_prints_epochs() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "query",
                str(fixture),
                "--query-type",
                "chapter",
                "--start",
                "2026-03-01",
                "--end",
                "2026-03-31",
            ]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["result_type"] == "chapter_summary"


def test_cli_maintain_returns_revision_records() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "maintain",
                str(fixture),
                "--reference-time",
                "2027-03-31T00:00:00Z",
            ]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert "revision_records" in payload


def test_cli_revise_returns_superseding_event() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    revise_stream = StringIO()
    with redirect_stdout(revise_stream):
        code = main(
            [
                "revise",
                str(fixture),
                "--target-id",
                "event:0",
                "--revision-type",
                "factual_revision",
                "--reason",
                "Need a more formal summary",
                "--summary",
                "今天我们正式决定将项目定位为时间优先的长期记忆系统。",
            ]
        )

    payload = json.loads(revise_stream.getvalue())
    assert code == 0
    assert payload["revision_records"][0]["target_old_id"].startswith("event_")


def test_cli_profile_query_prints_profile_memories(tmp_path: Path) -> None:
    fixture = tmp_path / "profile_transcript.json"
    fixture.write_text(
        json.dumps(
            {
                "turns": [
                    {
                        "turn_id": "turn_001",
                        "speaker": "user",
                        "text": "Please use Chinese responses.",
                        "timestamp": "2026-03-22T09:00:00Z",
                    },
                    {
                        "turn_id": "turn_002",
                        "speaker": "assistant",
                        "text": "The project must preserve evidence traces.",
                        "timestamp": "2026-03-22T10:00:00Z",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "query",
                str(fixture),
                "--query-type",
                "profile",
                "--subject",
                "user",
            ]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["result_type"] == "profile_lookup"
    assert payload["items"]


def test_cli_plan_query_outputs_structured_plan() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "plan-query",
                str(fixture),
                "--request",
                "What changed in the project this month?",
                "--reference-time",
                "2026-03-31T00:00:00Z",
            ]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["intent"] == "summarize_recent_changes"
    assert payload["steps"][0]["step_type"] == "range_query"


def test_cli_ask_executes_plan_and_returns_artifacts() -> None:
    fixture = FIXTURES_DIR / "sample_transcript.json"
    stream = StringIO()
    with redirect_stdout(stream):
        code = main(
            [
                "ask",
                str(fixture),
                "--request",
                "How has memory-system evolved so far?",
                "--reference-time",
                "2026-03-31T00:00:00Z",
            ]
        )

    payload = json.loads(stream.getvalue())
    assert code == 0
    assert payload["plan"]["intent"] == "trace_theme"
    assert "theme_evolution" in payload["artifacts"]
    assert payload["answer"]["strategy"] == "theme_first"
    assert payload["answer"]["summary"]
    assert "blockers" in payload["answer"]
    assert "observed" in payload["answer"]
