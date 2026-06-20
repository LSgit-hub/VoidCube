# Mainline and Sideline Rules v1

## 1. Purpose

This document defines how the system classifies developments into mainlines, sidelines, dormant lines, and noise. The purpose is to ensure that long-term memory reflects enduring narrative structure rather than isolated dramatic moments.

## 2. Definitions

- `Mainline`: a sustained and structurally central line of development.
- `Sideline`: a secondary but still memory-worthy line with weaker continuity or impact.
- `Dormant Line`: a previously important line currently inactive but not historically closed.
- `Noise`: material not worth promoting into durable long-term memory.

## 3. Governing Principle

Line importance is determined by continuity and historical consequence, not by emotional vividness alone.

## 4. Scoring Dimensions

Each candidate line receives normalized scores in the range `[0.0, 1.0]` across the following dimensions.

### `frequency`
- How often the line or theme reappears.
- High score when it recurs across multiple turns, scenes, or sessions.

### `duration`
- How long the line remains active.
- High score when it spans meaningful time intervals rather than a single burst.

### `impact`
- How much the line affects downstream events, decisions, or summaries.
- High score when later scenes depend on it.

### `goal_coherence`
- Whether the line has a stable objective, tension, or organizing question.
- High score when developments can be read as part of one continuing thread.

### `reactivation`
- Whether the line returns after pauses.
- High score when it is reintroduced and remains relevant.

### `dependency`
- Whether other events are attached to or explained by this line.
- High score when the line acts as a structural backbone.

## 5. Default Scoring Formula

```text
arc_score =
  0.25 * frequency +
  0.20 * duration +
  0.20 * impact +
  0.15 * goal_coherence +
  0.10 * reactivation +
  0.10 * dependency
```

This is a v1 default, not an immutable law. It may later be learned or tuned.

## 6. Classification Thresholds

- `arc_score >= 0.70`: classify as `main`
- `0.40 <= arc_score < 0.70`: classify as `side`
- `arc_score < 0.40`: keep in short-term staging or discard as noise

These thresholds should be applied after minimum evidence checks.

## 7. Minimum Evidence Checks

A line should not be classified as `main` unless at least one of the following holds:
- it spans at least three scenes,
- it reappears across multiple sessions or dates,
- it produces at least one turning point with downstream effects,
- it clearly organizes a cluster of dependent events.

## 8. Promotion Rules

Promotion from side to main is allowed when:
- the line persists across new scenes,
- downstream dependency increases,
- a once-local issue becomes globally relevant,
- or a repeated unresolved question becomes a dominant organizing force.

Promotion must create an explicit update in the current-valid view.

## 9. Demotion Rules

Demotion from main to side is allowed when:
- prior centrality was overestimated,
- evidence shows limited downstream influence,
- the line resolves quickly and does not structure later history,
- or its apparent continuity was due to duplicate phrasing rather than real persistence.

Demotion should trigger a revision record, not a silent relabel.

## 10. Dormancy Rules

A line becomes `dormant` when:
- it was historically important,
- it has not received significant updates for a configured interval,
- and there is insufficient evidence that it has truly resolved.

Recommended v1 dormancy trigger:
- no material update for 30 days or 5 scene windows,
- with previous importance above a mainline-preservation threshold.

Dormant lines remain retrievable and should still influence chapter summaries when historically relevant.

## 11. Closure Rules

A line may be marked `resolved` or `closed` when:
- its organizing goal has been completed,
- its central tension is explicitly ended,
- or later evidence confirms that the line no longer structures subsequent scenes.

Closure is not deletion.

## 12. Noise Rules

Material should remain noise when:
- it appears only once,
- it has low novelty,
- it produces no later dependency,
- it carries no stable temporal or structural significance,
- or it is pure conversational filler.

Noise may remain in ephemeral cache for local context but should not enter durable memory.

## 13. Guardrails Against False Mainlines

The following should not become mainlines by default:
- isolated emotional spikes,
- one-off complaints,
- speculative self-descriptions,
- hypothetical scenarios,
- casual preferences stated once,
- interruptions with no later consequence.

## 14. Evidence Escalation Policy

When evidence is mixed:
- prefer `side` over `main`,
- prefer `dormant` over `closed`,
- prefer `unknown` over stable interpretation.

The system should underclaim rather than overclaim.

## 15. Implementation Notes

Recommended v1 flow:
1. cluster related events,
2. compute dimension scores,
3. apply minimum evidence checks,
4. assign `main`, `side`, `dormant`, or `noise`,
5. write explanation metadata for audit.

Suggested audit fields:

```json
{
  "classification_score": 0.76,
  "classification_reason": [
    "reappears across 4 scenes",
    "organizes downstream decisions",
    "remains active over 6 weeks"
  ]
}
```
