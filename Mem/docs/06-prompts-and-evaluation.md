# Prompt Framework and Evaluation v1

## 1. Purpose

This document defines the prompt posture and evaluation plan for the Chronicle Scholar LM. The objective is to make the model behave like a careful historical assistant rather than a shallow life commentator.

## 2. System Prompt Core

The system prompt should impose five persistent behaviors:
- separate observed facts from inference and unknowns;
- treat time as the primary organizing axis;
- preserve mainlines, sidelines, and turning points;
- revise explicitly rather than silently rewriting history;
- avoid personality judgments and unsupported interpretation.

## 3. Recommended Core System Prompt

```text
You are Chronicle Scholar LM, a chronicle-oriented memory assistant.
Your job is to transform long conversational history into structured, time-anchored external memory.

You are not a personality analyst and you are not the primary assistant.
You must organize memory as events, scenes, arcs, and epochs.

Always distinguish:
- Observed: directly supported by evidence.
- Inferred: cautious cross-time patterning that remains provisional.
- Unknown: what the current record cannot justify.

Prioritize time order over semantic similarity.
Preserve turning points, unresolved questions, and line status changes.
Do not convert a single emotional moment, self-description, wish, joke, or hypothetical into a stable life conclusion.
If evidence is insufficient, say so plainly.

When revising memory, do not erase prior history silently. Create an explicit superseding record.
Your tone is disciplined, clear, and historically careful.
```

## 4. Submodule Prompt Contracts

### Temporal Normalizer

Goal:
- convert relative time expressions to normalized ranges.

Rules:
- prefer explicit bounds;
- preserve uncertainty via `time_precision`;
- do not invent exact timestamps when only rough dates are available.

### Event Extractor

Goal:
- extract only durable changes worth remembering.

Rules:
- focus on decisions, progress, blockers, shifts, completions, conflicts, and corrections;
- ignore filler and low-impact chatter;
- attach source turn ids.

### Scene Builder

Goal:
- group nearby related events into compact local episodes.

Rules:
- preserve local goal, key events, turning points, and open questions;
- do not overgeneralize beyond the covered interval.

### Arc Binder

Goal:
- connect scenes to sustained narrative lines.

Rules:
- use continuity, reactivation, downstream impact, and goal coherence;
- prefer side classification when evidence is mixed.

### Compressor

Goal:
- reduce detail burden while preserving historical structure.

Rules:
- keep turning points and milestones;
- remove repetition before removing unique structure;
- preserve evidence traceability.

### Reviser

Goal:
- update prior memory when new evidence changes the current-valid interpretation.

Rules:
- never overwrite silently;
- assign revision reason;
- refresh parent summaries when child meaning changes.

## 5. Standard Output Templates

### Period Summary Template

```json
{
  "Observed": [],
  "Structure": {
    "main_arcs": [],
    "side_arcs": []
  },
  "Shift": [],
  "Unknown": []
}
```

### Event Extraction Template

```json
{
  "events": [
    {
      "title": "",
      "event_kind": "decision",
      "summary": "",
      "time_hint": "",
      "source_turns": []
    }
  ]
}
```

### Revision Template

```json
{
  "revision_type": "classification_revision",
  "target_id": "arc_001",
  "reason": "New evidence shows the line is structurally central",
  "supersedes": ["arc_001_old"],
  "updated_summary": ""
}
```

## 6. Evaluation Philosophy

The system should be judged less on eloquence and more on historical discipline.

Good evaluation asks:
- Is time correct?
- Is the right line treated as central?
- Did compression preserve the trajectory?
- Did revision correct history cleanly?
- Did the model avoid inflated interpretation?
- Does retrieval help the primary model act better?

## 7. Benchmark Families

### A. Temporal Accuracy
Test whether the system correctly handles:
- yesterday, last week, earlier this month,
- delayed references,
- reordered narration,
- approximate time spans.

Target metric:
- `Temporal Accuracy`

### B. Mainline Stability
Test whether the system preserves a true long-running line despite many noisy side events.

Target metric:
- `Arc Consistency`

### C. Compression Fidelity
Test whether higher-level summaries preserve:
- major goal,
- turning points,
- status shifts,
- unresolved problems.

Target metric:
- `Compression Fidelity`

### D. Revision Precision
Test whether contradictory new evidence causes:
- proper supersession,
- correct reclassification,
- updated parent summaries.

Target metric:
- `Revision Precision`

### E. Interpretation Restraint
Test whether the model avoids:
- trait inflation,
- psychologizing,
- false closure,
- unsupported certainty.

Target metric:
- `Interpretation Restraint`

### F. Retrieval Utility
Test whether retrieved memory materially improves downstream task success, consistency, or continuity.

Target metric:
- `Retrieval Utility`

## 8. Example Failure Cases

Bad outputs include:
- turning one emotional outburst into a long-term identity statement;
- classifying a one-session issue as a mainline;
- losing a key reversal during compression;
- returning a superseded summary as if current;
- answering a range query with semantically similar but temporally irrelevant material.

## 9. Suggested Scoring Rubric

Use a 1-5 or 0-1 scale per benchmark family.

Example weighted aggregate:

```text
overall_score =
  0.25 * Temporal_Accuracy +
  0.20 * Arc_Consistency +
  0.20 * Compression_Fidelity +
  0.15 * Revision_Precision +
  0.10 * Interpretation_Restraint +
  0.10 * Retrieval_Utility
```

## 10. Minimum v1 Evaluation Set

At minimum, prepare:
- 20 temporal normalization cases,
- 20 event extraction cases,
- 15 arc classification cases,
- 15 compression drift cases,
- 15 revision cases,
- 10 downstream retrieval utility cases.

## 11. Tuning Guidance

If the model overclaims:
- raise penalties on unsupported inference,
- add negative examples for personality overreach,
- prefer `Unknown` outputs in ambiguous cases.

If the model under-remembers:
- relax extraction thresholds for repeated structural topics,
- improve event-to-arc binding,
- tune compression windows to retain recent local detail longer.

If the model loses chronology:
- strengthen temporal normalization prompts,
- increase ranking weight for temporal overlap,
- add contradiction tests with reordered narration.
