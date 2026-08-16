# Memory Resource Contract

VoidCube Memory uses independent classification axes. A record must not use
one field to represent more than one axis.

## Resource axes

- Storage tier: active Turn, archived Turn, durable memory.
- Durable type: Event, Scene, Arc, Epoch, Profile, or a time-summary index node.
- Scope: `owner_id`, `workspace_id`, and `memory_domain`.
- Identity layer: founding, experience, or self-narrative.
- Lifecycle state: Turn compression status or durable-memory revision status.

## Timeline and provenance

`timeline_parent_id` is exclusively an abstraction edge:

```text
Event -> Scene -> Arc -> Epoch
```

`derived_from_id` is exclusively a provenance edge from a newly synthesized
memory to the source memory it summarizes. `superseded_by` is exclusively a
revision edge. These fields are not interchangeable.

Writers discard missing or type-invalid timeline targets. Database migration
removes dangling legacy targets instead of fabricating parent resources.

## Permanent time-summary index capability

The time-summary index capability has four deterministic levels:

```text
MonthSummary -> WeekSummary -> DaySummary -> SessionSummary
```

`time_summaries` stores immutable versions of these nodes. A node is scoped by
`owner_id`, `workspace_id`, and `memory_domain`, and identified logically by
`summary_type` plus `bucket_key`. Only one version of a logical bucket may have
the `active` status. Corrections create a higher version whose
`supersedes_summary_id` points to an older version of the same scoped bucket.
`source_hash` identifies the exact ordered direct-source snapshot used for
idempotency; `content_hash` identifies the normalized summary output. Neither
hash is a substitute for source references.

This is one index capability among several. Semantic, lexical, entity, relation,
and application Profile indexes may coexist and may answer queries without
traversing the calendar hierarchy. Time-summary nodes do not replace those
indexes or define a universal recall order.

`time_summary_links` stores only direct containment edges. Links must remain in
one scope and may only connect month to week, week to day, or day to session.
Generating a parent never supersedes or deletes its children. These index nodes
are outside the Turn decay and `Event -> Scene -> Arc -> Epoch` compression
lifecycle.

`session_summary_sources` records the ordered Turn IDs, timestamps, and
evidence hashes used by each immutable SessionSummary version. It deliberately
does not cascade from the active Turn table, so Turn retirement cannot erase
the historical evidence directory.

At a real Agent session boundary, the Mem Provider queues `close_session` in
the same durable outbox as Turn pairs. A close item cannot pass an older
pending, inflight, or dead-letter Turn write. The Memory Service close handler
uses the configured Mem `summarization` role and publishes only if the stored
Turn snapshot still matches the input hash. It then incrementally rebuilds the
affected DaySummary before acknowledging the durable close. A session belongs
to the natural day containing its first Turn in `time_summary_timezone`; this
keeps even a cross-midnight session in exactly one deterministic bucket.

DaySummary generation reads only active SessionSummary versions assigned to
that day, ordered by historical start time. It rechecks the exact ordered
source snapshot inside the publish transaction and records direct child edges
in `time_summary_links`. The explicit repair endpoint is
`POST /time-summaries/days/{day_key}/aggregate`. Empty days do not receive
placeholder summaries. If a time correction moves the final active session out
of a previously indexed day, that day's last active version becomes
`superseded` without an empty replacement.

Permanent means exempt from automatic decay and lifecycle retirement. An
explicit confirmed `/forget` remains authoritative: deleting a session removes
all of its SessionSummary versions and evidence rows, then walks upward through
`time_summary_links` so no parent summary retains the deleted history.

The full target behavior, including calendar bucketing and reverse expansion,
is defined in
[`../Mem/docs/11-time-summary-index-hierarchy.md`](../Mem/docs/11-time-summary-index-hierarchy.md).

## Profile memory

Profile memory is captured only from explicit user turns. Tier 2 extraction
does not write Profile records.

Scalar predicates, such as `preferred_language`, have one active slot and a
new value supersedes the previous value. Collection predicates, such as
`long_term_preference`, `allergy`, `prefers`, and `requires`, use a value-based
`slot_key`, so independent values coexist.

Legacy heuristic Profile records remain stored for audit but use the
`quarantined` status and are excluded from normal recall.

## Turn compression lifecycle

`compression_status` has four values:

- `pending`: available for Tier 1 recall and compression.
- `retry_wait`: available for Tier 1 recall; compression waits for backoff.
- `compressed`: recalled through archive or durable resources.
- `quality_quarantined`: excluded from compression but retained in Tier 1
  recall so a failed summary cannot make the source conversation disappear.

The retired integer compression flag and overloaded parent field are accepted
only by the one-time database migration. Runtime code must not read or write
them.
