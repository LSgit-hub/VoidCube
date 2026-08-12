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
from typing import Any, Iterable, Mapping, Sequence

from systems.memory.lexical_index import search_memory_fts
from systems.memory.ranking_policy import compute_dynamic_weight
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
# Queries about the *current* state of something ("现在用什么", "目前如何").
# For these, recency matters more than for historical/past queries, because the
# latest statement about a topic supersedes older ones (knowledge updates).
_CURRENT_STATE_MARKERS = (
    "最新",
    "最近一次",
    "现在",
    "目前",
    "当前",
    "如今",
    "眼下",
    "now",
    "currently",
    "nowadays",
    "at present",
)
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
_IDENTITY_QUERY_PATTERNS = (
    "你是谁",
    "你叫什么",
    "你还记得自己",
    "你记得自己",
    "你记得你是谁",
    "我们是谁",
    "你记得锚点",
    "who are you",
    "what is your name",
    "what's your name",
    "do you remember who you are",
    "do you remember yourself",
)
_IDENTITY_TOPIC_MARKERS = (
    "你的身份",
    "自身身份",
    "身份连续",
    "身份历史",
    "voidcube identity",
    "persistent identity",
    "identity continuity",
)
_IDENTITY_CONCEPT_TERMS = (
    "身份",
    "星子",
    "小星",
    "voidcube",
    "锚点",
    "信任",
    "identity",
    "self",
)
_FOUNDING_IDENTITY_PRIORITY = {
    "identity-founding-purpose": 0.08,
    "identity-founding-trust": 0.07,
    "identity-founding-values": 0.06,
    "identity-founding-architecture": 0.05,
    "identity-founding-vision": 0.04,
}
_CONCEPT_GROUPS = (
    ("失效", "故障", "失败", "不可用", "不工作", "坏了", "异常"),
    ("讨论", "聊到", "聊过", "聊天", "谈到", "提到"),
    ("记忆", "回忆", "历史"),
    ("保存", "记录", "记住", "沉淀"),
    ("原因", "根因", "为什么", "为何", "怎么回事"),
    ("名字", "姓名", "称呼", "叫什么", "name", "called"),
    ("偏好", "喜欢", "首选", "prefer", "preference"),
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
    temporal_intent: str = "none"
    as_of: str | None = None
    current_state_intent: bool = False

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
            "temporal_intent": self.temporal_intent,
            "as_of": self.as_of,
            "current_state_intent": self.current_state_intent,
            "method": "lexical_concept_hybrid",
        }


def normalize_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _contains_current_state_marker(normalized: str) -> bool:
    """Match Chinese markers as phrases and English markers as whole words."""
    for marker in _CURRENT_STATE_MARKERS:
        if re.search(r"[a-z]", marker):
            if re.search(rf"\b{re.escape(marker)}\b", normalized):
                return True
        elif marker in normalized:
            return True
    return False


def build_recall_plan(
    query: str,
    *,
    memory_type: str | Sequence[str] | None = None,
    topic: str | None = None,
    timespan_start: str | None = None,
    timespan_end: str | None = None,
    as_of: str | None = None,
    now: datetime | None = None,
) -> RecallPlan:
    raw_query = str(query or "").strip()
    normalized = normalize_text(raw_query)
    wall_clock = now or datetime.now().astimezone()
    if wall_clock.tzinfo is None:
        wall_clock = wall_clock.replace(tzinfo=timezone.utc)
    start = _optional_text(timespan_start)
    end = _optional_text(timespan_end)

    temporal_intent = "explicit" if (start or end) else "none"
    if not start and not end:
        resolved_start, resolved_end, resolved_intent = _resolve_temporal_scope(
            normalized, wall_clock
        )
        start = resolved_start
        end = resolved_end
        temporal_intent = resolved_intent

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

    recency_intent = any(marker in normalized for marker in _RECENCY_MARKERS)
    intent_query = normalized.rstrip(" ?？。.!！")
    if _is_identity_query(intent_query):
        intent = "identity"
    elif any(
        intent_query.endswith(pattern) for pattern in _RECENT_CONVERSATION_PATTERNS
    ):
        intent = "recent_conversation"
    else:
        intent = "specific_memory"
    terms = (
        (() if topic is None else tuple(_extract_terms("", topic=topic)))
        if intent == "identity"
        else tuple(_extract_terms(normalized, topic=topic))
    )
    concept_terms = (
        _IDENTITY_CONCEPT_TERMS
        if intent == "identity"
        else tuple(_expand_concepts(normalized, terms))
    )
    return RecallPlan(
        query=raw_query,
        normalized_query=normalized,
        terms=terms,
        concept_terms=tuple(concept_terms),
        timespan_start=start,
        timespan_end=end,
        memory_types=tuple(types),
        topic=_optional_text(topic),
        recency_intent=recency_intent,
        immediate_recency=any(
            marker in normalized for marker in _IMMEDIATE_RECENCY_MARKERS
        ),
        intent=intent,
        temporal_intent=temporal_intent,
        as_of=_optional_text(as_of),
        current_state_intent=_contains_current_state_marker(normalized),
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
    source_domains: Sequence[str] = ("agent_interaction",),
    semantic_matches: dict[tuple[str, str], float] | None = None,
    record_filter: Mapping[str, Sequence[str]] | None = None,
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
        source_domains=source_domains,
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
                source_domains=source_domains,
                lexical_matches=lexical_matches,
                semantic_matches=semantic_matches,
                record_filter=record_filter,
            )
        )
        if plan.intent != "identity":
            candidates.extend(
                _profile_candidates(
                    conn,
                    plan,
                    bounded_candidates,
                    reference,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    source_domains=source_domains,
                    lexical_matches=lexical_matches,
                    semantic_matches=semantic_matches,
                    record_filter=record_filter,
                )
            )
    if include_tier1 and plan.intent != "identity":
        candidates.extend(
            _tier1_candidates(
                conn,
                plan,
                bounded_candidates,
                reference,
                current_session_id=_optional_text(current_session_id),
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_domains=source_domains,
                lexical_matches=lexical_matches,
                semantic_matches=semantic_matches,
                record_filter=record_filter,
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
                    source_domains=source_domains,
                    lexical_matches=lexical_matches,
                    semantic_matches=semantic_matches,
                    record_filter=record_filter,
                )
            )
    if include_tier2 and plan.intent == "specific_memory":
        existing_ids = {str(item.get("id") or "") for item in candidates}
        candidates.extend(
            _graph_candidates(
                conn,
                plan,
                bounded_candidates,
                reference,
                owner_id=owner_id,
                workspace_id=workspace_id,
                source_domains=source_domains,
                existing_ids=existing_ids,
            )
        )

    candidates = _apply_feedback_scores(
        conn,
        candidates,
        owner_id=owner_id,
        workspace_id=workspace_id,
        source_domains=source_domains,
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
    _record_tier2_accesses(
        conn,
        selected,
        reference,
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
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


def merge_recall_results(
    result_sets: Sequence[Sequence[dict[str, Any]]],
    *,
    limit: int,
    max_context_chars: int,
    per_session_limit: int = 2,
) -> dict[str, Any]:
    """Rank and budget already-scored recall results from multiple sources."""
    candidates = [dict(item) for items in result_sets for item in items]
    ranked, dedup_truncated = _deduplicate_and_rank(
        candidates,
        limit=max(1, min(int(limit), 50)),
        per_session_limit=max(1, min(int(per_session_limit), 50)),
    )
    selected, context_chars, budget_truncated = _apply_context_budget(
        ranked,
        limit=max(1, min(int(limit), 50)),
        max_chars=max(256, min(int(max_context_chars), 20000)),
    )
    return {
        "results": selected,
        "count": len(selected),
        "context_chars": context_chars,
        "truncated": dedup_truncated or budget_truncated,
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
        promotion_ref = str(result.get("promotion_ref_id") or "").strip()
        if promotion_ref:
            attributes.append(f"promotion={promotion_ref}")
            attributes.append(
                "source="
                + ":".join(
                    part
                    for part in (
                        str(result.get("source_memory_domain") or ""),
                        str(result.get("source_memory_id") or ""),
                    )
                    if part
                )
            )
        lines.append(
            f"- [{label}{date_suffix} {' '.join(attributes)}] {title}: {summary}"
        )
    if not lines:
        return ""
    return "Relevant recalled memory:\n" + "\n".join(lines)


_IMPLICIT_RECENT_MARKERS = ("最近", "近期", "recent")
_IMPLICIT_HISTORY_MARKERS = ("历史", "过去", "historically", "history")
_ISO_DATE_RE = re.compile(r"(20\d{2})[-/](\d{1,2})(?:[-/](\d{1,2}))?")


def _resolve_temporal_scope(
    normalized_query: str,
    anchor: datetime,
) -> tuple[str | None, str | None, str]:
    """Resolve an explicit or implicit temporal window from the query text.

    Returns ``(start, end, temporal_intent)``:
      - ``start``/``end`` are ISO-8601 strings, or ``None`` when unbounded.
      - ``temporal_intent`` is ``"explicit"`` (calendar phrase or ISO date —
        a temporal-fit ranking bonus applies), ``"implicit"`` (vague recent /
        history phrasing — carried for audit, no ranking bonus), or
        ``"none"``.

    This is the runtime port of ``memai.QueryPlanner._resolve_temporal_scope``
    (Mem/src/memai/query_planner.py) so time-first recall semantics reach the
    live ``/recall`` path instead of living only in the memai CLI.
    """
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    utc = timezone.utc

    if "今天" in normalized_query or "today" in normalized_query:
        day_start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start.astimezone(utc).isoformat()
        end = (day_start + timedelta(days=1)).astimezone(utc).isoformat()
        return start, end, "explicit"
    if "昨天" in normalized_query or "yesterday" in normalized_query:
        day_end = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        start = (day_end - timedelta(days=1)).astimezone(utc).isoformat()
        end = day_end.astimezone(utc).isoformat()
        return start, end, "explicit"
    if any(marker in normalized_query for marker in ("本周", "这周", "this week")):
        week_start = (anchor - timedelta(days=anchor.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = week_start.astimezone(utc).isoformat()
        end = (week_start + timedelta(days=7)).astimezone(utc).isoformat()
        return start, end, "explicit"
    if any(marker in normalized_query for marker in ("上周", "last week")):
        week_end = (anchor - timedelta(days=anchor.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = (week_end - timedelta(days=7)).astimezone(utc).isoformat()
        end = week_end.astimezone(utc).isoformat()
        return start, end, "explicit"
    if any(marker in normalized_query for marker in ("本月", "这个月", "this month")):
        month_start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)
        start = month_start.astimezone(utc).isoformat()
        end = (month_end - timedelta(seconds=1)).astimezone(utc).isoformat()
        return start, end, "explicit"
    if any(marker in normalized_query for marker in ("上个月", "上月", "last month")):
        month_start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if month_start.month == 1:
            month_start = month_start.replace(year=month_start.year - 1, month=12)
        else:
            month_start = month_start.replace(month=month_start.month - 1)
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)
        start = month_start.astimezone(utc).isoformat()
        end = (month_end - timedelta(seconds=1)).astimezone(utc).isoformat()
        return start, end, "explicit"

    iso_match = _ISO_DATE_RE.search(normalized_query)
    if iso_match:
        year, month = int(iso_match.group(1)), int(iso_match.group(2))
        day_text = iso_match.group(3)
        base = datetime(year, month, int(day_text) if day_text else 1, tzinfo=utc)
        if day_text:
            start = base.isoformat()
            end = (base + timedelta(days=1) - timedelta(seconds=1)).isoformat()
        else:
            if month == 12:
                month_end = base.replace(year=year + 1, month=1)
            else:
                month_end = base.replace(month=month + 1)
            start = base.isoformat()
            end = (month_end - timedelta(seconds=1)).isoformat()
        return start, end, "explicit"

    if any(marker in normalized_query for marker in _IMPLICIT_RECENT_MARKERS):
        start = (anchor - timedelta(days=30)).astimezone(utc).isoformat()
        end = anchor.astimezone(utc).isoformat()
        return start, end, "implicit"

    if any(marker in normalized_query for marker in _IMPLICIT_HISTORY_MARKERS):
        return None, None, "implicit"

    return None, None, "none"


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


def _is_identity_query(normalized_query: str) -> bool:
    compact = re.sub(r"[\s?？。.!！,，'’]+", "", normalized_query)
    if any(pattern in normalized_query for pattern in _IDENTITY_QUERY_PATTERNS):
        return True
    if any(marker in normalized_query for marker in _IDENTITY_TOPIC_MARKERS):
        return True
    # A bare identity noun is not enough: "查看星子的配置" and
    # "星子昨天做了什么" are ordinary memory queries. Require a short
    # identity-focused phrase with no operational/temporal verb.
    identity_terms = ("星子", "小星", "锚点")
    functional_terms = ("配置", "做了", "做什么", "昨天", "今天", "如何", "什么", "查看", "查找")
    identity_question = ("是谁", "叫什么", "什么身份", "who is", "what is", "信任", "身份")
    return len(compact) <= 15 and any(term in normalized_query for term in identity_terms) and not any(
        term in normalized_query for term in functional_terms
    ) and any(term in normalized_query for term in identity_question)


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
    source_domains: Sequence[str],
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
    record_filter: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    if plan.as_of:
        # Bi-temporal as-of (transaction time): the version that was current
        # at :as_of is the newest in its superseded_by chain with a creation
        # time at or before :as_of.
        clauses = [
            "(COALESCE(created_at, compressed_at) <= ? "
            "AND NOT EXISTS (SELECT 1 FROM compressed_memories successor "
            "WHERE compressed_memories.superseded_by = successor.memory_id "
            "AND COALESCE(successor.created_at, successor.compressed_at) <= ?))",
            "hidden = 0",
            "((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?))",
        ]
        params: list[Any] = [
            plan.as_of,
            plan.as_of,
            owner_id,
            workspace_id,
            GLOBAL_SCOPE_ID,
            GLOBAL_SCOPE_ID,
        ]
    else:
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
    domain_placeholders = ",".join("?" for _ in source_domains)
    clauses.append(f"memory_domain IN ({domain_placeholders})")
    params.extend(source_domains)
    if not _append_record_filter(
        clauses,
        params,
        source_type="compressed",
        id_column="memory_id",
        record_filter=record_filter,
    ):
        return []
    if plan.intent == "identity":
        clauses.extend(
            (
                "identity_layer = 'founding'",
                "memory_id LIKE 'identity-founding-%'",
                "pinned = 1",
            )
        )
    else:
        clauses.append("COALESCE(identity_layer, '') != 'founding'")
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
        "identity_layer, evidence_refs, memory_domain "
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
        if (
            plan.intent != "identity"
            and lexical <= 0
            and plan.terms
            and semantic < 0.35
        ):
            continue
        dynamic_weight = _dynamic_weight(
            float(row[15] or 0.0),
            event_kind=row[11],
            access_count=int(row[12] or 0),
            citation_count=int(row[13] or 0),
            pinned=bool(row[14]),
        )
        recency = _recency_score(row[5], now)
        temporal_fit = (
            _temporal_fit_score(row[4], row[5], plan.timespan_start, plan.timespan_end)
            if plan.temporal_intent == "explicit"
            else 0.0
        )
        if plan.intent == "identity":
            score = (
                0.69
                + _FOUNDING_IDENTITY_PRIORITY.get(str(row[0]), 0.0)
                + 0.15 * lexical
                + 0.03 * dynamic_weight
                + 0.03 * float(row[6] or 0.0)
            )
        elif semantic > 0:
            score = (
                0.42 * lexical
                + 0.30 * semantic
                + 0.12 * dynamic_weight
                + 0.10 * float(row[6] or 0.0)
                + (0.12 if plan.current_state_intent else 0.06) * recency
                + 0.10 * temporal_fit
            )
        else:
            score = (
                (0.58 if plan.current_state_intent else 0.62) * lexical
                + 0.16 * dynamic_weight
                + 0.12 * float(row[6] or 0.0)
                + (0.16 if plan.current_state_intent else 0.08) * recency
                + 0.10 * temporal_fit
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
                "memory_domain": row[18],
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "dynamic_weight": round(dynamic_weight, 6),
                    "importance": round(float(row[6] or 0.0), 6),
                    "recency": round(recency, 6),
                    "semantic": round(semantic, 6),
                    "temporal_fit": round(temporal_fit, 6),
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
    source_domains: Sequence[str],
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
    record_filter: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    if plan.memory_types and "profile" not in plan.memory_types:
        return []
    if plan.as_of:
        # Bi-temporal as-of (transaction time): current at :as_of means
        # created at or before it and not superseded by a successor created
        # at or before it (supersession is recorded in the successor's
        # ``supersedes`` JSON list).
        clauses = [
            "(created_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM profile_memories successor "
            "WHERE successor.memory_id != profile_memories.memory_id "
            "AND successor.created_at <= ? "
            "AND EXISTS (SELECT 1 FROM json_each(successor.supersedes) "
            "WHERE json_each.value = profile_memories.memory_id)))",
            "owner_id = ?",
            "workspace_id = ?",
        ]
        params: list[Any] = [plan.as_of, plan.as_of, owner_id, workspace_id]
    else:
        clauses = [
            "status = 'active'",
            "owner_id = ?",
            "workspace_id = ?",
        ]
        params: list[Any] = [owner_id, workspace_id]
    domain_placeholders = ",".join("?" for _ in source_domains)
    clauses.append(f"memory_domain IN ({domain_placeholders})")
    params.extend(source_domains)
    if not _append_record_filter(
        clauses,
        params,
        source_type="profile",
        id_column="memory_id",
        record_filter=record_filter,
    ):
        return []
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
        "source_turns, memory_domain FROM profile_memories WHERE "
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
        temporal_fit = (
            _temporal_fit_score(row[8], row[9], plan.timespan_start, plan.timespan_end)
            if plan.temporal_intent == "explicit"
            else 0.0
        )
        if semantic > 0:
            score = (
                0.46 * lexical
                + 0.32 * semantic
                + 0.16 * float(row[6] or 0.0)
                + 0.06 * recency
                + 0.10 * temporal_fit
            )
        else:
            score = (
                0.70 * lexical
                + 0.22 * float(row[6] or 0.0)
                + 0.08 * recency
                + 0.10 * temporal_fit
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
                "memory_domain": row[12],
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "confidence": round(float(row[6] or 0.0), 6),
                    "recency": round(recency, 6),
                    "semantic": round(semantic, 6),
                    "temporal_fit": round(temporal_fit, 6),
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
    source_domains: Sequence[str],
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
    record_filter: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    clauses = [
        "owner_id = ?",
        "workspace_id = ?",
        "original_text IS NOT NULL",
        "NOT EXISTS (SELECT 1 FROM profile_memory_tombstones tombstone, "
        "json_each(tombstone.evidence_turns) evidence "
        "WHERE tombstone.owner_id = turns_archive.owner_id "
        "AND tombstone.workspace_id = turns_archive.workspace_id "
        "AND tombstone.memory_domain = turns_archive.memory_domain "
        "AND evidence.value = turns_archive.turn_id)",
    ]
    params: list[Any] = [owner_id, workspace_id]
    domain_placeholders = ",".join("?" for _ in source_domains)
    clauses.append(f"memory_domain IN ({domain_placeholders})")
    params.extend(source_domains)
    if not _append_record_filter(
        clauses,
        params,
        source_type="archive",
        id_column="turn_id",
        record_filter=record_filter,
    ):
        return []
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
    if plan.as_of:
        clauses.append("timestamp <= ?")
        params.append(plan.as_of)
    params.append(candidate_limit)
    rows = conn.execute(
        "SELECT turn_id, session_id, speaker, original_text, text_summary, "
        "timestamp, event_ids, scene_ids, memory_domain FROM turns_archive WHERE "
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
        temporal_fit = (
            _temporal_fit_score(row[5], row[5], plan.timespan_start, plan.timespan_end)
            if plan.temporal_intent == "explicit"
            else 0.0
        )
        if semantic > 0:
            score = (
                0.48 * lexical
                + 0.34 * semantic
                + 0.06
                + 0.12 * recency
                + 0.10 * temporal_fit
            )
        else:
            score = (
                0.72 * lexical
                + 0.08
                + 0.20 * recency
                + 0.10 * temporal_fit
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
                "memory_domain": row[8],
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "recency": round(recency, 6),
                    "archive_fallback": True,
                    "semantic": round(semantic, 6),
                    "temporal_fit": round(temporal_fit, 6),
                },
            }
        )
    return results


def _graph_candidates(
    conn: sqlite3.Connection,
    plan: RecallPlan,
    candidate_limit: int,
    now: datetime,
    *,
    owner_id: str,
    workspace_id: str,
    source_domains: Sequence[str],
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    """Entity-graph expansion: surface memories connected to query entities.

    When the query references known entity nodes, this returns memories that
    reference those entities directly (proximity 1.0) or reference co-occurring
    neighbor entities (proximity 0.6) — the multi-hop access pattern plain
    lexical FTS cannot provide. Memories already surfaced by other candidate
    sources are skipped. Returns an empty list when the query matches no known
    entities, so recall is unaffected when the graph is empty.
    """
    from systems.memory.entity_graph import (
        entity_names_matching_query,
        graph_expand_memory_ids,
    )

    query_entities = entity_names_matching_query(
        conn,
        plan.search_terms,
        owner_id=owner_id,
        workspace_id=workspace_id,
        source_domains=source_domains,
    )
    if not query_entities:
        return []
    expanded = graph_expand_memory_ids(
        conn,
        query_entities,
        owner_id=owner_id,
        workspace_id=workspace_id,
        source_domains=source_domains,
        as_of=plan.as_of,
        max_depth=1,
        limit=candidate_limit,
    )
    if not expanded:
        return []
    placeholders = ",".join("?" for _ in expanded)
    rows = conn.execute(
        "SELECT memory_id, memory_type, title, summary, timespan_start, "
        "timespan_end, importance, confidence, topics, entities, source_turns, "
        "event_kind, access_count, citation_count, pinned, weight, "
        "identity_layer, evidence_refs, memory_domain "
        f"FROM compressed_memories WHERE memory_id IN ({placeholders})",
        list(expanded),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        memory_id = str(row[0])
        if memory_id in existing_ids:
            continue
        proximity = float(expanded.get(memory_id, 0.0))
        dynamic_weight = _dynamic_weight(
            float(row[15] or 0.0),
            event_kind=row[11],
            access_count=int(row[12] or 0),
            citation_count=int(row[13] or 0),
            pinned=bool(row[14]),
        )
        importance = float(row[6] or 0.0)
        recency = _recency_score(row[5], now)
        score = (
            0.55 * proximity
            + 0.25 * dynamic_weight
            + 0.20 * importance
            + 0.05 * recency
        )
        results.append(
            {
                "id": memory_id,
                "tier": "graph",
                "memory_type": row[1],
                "title": row[2],
                "summary": row[3],
                "timespan_start": row[4],
                "timespan_end": row[5],
                "topics": _json_list(row[8]),
                "entities": _json_list(row[9]),
                "source_turns": _json_list(row[10]),
                "identity_layer": row[16],
                "evidence_refs": _json_list(row[17]),
                "memory_domain": row[18],
                "score": round(min(score, 1.0), 6),
                "matched_terms": list(query_entities),
                "signals": {
                    "graph_proximity": round(proximity, 6),
                    "dynamic_weight": round(dynamic_weight, 6),
                    "importance": round(importance, 6),
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
    owner_id: str,
    workspace_id: str,
    source_domains: Sequence[str],
    lexical_matches: dict[str, tuple[str, ...]],
    semantic_matches: dict[tuple[str, str], float],
    record_filter: Mapping[str, Sequence[str]] | None,
) -> list[dict[str, Any]]:
    clauses = [
        "compressed_to_tier2 = 0",
        "owner_id = ?",
        "workspace_id = ?",
    ]
    params: list[Any] = [owner_id, workspace_id]
    domain_placeholders = ",".join("?" for _ in source_domains)
    clauses.append(f"memory_domain IN ({domain_placeholders})")
    params.extend(source_domains)
    if not _append_record_filter(
        clauses,
        params,
        source_type="turn",
        id_column="turn_id",
        record_filter=record_filter,
    ):
        return []
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
    if plan.as_of:
        clauses.append("timestamp <= ?")
        params.append(plan.as_of)
    params.append(candidate_limit)
    rows = conn.execute(
        "SELECT turn_id, session_id, speaker, text, timestamp, relevance_score, tags, memory_domain "
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
        temporal_fit = (
            _temporal_fit_score(row[4], row[4], plan.timespan_start, plan.timespan_end)
            if plan.temporal_intent == "explicit"
            else 0.0
        )
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
        elif semantic > 0:
            score = (
                0.50 * lexical
                + 0.34 * semantic
                + 0.08 * float(row[5] or 0.0)
                + (0.18 if plan.current_state_intent else 0.08) * recency
                + 0.10 * temporal_fit
            )
        else:
            score = (
                (0.60 if plan.current_state_intent else 0.76) * lexical
                + 0.10 * float(row[5] or 0.0)
                + (0.30 if plan.current_state_intent else 0.12) * recency
                + 0.10 * temporal_fit
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
                "memory_domain": row[7],
                "score": round(min(score, 1.0), 6),
                "matched_terms": matched,
                "signals": {
                    "lexical": round(lexical, 6),
                    "relevance": round(float(row[5] or 0.0), 6),
                    "recency": round(recency, 6),
                    "same_session": same_session,
                    "semantic": round(semantic, 6),
                    "temporal_fit": round(temporal_fit, 6),
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


def _append_record_filter(
    clauses: list[str],
    params: list[Any],
    *,
    source_type: str,
    id_column: str,
    record_filter: Mapping[str, Sequence[str]] | None,
) -> bool:
    if record_filter is None:
        return True
    candidate_ids = tuple(
        dict.fromkeys(
            str(item)
            for item in record_filter.get(source_type, ())
            if str(item)
        )
    )
    if not candidate_ids:
        return False
    placeholders = ",".join("?" for _ in candidate_ids)
    clauses.append(f"{id_column} IN ({placeholders})")
    params.extend(candidate_ids)
    return True


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
            _structural_rank(item),
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
        if any(
            _jaccard(shingles, previous) >= 0.88
            or _overlap_coefficient(shingles, previous) >= 0.92
            for previous in seen_shingles
        ):
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


def _structural_rank(item: Mapping[str, Any]) -> int:
    """Structural hierarchy level used as a ranking tie-breaker.

    Higher-level durable memory (epoch > arc > scene > event) is preferred
    over raw turns / profiles when scores are close, per the doctrine
    "structure over accumulation". tier2 entries always rank above non-tier2
    (all four levels score >= 1 vs 0 for raw records).
    """
    memory_type = str(item.get("memory_type") or "")
    return {"epoch": 4, "arc": 3, "scene": 2, "event": 1}.get(memory_type, 0)


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


def _overlap_coefficient(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _record_tier2_accesses(
    conn: sqlite3.Connection,
    selected: Sequence[dict[str, Any]],
    now: datetime,
    *,
    owner_id: str,
    workspace_id: str,
) -> None:
    records = [item for item in selected if item.get("tier") == "tier2"]
    if not records:
        return
    try:
        conn.executemany(
            "UPDATE compressed_memories SET access_count = access_count + 1, "
            "last_accessed_at = ? WHERE memory_id = ? AND memory_domain = ? "
            "AND ((owner_id = ? AND workspace_id = ?) OR "
            "(owner_id = ? AND workspace_id = ?))",
            [
                (
                    now.isoformat(),
                    str(item["id"]),
                    str(item.get("memory_domain") or "agent_interaction"),
                    owner_id,
                    workspace_id,
                    GLOBAL_SCOPE_ID,
                    GLOBAL_SCOPE_ID,
                )
                for item in records
            ],
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
    source_domains: Sequence[str],
) -> list[dict[str, Any]]:
    ids = [str(item.get("id") or "") for item in candidates if item.get("id")]
    if not ids:
        return list(candidates)
    placeholders = ",".join("?" for _ in ids)
    domain_placeholders = ",".join("?" for _ in source_domains)
    rows = conn.execute(
        "SELECT memory_id, verdict, created_at FROM recall_feedback "
        f"WHERE owner_id = ? AND workspace_id = ? AND memory_domain IN ({domain_placeholders}) "
        f"AND memory_id IN ({placeholders})",
        [owner_id, workspace_id, *source_domains, *ids],
    ).fetchall()
    counts: dict[str, dict[str, float]] = {}
    now = datetime.now(timezone.utc)
    for memory_id, verdict, created_at in rows:
        try:
            stamp = _aware_datetime(datetime.fromisoformat(str(created_at)))
            age_days = max(0.0, (now - stamp).total_seconds() / 86400.0)
            decay = math.exp(-age_days / 90.0)
        except (TypeError, ValueError):
            decay = 1.0
        bucket = counts.setdefault(str(memory_id), {})
        bucket[str(verdict)] = bucket.get(str(verdict), 0.0) + decay
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
    return compute_dynamic_weight(
        base_weight,
        event_kind=event_kind,
        access_count=access_count,
        citation_count=citation_count,
        pinned=pinned,
    )


def _temporal_fit_score(
    candidate_start: object,
    candidate_end: object,
    query_start: str | None,
    query_end: str | None,
) -> float:
    """Score how well a candidate's span covers the query time window.

    Mirrors the memai ``MemoryQueryEngine`` range ranking: a span candidate is
    scored by its overlap ratio with the window (a memory covering the whole
    queried period scores 1.0, a one-day memory inside a 30-day window scores
    ~1/30). A point candidate (tier1/archive single timestamp) scores 1.0 when
    contained and decays by distance when outside.

    Returns 0.0 when no usable query window is present.
    """
    if not (query_start and query_end):
        return 0.0
    q_start = _parse_datetime(query_start)
    q_end = _parse_datetime(query_end)
    if q_start is None or q_end is None:
        return 0.0
    c_start = _parse_datetime(candidate_start)
    if c_start is None:
        return 0.0
    c_end = _parse_datetime(candidate_end) if candidate_end else c_start
    if c_end is None:
        c_end = c_start

    query_seconds = max((q_end - q_start).total_seconds(), 1.0)
    if c_start == c_end:
        # Point candidate.
        if q_start <= c_start <= q_end:
            return 1.0
        # An explicit window is a hard relevance boundary for point events.
        # Recency and lexical/semantic signals may still surface the record,
        # but temporal fit must not reward an out-of-window point above any
        # candidate with positive overlap.
        return 0.0

    # Span candidate: overlap ratio over the query window.
    overlap_start = max(c_start, q_start)
    overlap_end = min(c_end, q_end)
    overlap_seconds = max((overlap_end - overlap_start).total_seconds(), 0.0)
    return max(0.0, min(1.0, overlap_seconds / query_seconds))


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
