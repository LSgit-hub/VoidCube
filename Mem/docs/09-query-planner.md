# Query Planner v0.2

## 1. Purpose

This document defines a planning layer above the existing query interface.

MemAI v1 already exposes strong structural queries such as:

- `point_query`
- `range_query`
- `theme_evolution`
- `active_arcs`
- `chapter_summary`
- `evidence_trace`

These are useful for system integration and precise tooling, but they still require downstream callers to know which query type to use.

The purpose of the query planner is to let a caller ask in natural task language and have MemAI decide:

- which query operations to run,
- in which order,
- how to merge their outputs,
- how to keep the answer evidence-bound and uncertainty-aware.

## 2. Design Goal

The query planner should turn requests like:

- "What changed in the project this month?"
- "What is the current state of retrieval work?"
- "What unresolved blockers still affect the mainline?"
- "What stable constraints should I remember before answering?"

into a structured retrieval plan.

The planner should not:

- replace the core query engine,
- bypass evidence handling,
- silently hallucinate unsupported structure,
- become an opaque agent that cannot explain its decisions.

## 3. Position in the Architecture

The planner sits above the query engine and below final answer assembly.

Suggested flow:

```text
user request
-> query planner
-> query plan
-> query engine execution
-> evidence-aware answer assembler
-> final response
```

It should use the existing query layer rather than duplicate retrieval logic.

## 4. Core Principle

The planner is a retrieval orchestrator, not a summarizer.

Its job is to decide:

- what information to retrieve,
- what structure to prefer,
- when more evidence is needed,
- when to stop and admit uncertainty.

The summarization step should happen after planning and retrieval, not inside the planner itself.

## 5. Canonical Inputs

Suggested planner input shape:

```json
{
  "request": "What changed in the memory system this month?",
  "reference_time": "2026-03-31T00:00:00Z",
  "detail_level": "standard",
  "include_evidence": true,
  "max_results": 8,
  "mode": "default"
}
```

### Input Fields

- `request`: natural-language retrieval request
- `reference_time`: optional current time anchor for relative phrases
- `detail_level`: brief, standard, or deep
- `include_evidence`: whether final assembly should expose evidence refs
- `max_results`: soft output limit
- `mode`: planner posture such as default, audit, or conservative

## 6. Canonical Outputs

Suggested planner output shape:

```json
{
  "plan_type": "timeline_summary",
  "intent": "summarize_recent_changes",
  "steps": [
    {
      "step_type": "range_query",
      "arguments": {
        "time_start": "2026-03-01T00:00:00Z",
        "time_end": "2026-03-31T23:59:59Z",
        "topic": "memory-system"
      },
      "reason": "The request asks for changes during a bounded recent period."
    },
    {
      "step_type": "evidence_trace",
      "target_source": "top_main_arc",
      "reason": "The response should remain evidence-aware."
    }
  ],
  "answer_strategy": "timeline_first",
  "requires_audit_note": false,
  "uncertainty_flags": []
}
```

## 7. Planner Responsibilities

The planner should perform five tasks:

1. identify intent
2. infer temporal scope
3. choose retrieval primitives
4. choose answer strategy
5. attach uncertainty notes when planning confidence is limited

## 8. Intent Classes

The first cut of v0.2 should support a small, explicit intent set.

### `summarize_recent_changes`

Use when the request asks what changed over a period.

Preferred primitives:

- `range_query`
- optional `chapter_summary`
- optional `evidence_trace`

### `trace_theme`

Use when the request asks how a topic evolved.

Preferred primitives:

- `theme_evolution`
- optional `range_query`
- optional `evidence_trace`

### `inspect_current_state`

Use when the request asks what is currently active, stalled, or unresolved.

Preferred primitives:

- `active_arcs`
- optional `range_query`

### `explain_memory`

Use when the request asks where a summary or conclusion came from.

Preferred primitives:

- `evidence_trace`

### `retrieve_stable_context`

Use when the request asks for persistent preferences, facts, or constraints.

Preferred primitives:

- future `profile_lookup`
- future `fact_lookup`
- optional `memory_audit`

### `audit_or_dispute`

Use when the request asks about prior versions, contradictions, or uncertain memories.

Preferred primitives:

- `evidence_trace`
- future `memory_audit`
- future conflict-aware profile retrieval

## 9. Temporal Scope Resolution

The planner should resolve time expressions conservatively.

Examples:

- "today" -> point or short range around `reference_time`
- "this week" -> bounded week interval
- "this month" -> bounded month interval
- "recently" -> soft recent interval, possibly last 14 or 30 days depending on system policy
- "historically" -> broad range or chapter-level strategy

When the request lacks a clear time span:

- prefer current-state or theme retrieval if the wording suggests it
- otherwise attach an uncertainty note rather than inventing an arbitrary window silently

## 10. Entity and Theme Extraction

The planner should identify lightweight retrieval anchors from the request:

- topic
- entity
- subject
- memory kind
- status targets such as active, stalled, blocked, unresolved

The first cut can do this with rule-based extraction:

- keyword dictionaries
- simple alias maps
- exact phrase matches from known topics and entities

This should remain explainable and deterministic before adding optional LLM planning later.

## 11. Step Selection Rules

### 11.1 When to Use `range_query`

Prefer `range_query` when:

- the request includes a bounded period
- the user asks "what changed"
- the user asks for recent developments or progress

### 11.2 When to Use `theme_evolution`

Prefer `theme_evolution` when:

- the user asks "how has X evolved"
- the request centers on one theme or entity across time

### 11.3 When to Use `active_arcs`

Prefer `active_arcs` when:

- the user asks what is current
- the user asks about active, stalled, dormant, or unresolved work

### 11.4 When to Use `chapter_summary`

Prefer `chapter_summary` when:

- the requested horizon is large
- the user asks for phase-level or historical overview
- the time span is too broad for scene/arc-heavy narration alone

### 11.5 When to Use `evidence_trace`

Prefer `evidence_trace` when:

- the user asks "why"
- the user asks for support
- the planner is in audit mode
- the answer uses a strong claim that should remain inspectable

## 12. Answer Strategies

The planner should explicitly choose an answer assembly strategy.

Suggested initial strategies:

### `timeline_first`

Use for recent changes and bounded summaries.

Output posture:

- observed developments first
- main arcs and turning points second
- evidence and uncertainties last

### `theme_first`

Use for longitudinal topic evolution.

Output posture:

- timeline of shifts
- current state
- major turning points

### `state_first`

Use for current status or blocker inspection.

Output posture:

- active lines
- stalled or unresolved lines
- open questions

### `audit_first`

Use for disputes, revisions, or provenance inspection.

Output posture:

- current claim
- prior versions
- conflict branches
- evidence chain

### `stable_context_first`

Use for future profile/fact memory requests.

Output posture:

- stable constraints, preferences, and facts
- timeline context only if needed

## 13. Uncertainty Handling

The planner should produce uncertainty notes when:

- the time range cannot be resolved cleanly
- the request mixes several intents
- no query primitive clearly dominates
- available retrieval signals are too weak

Example flags:

```json
{
  "uncertainty_flags": [
    "time_window_is_implicit",
    "request_has_multiple_possible_intents"
  ]
}
```

These flags should later help downstream answer assembly produce honest wording.

## 14. Planner Modes

Suggested initial modes:

### `default`

- balanced planning
- evidence-aware but concise

### `conservative`

- narrower retrieval
- stronger preference for uncertainty notes
- avoid combining too many query types

### `audit`

- maximize traceability
- prefer evidence and supersession visibility
- include disputed or weak memories only with explicit warnings

## 15. Canonical Step Schema

Suggested step shape:

```json
{
  "step_type": "range_query",
  "arguments": {},
  "reason": "Why this query was selected",
  "required": true,
  "optional": false,
  "consumes": [],
  "produces": ["range_summary"]
}
```

Field semantics:

- `step_type`: retrieval primitive
- `arguments`: exact query arguments
- `reason`: human-readable rationale
- `required`: whether final answer depends on this step
- `optional`: whether the plan may skip this step when upstream results are weak
- `consumes`: identifiers of prior outputs needed by this step
- `produces`: labeled output artifact

## 16. Example Plans

### Example A: Recent Changes

Request:

- "What changed in the project this month?"

Plan:

1. resolve current month bounds
2. run `range_query`
3. if a strong main arc exists, run `evidence_trace` on the top result
4. assemble with `timeline_first`

### Example B: Theme Evolution

Request:

- "How has retrieval evolved so far?"

Plan:

1. extract topic = retrieval
2. run `theme_evolution`
3. optionally run broad `range_query` if timeline density is low
4. assemble with `theme_first`

### Example C: Current Blockers

Request:

- "What unresolved blockers still affect the mainline?"

Plan:

1. run `active_arcs`
2. filter for stalled, blocked, or unresolved patterns
3. optionally run `range_query` over recent period for supporting developments
4. assemble with `state_first`

### Example D: Explain a Summary

Request:

- "Where did this conclusion come from?"

Plan:

1. identify target memory id if available
2. run `evidence_trace`
3. assemble with `audit_first`

## 17. Interaction with Profile and Fact Memory

Once v0.2 adds profile/fact memory, the planner should also detect requests such as:

- "What preferences should I remember?"
- "What stable constraints apply before answering?"
- "Has the user's language preference changed?"

In those cases:

- stable memory retrieval should happen before timeline retrieval
- timeline retrieval should be used only as supporting context

This is why the planner should choose an explicit answer strategy rather than assuming timeline-first for every request.

## 18. Failure Rules

The planner should fail conservatively.

Bad planner behavior includes:

- selecting a broad historical summary when the user asked about a recent bounded period
- inventing a theme not grounded in the request
- omitting evidence planning for audit-oriented questions
- answering mixed-intent requests as if they were unambiguous

Preferred failure posture:

- generate a narrower plan
- attach uncertainty flags
- request a limited clarification only if absolutely necessary

## 19. Benchmark Guidance

Add planner-specific fixture families such as:

- recent period summary requests
- theme evolution requests
- current-state requests
- audit and provenance requests
- mixed-intent ambiguous requests
- profile/fact retrieval requests

Suggested metrics:

- intent_classification_accuracy
- temporal_scope_accuracy
- query_step_selection_accuracy
- evidence_step_recall
- uncertainty_restraint

## 20. Minimal Viable v0.2 Slice

The first usable planner is good enough if it can:

- classify 4 to 6 common intents
- resolve simple temporal phrases
- generate explicit plans using existing query primitives
- explain why each step was selected
- attach uncertainty flags for ambiguous requests

This can all be implemented heuristically at first.

## 21. Recommended Implementation Order

1. define planner input and output schema
2. add rule-based intent classifier
3. add temporal scope resolver
4. add step selection rules
5. add answer strategy selection
6. add uncertainty flags
7. add planner benchmark fixtures
8. later consider optional LLM-assisted planning

## 22. Success Criteria

The planner is successful when a caller no longer needs to know MemAI's internal query taxonomy to use the system effectively.

That means the caller can ask:

- "What changed?"
- "What is still active?"
- "How did this evolve?"
- "What should I remember?"
- "Where did this come from?"

and MemAI can translate those into retrieval behavior that remains structured, evidence-aware, and auditable.
