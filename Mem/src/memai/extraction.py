from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any, Protocol, Sequence

from .schema import (
    CertaintyState,
    Event,
    EventKind,
    ImpactScope,
    MainOrSide,
    MemoryKind,
    ProfileMemory,
    Scene,
    TemporalSpan,
    TimePrecision,
    TranscriptTurn,
)
from .temporal import TemporalNormalizer


TOPIC_LEXICON: dict[str, tuple[str, ...]] = {
    "memory-system": ("memory", "记忆", "长期记忆", "memory system", "memory manager"),
    "timeline-indexing": (
        "timeline",
        "temporal",
        "chronology",
        "时间",
        "时间线",
        "时序",
    ),
    "compression": ("compress", "compression", "压缩"),
    "revision": ("revise", "revision", "修订", "更正"),
    "retrieval": ("retrieve", "retrieval", "召回", "检索"),
    "schema": ("schema", "字段", "结构", "类型"),
    "prompting": ("prompt", "提示词"),
    "evaluation": ("benchmark", "evaluation", "评测", "测试"),
    "architecture": ("architecture", "framework", "module", "架构", "框架", "模块"),
    "project-definition": ("project", "项目", "定位", "goal"),
}

STOPWORD_ENTITIES = {"This", "That", "These", "Those", "Today", "Yesterday", "We", "I"}

EVENT_PATTERNS: list[tuple[EventKind, tuple[str, ...]]] = [
    (EventKind.CORRECTION, ("更正", "改正", "不是", "actually", "correction")),
    (EventKind.CONFLICT, ("冲突", "分歧", "conflict", "disagree")),
    (EventKind.BLOCKER, ("卡住", "问题", "报错", "失败", "blocked", "issue", "error")),
    (EventKind.COMPLETION, ("完成", "结束", "done", "finished", "shipped")),
    (EventKind.SHIFT, ("改成", "转向", "不再", "instead", "pivot", "change")),
    (EventKind.DECISION, ("决定", "打算", "计划", "准备", "decide", "plan", "will")),
    (
        EventKind.PROGRESS,
        ("实现", "已经", "推进", "构建", "implemented", "built", "progress"),
    ),
]


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"[。！？!?\n;；]+", text)
    return [part.strip() for part in parts if part.strip()]


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _coerce_text(value: Any, fallback: str = "") -> str:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or fallback
    if value is None:
        return fallback
    normalized = str(value).strip()
    return normalized or fallback


def _coerce_string_list(value: Any, fallback: Sequence[str] | None = None) -> list[str]:
    fallback_values = list(fallback or [])
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else fallback_values
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [_coerce_text(item) for item in value if _coerce_text(item)]
        return items or fallback_values
    return fallback_values


def _coerce_float(value: Any, fallback: float) -> float:
    try:
        return clamp(float(value))
    except (TypeError, ValueError):
        return clamp(fallback)


def _coerce_enum(enum_cls, value: Any, fallback):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().lower())
    except (TypeError, ValueError):
        return fallback


class EventExtractionBackend(Protocol):
    name: str

    def extract(
        self,
        turns: Sequence[TranscriptTurn],
        temporal_normalizer: TemporalNormalizer,
    ) -> list[Event]: ...


class LLMExtractionClient(Protocol):
    def extract_events(
        self, turns: Sequence[TranscriptTurn]
    ) -> Sequence[dict[str, Any]]: ...


class HeuristicEventExtractionBackend:
    name = "heuristic"

    def extract(
        self,
        turns: Sequence[TranscriptTurn],
        temporal_normalizer: TemporalNormalizer,
    ) -> list[Event]:
        events: list[Event] = []
        for turn in turns:
            for clause in split_clauses(turn.text):
                event = self._extract_clause(turn, clause, temporal_normalizer)
                if event is not None:
                    events.append(event)
        return events

    def _extract_clause(
        self,
        turn: TranscriptTurn,
        clause: str,
        temporal_normalizer: TemporalNormalizer,
    ) -> Event | None:
        event_kind = self._classify_event_kind(clause)
        if event_kind is None:
            return None

        topics = self._extract_topics(clause)
        entities = self._extract_entities(turn, clause)
        timespan = self._extract_timespan(turn, clause, temporal_normalizer)
        importance = self._score_importance(event_kind, topics, clause)
        confidence = self._score_confidence(event_kind, clause)
        impact_scope = self._infer_impact_scope(importance)
        main_or_side = MainOrSide.MAIN if importance >= 0.75 else MainOrSide.SIDE
        novelty = clamp(
            0.45
            + (0.1 * len(topics))
            + (0.1 if event_kind in {EventKind.DECISION, EventKind.SHIFT} else 0.0)
        )

        return Event.create(
            title=self._make_title(event_kind, clause),
            summary=clause,
            timespan=timespan,
            importance=importance,
            confidence=confidence,
            event_kind=event_kind,
            impact_scope=impact_scope,
            topics=topics,
            entities=entities,
            evidence_refs=[turn.turn_id],
            source_turns=[turn.turn_id],
            main_or_side=main_or_side,
            novelty=novelty,
        )

    def _classify_event_kind(self, clause: str) -> EventKind | None:
        lowered = clause.lower()
        for event_kind, patterns in EVENT_PATTERNS:
            if any(pattern.lower() in lowered for pattern in patterns):
                return event_kind
        return None

    def _extract_topics(self, clause: str) -> list[str]:
        lowered = clause.lower()
        topics = [
            topic
            for topic, keywords in TOPIC_LEXICON.items()
            if any(keyword.lower() in lowered for keyword in keywords)
        ]
        if topics:
            return sorted(set(topics))

        code_tokens = re.findall(r"`([^`]+)`", clause)
        if code_tokens:
            return [
                token.strip().lower().replace(" ", "-") for token in code_tokens[:3]
            ]

        english_words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", clause)
        if english_words:
            counts = Counter(word.lower() for word in english_words)
            return [word for word, _ in counts.most_common(2)]

        return ["general"]

    def _extract_entities(self, turn: TranscriptTurn, clause: str) -> list[str]:
        entities = {turn.speaker}
        for token in re.findall(r"`([^`]+)`", clause):
            entities.add(token.strip())
        for token in re.findall(r"\b[A-Z][A-Za-z0-9_-]{1,}\b", clause):
            if token not in STOPWORD_ENTITIES:
                entities.add(token)
        if "用户" in clause or "user" in clause.lower():
            entities.add("user")
        if "项目" in clause or "project" in clause.lower():
            entities.add("project")
        return sorted(entities)

    def _extract_timespan(
        self,
        turn: TranscriptTurn,
        clause: str,
        temporal_normalizer: TemporalNormalizer,
    ) -> TemporalSpan:
        contains_explicit_time = any(
            token in clause.lower()
            for token in (
                "today",
                "yesterday",
                "this week",
                "last week",
                "this month",
                "last month",
                "recently",
            )
        ) or any(
            token in clause
            for token in (
                "今天",
                "昨天",
                "本周",
                "这周",
                "上周",
                "本月",
                "这个月",
                "上个月",
                "最近",
                "前阵子",
            )
        )

        if contains_explicit_time or re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", clause):
            return temporal_normalizer.normalize(clause, turn.timestamp)

        start = turn.timestamp
        end = turn.timestamp + timedelta(minutes=5)
        return TemporalSpan(
            start=start,
            end=end,
            precision=TimePrecision.EXACT,
            confidence=0.9,
            source_text=turn.turn_id,
        )

    def _score_importance(
        self, event_kind: EventKind, topics: list[str], clause: str
    ) -> float:
        base = {
            EventKind.DECISION: 0.76,
            EventKind.SHIFT: 0.8,
            EventKind.COMPLETION: 0.72,
            EventKind.BLOCKER: 0.68,
            EventKind.CONFLICT: 0.66,
            EventKind.CORRECTION: 0.74,
            EventKind.PROGRESS: 0.62,
        }[event_kind]
        if len(topics) > 1:
            base += 0.05
        if "项目" in clause or "project" in clause.lower():
            base += 0.04
        if "长期" in clause or "long-term" in clause.lower():
            base += 0.04
        return clamp(base)

    def _score_confidence(self, event_kind: EventKind, clause: str) -> float:
        confidence = 0.76
        if event_kind in {
            EventKind.DECISION,
            EventKind.COMPLETION,
            EventKind.CORRECTION,
        }:
            confidence += 0.08
        if len(clause) > 20:
            confidence += 0.04
        return clamp(confidence)

    def _infer_impact_scope(self, importance: float) -> ImpactScope:
        if importance >= 0.85:
            return ImpactScope.EPOCH
        if importance >= 0.72:
            return ImpactScope.ARC
        if importance >= 0.6:
            return ImpactScope.THREAD
        return ImpactScope.LOCAL

    def _make_title(self, event_kind: EventKind, clause: str) -> str:
        cleaned = re.sub(r"\s+", " ", clause).strip()
        if len(cleaned) > 64:
            cleaned = cleaned[:61] + "..."
        return f"{event_kind.value.capitalize()}: {cleaned}"


class LLMEventExtractionBackend:
    name = "llm"

    def __init__(self, client: LLMExtractionClient) -> None:
        self.client = client

    def extract(
        self,
        turns: Sequence[TranscriptTurn],
        temporal_normalizer: TemporalNormalizer,
    ) -> list[Event]:
        payloads = self.client.extract_events(turns)
        events: list[Event] = []
        turn_index = {turn.turn_id: turn for turn in turns}

        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            source_turns = [
                turn_id
                for turn_id in _coerce_string_list(payload.get("source_turns"))
                if turn_id in turn_index
            ]
            if not source_turns:
                continue
            anchor_turn = turn_index.get(source_turns[0])
            if anchor_turn is None:
                continue
            time_hint = _coerce_text(payload.get("time_hint"), anchor_turn.turn_id)
            timespan = temporal_normalizer.normalize(
                str(time_hint), anchor_turn.timestamp
            )
            summary = _coerce_text(payload.get("summary"), anchor_turn.text)
            title = _coerce_text(
                payload.get("title"),
                f"Generated: {summary[:40]}",
            )
            events.append(
                Event.create(
                    title=title,
                    summary=summary,
                    timespan=timespan,
                    importance=_coerce_float(payload.get("importance"), 0.7),
                    confidence=_coerce_float(payload.get("confidence"), 0.75),
                    event_kind=_coerce_enum(
                        EventKind,
                        payload.get("event_kind"),
                        EventKind.PROGRESS,
                    ),
                    impact_scope=_coerce_enum(
                        ImpactScope,
                        payload.get("impact_scope"),
                        ImpactScope.THREAD,
                    ),
                    topics=_coerce_string_list(payload.get("topics"), ["general"]),
                    entities=_coerce_string_list(
                        payload.get("entities"), [anchor_turn.speaker]
                    ),
                    evidence_refs=source_turns,
                    source_turns=source_turns,
                    main_or_side=_coerce_enum(
                        MainOrSide,
                        payload.get("main_or_side"),
                        MainOrSide.UNDETERMINED,
                    ),
                    novelty=_coerce_float(payload.get("novelty"), 0.6),
                )
            )
        return events


class EventExtractor:
    def __init__(
        self,
        temporal_normalizer: TemporalNormalizer | None = None,
        backend: EventExtractionBackend | None = None,
    ) -> None:
        self.temporal_normalizer = temporal_normalizer or TemporalNormalizer()
        self.backend = backend or HeuristicEventExtractionBackend()

    def extract(self, turns: Sequence[TranscriptTurn]) -> list[Event]:
        return self.backend.extract(turns, self.temporal_normalizer)


class ProfileMemoryExtractor:
    def extract(
        self,
        turns: Sequence[TranscriptTurn],
        events: Sequence[Event],
        scenes: Sequence[Scene],
    ) -> list[ProfileMemory]:
        scene_by_turn_id = self._scene_by_turn_id(events, scenes)
        extracted: dict[tuple[str, str, str, str], ProfileMemory] = {}

        for turn in turns:
            for clause in split_clauses(turn.text):
                candidate = self._extract_clause(
                    turn, clause, scene_by_turn_id.get(turn.turn_id)
                )
                if candidate is None:
                    continue
                key = (
                    candidate.memory_kind.value,
                    candidate.subject.lower(),
                    candidate.predicate.lower(),
                    candidate.value.strip().lower(),
                )
                existing = extracted.get(key)
                if existing is None:
                    extracted[key] = candidate
                    continue
                if candidate.value == existing.value:
                    existing.evidence_refs = sorted(
                        set(existing.evidence_refs) | set(candidate.evidence_refs)
                    )
                    existing.source_turns = sorted(
                        set(existing.source_turns) | set(candidate.source_turns)
                    )
                    existing.parent_timeline_refs = sorted(
                        set(existing.parent_timeline_refs)
                        | set(candidate.parent_timeline_refs)
                    )
                    existing.confidence = clamp(existing.confidence + 0.08)
                    existing.certainty_state = CertaintyState.CONFIRMED
                    existing.touch()
                    continue
        return normalize_profile_memories(extracted.values())

    def _extract_clause(
        self,
        turn: TranscriptTurn,
        clause: str,
        parent_scene_id: str | None,
    ) -> ProfileMemory | None:
        lowered = clause.lower()
        parent_refs = [parent_scene_id] if parent_scene_id else []

        if any(token in lowered for token in ("prefer", "prefers", "please use")):
            value = self._extract_tail_value(
                clause, ("prefer", "prefers", "please use")
            )
            if value:
                return ProfileMemory.create(
                    memory_kind=MemoryKind.PREFERENCE,
                    subject="user" if turn.speaker == "user" else "project",
                    predicate="prefers",
                    value=value,
                    summary=f"{turn.speaker} prefers {value}.",
                    confidence=0.9,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )
        if any(token in clause for token in ("偏好", "请用")):
            value = self._extract_chinese_tail_value(clause, ("偏好", "请用"))
            if value:
                return ProfileMemory.create(
                    memory_kind=MemoryKind.PREFERENCE,
                    subject="user",
                    predicate="prefers",
                    value=value,
                    summary=f"user prefers {value}.",
                    confidence=0.9,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )

        if any(
            token in lowered for token in ("must", "must not", "should not", "do not")
        ):
            value = self._extract_tail_value(
                clause,
                ("must not", "should not", "do not", "must"),
            )
            if value:
                return ProfileMemory.create(
                    memory_kind=MemoryKind.CONSTRAINT,
                    subject="project",
                    predicate="requires",
                    value=value,
                    summary=f"The project requires {value}.",
                    confidence=0.92,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )
        if any(token in clause for token in ("必须", "不能", "不要")):
            value = self._extract_chinese_tail_value(clause, ("必须", "不能", "不要"))
            if value:
                return ProfileMemory.create(
                    memory_kind=MemoryKind.CONSTRAINT,
                    subject="project",
                    predicate="requires",
                    value=value,
                    summary=f"The project requires {value}.",
                    confidence=0.92,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )

        if " means " in lowered or " is defined as " in lowered:
            subject, value = self._extract_definition(clause)
            if subject and value:
                return ProfileMemory.create(
                    memory_kind=MemoryKind.DEFINITION,
                    subject=subject,
                    predicate="means",
                    value=value,
                    summary=f"{subject} means {value}.",
                    confidence=0.88,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )
        if "指的是" in clause:
            subject, value = self._extract_chinese_definition(clause)
            if subject and value:
                return ProfileMemory.create(
                    memory_kind=MemoryKind.DEFINITION,
                    subject=subject,
                    predicate="means",
                    value=value,
                    summary=f"{subject} means {value}.",
                    confidence=0.88,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )

        fact_patterns = [
            ("default", "is_default"),
            ("optional", "is_optional"),
            ("incremental", "update_mode"),
            ("默认", "is_default"),
            ("可选", "is_optional"),
            ("增量", "update_mode"),
        ]
        for token, predicate in fact_patterns:
            if token.lower() in lowered or token in clause:
                subject = "project"
                value = clause.strip()
                return ProfileMemory.create(
                    memory_kind=MemoryKind.FACT,
                    subject=subject,
                    predicate=predicate,
                    value=value,
                    summary=value,
                    confidence=0.8,
                    certainty_state=CertaintyState.OBSERVED,
                    valid_from=turn.timestamp,
                    evidence_refs=[turn.turn_id, *parent_refs],
                    source_turns=[turn.turn_id],
                    parent_timeline_refs=parent_refs,
                )

        return None

    def _scene_by_turn_id(
        self,
        events: Sequence[Event],
        scenes: Sequence[Scene],
    ) -> dict[str, str]:
        event_parent_scene = {
            event.id: event.parent_ids[0] for event in events if event.parent_ids
        }
        scene_by_turn: dict[str, str] = {}
        for event in events:
            scene_id = event_parent_scene.get(event.id)
            if scene_id is None:
                continue
            for turn_id in event.source_turns:
                scene_by_turn[turn_id] = scene_id
        return scene_by_turn

    def _extract_tail_value(self, clause: str, markers: Sequence[str]) -> str:
        lowered = clause.lower()
        for marker in markers:
            index = lowered.find(marker)
            if index == -1:
                continue
            value = clause[index + len(marker) :].strip(" .:;-\t")
            if value:
                return value
        return ""

    def _extract_chinese_tail_value(self, clause: str, markers: Sequence[str]) -> str:
        for marker in markers:
            index = clause.find(marker)
            if index == -1:
                continue
            value = clause[index + len(marker) :].strip(" ，。:：；;")
            if value:
                return value
        return ""

    def _extract_definition(self, clause: str) -> tuple[str, str]:
        if " is defined as " in clause.lower():
            parts = re.split(
                r"\bis defined as\b", clause, maxsplit=1, flags=re.IGNORECASE
            )
        else:
            parts = re.split(r"\bmeans\b", clause, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2:
            return "", ""
        return parts[0].strip(" `.:;-"), parts[1].strip(" `.:;-")

    def _extract_chinese_definition(self, clause: str) -> tuple[str, str]:
        parts = clause.split("指的是", 1)
        if len(parts) != 2:
            return "", ""
        return parts[0].strip(" `，。:：；;"), parts[1].strip(" `，。:：；;")


def normalize_profile_memories(
    memories: Sequence[ProfileMemory],
) -> list[ProfileMemory]:
    normalized = sorted(memories, key=lambda item: item.valid_from)
    grouped: dict[tuple[str, str, str], list[ProfileMemory]] = {}
    for item in normalized:
        key = (
            item.memory_kind.value,
            item.subject.lower(),
            item.predicate.lower(),
        )
        grouped.setdefault(key, []).append(item)

    for items in grouped.values():
        distinct_values = {
            item.value.strip().lower() for item in items if item.value.strip()
        }
        if len(distinct_values) <= 1:
            for item in items:
                item.conflict_refs = []
                if item.certainty_state == CertaintyState.DISPUTED:
                    item.certainty_state = CertaintyState.OBSERVED
            continue
        all_ids = [item.id for item in items]
        for item in items:
            item.conflict_refs = [
                other_id for other_id in all_ids if other_id != item.id
            ]
            item.certainty_state = CertaintyState.DISPUTED
            item.touch()

    return normalized
