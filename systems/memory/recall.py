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

from systems.memory.lexical_index import search_memory_fts
from systems.memory.scope import (
    DEFAULT_OWNER_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_SCOPE_ID,
)


_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*", re.IGNORECASE)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_RECENCY_MARKERS = (
    "最近",
    "上次",
    "刚才",
    "刚刚",
    "方才",
    "之前",
    "过去",
    "先前",
    "recent",
    "previous",
    "last time",
    "earlier",
)
_IMMEDIATE_RECENCY_MARKERS = ("刚才", "刚刚", "方才")
_RECENT_CONVERSATION_PATTERNS = (
    "聊了什么",
    "讨论了什么",
    "谈了什么",
    "说了什么",
    "做了什么",
    "聊过什么",
    "讨论过什么",
    "what did we discuss",
    "what did we talk",
    "what were we discussing",
)
_CONCEPT_GROUPS = (
    ("失效", "故障", "失败", "不可用", "不工作", "坏了", "异常"),
    ("讨论", "聊到", "聊过", "聊天", "谈到", "提到"),
    ("记忆", "回忆", "历史"),
    ("保存", "记录", "记住", "沉淀"),
    ("原因", "根因", "为什么", "为何", "怎么回事"),
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
    "为什么",
    "怎么回事",
    "为何",
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
    "刚才",
    "刚刚",
    "方才",
    "这个",
    "那个",
    "和",
    "的",
    "是",
    "了",
    "吗",
    "呢",
}


@dataclass(frozen=True, slots=True)
class RecallPlan:
    query: str
    normalized_query: str
    terms: tuple[str, ...]
    concept_terms: tuple[str, ...]
    timespan_start: str | None
    timespan_end: str | None
    memory_types: tuple[str, ...]
    topic: str | None
    recency_intent: bool
    immediate_recency: bool
    intent: str

    @property
    def search_terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.terms, *self.concept_terms)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "terms": list(self.terms),
            "concept_terms": list(self.concept_terms),
            "timespan_start": self.timespan_start,
            "timespan_end": self.timespan_end,
            "memory_types": list(self.memory_types),
            "topic": self.topic,
            "recency_intent": self.recency_intent,
            "immediate_recency": self.immediate_recency,
            "intent": self.intent,
            "method": "lexical_concept_hybrid",
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
        if candidate in {"event", "scene", "arc", "epoch", "profile"} and candidate not in types:
            types.append(candidate)

    terms = tuple(_extract_terms(normalized, topic=topic))
    recency_intent = any(marker in normalized for marker in _RECENCY_MARKERS)
    intent_query = normalized.rstrip(" ?？。.!！")
    intent = (
        "recent_conversation"
        if any(intent_query.endswith(pattern) for pattern in _RECENT_CONVERSATION_PATTERNS)
        else "specific_memory"
    )
    return RecallPlan(
        query=raw_query,
        normalized_query=normalized,
        terms=terms,
        concept_terms=tuple(_expand_concepts(normalized, terms)),
        timespan_start=start,
        timespan_end=end,
        memory_types=tuple(types),
        topic=_optional_text(topic),
        recency_intent=recency_intent,
        immediate_recency=any(
            marker in normalized for marker in _IMMEDIATE_RECENCY_MARKERS
        ),
        intent=intent,
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
    owner_id: str = DEFAULT_OWNER_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    semantic_matches: dict[tuple[str, str], float] | None = None,
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
    semantic_matches = dict(semantic_matches or {})
    lexical_matches = search_memory_fts(
        conn,
        plan.search_terms,
        owner_id=owner_id,
        workspace_id=workspace_id,
        limit=bounded_candidates * 4,
    )

    candidates: list[dict[str, Any]] = []
    if (
        include_tier2
        and plan.intent != "recent_conversation"
        and not plan.immediate_recency
    ):
        candidates.extend(
            _tier2_candidates(
                conn,
                plan,
                bounded_candidates,
                reference,
                owner_id=owner_id,
                workspace_id=workspace_id,
                lexical_matches=lexical_matches,
                semantic_matches=semantic_matches,
            )
        )
        candidates.extend(
            _profile_candidates(
                conn,
                plan,
                bounded_candidates,
                reference,
                owner_id=owner_id,
                workspace_id=workspace_id,
                lexical_matches=lexical_matches,
                semantic_matches=semantic_matches,
            )
        )
    if include_tier1:
        candidates.extend(
            _tier1_candidates(
                conn,
                plan,
                bounded_candidates,
                reference,
                current_session_id=_optional_text(current_session_id),
                owner_id=owner_id,
                workspace_id=workspace_id,
                lexical_matches=lexical_matches,
                semantic_matches=semantic_matches,
            )
        )
        if not plan.immediate_recency and plan.intent != "recent_conversation":
            candidates.extend(
                _archive_candidates(
                    conn,
                    plan,
                    bounded_candidates,
                    reference,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    lexical_matches=lexical_matches,
                    semantic_matches=semantic_matches,
                )
            )

    candidates = _apply_feedback_scores(
        conn,
        candidates,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    ranked, dedup_truncated = _deduplicate_and_rank(
        [
            candidate
            for candidate in candidates
            if float(candidate.get("score") or 0.0) >= bounded_min_score
        ],
        limit=bounded_limit,
        per_session_limit=(bounded_limit if plan.intent == "recent_conversation" else 2),
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
        memory_id = str(result.get("id") or "unknown")
        score = float(result.get("score") or 0.0)
        matched = ",".join(str(item) for item in result.get("matched_terms") or [])
        evidence = result.get("evidence_refs") or result.get("source_turns") or []
        evidence_text = ",".join(str(item) for item in list(evidence)[:3])
        label = ":".join(part for part in (tier, kind) if part)
        date_suffix = f" {timestamp}" if timestamp else ""
        attributes = [f"id={memory_id}", f"score={score:.3f}"]
        if matched:
            attributes.append(f"matched={matched}")
        if evidence_text:
            attributes.append(f"evidence={evidence_text}")
        lines.append(
            f"- [{label}{date_suffix} {' '.join(attributes)}] {title}: {summary}"
        )
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
            if len(segment) <= 4:
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


def _expand_concepts(normalized_query: str, terms: Sequence[str]) -> list[str]:
    exact = set(terms)
    expanded: list[str] = []
    for group in _CONCEPT_GROUPS:
        if not any(alias in normalized_query for alias in group):
            continue
        for alias in group:
            normalized = normalize_text(alias)
            if normalized not in exact and normalized not in expanded:
                expanded.append(normalized)
    return expanded[:16]


def _tier2_candidates(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    candidate_limit: int,
    now: datetime,
    *,
    owner_id: str,
    workspace_id: str,
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    clauses = [
        "status = 'active'",
        "hidden = 0",
        "((owner_id = ? AND workspace_id = ?) OR "
        "(owner_id = ? AND workspace_id = ?))",
    ]
    params: list[Any] = [
        owner_id,
        workspace_id,
        GLOBAL_SCOPE_ID,
        GLOBAL_SCOPE_ID,
    ]
    lexical_ids = lexical_matches.get("compressed", ())
    semantic_ids = _semantic_ids(semantic_matches, "compressed")
    _append_search_predicates(
        clauses,
        params,
        id_column="memory_id",
        lexical_ids=lexical_ids,
        semantic_ids=semantic_ids,
    )
    tier2_types = tuple(item for item in plan.memory_types if item != "profile")
    if plan.memory_types and not tier2_types:
        return []
    if tier2_types:
        placeholders = ",".join("?" for _ in tier2_types)
        clauses.append(f"memory_type IN ({placeholders})")
        params.extend(tier2_types)
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
        "event_kind, access_count, citation_count, pinned, weight, "
        "identity_layer, evidence_refs "
        "FROM compressed_memories WHERE "
        + " AND ".join(clauses)
        + " ORDER BY pinned DESC, timespan_end DESC LIMIT ?",
        params,
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        topics = _json_list(row[8])
        entities = _json_list(row[9])
        searchable_text = " ".join(
            [str(row[2] or ""), str(row[3] or ""), *topics, *entities]
        )
        lexical, matched = _lexical_score(plan, searchable_text)
        semantic = float(semantic_matches.get(("compressed", str(row[0])), 0.0))
        if lexical <= 0 and plan.terms and semantic < 0.35:
            continue
        dynamic_weight = _dynamic_weight(
            float(row[15] or 0.0),
            event_kind=row[11],
            access_count=int(row[12] or 0),
            citation_count=int(row[13] or 0),
            pinned=bool(row[14]),
        )
        recency = _recency_score(row[5], now)
        score = (
            0.42 * lexical
            + 0.30 * semantic
            + 0.12 * dynamic_weight
            + 0.10 * float(row[6] or 0.0)
            + 0.06 * recency
            if semantic > 0
            else 0.62 * lexical
            + 0.18 * dynamic_weight
            + 0.12 * float(row[6] or 0.0)
            + 0.08 * recency
        )
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
                "identity_layer": row[16],
                "evidence_refs": _json_list(row[17]),
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "dynamic_weight": round(dynamic_weight, 6),
                    "importance": round(float(row[6] or 0.0), 6),
                    "recency": round(recency, 6),
                    "semantic": round(semantic, 6),
                },
            }
        )
    return results


def _profile_candidates(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    candidate_limit: int,
    now: datetime,
    *,
    owner_id: str,
    workspace_id: str,
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    if plan.memory_types and "profile" not in plan.memory_types:
        return []
    clauses = [
        "status = 'active'",
        "owner_id = ?",
        "workspace_id = ?",
    ]
    params: list[Any] = [owner_id, workspace_id]
    lexical_ids = lexical_matches.get("profile", ())
    semantic_ids = _semantic_ids(semantic_matches, "profile")
    _append_search_predicates(
        clauses,
        params,
        id_column="memory_id",
        lexical_ids=lexical_ids,
        semantic_ids=semantic_ids,
    )
    if plan.timespan_start:
        clauses.append("COALESCE(valid_to, valid_from) >= ?")
        params.append(plan.timespan_start)
    if plan.timespan_end:
        clauses.append("valid_from <= ?")
        params.append(plan.timespan_end)
    params.append(candidate_limit)
    rows = conn.execute(
        "SELECT memory_id, memory_kind, subject, predicate, value, summary, "
        "confidence, certainty_state, valid_from, valid_to, evidence_refs, "
        "source_turns FROM profile_memories WHERE "
        + " AND ".join(clauses)
        + " ORDER BY confidence DESC, valid_from DESC LIMIT ?",
        params,
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        searchable_text = " ".join(str(value or "") for value in row[2:6])
        lexical, matched = _lexical_score(plan, searchable_text)
        semantic = float(semantic_matches.get(("profile", str(row[0])), 0.0))
        if lexical <= 0 and plan.terms and semantic < 0.35:
            continue
        recency = _recency_score(row[8], now, decay_days=365.0)
        score = (
            0.46 * lexical
            + 0.32 * semantic
            + 0.16 * float(row[6] or 0.0)
            + 0.06 * recency
            if semantic > 0
            else 0.70 * lexical
            + 0.22 * float(row[6] or 0.0)
            + 0.08 * recency
        )
        results.append(
            {
                "id": row[0],
                "tier": "profile",
                "memory_type": "profile",
                "profile_kind": row[1],
                "title": f"{row[2]} {row[3]}",
                "summary": row[5] or f"{row[2]} {row[3]} {row[4]}",
                "subject": row[2],
                "predicate": row[3],
                "value": row[4],
                "certainty_state": row[7],
                "timespan_start": row[8],
                "timespan_end": row[9],
                "evidence_refs": _json_list(row[10]),
                "source_turns": _json_list(row[11]),
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "confidence": round(float(row[6] or 0.0), 6),
                    "recency": round(recency, 6),
                    "semantic": round(semantic, 6),
                },
            }
        )
    return results


def _archive_candidates(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    candidate_limit: int,
    now: datetime,
    *,
    owner_id: str,
    workspace_id: str,
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    clauses = [
        "owner_id = ?",
        "workspace_id = ?",
        "original_text IS NOT NULL",
    ]
    params: list[Any] = [owner_id, workspace_id]
    lexical_ids = lexical_matches.get("archive", ())
    semantic_ids = _semantic_ids(semantic_matches, "archive")
    _append_search_predicates(
        clauses,
        params,
        id_column="turn_id",
        lexical_ids=lexical_ids,
        semantic_ids=semantic_ids,
    )
    if plan.timespan_start:
        clauses.append("timestamp >= ?")
        params.append(plan.timespan_start)
    if plan.timespan_end:
        clauses.append("timestamp <= ?")
        params.append(plan.timespan_end)
    params.append(candidate_limit)
    rows = conn.execute(
        "SELECT turn_id, session_id, speaker, original_text, text_summary, "
        "timestamp, event_ids, scene_ids FROM turns_archive WHERE "
        + " AND ".join(clauses)
        + " ORDER BY timestamp DESC LIMIT ?",
        params,
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        text = str(row[3] or row[4] or "")
        lexical, matched = _lexical_score(plan, text)
        semantic = float(semantic_matches.get(("archive", str(row[0])), 0.0))
        if lexical <= 0 and plan.terms and semantic < 0.35:
            continue
        recency = _recency_score(row[5], now, decay_days=180.0)
        score = (
            0.48 * lexical + 0.34 * semantic + 0.06 + 0.12 * recency
            if semantic > 0
            else 0.72 * lexical + 0.08 + 0.20 * recency
        )
        results.append(
            {
                "id": row[0],
                "tier": "archive",
                "speaker": row[2],
                "title": f"Archived conversation turn from {str(row[5])[:10]}",
                "summary": text,
                "timestamp": row[5],
                "session_id": row[1],
                "source_turns": [row[0]],
                "evidence_refs": [*_json_list(row[6]), *_json_list(row[7])],
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "recency": round(recency, 6),
                    "archive_fallback": True,
                    "semantic": round(semantic, 6),
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
    owner_id: str,
    workspace_id: str,
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    clauses = [
        "compressed_to_tier2 = 0",
        "owner_id = ?",
        "workspace_id = ?",
    ]
    params: list[Any] = [owner_id, workspace_id]
    if plan.intent != "recent_conversation":
        lexical_ids = lexical_matches.get("turn", ())
        semantic_ids = _semantic_ids(semantic_matches, "turn")
        _append_search_predicates(
            clauses,
            params,
            id_column="turn_id",
            lexical_ids=lexical_ids,
            semantic_ids=semantic_ids,
        )
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
    results: list[dict[str, Any]] = []
    for row in rows:
        lexical, matched = _lexical_score(plan, f"{row[3]} {' '.join(_json_list(row[6]))}")
        semantic = float(semantic_matches.get(("turn", str(row[0])), 0.0))
        if (
            lexical <= 0
            and plan.intent != "recent_conversation"
            and semantic < 0.35
        ):
            continue
        recency = _recency_score(
            row[4],
            now,
            decay_days=(2.0 if plan.immediate_recency else 90.0),
        )
        same_session = bool(current_session_id and str(row[1]) == current_session_id)
        if plan.intent == "recent_conversation":
            score = (
                0.68
                + 0.20 * recency
                + 0.07 * float(row[5] or 0.0)
                + (0.05 if same_session else 0.0)
            )
        elif plan.immediate_recency:
            score = (
                0.50 * lexical
                + 0.42 * recency
                + 0.08 * float(row[5] or 0.0)
                + (0.05 if same_session else 0.0)
            )
        else:
            score = (
                0.50 * lexical
                + 0.34 * semantic
                + 0.08 * float(row[5] or 0.0)
                + 0.08 * recency
                if semantic > 0
                else 0.76 * lexical
                + 0.12 * float(row[5] or 0.0)
                + 0.12 * recency
            )
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
                    "same_session": same_session,
                    "semantic": round(semantic, 6),
                },
            }
        )
    return results


def _semantic_ids(
    semantic_matches: dict[tuple[str, str], float],
    source_type: str,
    *,
    min_similarity: float = 0.35,
) -> tuple[str, ...]:
    return tuple(
        memory_id
        for (candidate_type, memory_id), similarity in semantic_matches.items()
        if candidate_type == source_type and float(similarity) >= min_similarity
    )


def _append_search_predicates(
    clauses: list[str],
    params: list[Any],
    *,
    id_column: str,
    lexical_ids: Sequence[str],
    semantic_ids: Sequence[str],
) -> None:
    candidate_ids = tuple(dict.fromkeys([*lexical_ids, *semantic_ids]))
    if not candidate_ids:
        return
    clauses.append(f"{id_column} IN ({','.join('?' for _ in candidate_ids)})")
    params.extend(candidate_ids)


def _lexical_score(plan: RecallPlan, value: object) -> tuple[float, list[str]]:
    haystack = normalize_text(value)
    if not haystack:
        return 0.0, []
    exact_matched = _filter_negated_subterms(
        plan,
        haystack,
        [term for term in plan.terms if term in haystack],
    )
    concept_matched = _concept_matches(plan, haystack)
    matched = [*_maximal_terms(exact_matched), *concept_matched]
    if plan.intent == "recent_conversation":
        return 0.25, matched
    if not plan.search_terms:
        return (0.25 if plan.recency_intent else 0.0), []
    cjk_query = _meaningful_cjk_query(plan.normalized_query)
    non_cjk_terms = [
        term for term in plan.terms if not _CJK_RUN_RE.fullmatch(term)
    ]
    non_cjk_matches = [
        term for term in exact_matched if not _CJK_RUN_RE.fullmatch(term)
    ]
    cjk_coverage = _covered_cjk_chars(cjk_query, exact_matched)
    concept_coverage = _concept_coverage_chars(plan, concept_matched)
    non_cjk_total = sum(max(2, min(len(term), 8)) for term in non_cjk_terms)
    non_cjk_coverage = sum(
        max(2, min(len(term), 8)) for term in non_cjk_matches
    )
    total_weight = len(cjk_query) + non_cjk_total
    matched_weight = cjk_coverage + concept_coverage + non_cjk_coverage
    coverage = matched_weight / max(total_weight, 1)
    phrase_bonus = 0.0
    if (
        plan.normalized_query
        and len(plan.normalized_query) <= 120
        and plan.normalized_query in haystack
    ):
        phrase_bonus = 0.35
    return min(1.0, coverage + phrase_bonus), matched


def _maximal_terms(terms: Sequence[str]) -> list[str]:
    """Collapse overlapping CJK n-grams for scoring, not candidate lookup."""
    ordered = sorted(dict.fromkeys(terms), key=lambda item: (-len(item), item))
    return [
        term
        for term in ordered
        if not any(term != other and term in other for other in ordered)
    ]


def _filter_negated_subterms(
    plan: RecallPlan,
    haystack: str,
    matched: Sequence[str],
) -> list[str]:
    negated = [
        term
        for term in plan.terms
        if len(term) >= 2
        and term.startswith(("不", "未", "无", "没"))
        and term not in haystack
    ]
    return [
        term
        for term in matched
        if not any(term != phrase and term in phrase for phrase in negated)
    ]


def _meaningful_cjk_query(normalized_query: str) -> str:
    value = normalized_query
    for stop_term in sorted(_CJK_STOP_TERMS, key=len, reverse=True):
        value = value.replace(stop_term, "")
    return "".join(_CJK_RUN_RE.findall(value))


def _covered_cjk_chars(query: str, matched_terms: Sequence[str]) -> float:
    covered: set[int] = set()
    for term in matched_terms:
        if not _CJK_RUN_RE.fullmatch(term):
            continue
        start = 0
        while True:
            index = query.find(term, start)
            if index < 0:
                break
            covered.update(range(index, index + len(term)))
            start = index + 1
    return float(len(covered))


def _concept_coverage_chars(
    plan: RecallPlan,
    concept_matched: Sequence[str],
) -> float:
    covered = 0.0
    for matched in concept_matched:
        group = next(
            (group for group in _CONCEPT_GROUPS if matched in group),
            (),
        )
        query_aliases = [
            normalize_text(alias)
            for alias in group
            if normalize_text(alias) in plan.normalized_query
        ]
        if query_aliases:
            covered += max(len(alias) for alias in query_aliases) * 0.65
    return covered


def _concept_matches(plan: RecallPlan, haystack: str) -> list[str]:
    """Return at most one synonymous match per concept group."""
    matched: list[str] = []
    for group in _CONCEPT_GROUPS:
        if "为什么" in group:
            continue
        query_aliases = [
            normalize_text(alias)
            for alias in group
            if normalize_text(alias) in plan.normalized_query
        ]
        if not query_aliases:
            continue
        if any(alias in haystack for alias in query_aliases):
            continue
        alternatives = sorted(
            (
                normalize_text(alias)
                for alias in group
                if normalize_text(alias) not in query_aliases
                and normalize_text(alias) in haystack
            ),
            key=lambda item: (-len(item), item),
        )
        if alternatives:
            matched.append(alternatives[0])
    return matched


def _deduplicate_and_rank(
    candidates: Sequence[dict[str, Any]],
    *,
    limit: int,
    per_session_limit: int,
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
        if session_id and per_session.get(session_id, 0) >= per_session_limit:
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


def _apply_feedback_scores(
    conn: sqlite3.Connection,
    candidates: Sequence[dict[str, Any]],
    *,
    owner_id: str,
    workspace_id: str,
) -> list[dict[str, Any]]:
    ids = [str(item.get("id") or "") for item in candidates if item.get("id")]
    if not ids:
        return list(candidates)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT memory_id, verdict, COUNT(*) FROM recall_feedback "
        f"WHERE owner_id = ? AND workspace_id = ? AND memory_id IN ({placeholders}) "
        "GROUP BY memory_id, verdict",
        [owner_id, workspace_id, *ids],
    ).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for memory_id, verdict, count in rows:
        counts.setdefault(str(memory_id), {})[str(verdict)] = int(count)
    adjusted: list[dict[str, Any]] = []
    verdict_weights = {
        "relevant": 0.08,
        "irrelevant": -0.25,
        "outdated": -0.35,
        "incorrect": -0.50,
    }
    for source in candidates:
        item = dict(source)
        feedback = counts.get(str(item.get("id") or ""), {})
        delta = sum(verdict_weights.get(verdict, 0.0) * count for verdict, count in feedback.items())
        delta = max(-0.65, min(0.20, delta))
        item["score"] = round(
            max(0.0, min(1.0, float(item.get("score") or 0.0) + delta)),
            6,
        )
        signals = dict(item.get("signals") or {})
        signals["feedback_delta"] = round(delta, 6)
        signals["feedback_counts"] = feedback
        item["signals"] = signals
        adjusted.append(item)
    return adjusted


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
    citation_bonus = min(citation_count / 5.0, 1.0) * 0.10
    # Retrieval count is observability, not relevance feedback. Boosting on
    # access alone creates a self-reinforcing ranking loop.
    del access_count
    return max(0.0, min(1.0, base_weight + content_bonus + citation_bonus))


def _recency_score(
    value: object,
    now: datetime,
    *,
    decay_days: float = 90.0,
) -> float:
    parsed = _parse_datetime(value)
    if parsed is None:
        return 0.0
    age_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
    return math.exp(-age_days / max(decay_days, 0.01))


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
