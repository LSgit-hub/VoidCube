from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.database import open_memory_sqlite
from systems.memory.resource_contract import profile_slot_key


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
    """Seed benchmark records into the service SQLite store.

    Records may anchor their timestamps relative to "now" via optional
    ``timestamp_days_ago`` (turn/archive), ``timespan_days_ago`` (compressed),
    or ``valid_from_days_ago`` (profile) fields so temporal benchmark cases
    never go stale. Compressed records may also set ``memory_type``,
    ``importance`` and ``confidence``. Fields omitted fall back to the
    previous defaults (anchored at now).
    """
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    def days_ago(days: Any) -> str:
        return (now_dt - timedelta(days=max(0, int(days)))).isoformat()

    def month_window(months_back: int) -> tuple[str, str]:
        """Return (first-day, last-day) ISO bounds of a calendar month."""
        first_current = now_dt.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        year, month = first_current.year, first_current.month
        for _ in range(max(0, int(months_back))):
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        first = first_current.replace(year=year, month=month)
        if first.month == 12:
            next_month = first.replace(year=first.year + 1, month=1)
        else:
            next_month = first.replace(month=first.month + 1)
        last = next_month - timedelta(seconds=1)
        return first.isoformat(), last.isoformat()

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
                timestamp = days_ago(record.get("timestamp_days_ago", 0))
                conn.execute(
                    "INSERT INTO turns "
                    "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
                    "decay_factor, tags, metadata, compression_status, owner_id, workspace_id) "
                    "VALUES (?, ?, 'user', ?, ?, 1.0, 0.01, '[]', '{}', 'pending', ?, ?)",
                    (memory_id, session_id, record["text"], timestamp, owner_id, workspace_id),
                )
            elif source_type == "archive":
                timestamp = days_ago(record.get("timestamp_days_ago", 0))
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
                        timestamp,
                        now,
                        owner_id,
                        workspace_id,
                    ),
                )
            elif source_type == "compressed":
                anchor = str(record.get("timespan_anchor") or "")
                if anchor == "this_month":
                    span_start, span_end = month_window(0)
                elif anchor == "last_month":
                    span_start, span_end = month_window(1)
                else:
                    span = days_ago(record.get("timespan_days_ago", 0))
                    span_start, span_end = span, span
                memory_type = str(record.get("memory_type") or "event")
                importance = float(record.get("importance") or 0.8)
                confidence = float(record.get("confidence") or 0.9)
                conn.execute(
                    "INSERT INTO compressed_memories "
                    "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                    "importance, confidence, topics, entities, source_turns, compressed_at, "
                    "status, weight, event_kind, owner_id, workspace_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, "
                    "'active', ?, 'decision', ?, ?)",
                    (
                        memory_id,
                        memory_type,
                        record["title"],
                        record["summary"],
                        span_start,
                        span_end,
                        importance,
                        confidence,
                        json.dumps(record.get("topics") or [], ensure_ascii=False),
                        now,
                        float(record.get("weight") or importance),
                        owner_id,
                        workspace_id,
                    ),
                )
            elif source_type == "profile":
                valid_from = days_ago(record.get("valid_from_days_ago", 0))
                conn.execute(
                    "INSERT INTO profile_memories "
                    "(memory_id, memory_kind, subject, predicate, slot_key, value, summary, confidence, "
                    "certainty_state, status, valid_from, evidence_refs, source_turns, "
                    "supersedes, conflict_refs, owner_id, workspace_id, created_at, updated_at, "
                    "capture_source) VALUES (?, 'preference', ?, ?, ?, ?, ?, 0.95, "
                    "'confirmed', 'active', ?, '[]', '[]', '[]', '[]', ?, ?, ?, ?, "
                    "'benchmark')",
                    (
                        memory_id,
                        record["subject"],
                        record["predicate"],
                        profile_slot_key(record["predicate"], record["value"]),
                        record["value"],
                        record["summary"],
                        valid_from,
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
        average_precisions = []
        hits = 0
        leak_cases = 0
        forbidden_cases = 0
        for case in benchmark["cases"]:
            result = await service.recall(
                RecallRequest(
                    query=case["query"],
                    memory_type=case.get("memory_type"),
                    timespan_start=case.get("timespan_start"),
                    timespan_end=case.get("timespan_end"),
                    limit=5,
                    owner_id=case["owner_id"],
                    workspace_id=case["workspace_id"],
                )
            )
            returned = [str(item["id"]) for item in result["results"]]
            expected = {str(item) for item in case["expected_ids"]}
            scope_forbidden = {
                str(item) for item in case.get("forbidden_ids") or []
            }
            relevance_forbidden = {
                str(item) for item in case.get("relevance_forbidden_ids") or []
            }
            ranks = [index + 1 for index, item in enumerate(returned) if item in expected]
            hit = bool(ranks)
            leaked = bool(scope_forbidden & set(returned))
            forbidden_returned = bool(relevance_forbidden & set(returned))
            hits += int(hit)
            leak_cases += int(leaked)
            forbidden_cases += int(forbidden_returned)
            reciprocal_ranks.append(1.0 / min(ranks) if ranks else 0.0)
            expected_count = min(len(expected), 5)
            if expected_count:
                precision_sum = 0.0
                for index, item in enumerate(returned):
                    if item not in expected:
                        continue
                    hits_in_top = sum(
                        1 for r in returned[: index + 1] if r in expected
                    )
                    precision_sum += hits_in_top / (index + 1)
                average_precisions.append(precision_sum / expected_count)
            else:
                average_precisions.append(0.0)
            details.append(
                {
                    "name": case["name"],
                    "returned_ids": returned,
                    "expected_ids": sorted(expected),
                    "hit": hit,
                    "scope_leak": leaked,
                    "forbidden_returned": forbidden_returned,
                }
            )

    case_count = len(details)
    metrics = {
        "recall_at_5": round(hits / case_count, 6),
        "mrr": round(sum(reciprocal_ranks) / case_count, 6),
        "map_at_5": round(sum(average_precisions) / case_count, 6),
        "scope_leakage_rate": round(leak_cases / case_count, 6),
        "forbidden_return_rate": round(forbidden_cases / case_count, 6),
    }
    thresholds = dict(benchmark["thresholds"])
    passed_checks = [
        metrics["recall_at_5"] >= float(thresholds["recall_at_5"]),
        metrics["mrr"] >= float(thresholds["mrr"]),
        metrics["scope_leakage_rate"] <= float(thresholds["scope_leakage_rate"]),
    ]
    if "map_at_5" in thresholds:
        passed_checks.append(metrics["map_at_5"] >= float(thresholds["map_at_5"]))
    if "forbidden_return_rate" in thresholds:
        passed_checks.append(
            metrics["forbidden_return_rate"]
            <= float(thresholds["forbidden_return_rate"])
        )
    passed = all(passed_checks)
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
