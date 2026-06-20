# Schema v1

## 1. Design Goal

The schema defines a layered memory model for time-centric external memory. It is designed to support:
- gradual abstraction from events to chapters,
- explicit revision and supersession,
- evidence-aware retrieval,
- selective forgetting,
- stable mainline and sideline tracking.

The v1 hierarchy is:

`Event -> Scene -> Arc -> Epoch`

## 2. Shared Base Type

All memory units inherit a common base structure.

```json
{
  "id": "mem_xxx",
  "type": "event|scene|arc|epoch",
  "title": "string",
  "summary": "string",
  "timespan_start": "ISO datetime",
  "timespan_end": "ISO datetime",
  "time_precision": "exact|day|week|month|approx",
  "importance": 0.0,
  "confidence": 0.0,
  "status": "active|dormant|closed|superseded",
  "main_or_side": "main|side|undetermined",
  "topics": ["string"],
  "entities": ["string"],
  "evidence_refs": ["string"],
  "parent_ids": ["string"],
  "child_ids": ["string"],
  "supersedes": ["string"],
  "compression_level": 0,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime",
  "last_reviewed_at": "ISO datetime"
}
```

## 3. Field Semantics

- `id`: immutable object identifier.
- `type`: layer identifier.
- `title`: short human-readable label.
- `summary`: compact historical description.
- `timespan_start` and `timespan_end`: best available temporal bounds.
- `time_precision`: reliability and granularity of the temporal bounds.
- `importance`: expected long-term retrieval and retention value.
- `confidence`: confidence in object accuracy.
- `status`: lifecycle state.
- `main_or_side`: structural role within broader narrative.
- `topics`: thematic tags.
- `entities`: people, projects, places, or other named entities.
- `evidence_refs`: raw turn ids, source snippets, or lower-level object ids supporting the record.
- `parent_ids`: direct higher-order containers.
- `child_ids`: direct lower-order members.
- `supersedes`: prior versions of the same logical memory unit.
- `compression_level`: abstraction depth.

## 4. Enumerations

### `time_precision`

```text
exact | day | week | month | approx
```

### `status`

```text
active | dormant | closed | superseded
```

### `main_or_side`

```text
main | side | undetermined
```

## 5. Event

An `Event` is the smallest durable memory unit. It records a meaningful change, not a generic utterance.

### Event Shape

```json
{
  "id": "event_001",
  "type": "event",
  "title": "Decides to define the memory system around time",
  "summary": "The user explicitly frames the project as a time-centered memory manager for large models.",
  "timespan_start": "2026-03-22T10:00:00Z",
  "timespan_end": "2026-03-22T10:05:00Z",
  "time_precision": "exact",
  "importance": 0.82,
  "confidence": 0.95,
  "status": "active",
  "main_or_side": "main",
  "topics": ["memory-system", "project-definition"],
  "entities": ["user", "project"],
  "evidence_refs": ["turn_011"],
  "parent_ids": ["scene_001"],
  "child_ids": [],
  "supersedes": [],
  "compression_level": 0,
  "created_at": "2026-03-22T10:05:00Z",
  "updated_at": "2026-03-22T10:05:00Z",
  "last_reviewed_at": "2026-03-22T10:05:00Z",
  "event_kind": "decision",
  "novelty": 0.88,
  "impact_scope": "arc",
  "source_turns": ["turn_011"]
}
```

### Event-Specific Fields

- `event_kind`: one of the controlled event categories.
- `novelty`: how much new information the event contributes.
- `impact_scope`: expected downstream range.
- `source_turns`: direct turn ids from which the event was extracted.

### `event_kind`

```text
decision | progress | blocker | shift | completion | conflict | correction
```

### `impact_scope`

```text
local | thread | arc | epoch
```

### Event Rules

- An event must represent a change, decision, movement, correction, or interruption.
- A casual statement with no durable consequence should not become an event.
- Each event should be traceable to one or more source turns.

## 6. Scene

A `Scene` groups temporally close and topically related events. It usually covers a day or a week.

### Scene Shape

```json
{
  "id": "scene_001",
  "type": "scene",
  "title": "Project framing and role definition",
  "summary": "During this period the project was framed as a structured memory layer centered on time, compression, and narrative organization.",
  "timespan_start": "2026-03-22T00:00:00Z",
  "timespan_end": "2026-03-22T23:59:59Z",
  "time_precision": "day",
  "importance": 0.86,
  "confidence": 0.92,
  "status": "active",
  "main_or_side": "main",
  "topics": ["architecture", "role-definition"],
  "entities": ["user", "project"],
  "evidence_refs": ["turn_009", "turn_011", "turn_013"],
  "parent_ids": ["arc_001"],
  "child_ids": ["event_001", "event_002", "event_003"],
  "supersedes": [],
  "compression_level": 1,
  "created_at": "2026-03-22T23:59:59Z",
  "updated_at": "2026-03-22T23:59:59Z",
  "last_reviewed_at": "2026-03-22T23:59:59Z",
  "scene_goal": "Clarify the project's conceptual foundation",
  "key_events": ["event_001", "event_002"],
  "local_turning_points": ["event_002"],
  "open_questions": ["How much evidence should remain in long-term storage?"]
}
```

### Scene-Specific Fields

- `scene_goal`: local organizing aim, if any.
- `key_events`: events essential to the scene summary.
- `local_turning_points`: events that materially change direction within the scene.
- `open_questions`: unresolved issues still active at the end of the scene.

### Scene Rules

- A scene must contain at least one event.
- `key_events` must be a subset of `child_ids`.
- `local_turning_points` must be a subset of `child_ids`.
- A scene summary should represent local development, not entire-arc judgment.

## 7. Arc

An `Arc` is the principal unit of durable narrative continuity. It spans multiple scenes and represents a sustained line of development.

### Arc Shape

```json
{
  "id": "arc_001",
  "type": "arc",
  "title": "Long-term memory manager design mainline",
  "summary": "This arc tracks the construction of a time-first external memory framework for large models, including role boundaries, layered structure, and long-range compression logic.",
  "timespan_start": "2026-03-22T00:00:00Z",
  "timespan_end": "2026-04-30T23:59:59Z",
  "time_precision": "approx",
  "importance": 0.93,
  "confidence": 0.89,
  "status": "active",
  "main_or_side": "main",
  "topics": ["memory-architecture", "timeline-indexing", "compression"],
  "entities": ["user", "project"],
  "evidence_refs": ["scene_001"],
  "parent_ids": ["epoch_001"],
  "child_ids": ["scene_001"],
  "supersedes": [],
  "compression_level": 2,
  "created_at": "2026-03-22T23:59:59Z",
  "updated_at": "2026-03-22T23:59:59Z",
  "last_reviewed_at": "2026-03-22T23:59:59Z",
  "arc_goal": "Define and eventually implement the memory system's governing framework",
  "arc_state": "active",
  "drivers": ["Need to manage memory beyond context length", "Need to keep chronology coherent"],
  "obstacles": ["Avoid shallow personality interpretation"],
  "milestones": ["scene_001"],
  "turning_points": ["scene_001"]
}
```

### Arc-Specific Fields

- `arc_goal`: sustained goal or organizing problem.
- `arc_state`: current dynamic status.
- `drivers`: factors pushing the arc forward.
- `obstacles`: factors slowing or distorting the arc.
- `milestones`: major progress markers.
- `turning_points`: scenes or events that changed the arc trajectory.

### `arc_state`

```text
emerging | active | stalled | dormant | resolved
```

### Arc Rules

- An arc should contain one or more scenes.
- A main arc should typically exceed a configured importance threshold such as `0.70`.
- Arc summaries should describe the trajectory, not simply list member scenes.
- An arc may change state over time without changing identity.

## 8. Epoch

An `Epoch` is the highest-order chapter memory in v1. It represents a large historical phase.

### Epoch Shape

```json
{
  "id": "epoch_001",
  "type": "epoch",
  "title": "Conceptual foundation period",
  "summary": "This chapter establishes the theoretical basis for a time-centric external memory system: layered chronology, explicit compression, and evidence-bound revision.",
  "timespan_start": "2026-03-01T00:00:00Z",
  "timespan_end": "2026-06-30T23:59:59Z",
  "time_precision": "month",
  "importance": 0.90,
  "confidence": 0.84,
  "status": "active",
  "main_or_side": "main",
  "topics": ["foundation", "memory-theory"],
  "entities": ["user", "project"],
  "evidence_refs": ["arc_001"],
  "parent_ids": [],
  "child_ids": ["arc_001"],
  "supersedes": [],
  "compression_level": 3,
  "created_at": "2026-03-31T23:59:59Z",
  "updated_at": "2026-03-31T23:59:59Z",
  "last_reviewed_at": "2026-03-31T23:59:59Z",
  "epoch_theme": "Theoretical and architectural grounding of the memory system",
  "major_arcs": ["arc_001"],
  "chapter_shift": "Moves from a general idea into a formal design program",
  "long_term_effects": ["Establishes stable concepts for later implementation and evaluation"]
}
```

### Epoch-Specific Fields

- `epoch_theme`: defining theme of the historical period.
- `major_arcs`: arcs that substantiate the chapter.
- `chapter_shift`: what changed across the period.
- `long_term_effects`: durable consequences into later periods.

### Epoch Rules

- An epoch should aggregate one or more arcs.
- Epoch summaries should emphasize major historical shifts and durable consequences.
- Epochs are not personality labels; they are chapter-level chronologies.

## 9. Layer Constraints

- `Event -> Scene -> Arc -> Epoch` is the canonical containment chain.
- Higher layers must remain auditable from lower layers.
- `supersedes` should only target objects of the same type.
- `compression_level` should remain stable by type:

```text
Event = 0
Scene = 1
Arc = 2
Epoch = 3
```

## 10. Storage Notes

v1 storage can be plain JSON files or newline-delimited JSON records. The storage backend is not part of the schema contract.

Recommended persistence split:
- raw transcript or source turns stored separately,
- structured memory units stored in indexed files or a lightweight database,
- optional graph edges materialized for parent-child and supersession relations.

## 11. Minimal Viable Compliance

An implementation is schema-v1 compliant if it can:
- create valid `Event` records,
- aggregate events into `Scene` records,
- bind scenes into `Arc` records,
- optionally generate `Epoch` records at lower frequency,
- preserve evidence references,
- support same-type supersession links.
