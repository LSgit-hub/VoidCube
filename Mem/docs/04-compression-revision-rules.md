# Compression and Revision Rules v1

## 1. Purpose

This document defines how the memory system compresses old material, revises outdated summaries, and forgets low-value detail without losing historical coherence.

## 2. Central Idea

The system should age memory in the same way a careful historian ages notes:
- recent material remains detailed,
- older material becomes structured summary,
- long-range history becomes chapter-level abstraction,
- but major transitions and valid corrections remain recoverable.

## 3. Compression Ladder

The default abstraction ladder is:

`Event -> Scene -> Arc -> Epoch`

Compression means transferring informational value upward while reducing local detail burden.

## 4. Temporal Compression Windows

Recommended v1 windows:
- `0-7 days`: keep detailed events and provisional scenes.
- `7-30 days`: consolidate events into scenes and remove low-value duplicates.
- `30-180 days`: strengthen arc summaries and retire redundant scene detail.
- `180+ days`: maintain epoch-level chapter summaries plus selected arc anchors.

These are policy defaults and may later become adaptive.

## 5. Compression Priorities

When compressing, preserve in this order:
1. turning points,
2. corrections and reversals,
3. milestones,
4. active unresolved questions,
5. structural continuity,
6. supporting detail only if still useful.

## 6. What Compression May Remove

Compression may remove or collapse:
- repeated phrasing,
- near-duplicate events,
- low-impact local detail,
- temporary wording differences,
- filler that does not affect trajectory.

## 7. What Compression Must Preserve

Compression must preserve:
- event order,
- changes in direction,
- status changes,
- line classification changes,
- evidence traceability,
- unresolved historically significant tensions.

## 8. Compression Pass Types

### Scene Consolidation
- merge overlapping or redundant events;
- generate a compact local summary;
- keep key events and local turning points.

### Arc Consolidation
- absorb aging scenes into a trajectory summary;
- retain milestones and turning points;
- downgrade detailed scenes when their information is fully represented.

### Epoch Consolidation
- summarize major historical periods;
- preserve major arcs and chapter shifts;
- reduce the need to load old intermediate detail unless requested.

## 9. Compression Preconditions

Before compressing an object, the system should verify:
- the object is older than the relevant policy window,
- its children have enough evidence to support a higher-level summary,
- no unresolved revision is pending,
- and the summary target will preserve critical structure.

## 10. Revision Triggers

Revision should trigger when:
- a timestamp or temporal order was wrong,
- an event was attached to the wrong line,
- a sideline became a mainline,
- a mainline was overstated,
- a prior summary omitted a turning point,
- new evidence contradicts an old conclusion,
- compression produced drift or overstatement.

## 11. Revision Types

Recommended v1 revision taxonomy:

```text
time_correction
attachment_correction
classification_revision
factual_revision
compression_upgrade
confidence_update
closure_update
```

## 12. Revision Procedure

When revision is required:
1. identify the affected object,
2. locate supporting and conflicting evidence,
3. generate a revised object,
4. mark the older object as `superseded`,
5. link the new object via `supersedes`,
6. propagate updates to parent summaries if needed,
7. retain auditability.

## 13. Revision Invariants

- Revisions must never silently overwrite historical state.
- Revised summaries must remain traceable to evidence.
- Superseded objects should not appear in default retrieval unless audit or historical comparison is requested.
- Parent summaries must be refreshed when child meaning materially changes.

## 14. Controlled Forgetting

The system forgets by stages.

### Stage 1: detail thinning
- remove redundant and low-impact event detail.

### Stage 2: summary substitution
- replace older clusters with scene or arc summaries.

### Stage 3: archival retirement
- retain only compact historical anchors for very low-value material.

Hard deletion should be rare in v1 and limited to low-value, non-structural residue.

## 15. Mainline Preservation Rule

Mainlines are compressed, not discarded.

Even when a mainline becomes dormant or resolved, the system must preserve:
- its arc summary,
- its major milestones,
- its turning points,
- its closure or dormancy state.

## 16. Sideline Retirement Rule

Sidelines may be retired when all of the following hold:
- low importance,
- low reactivation,
- no major downstream dependency,
- no role in chapter shifts,
- and content already represented elsewhere.

Before retirement, the system should attempt one final compressed label or sentence-level archive.

## 17. Drift Detection

Compression drift occurs when a summary becomes more interpretive, more certain, or more global than its evidence supports.

Signals of drift:
- stronger claims than source evidence,
- trait-like language absent in the record,
- omitted turning points,
- inverted event order,
- false closure.

Drift should trigger review and possible revision.

## 18. Review Cadence

Recommended v1 background maintenance:
- daily: event and scene hygiene,
- weekly: scene consolidation and line rescoring,
- monthly: arc review and dormancy checks,
- quarterly: epoch review and deep compression audit.

## 19. Audit Metadata

Each compression or revision action should log:

```json
{
  "action_type": "compression|revision|retirement",
  "reason": "aging_window_elapsed",
  "source_ids": ["scene_004", "scene_005"],
  "result_id": "arc_010",
  "timestamp": "2026-03-22T12:00:00Z"
}
```

## 20. Safety Posture

If unsure whether to compress or revise aggressively, the system should choose the more conservative operation:
- compress less,
- preserve more,
- and mark uncertainty explicitly.
