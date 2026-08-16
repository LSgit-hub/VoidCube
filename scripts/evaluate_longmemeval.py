"""Evaluate memory recall against a LongMemEval-schema benchmark.

LongMemEval (arXiv 2410.10813) is the de-facto standard long-term memory
benchmark for chat assistants. Its instances are attribute-controlled
conversation histories with questions; evidence turns are flagged
``has_answer: true``, enabling **turn-level memory recall** evaluation.

This harness consumes the same JSON schema:

.. code-block:: json
   {
     "question_id": "...",
     "question_type": "single-session-user|single-session-assistant|"
                      "single-session-preference|multi-session|"
                      "temporal-reasoning|knowledge-update",
     "question": "...",
     "answer": "...",
     "haystack_session_ids": ["s0", "s1", ...],
     "haystack_dates": ["ISO", ...],          // absolute (real data)
     "haystack_sessions": [ [ {role, content}, ... ], ... ],
     "answer_session_ids": [...],
     // optional for self-contained time-robust subsets:
     "date_specs": [ {"offset_days": N} | {"calendar": "last_month"}, ... ]
   }

For each sampled question we seed the history into a fresh MemoryService
database as Tier 1 turns, run ``/recall`` with the question text, and measure
whether the ``has_answer`` turns are retrieved (recall@5 / MRR / MAP@5).
Abstention questions (ids ending ``_abs``) have no ``has_answer`` turn and are
reported as informational (they require LLM judgment, not retrieval alone).

Usage::

    python scripts/evaluate_longmemeval.py \
        --dataset Mem/benchmarks/longmemeval_zh.v1.json \
        --limit-per-category 3 --output longmemeval_result.json
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# Running as a script puts only ``scripts/`` on sys.path; add the repo root so
# first-party top-level packages (VoidCube_app, systems, agent) resolve.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.database import open_memory_sqlite


DEFAULT_DATASET = REPO_ROOT / "Mem" / "benchmarks" / "longmemeval_zh.v1.json"

CATEGORIES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)


def load_dataset(path: str | Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    instances = payload.get("instances") or payload
    if not isinstance(instances, list) or not instances:
        raise ValueError("LongMemEval dataset requires a non-empty instances list")
    return [instance for instance in instances if isinstance(instance, dict)]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def resolve_date_spec(spec: Any, now: datetime) -> datetime:
    """Convert a date spec (or ISO string) to an aware datetime at run time.

    Real LongMemEval data uses absolute ``haystack_dates`` (pass through).
    Self-contained subsets use relative specs so they never go stale:
      ``{"offset_days": N}`` or ``{"calendar": "today|yesterday|this_week|
      last_week|this_month|last_month"}``.
    """
    if isinstance(spec, str):
        value = datetime.fromisoformat(spec.replace("Z", "+00:00"))
        return value.astimezone(timezone.utc)
    if not isinstance(spec, dict):
        return now
    if "offset_days" in spec:
        return (now - timedelta(days=max(0, int(spec["offset_days"])))).astimezone(timezone.utc)
    calendar = str(spec.get("calendar") or "")
    if calendar == "today":
        return now.astimezone(timezone.utc)
    if calendar == "yesterday":
        return (now - timedelta(days=1)).astimezone(timezone.utc)
    if calendar == "this_week":
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        candidate = now - timedelta(days=1)
        return max(monday, candidate).astimezone(timezone.utc)
    if calendar == "last_week":
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        return (monday - timedelta(days=3)).astimezone(timezone.utc)
    if calendar == "this_month":
        first = now.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
        candidate = now - timedelta(days=2)
        return max(first, candidate).astimezone(timezone.utc)
    if calendar == "last_month":
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        year, month = first.year, first.month
        month -= 1
        if month < 1:
            month = 12
            year -= 1
        return first.replace(year=year, month=month, day=15, hour=12).astimezone(timezone.utc)
    return now


def _session_dates(instance: dict[str, Any], now: datetime) -> list[datetime]:
    specs = instance.get("date_specs") or instance.get("haystack_dates") or []
    if not specs:
        base = now - timedelta(days=len(instance.get("haystack_sessions") or []) * 2)
        return [base + timedelta(days=2 * i) for i in range(len(instance.get("haystack_sessions") or []))]
    return [resolve_date_spec(spec, now) for spec in specs]


def seed_instance(
    service: MemoryService,
    instance: dict[str, Any],
    *,
    instance_index: int,
    now: datetime,
) -> set[str]:
    """Insert the instance history as Tier 1 turns; return evidence turn ids."""
    sessions = instance.get("haystack_sessions") or []
    session_ids = instance.get("haystack_session_ids") or [
        f"q{instance_index}-s{i}" for i in range(len(sessions))
    ]
    dates = _session_dates(instance, now)
    if len(dates) < len(sessions):
        dates = dates + [dates[-1]] * (len(sessions) - len(dates))
    evidence_ids: set[str] = set()
    conn = open_memory_sqlite(service._db_path)
    try:
        for session_index, session in enumerate(sessions):
            session_id = str(session_ids[session_index]) if session_index < len(session_ids) else f"q{instance_index}-s{session_index}"
            session_stamp = dates[session_index].astimezone(timezone.utc)
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
                "VALUES (?, 'local-user', 'default', ?, ?, '{}')",
                (session_id, _iso(session_stamp), _iso(session_stamp)),
            )
            for turn_index, turn in enumerate(session):
                if not isinstance(turn, dict):
                    continue
                role = str(turn.get("role") or "user")
                content = str(turn.get("content") or "")
                timestamp = session_stamp + timedelta(seconds=turn_index)
                turn_id = f"q{instance_index}-s{session_index}-t{turn_index}"
                speaker = "user" if role == "user" else "agent"
                conn.execute(
                    "INSERT INTO turns "
                    "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
                    "decay_factor, tags, metadata, compression_status, owner_id, workspace_id, "
                    "memory_domain) VALUES (?, ?, ?, ?, ?, 1.0, 0.01, '[]', '{}', 'pending', "
                    "'local-user', 'default', 'agent_interaction')",
                    (turn_id, session_id, speaker, content, _iso(timestamp)),
                )
                if turn.get("has_answer"):
                    evidence_ids.add(turn_id)
        conn.commit()
    finally:
        conn.close()
    return evidence_ids


def _metric_contribs(returned_ids: list[str], evidence: set[str]) -> tuple[bool, float, float]:
    ranks = [index + 1 for index, item in enumerate(returned_ids) if item in evidence]
    hit = bool(ranks)
    mrr = 1.0 / min(ranks) if ranks else 0.0
    evidence_count = len(evidence)
    if evidence_count:
        precision_sum = 0.0
        for index, item in enumerate(returned_ids):
            if item not in evidence:
                continue
            hits_in_top = sum(1 for r in returned_ids[: index + 1] if r in evidence)
            precision_sum += hits_in_top / (index + 1)
        map_contrib = precision_sum / min(evidence_count, 5)
    else:
        map_contrib = 0.0
    return hit, mrr, map_contrib


def _semantic_config_from_overrides(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
):
    """Build an explicit SemanticIndexConfig from CLI embedding overrides.

    Returns None when no embedding override is requested, in which case the
    service uses its configured default (the local CharNgramEmbedder, or any
    provider set in ~/.VoidCube/config.yaml).
    """
    if not (provider or model or base_url):
        return None
    from systems.memory.semantic_index import SemanticIndexConfig

    return SemanticIndexConfig(
        enabled=True,
        provider=(provider or "openai").strip() or "openai",
        model=(model or "").strip(),
        base_url=(base_url or "").strip().rstrip("/"),
        api_key=(
            os.environ.get(api_key_env, "") if api_key_env else ""
        ),
        dimensions=None if (provider or base_url) else 256,
    )


async def evaluate_longmemeval(
    dataset_path: str | Path = DEFAULT_DATASET,
    *,
    limit_per_category: int = 3,
    output: str | Path | None = None,
    semantic_provider: str | None = None,
    semantic_model: str | None = None,
    semantic_base_url: str | None = None,
    semantic_api_key_env: str | None = None,
) -> dict[str, Any]:
    instances = load_dataset(dataset_path)
    now = datetime.now(timezone.utc)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for instance in instances:
        qtype = str(instance.get("question_type") or "single-session-user")
        by_category.setdefault(qtype, []).append(instance)
    for qtype in CATEGORIES:
        by_category.setdefault(qtype, [])

    sampled: list[dict[str, Any]] = []
    for qtype in [*CATEGORIES, "abstention"]:
        pool = by_category[qtype]
        if pool:
            sampled.extend(pool[: max(1, int(limit_per_category))])

    semantic_config = _semantic_config_from_overrides(
        semantic_provider,
        semantic_model,
        semantic_base_url,
        semantic_api_key_env,
    )
    details: list[dict[str, Any]] = []
    hits = 0
    mrr_sum = 0.0
    map_sum = 0.0
    abstention_counts: list[int] = []
    with tempfile.TemporaryDirectory(prefix="voidcube-longmemeval-") as temp_dir:
        for index, instance in enumerate(sampled):
            service = MemoryService(
                MemoryServiceConfig(
                    db_path=str(Path(temp_dir) / f"q{index}.db"),
                    recall_default_limit=5,
                    recall_candidate_limit=100,
                )
            )
            if semantic_config is not None:
                from systems.memory.semantic_index import SemanticMemoryIndex

                service._semantic_index = SemanticMemoryIndex(
                    service._db_path, semantic_config
                )
            evidence = seed_instance(service, instance, instance_index=index, now=now)
            # Build the semantic index for this instance's seeded turns so the
            # recall can use embedding similarity in addition to lexical match.
            try:
                service._semantic_index.index_pending(limit=10000)
            except Exception:
                pass
            result = await service.recall(
                RecallRequest(
                    query=instance["question"],
                    limit=5,
                    owner_id="local-user",
                    workspace_id="default",
                )
            )
            returned = [str(item["id"]) for item in result["results"]]
            hit, mrr_contrib, map_contrib = _metric_contribs(returned, evidence)
            is_abstention = str(instance.get("question_id") or "").endswith("_abs")
            details.append(
                {
                    "question_id": instance.get("question_id", index),
                    "question_type": instance.get("question_type", ""),
                    "question": instance["question"],
                    "abstention": is_abstention,
                    "evidence_count": len(evidence),
                    "returned_ids": returned,
                    "hit": hit,
                }
            )
            if is_abstention:
                abstention_counts.append(len(returned))
                continue
            hits += int(hit)
            mrr_sum += mrr_contrib
            map_sum += map_contrib

    answerable = [d for d in details if not d["abstention"]]
    case_count = len(answerable) or 1
    by_category_metrics: dict[str, dict[str, Any]] = {}
    for qtype in CATEGORIES:
        subset = [d for d in answerable if d["question_type"] == qtype]
        if not subset:
            continue
        cat_hits = sum(1 for d in subset if d["hit"])
        by_category_metrics[qtype] = {
            "case_count": len(subset),
            "recall_at_5": round(cat_hits / len(subset), 6),
        }

    metrics = {
        "recall_at_5": round(hits / case_count, 6),
        "mrr": round(mrr_sum / case_count, 6),
        "map_at_5": round(map_sum / case_count, 6),
        "abstention_avg_returned": (
            round(sum(abstention_counts) / len(abstention_counts), 4)
            if abstention_counts
            else None
        ),
        "abstention_false_positive_rate": (
            round(sum(count > 0 for count in abstention_counts) / len(abstention_counts), 6)
            if abstention_counts
            else None
        ),
        "abstention_empty_rate": (
            round(sum(count == 0 for count in abstention_counts) / len(abstention_counts), 6)
            if abstention_counts
            else None
        ),
    }
    report = {
        "dataset": str(Path(dataset_path)),
        "instance_count": len(sampled),
        "case_count": case_count,
        "abstention_count": len(abstention_counts),
        "metrics": metrics,
        "by_category": by_category_metrics,
        "details": details,
    }
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate LongMemEval-style memory recall")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit-per-category", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--semantic-provider", help="embedding provider (e.g. openai, ollama)")
    parser.add_argument("--semantic-model", help="embedding model id (e.g. text-embedding-3-small)")
    parser.add_argument("--semantic-base-url", help="OpenAI-compatible /embeddings base URL")
    parser.add_argument("--semantic-api-key-env", help="env var holding the API key")
    args = parser.parse_args()
    report = asyncio.run(
        evaluate_longmemeval(
            args.dataset,
            limit_per_category=args.limit_per_category,
            output=args.output,
            semantic_provider=args.semantic_provider,
            semantic_model=args.semantic_model,
            semantic_base_url=args.semantic_base_url,
            semantic_api_key_env=args.semantic_api_key_env,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
