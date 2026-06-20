# Profile and Fact Memory v0.2

## 1. Purpose

This document defines the first non-timeline memory layer for MemAI v0.2.

The v1 system is strong at organizing historical development through:

- `Event`
- `Scene`
- `Arc`
- `Epoch`

However, many durable memories are not naturally expressed as time-bounded episodes. Examples include:

- stable user preferences,
- persistent project constraints,
- long-lived terminology definitions,
- explicit identity statements,
- durable factual assertions that remain relevant across many scenes.

The goal of v0.2 is to add a structured memory family for these items without weakening the time-first design of the existing timeline chain.

## 2. Design Goal

The new layer should let the system remember:

- what remains true across time,
- what the user or project consistently prefers,
- what definitions or rules govern future work,
- what facts have been corrected, disputed, or replaced.

It should not:

- replace the timeline chain,
- infer personality traits from sparse signals,
- turn one-off statements into stable identity claims,
- silently overwrite prior fact states.

## 3. Core Concept

Profile and fact memory is a parallel memory family, not a new step inside `Event -> Scene -> Arc -> Epoch`.

That means:

- timeline memory answers: "what happened and how did it evolve?"
- profile/fact memory answers: "what should remain stably known right now?"

These two layers should reference each other, but they serve different retrieval purposes.

## 4. Memory Types

v0.2 should begin with a single shared type family called `ProfileMemory`, with a required subtype field:

```text
preference | identity | constraint | definition | fact
```

This keeps the initial implementation small while still allowing differentiated behavior.

### `preference`

Use for durable user or project preferences.

Examples:

- prefers Chinese responses
- prefers concise summaries
- prefers evidence-first answers

### `identity`

Use for explicit self-described roles or persistent identity facts only when directly stated or strongly evidenced.

Examples:

- user is the project owner
- assistant acts as a memory steward

### `constraint`

Use for operating rules, guardrails, or non-negotiable project constraints.

Examples:

- do not silently rewrite history
- evidence must remain visible
- provider compatibility must be regression tested

### `definition`

Use for stable terminology or conceptual mappings.

Examples:

- mainline means structurally central long-running development
- epoch means chapter-level historical period

### `fact`

Use for durable assertions that remain useful outside a single moment.

Examples:

- heuristic backend is the default
- LLM backend is optional
- state updates are incremental rather than full rebuilds

## 5. Canonical Shape

Suggested initial shape:

```json
{
  "id": "profile_001",
  "type": "profile_memory",
  "memory_kind": "constraint",
  "subject": "project",
  "predicate": "requires",
  "value": "evidence-first retrieval",
  "summary": "The project requires evidence-first retrieval rather than unsupported abstraction.",
  "confidence": 0.92,
  "certainty_state": "observed",
  "status": "active",
  "valid_from": "2026-03-22T00:00:00Z",
  "valid_to": null,
  "evidence_refs": ["turn_001", "scene_001"],
  "source_turns": ["turn_001"],
  "parent_timeline_refs": ["scene_001", "arc_001"],
  "supersedes": [],
  "conflict_refs": [],
  "created_at": "2026-03-22T00:00:00Z",
  "updated_at": "2026-03-22T00:00:00Z",
  "last_reviewed_at": "2026-03-22T00:00:00Z"
}
```

## 6. Field Semantics

- `id`: immutable memory identifier
- `type`: fixed as `profile_memory`
- `memory_kind`: subtype classifier
- `subject`: the entity, actor, or scope the memory is about
- `predicate`: normalized relation label
- `value`: the current asserted value
- `summary`: human-readable restatement
- `confidence`: confidence in the assertion
- `certainty_state`: epistemic state of the assertion
- `status`: lifecycle state such as active, dormant, superseded
- `valid_from`: when the assertion becomes valid
- `valid_to`: when the assertion stops being valid, if known
- `evidence_refs`: supporting source refs
- `source_turns`: direct supporting turns, when available
- `parent_timeline_refs`: timeline memories that gave rise to the fact memory
- `supersedes`: older versions of the same logical fact
- `conflict_refs`: parallel assertions that conflict with this one

## 7. New Epistemic State

Profile/fact memory should introduce a new common field:

```text
certainty_state = observed | inferred | disputed | pending_verification | confirmed
```

### `observed`

Directly supported by explicit source material.

### `inferred`

Supported by repeated or structural evidence, but still interpretive.

### `disputed`

Conflicts with another active assertion.

### `pending_verification`

Plausible but not yet strong enough for default retrieval.

### `confirmed`

Observed and reinforced across multiple sources or revisions.

This field should likely be generalized later to more of the memory system, but v0.2 can start here.

## 8. Extraction Rules

### 8.1 Positive Extraction Criteria

Create profile/fact memory only when at least one of these is true:

- the source explicitly states a stable preference, role, constraint, or fact
- the same claim recurs across multiple turns or scenes
- the claim governs downstream decisions repeatedly
- the claim remains useful even when detached from the original local scene

### 8.2 Negative Extraction Criteria

Do not create profile/fact memory for:

- transient mood
- one-off wishes with no repetition
- hypothetical ideas not adopted
- unsupported personality conclusions
- isolated emotional expressions
- weakly implied preferences without reinforcement

### 8.3 Escalation Threshold

A safe initial heuristic:

- one explicit strong statement may create `preference`, `constraint`, or `definition`
- `identity` and more sensitive `fact` memories should require stronger support or repeated evidence

## 9. Relationship to Timeline Memory

Profile/fact memory should not break the canonical containment chain.

Instead, it should connect to timeline memory through references:

- `parent_timeline_refs`
- `evidence_refs`

Recommended behavior:

- events and scenes remain the extraction source
- arcs and epochs may reinforce profile/fact confidence
- profile/fact memory may be surfaced alongside timeline query results when relevant

## 10. Revision and Conflict Rules

### 10.1 Supersession

Use `supersedes` when a newer memory replaces an older assertion of the same logical fact.

Example:

- old: prefers long explanations
- new: now prefers concise responses

The old record should remain auditable, not deleted.

### 10.2 Conflict

Use `conflict_refs` when two live assertions cannot both be treated as current-valid.

Example:

- one memory says the user prefers Chinese
- another says the user now prefers English for code review sessions

This may later resolve through revision, but should not be flattened immediately.

### 10.3 Validity Window

`valid_from` and `valid_to` should allow time-sensitive facts such as:

- temporary preference changes
- project constraints that only apply during a phase
- role shifts over time

## 11. Query Surface

Suggested v0.2 query additions:

### `profile_lookup`

Return current stable memories for a subject or scope.

Example request:

```json
{
  "query_type": "profile_lookup",
  "subject": "user",
  "memory_kinds": ["preference", "identity"],
  "include_disputed": false
}
```

### `fact_lookup`

Return stable facts and constraints for a project, person, or entity.

### `memory_audit`

Return active, superseded, and disputed versions of the same logical assertion.

## 12. Retrieval Behavior

Default retrieval should:

- prioritize `confirmed` and `observed`
- include `inferred` only when confidence is sufficient
- hide `disputed` unless audit mode is requested
- hide `pending_verification` from concise answers by default

When a timeline answer is assembled, relevant profile/fact memory may be attached under a separate section such as:

```json
{
  "stable_context": [],
  "timeline_result": {}
}
```

This avoids mixing "what happened" with "what remains true."

## 13. Storage Strategy

Recommended initial implementation:

- store profile/fact memory alongside the persistent state file
- keep it as a separate collection from events/scenes/arcs/epochs
- preserve references to timeline ids and source turns

Suggested top-level state split:

```json
{
  "version": 1,
  "result": {
    "turns": [],
    "events": [],
    "scenes": [],
    "arcs": [],
    "epochs": [],
    "profiles": []
  }
}
```

## 14. Benchmark Guidance

v0.2 should add dedicated fixture families for profile/fact memory:

- repeated preference extraction
- one-off preference non-extraction
- explicit constraint capture
- terminology definition capture
- conflict creation between facts
- revision from old fact to new fact
- refusal to create unsupported personality memory

Key metrics:

- profile_precision
- fact_precision
- dispute_detection
- overreach_restraint
- revision_traceability

## 15. Minimum Viable v0.2 Slice

An initial implementation is good enough if it can:

- extract explicit `preference`, `constraint`, and `definition` memories
- keep them outside the timeline containment chain
- support explicit supersession
- expose a small query surface for current-valid profile/fact retrieval
- preserve evidence links and auditability

## 16. Non-Goals for the First Cut

The first v0.2 slice should not try to do all of the following yet:

- full ontology learning
- personality modeling
- probabilistic graph reasoning
- automatic conflict resolution without explicit policy
- complex LLM-only extraction pipelines

The first cut should remain conservative, explainable, and evidence-bound.

## 17. Recommended Implementation Order

1. Add the schema definition for `ProfileMemory`
2. Extend persistent state storage
3. Implement heuristic extraction for explicit preferences, constraints, and definitions
4. Add query support for current-valid profile/fact retrieval
5. Add revision and conflict handling
6. Add benchmark fixtures
7. Add optional LLM-backed extraction later

## 18. Success Criteria

This design is successful when MemAI can cleanly answer both of these classes of questions:

- "What happened over time in this project?"
- "What stable preferences, constraints, and facts should the system remember right now?"

Without this layer, MemAI remains primarily a timeline system.
With this layer, it starts becoming a fuller long-term memory framework.
