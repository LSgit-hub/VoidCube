"""Bounded hybrid recall across recent turns and compressed memories.

The recall engine deliberately owns retrieval and ranking only.  Memory
creation, compression, and lifecycle remain owned by ``MemoryService`` and
``Tier1ToTier2Bridge``.  Keeping recall pure makes the ranking policy easy to
test without starting HTTP services or mutating the database.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence


_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_RECENCY_MARKERS = (
    "最近",
    "上次",
    "之前",
    "过去",
    "先前",
    "recent",
    "previous",
    "last time",
    "earlier",
)
_LATIN_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "did",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "this",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "with",
    "you",
}
_CJK_STOP_TERMS = {
    "一个",
    "一下",
    "之前",
    "什么",
    "关于",
    "可以",
    "告诉",
    "做了",
    "如何",
    "我们",
    "是否",
    "曾经",
    "最近",
    "聊了",
    "用户",
    "记得",
    "过去",
    "先前",
    "上次",
    "这个",
    "那个",
}


@dataclass(frozen=True, slots=True)
class RecallPlan:
    query: str
    normalized_query: str
    terms: tuple[str, ...]
    timespan_start: str | None
    timespan_end: str | None
    memory_types: tuple[str, ...]
    topic: str | None
    recency_intent: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "terms": list(self.terms),
            "timespan_start": self.timespan_start,
            "timespan_end": self.timespan_end,
            "memory_types": list(self.memory_types),
            "topic": self.topic,
            "recency_intent": self.recency_intent,
            "method": "multilingual_hybrid",
        }


def normalize_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def build_recall_plan(
    query: str,
    *,
    memory_type: str | Sequence[str] | None = None,
    topic: str | None = None,
    timespan_start: str | None = None,
    timespan_end: str | None = None,
    now: datetime | None = None,
) -> RecallPlan:
    raw_query = str(query or "").strip()
    normalized = normalize_text(raw_query)
    wall_clock = now or datetime.now().astimezone()
    if wall_clock.tzinfo is None:
        wall_clock = wall_clock.replace(tzinfo=timezone.utc)
    start = _optional_text(timespan_start)
    end = _optional_text(timespan_end)

    if not start and ("今天" in normalized or "today" in normalized):
        day_start = wall_clock.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start.astimezone(timezone.utc).isoformat()
        end = (day_start + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    elif not start and ("昨天" in normalized or "yesterday" in normalized):
        day_end = wall_clock.replace(hour=0, minute=0, second=0, microsecond=0)
        start = (day_end - timedelta(days=1)).astimezone(timezone.utc).isoformat()
        end = day_end.astimezone(timezone.utc).isoformat()

    types: list[str] = []
    raw_types: Iterable[object]
    if isinstance(memory_type, str):
        raw_types = (memory_type,)
    else:
        raw_types = memory_type or ()
    for value in raw_types:
        candidate = normalize_text(value)
        if candidate in {"event", "scene", "arc", "epoch"} and candidate not in types:
            types.append(candidate)

    return RecallPlan(
        query=raw_query,
        normalized_query=normalized,
        terms=tuple(_extract_terms(normalized, topic=topic)),
        timespan_start=start,
        timespan_end=end,
        memory_types=tuple(types),
        topic=_optional_text(topic),
        recency_intent=any(marker in normalized for marker in _RECENCY_MARKERS),
    )


def recall_memories(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    *,
    limit: int = 5,
    candidate_limit: int = 200,
    max_context_chars: int = 3500,
    min_score: float = 0.2,
    current_session_id: str | None = None,
    include_tier1: bool = True,
    include_tier2: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a small, ranked, traceable recall set.

    Candidate selection is lexical and bounded in SQLite.  Ranking combines
    multilingual term coverage with recency and the durable importance signals
    already maintained by the memory lifecycle.
    """
    bounded_limit = max(1, min(int(limit), 50))
    bounded_candidates = max(bounded_limit, min(int(candidate_limit), 2000))
    bounded_chars = max(256, min(int(max_context_chars), 20000))
    bounded_min_score = max(0.0, min(float(min_score), 1.0))
    reference = _aware_datetime(now or datetime.now().astimezone())

    candidates: list[dict[str, Any]] = []
    if include_tier2:
        candidates.extend(
            _tier2_candidates(conn, plan, bounded_candidates, reference)
        )
    if include_tier1:
        candidates.extend(
            _tier1_candidates(
                conn,
                plan,
                bounded_candidates,
                reference,
                current_session_id=_optional_text(current_session_id),
            )
        )

    ranked, dedup_truncated = _deduplicate_and_rank(
        [
            candidate
            for candidate in candidates
            if float(candidate.get("score") or 0.0) >= bounded_min_score
        ],
        limit=bounded_limit,
    )
    selected, used_chars, budget_truncated = _apply_context_budget(
        ranked,
        limit=bounded_limit,
        max_chars=bounded_chars,
    )
    _record_tier2_accesses(conn, selected, reference)
    return {
        "results": selected,
        "count": len(selected),
        "candidate_count": len(candidates),
        "query_plan": plan.as_dict(),
        "context_chars": used_chars,
        "max_context_chars": bounded_chars,
        "min_score": bounded_min_score,
        "truncated": (
            budget_truncated
            or dedup_truncated
            or len(selected) < len(ranked)
        ),
    }


def format_recall_context(results: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for result in results:
        tier = str(result.get("tier") or "memory")
        kind = str(result.get("memory_type") or result.get("speaker") or "record")
        timestamp = str(
            result.get("timespan_start") or result.get("timestamp") or ""
        )[:10]
        title = str(result.get("title") or "Memory").strip()
        summary = str(result.get("summary") or "").strip()
        label = ":".join(part for part in (tier, kind) if part)
        date_suffix = f" {timestamp}" if timestamp else ""
        lines.append(f"- [{label}{date_suffix}] {title}: {summary}")
    if not lines:
        return ""
    return "Relevant recalled memory:\n" + "\n".join(lines)


def _extract_terms(normalized_query: str, *, topic: str | None) -> list[str]:
    weighted: dict[str, int] = {}

    def add(term: str, weight: int) -> None:
        value = normalize_text(term).strip("._-")
        if len(value) < 2 or value in _LATIN_STOP_WORDS or value in _CJK_STOP_TERMS:
            return
        weighted[value] = max(weighted.get(value, 0), weight)

    for token in _LATIN_TOKEN_RE.findall(normalized_query):
        add(token, min(len(token), 8) + 4)
    for run in _CJK_RUN_RE.findall(normalized_query):
        conceptual = run
        for stop_term in sorted(_CJK_STOP_TERMS, key=len, reverse=True):
            conceptual = conceptual.replace(stop_term, " ")
        for segment in conceptual.split():
            if len(segment) <= 8:
                add(segment, len(segment) + 8)
            for size, base_weight in ((4, 8), (3, 6), (2, 4)):
                if len(segment) < size:
                    continue
                for index in range(len(segment) - size + 1):
                    add(segment[index : index + size], base_weight)
    if topic:
        add(topic, 20)

    ordered = sorted(weighted, key=lambda item: (-weighted[item], -len(item), item))
    # A small query plan keeps SQLite predicates and scoring bounded.
    return ordered[:16]


def _tier2_candidates(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    candidate_limit: int,
    now: datetime,
) -> list[dict[str, Any]]:
    clauses = ["status = 'active'", "hidden = 0"]
    params: list[Any] = []
    searchable = ("title", "summary", "topics", "entities")
    _append_term_predicates(clauses, params, searchable, plan.terms)
    if plan.memory_types:
        placeholders = ",".join("?" for _ in plan.memory_types)
        clauses.append(f"memory_type IN ({placeholders})")
        params.extend(plan.memory_types)
    if plan.topic:
        clauses.append("topics LIKE ?")
        params.append(f"%{plan.topic}%")
    if plan.timespan_start:
        clauses.append("timespan_end >= ?")
        params.append(plan.timespan_start)
    if plan.timespan_end:
        clauses.append("timespan_start <= ?")
        params.append(plan.timespan_end)
    params.append(candidate_limit)
    rows = conn.execute(
        "SELECT memory_id, memory_type, title, summary, timespan_start, "
        "timespan_end, importance, confidence, topics, entities, source_turns, "
        "event_kind, access_count, citation_count, pinned, weight "
        "FROM compressed_memories WHERE "
        + " AND ".join(clauses)
        + " ORDER BY pinned DESC, timespan_end DESC LIMIT ?",
        params,
    ).fetchall()
    if not rows and plan.recency_intent and plan.terms:
        fallback_plan = RecallPlan(
            query=plan.query,
            normalized_query=plan.normalized_query,
            terms=(),
            timespan_start=plan.timespan_start,
            timespan_end=plan.timespan_end,
            memory_types=plan.memory_types,
            topic=plan.topic,
            recency_intent=True,
        )
        return _tier2_candidates(conn, fallback_plan, candidate_limit, now)

    results: list[dict[str, Any]] = []
    for row in rows:
        topics = _json_list(row[8])
        entities = _json_list(row[9])
        searchable_text = " ".join(
            [str(row[2] or ""), str(row[3] or ""), *topics, *entities]
        )
        lexical, matched = _lexical_score(plan, searchable_text)
        if lexical <= 0 and plan.terms:
            continue
        dynamic_weight = _dynamic_weight(
            float(row[15] or 0.0),
            event_kind=row[11],
            access_count=int(row[12] or 0),
            citation_count=int(row[13] or 0),
            pinned=bool(row[14]),
        )
        recency = _recency_score(row[5], now)
        score = 0.62 * lexical + 0.18 * dynamic_weight + 0.12 * float(
            row[6] or 0.0
        ) + 0.08 * recency
        results.append(
            {
                "id": row[0],
                "tier": "tier2",
                "memory_type": row[1],
                "title": row[2],
                "summary": row[3],
                "timespan_start": row[4],
                "timespan_end": row[5],
                "topics": topics,
                "entities": entities,
                "source_turns": _json_list(row[10]),
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "dynamic_weight": round(dynamic_weight, 6),
                    "importance": round(float(row[6] or 0.0), 6),
                    "recency": round(recency, 6),
                },
            }
        )
    return results


def _tier1_candidates(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    candidate_limit: int,
    now: datetime,
    *,
    current_session_id: str | None,
) -> list[dict[str, Any]]:
    clauses = ["compressed_to_tier2 = 0"]
    params: list[Any] = []
    _append_term_predicates(clauses, params, ("text", "tags"), plan.terms)
    if plan.timespan_start:
        clauses.append("timestamp >= ?")
        params.append(plan.timespan_start)
    if plan.timespan_end:
        clauses.append("timestamp <= ?")
        params.append(plan.timespan_end)
    params.append(candidate_limit)
    rows = conn.execute(
        "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, tags "
        "FROM turns WHERE "
        + " AND ".join(clauses)
        + " ORDER BY timestamp DESC LIMIT ?",
        params,
    ).fetchall()
    if not rows and plan.recency_intent and plan.terms:
        fallback_plan = RecallPlan(
            query=plan.query,
            normalized_query=plan.normalized_query,
            terms=(),
            timespan_start=plan.timespan_start,
            timespan_end=plan.timespan_end,
            memory_types=plan.memory_types,
            topic=plan.topic,
            recency_intent=True,
        )
        return _tier1_candidates(
            conn,
            fallback_plan,
            candidate_limit,
            now,
            current_session_id=current_session_id,
        )

    results: list[dict[str, Any]] = []
    for row in rows:
        lexical, matched = _lexical_score(plan, f"{row[3]} {' '.join(_json_list(row[6]))}")
        if lexical <= 0 and plan.terms:
            continue
        recency = _recency_score(row[4], now)
        score = 0.76 * lexical + 0.12 * float(row[5] or 0.0) + 0.12 * recency
        if current_session_id and str(row[1]) == current_session_id:
            # Same-session content is usually already in the active transcript.
            score *= 0.82
            same_session_penalty = 0.82
        else:
            same_session_penalty = 1.0
        results.append(
            {
                "id": row[0],
                "tier": "tier1",
                "speaker": row[2],
                "title": f"Conversation turn from {str(row[4])[:10]}",
                "summary": str(row[3] or ""),
                "timestamp": row[4],
                "session_id": row[1],
                "source_turns": [row[0]],
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "relevance": round(float(row[5] or 0.0), 6),
                    "recency": round(recency, 6),
                    "same_session_factor": same_session_penalty,
                },
            }
        )
    return results


def _append_term_predicates(
    clauses: list[str],
    params: list[Any],
    columns: Sequence[str],
    terms: Sequence[str],
) -> None:
    if not terms:
        return
    predicates: list[str] = []
    for term in terms:
        for column in columns:
            predicates.append(f"LOWER({column}) LIKE ?")
            params.append(f"%{term}%")
    clauses.append("(" + " OR ".join(predicates) + ")")


def _lexical_score(plan: RecallPlan, value: object) -> tuple[float, list[str]]:
    haystack = normalize_text(value)
    if not haystack:
        return 0.0, []
    matched = [term for term in plan.terms if term in haystack]
    if not plan.terms:
        return (0.25 if plan.recency_intent else 0.0), []
    total_weight = sum(max(2, min(len(term), 8)) for term in plan.terms)
    matched_weight = sum(max(2, min(len(term), 8)) for term in matched)
    coverage = matched_weight / max(total_weight, 1)
    phrase_bonus = 0.0
    if (
        plan.normalized_query
        and len(plan.normalized_query) <= 120
        and plan.normalized_query in haystack
    ):
        phrase_bonus = 0.35
    return min(1.0, coverage + phrase_bonus), matched


def _deduplicate_and_rank(
    candidates: Sequence[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item.get("score") or 0.0),
            1 if item.get("tier") == "tier2" else 0,
            str(item.get("timespan_start") or item.get("timestamp") or ""),
        ),
        reverse=True,
    )
    seen: set[str] = set()
    seen_shingles: list[set[str]] = []
    per_session: dict[str, int] = {}
    unique: list[dict[str, Any]] = []
    for index, item in enumerate(ranked):
        fingerprint = normalize_text(item.get("summary"))[:240]
        if not fingerprint or fingerprint in seen:
            continue
        shingles = _text_shingles(fingerprint)
        if any(_jaccard(shingles, previous) >= 0.88 for previous in seen_shingles):
            continue
        session_id = str(item.get("session_id") or "")
        if session_id and per_session.get(session_id, 0) >= 2:
            continue
        seen.add(fingerprint)
        seen_shingles.append(shingles)
        if session_id:
            per_session[session_id] = per_session.get(session_id, 0) + 1
        unique.append(item)
        if len(unique) >= limit:
            return unique, index < len(ranked) - 1
    return unique, False


def _apply_context_budget(
    ranked: Sequence[dict[str, Any]],
    *,
    limit: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    selected: list[dict[str, Any]] = []
    prefix_chars = len("Relevant recalled memory:\n")
    used = prefix_chars if ranked else 0
    truncated = False
    for source in ranked:
        if len(selected) >= limit or used >= max_chars:
            truncated = True
            break
        item = dict(source)
        summary = str(item.get("summary") or "").strip()
        item["summary"] = ""
        line_overhead = len(format_recall_context([item])) - prefix_chars
        if selected:
            line_overhead += 1
        remaining = max_chars - used - line_overhead
        if remaining < 80:
            truncated = True
            break
        if len(summary) > remaining:
            summary = summary[: max(1, remaining - 3)].rstrip() + "..."
            truncated = True
        item["summary"] = summary
        selected.append(item)
        used += line_overhead + len(summary)
    context_chars = len(format_recall_context(selected))
    return selected, context_chars, truncated


def _text_shingles(value: str, size: int = 3) -> set[str]:
    compact = re.sub(r"\s+", "", normalize_text(value))
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _record_tier2_accesses(
    conn: sqlite3.Connection,
    selected: Sequence[dict[str, Any]],
    now: datetime,
) -> None:
    ids = [str(item["id"]) for item in selected if item.get("tier") == "tier2"]
    if not ids:
        return
    try:
        conn.executemany(
            "UPDATE compressed_memories SET access_count = access_count + 1, "
            "last_accessed_at = ? WHERE memory_id = ?",
            [(now.isoformat(), memory_id) for memory_id in ids],
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _dynamic_weight(
    base_weight: float,
    *,
    event_kind: str | None,
    access_count: int,
    citation_count: int,
    pinned: bool,
) -> float:
    if pinned:
        return 1.0
    content_bonus = {
        "decision": 0.15,
        "correction": 0.12,
        "shift": 0.12,
        "completion": 0.08,
        "conflict": 0.08,
        "blocker": 0.06,
        "progress": 0.04,
    }.get(str(event_kind or ""), 0.0)
    access_bonus = min(math.log(access_count + 1) / math.log(101), 1.0) * 0.10
    citation_bonus = min(citation_count / 5.0, 1.0) * 0.10
    return max(0.0, min(1.0, base_weight + content_bonus + access_bonus + citation_bonus))


def _recency_score(value: object, now: datetime) -> float:
    parsed = _parse_datetime(value)
    if parsed is None:
        return 0.0
    age_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
    return math.exp(-age_days / 90.0)


def _parse_datetime(value: object) -> datetime | None:
    try:
        return _aware_datetime(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_list(value: object) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
