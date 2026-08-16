# Memory Resource Contract

VoidCube Memory uses independent classification axes. A record must not use
one field to represent more than one axis.

## Resource axes

- Storage tier: active Turn, archived Turn, durable memory.
- Durable type: Event, Scene, Arc, Epoch, or Profile.
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
