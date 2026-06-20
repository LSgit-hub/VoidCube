# Query Interface v1

## 1. Purpose

This document defines how downstream systems query the memory layer. The interface is built around time-first retrieval, structural relevance, and evidence-aware return formats.

## 2. Retrieval Philosophy

The query layer should answer questions such as:
- what happened at a specific time,
- what the mainlines were during a period,
- how a theme evolved,
- which arcs are currently active or dormant,
- where a summary came from.

The interface should not default to raw transcript replay.

## 3. Core Query Types

### `point_query`
Return what happened at or near a specific time point.

### `range_query`
Return major developments across a time interval.

### `theme_evolution`
Return the history of a topic, entity, or project across time.

### `active_arcs`
Return currently active, stalled, or dormant major lines.

### `chapter_summary`
Return an epoch-level overview for a large historical period.

### `evidence_trace`
Return the evidence chain behind a memory object or summary.

## 4. Canonical Request Shape

```json
{
  "query_type": "range_query",
  "time_start": "2026-01-01T00:00:00Z",
  "time_end": "2026-03-31T23:59:59Z",
  "topic": "memory-system",
  "entity": "user",
  "status_filter": ["active", "dormant"],
  "detail_level": "brief|standard|deep",
  "include_evidence": true,
  "include_superseded": false,
  "max_results": 10
}
```

## 5. Query Parameters

- `query_type`: one of the supported query modes.
- `time_start`, `time_end`: optional temporal bounds.
- `topic`: optional thematic filter.
- `entity`: optional entity filter.
- `status_filter`: allowed lifecycle states.
- `detail_level`: output density control.
- `include_evidence`: whether to expose evidence references.
- `include_superseded`: whether obsolete versions are allowed.
- `max_results`: upper result bound.

## 6. Retrieval Pipeline

The default retrieval flow is:
1. temporal filtering,
2. structural filtering,
3. semantic expansion,
4. ranking,
5. compression-to-response.

### Stage 1: Temporal Filtering
- identify relevant intervals;
- expand slightly for approximate or overlapping ranges if needed.

### Stage 2: Structural Filtering
- prefer arcs for range summaries;
- prefer scenes for local episodes;
- prefer epochs for large chapter queries.

### Stage 3: Semantic Expansion
- use topics, entities, and semantic similarity to recover relevant candidates missed by exact filters.

### Stage 4: Ranking
- rank by temporal fit, structural relevance, importance, semantic fit, and recency.

### Stage 5: Compression-to-Response
- produce the smallest coherent answer shape for the requested detail level.

## 7. Ranking Formula

Recommended v1 ranking formula:

```text
final_rank =
  0.35 * temporal_overlap +
  0.25 * structural_relevance +
  0.20 * importance +
  0.10 * semantic_similarity +
  0.10 * recency
```

## 8. Canonical Response Shapes

### Range Summary Response

```json
{
  "result_type": "range_summary",
  "observed": ["..."],
  "main_arcs": ["..."],
  "side_arcs": ["..."],
  "turning_points": ["..."],
  "open_questions": ["..."],
  "evidence_refs": ["scene_001", "arc_003"],
  "confidence": 0.88
}
```

### Point Query Response

```json
{
  "result_type": "point_summary",
  "events": ["..."],
  "local_scene": "...",
  "related_arc": "...",
  "evidence_refs": ["event_014"],
  "confidence": 0.91
}
```

### Theme Evolution Response

```json
{
  "result_type": "theme_evolution",
  "theme": "memory-system",
  "timeline": [
    {
      "time": "2026-03",
      "shift": "Moves from broad idea to formal design"
    }
  ],
  "active_state": "active",
  "major_turning_points": ["scene_001"],
  "evidence_refs": ["arc_001"],
  "confidence": 0.87
}
```

### Evidence Trace Response

```json
{
  "result_type": "evidence_trace",
  "target_id": "arc_001",
  "summary": "...",
  "support_chain": [
    "arc_001",
    "scene_001",
    "event_001",
    "turn_011"
  ]
}
```

## 9. Detail Levels

### `brief`
- prefer one to three arc-level statements;
- minimize evidence exposure.

### `standard`
- include core structure, turning points, and active questions.

### `deep`
- include lower-layer support and more of the event-to-scene chain.

## 10. Superseded Handling

Default behavior:
- exclude superseded records from standard retrieval,
- include only current-valid views in concise answers,
- expose superseded material only when `include_superseded = true` or for audit mode.

## 11. Query Safety Rules

The system should refuse overclaiming answers when:
- time bounds are too vague,
- the requested theme lacks longitudinal evidence,
- all relevant results are superseded and unresolved,
- evidence is too sparse to support a structured answer.

In these cases, the interface should return a partial result plus an uncertainty note.

## 12. Minimal v1 API Surface

The smallest useful set is:
- `point_query`
- `range_query`
- `theme_evolution`
- `active_arcs`
- `evidence_trace`

`chapter_summary` can be added once epoch generation is stable.
