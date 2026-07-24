from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.tier1_to_tier2_bridge import open_memory_sqlite


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "Mem" / "benchmarks" / "recall_quality.v1.json"


def load_benchmark(path: str | Path = DEFAULT_DATASET) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("format") != "voidcube.memory.recall-benchmark":
        raise ValueError("Unsupported memory recall benchmark format")
    if not payload.get("records") or not payload.get("cases"):
        raise ValueError("Recall benchmark requires records and cases")
    return payload


def _seed_benchmark(service: MemoryService, records: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        for record in records:
            source_type = str(record["source_type"])
            memory_id = str(record["id"])
            owner_id = str(record["owner_id"])
            workspace_id = str(record["workspace_id"])
            session_id = f"benchmark-{owner_id}-{workspace_id}"
            if source_type in {"turn", "archive"}:
                conn.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, '{}')",
                    (session_id, owner_id, workspace_id, now, now),
                )
            if source_type == "turn":
                conn.execute(
                    "INSERT INTO turns "
                    "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
                    "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
                    "VALUES (?, ?, 'user', ?, ?, 1.0, 0.01, '[]', '{}', 0, ?, ?)",
                    (memory_id, session_id, record["text"], now, owner_id, workspace_id),
                )
            elif source_type == "archive":
                conn.execute(
                    "INSERT INTO turns_archive "
                    "(turn_id, session_id, speaker, text_summary, original_text, timestamp, "
                    "compressed_at, event_ids, scene_ids, owner_id, workspace_id) "
                    "VALUES (?, ?, 'user', ?, ?, ?, ?, '[]', '[]', ?, ?)",
                    (
                        memory_id,
                        session_id,
                        record["text"][:500],
                        record["text"],
                        now,
                        now,
                        owner_id,
                        workspace_id,
                    ),
                )
            elif source_type == "compressed":
                conn.execute(
                    "INSERT INTO compressed_memories "
                    "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                    "importance, confidence, topics, entities, source_turns, compressed_at, "
                    "status, weight, event_kind, owner_id, workspace_id) "
                    "VALUES (?, 'event', ?, ?, ?, ?, 0.8, 0.9, ?, '[]', '[]', ?, "
                    "'active', 0.8, 'decision', ?, ?)",
                    (
                        memory_id,
                        record["title"],
                        record["summary"],
                        now,
                        now,
                        json.dumps(record.get("topics") or [], ensure_ascii=False),
                        now,
                        owner_id,
                        workspace_id,
                    ),
                )
            elif source_type == "profile":
                conn.execute(
                    "INSERT INTO profile_memories "
                    "(memory_id, memory_kind, subject, predicate, value, summary, confidence, "
                    "certainty_state, status, valid_from, evidence_refs, source_turns, "
                    "supersedes, conflict_refs, owner_id, workspace_id, created_at, updated_at) "
                    "VALUES (?, 'preference', ?, ?, ?, ?, 0.95, 'confirmed', 'active', ?, "
                    "'[]', '[]', '[]', '[]', ?, ?, ?, ?)",
                    (
                        memory_id,
                        record["subject"],
                        record["predicate"],
                        record["value"],
                        record["summary"],
                        now,
                        owner_id,
                        workspace_id,
                        now,
                        now,
                    ),
                )
            else:
                raise ValueError(f"Unsupported benchmark source_type: {source_type}")
        conn.commit()
    finally:
        conn.close()


async def evaluate_recall_benchmark(
    dataset_path: str | Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    benchmark = load_benchmark(dataset_path)
    with tempfile.TemporaryDirectory(prefix="voidcube-memory-eval-") as temp_dir:
        service = MemoryService(
            MemoryServiceConfig(
                db_path=str(Path(temp_dir) / "memory.db"),
                recall_default_limit=5,
                recall_candidate_limit=100,
            )
        )
        _seed_benchmark(service, list(benchmark["records"]))
        details = []
        reciprocal_ranks = []
        hits = 0
        leak_cases = 0
        for case in benchmark["cases"]:
            result = await service.recall(
                RecallRequest(
                    query=case["query"],
                    memory_type=case.get("memory_type"),
                    limit=5,
                    owner_id=case["owner_id"],
                    workspace_id=case["workspace_id"],
                )
            )
            returned = [str(item["id"]) for item in result["results"]]
            expected = {str(item) for item in case["expected_ids"]}
            forbidden = {str(item) for item in case.get("forbidden_ids") or []}
            ranks = [index + 1 for index, item in enumerate(returned) if item in expected]
            hit = bool(ranks)
            leaked = bool(forbidden & set(returned))
            hits += int(hit)
            leak_cases += int(leaked)
            reciprocal_ranks.append(1.0 / min(ranks) if ranks else 0.0)
            details.append(
                {
                    "name": case["name"],
                    "returned_ids": returned,
                    "expected_ids": sorted(expected),
                    "hit": hit,
                    "scope_leak": leaked,
                }
            )

    case_count = len(details)
    metrics = {
        "recall_at_5": round(hits / case_count, 6),
        "mrr": round(sum(reciprocal_ranks) / case_count, 6),
        "scope_leakage_rate": round(leak_cases / case_count, 6),
    }
    thresholds = dict(benchmark["thresholds"])
    passed = (
        metrics["recall_at_5"] >= float(thresholds["recall_at_5"])
        and metrics["mrr"] >= float(thresholds["mrr"])
        and metrics["scope_leakage_rate"] <= float(thresholds["scope_leakage_rate"])
    )
    return {
        "dataset": str(Path(dataset_path)),
        "version": benchmark["version"],
        "case_count": case_count,
        "metrics": metrics,
        "thresholds": thresholds,
        "passed": passed,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate VoidCube memory recall quality")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(evaluate_recall_benchmark(args.dataset))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
