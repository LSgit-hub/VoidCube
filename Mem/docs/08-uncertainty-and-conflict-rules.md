# Uncertainty and Conflict Rules v0.2

## 1. Purpose

This document defines how MemAI v0.2 should represent uncertainty, disagreement, and revision without collapsing them into a single mechanism.

The central problem is simple:

- some memories are directly observed,
- some are inferred from repeated evidence,
- some remain weak and should not be surfaced by default,
- some conflict with each other,
- some are old versions that have been explicitly replaced.

If all of these are modeled only through `status` or only through `supersedes`, the system becomes hard to trust and hard to audit.

This document introduces explicit rules for:

- epistemic state,
- conflict state,
- revision state,
- default retrieval behavior.

## 2. Design Goal

MemAI should be able to answer all of the following distinctly:

- Is this memory directly supported?
- Is this memory an inference?
- Is this memory still unresolved?
- Is this memory in conflict with another memory?
- Was this memory replaced by a newer version?

These are different questions and should remain different in the data model.

## 3. Three Separate Concepts

### 3.1 Certainty

Certainty answers:

- how justified is this memory right now?

This should be represented by:

- `certainty_state`
- `confidence`

### 3.2 Conflict

Conflict answers:

- does this memory disagree with another currently relevant memory?

This should be represented by:

- `conflict_refs`

### 3.3 Revision

Revision answers:

- was this memory explicitly replaced by a newer version of the same logical assertion?

This should be represented by:

- `supersedes`
- `status = superseded`

## 4. Core Rule

MemAI must not treat these concepts as interchangeable.

Specifically:

- a disputed memory is not automatically superseded
- a superseded memory is not necessarily disputed
- an inferred memory is not automatically disputed
- a low-confidence memory is not automatically invalid

## 5. Certainty State

Suggested shared field:

```text
certainty_state = observed | inferred | pending_verification | disputed | confirmed
```

### `observed`

Use when the memory is directly supported by explicit source material.

Examples:

- a user explicitly states a preference
- a transcript explicitly records a decision
- a repository command or code result confirms a change

### `inferred`

Use when the memory is supported by repeated or structural evidence, but still depends on interpretation.

Examples:

- a project priority is inferred from repeated decisions
- a user preference is inferred from several consistent requests

### `pending_verification`

Use when the memory is plausible but not strong enough for default retrieval.

Examples:

- only one weak mention exists
- the evidence is indirect or context-dependent
- the system believes the claim may be useful later but should not act on it yet

### `disputed`

Use when the memory remains live but conflicts with another active assertion.

Examples:

- one memory says the user prefers Chinese responses
- another says the user prefers English during technical review

The system may later resolve this conflict through revision, scoping, or both.

### `confirmed`

Use when the memory began as observed or inferred but has since been reinforced strongly enough to be treated as highly stable.

Examples:

- the same preference recurs across many sessions
- a project rule is restated and relied upon repeatedly

## 6. Confidence vs Certainty State

`confidence` and `certainty_state` should both exist because they answer different questions.

- `confidence` is scalar
- `certainty_state` is categorical

Examples:

- an `observed` memory may still have moderate confidence if the wording is ambiguous
- an `inferred` memory may have high confidence if evidence is repeated and consistent
- a `disputed` memory may have high confidence individually but still be in conflict with another strong memory

## 7. Conflict Rules

### 7.1 What Counts as Conflict

A conflict exists when two memory items cannot both be treated as current-valid under the same scope.

Typical cases:

- contradictory preferences
- conflicting factual claims
- mutually exclusive constraints
- identity claims that apply to the same subject and period

### 7.2 What Does Not Automatically Count as Conflict

The following should not automatically be marked as conflict:

- older superseded versions
- broader vs narrower scopes
- timeline progression where both statements can be true at different times
- stylistic differences in summaries with equivalent substance

### 7.3 Conflict Representation

Suggested fields:

```json
{
  "conflict_refs": ["profile_014", "profile_021"],
  "certainty_state": "disputed"
}
```

Optional future fields:

- `conflict_reason`
- `scope_note`
- `resolution_status`

## 8. Revision Rules

### 8.1 When to Use Supersession

Use supersession when a newer memory is the current-valid replacement for an older memory of the same logical assertion.

Examples:

- old preference replaced by a new preference
- old summary corrected by a revised summary
- old fact updated due to explicit correction

### 8.2 Supersession Effects

When a memory is superseded:

- the old memory remains stored
- the new memory points to the old one in `supersedes`
- the old memory becomes `status = superseded`
- default retrieval should prefer the newest current-valid version

### 8.3 Supersession Is Not Deletion

MemAI should never erase the old version silently.

The goal is auditability:

- what used to be believed,
- why it changed,
- what is current-valid now.

## 9. Timeline Examples

### Example A: Revision Without Conflict

1. March 1: "We plan to use provider profile A."
2. March 10: "We switched to provider profile B."

Correct modeling:

- old memory becomes superseded
- new memory is active
- no persistent conflict is required if the switch is explicit and sequential

### Example B: Conflict Without Supersession

1. March 1: transcript suggests the user prefers Chinese
2. March 2: another transcript suggests the user prefers English for code review

Correct modeling:

- both memories may remain active
- both may carry `certainty_state = disputed`
- conflict should remain visible until the scope is clarified or a revision resolves it

### Example C: Pending Verification

1. A single weak statement suggests the user may dislike long responses

Correct modeling:

- create a low-surface memory only if the system policy allows it
- mark it `pending_verification`
- do not default to surfacing it in concise answers

## 10. Retrieval Rules

### 10.1 Default Retrieval

Default retrieval should prefer:

1. `confirmed`
2. `observed`
3. sufficiently strong `inferred`

Default retrieval should suppress:

- `pending_verification`
- `disputed`
- `superseded`

unless the user explicitly requests audit, conflict inspection, or historical lineage.

### 10.2 Audit Retrieval

Audit retrieval should expose:

- current active version
- superseded ancestors
- conflicting parallel assertions
- evidence traces for each branch

### 10.3 Answer Assembly Rule

When a response contains a disputed or weak memory, the answer must say so plainly.

The system should avoid phrasing such as:

- "The user prefers X"

when the safer phrasing is:

- "There is mixed evidence about whether the user prefers X"

## 11. State Transitions

Suggested common transitions:

```text
pending_verification -> observed
pending_verification -> inferred
observed -> confirmed
inferred -> confirmed
observed -> disputed
inferred -> disputed
disputed -> confirmed
disputed -> superseded
```

Not all transitions need to be implemented in v0.2, but they provide a good conceptual map.

## 12. Scope and Time Rules

Many apparent conflicts are really missing scope.

Examples:

- prefers Chinese generally
- prefers English for code review

These may both be valid if the system supports scope fields such as:

- `domain`
- `context`
- `session_type`
- `valid_from`
- `valid_to`

v0.2 does not need full scope modeling immediately, but conflict detection should leave room for this resolution path.

## 13. Suggested Data Additions

For v0.2, the smallest useful additions are:

- `certainty_state`
- `conflict_refs`

For later versions, useful additions may include:

- `resolution_note`
- `resolution_strategy`
- `scope_note`
- `verification_count`
- `support_count`

## 14. Benchmark Guidance

The evaluation layer should distinguish:

- correct supersession
- correct conflict creation
- correct suppression of weak memories
- correct wording of uncertainty in retrieval

Suggested fixture families:

- explicit correction with clean supersession
- unresolved contradiction
- time-scoped preference change
- repeated reinforcement leading to confirmation
- weak evidence remaining pending
- ambiguous evidence that should not become stable memory

Suggested metrics:

- uncertainty_precision
- conflict_detection
- supersession_traceability
- restraint_under_ambiguity
- current_view_correctness

## 15. Minimum Viable v0.2 Compliance

An implementation is good enough for the first cut if it can:

- assign `certainty_state` to profile/fact memory
- mark explicit replacement through `supersedes`
- represent parallel disagreement through `conflict_refs`
- exclude `pending_verification`, `disputed`, and `superseded` from default concise retrieval
- expose them through audit retrieval

## 16. Non-Goals

The first cut should not attempt:

- full truth-maintenance logic
- automatic probabilistic conflict resolution
- global theorem-like consistency guarantees
- complex ontology-driven contradiction reasoning

The right first step is explicit structure, not overly ambitious automation.

## 17. Recommended Implementation Order

1. Add `certainty_state` to the new profile/fact memory layer
2. Add `conflict_refs` to the same layer
3. Update retrieval defaults and audit views
4. Add revision rules that keep supersession and conflict separate
5. Add benchmark fixtures for both positive and negative cases
6. Later consider extending these concepts back into timeline memory

## 18. Success Criteria

This design is successful when MemAI can clearly separate:

- what is current-valid,
- what is uncertain,
- what is disputed,
- what was previously believed,
- what changed and why.

That separation is a prerequisite for turning MemAI from a memory summarizer into a trustworthy memory system.
