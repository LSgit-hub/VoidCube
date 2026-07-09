# Governance Event Schema v0.1

## 1. Purpose

This document defines the first governance-event schema for MemAI.

MemAI already models long-running memory as:

```text
Event -> Scene -> Arc -> Epoch
```

VoidCube also needs a parallel governance memory layer that can record decisions, failures, rollbacks, and self-evolution evidence without forcing those records into ordinary life-history events.

The new object type is:

```text
governance_event
```

## 2. Position In VoidCube

In VoidCube, governance events are soul-side audit records.

They support:

- body candidate review
- probe approval or failure
- active body switching
- watch-window pass / rollback
- self-evolution task approval / defer / rejection
- boundary violations
- execution outcomes
- failure sample reuse

They do not execute actions. They preserve evidence for Mem / supervisor governance.

## 3. Core Shape

```json
{
  "id": "gov_001",
  "type": "governance_event",
  "event_type": "boundary_defer",
  "task_id": "task-123",
  "body_id": "slot-B",
  "source_actor": "supervisor",
  "decision": "defer",
  "reason": "Candidate changed files outside the child-agent boundary.",
  "risk_level": "medium",
  "confidence": 0.92,
  "git_lineage": {},
  "probe_report_ref": null,
  "evolution_boundary": {},
  "execution_result": null,
  "failure_signature": {},
  "evidence_refs": [],
  "related_event_ids": [],
  "created_at": "2026-05-26T00:00:00Z"
}
```

## 4. Required Fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | Stable governance event id. |
| `type` | string | yes | Must be `governance_event`. |
| `event_type` | string | yes | Controlled event category. |
| `source_actor` | string | yes | Producer of the governance record. |
| `decision` | string | yes | Governance decision or outcome. |
| `reason` | string | yes | Human-readable evidence-bound reason. |
| `created_at` | ISO datetime | yes | Event creation time. |

## 5. Optional But Recommended Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `task_id` | string or null | Self-evolution task id. |
| `body_id` | string or null | Body slot or child Agent identity. |
| `risk_level` | string | `low`, `medium`, `high`, or `critical`. |
| `confidence` | number | Confidence in decision/evidence quality. |
| `git_lineage` | object | Branch, commit, diff, rollback, and changed files. |
| `probe_report_ref` | string or null | Probe report pointer. |
| `evolution_boundary` | object | Agent / mother-system boundary classification. |
| `execution_result` | object or null | Executor outcome summary. |
| `failure_signature` | object | Normalized failure fingerprint for reuse. |
| `evidence_refs` | string[] | References to source reports, logs, memory objects, or commits. |
| `related_event_ids` | string[] | Links to prior governance events. |

## 6. Event Types

Initial controlled set:

```text
candidate_review
probe_approval
probe_failure
switch_approval
switch_rejection
watch_window_pass
watch_window_rollback
boundary_defer
self_evolution_approval
self_evolution_defer
self_evolution_cancel
execution_outcome
rollback_outcome
memory_maintenance
```

## 7. Decisions

Initial controlled set:

```text
approve
approve_with_watch
defer
reject
cancel
pause
rollback_required
completed
failed
record_only
```

## 8. Risk Levels

```text
low
medium
high
critical
unknown
```

Rules:

- `boundary_defer` is at least `medium` unless violations are informational only.
- `watch_window_rollback` is at least `high`.
- `probe_failure` risk depends on failed checks.
- `execution_outcome` may be `low` when completed cleanly.

## 9. Git Lineage Shape

Governance events that involve code evolution should include:

```json
{
  "source_branch": "main",
  "source_commit": "aaa111",
  "candidate_branch": "evolution/task-123",
  "candidate_commit": "bbb222",
  "active_ref": "body/slot-A",
  "rollback_ref": "body/slot-A",
  "rollback_commit": "aaa111",
  "diff_summary": "Improve agent runtime behavior.",
  "changed_files": ["agent/stream_handler.py"]
}
```

Minimum for body self-evolution:

- `candidate_commit`
- `rollback_commit`
- `changed_files`

## 10. Evolution Boundary Shape

Boundary-aware events should include:

```json
{
  "ok": false,
  "changed_files": [
    "agent/stream_handler.py",
    "systems/body_registry.py"
  ],
  "allowed_files": ["agent/stream_handler.py"],
  "forbidden_files": ["systems/body_registry.py"],
  "unknown_files": [],
  "violations": ["systems/body_registry.py"]
}
```

Rules:

- `ok=false` should usually prevent formal body handoff.
- `violations` should be indexed for failure-sample reuse.
- Boundary records are evidence, not execution commands.

## 11. Failure Signature

The `failure_signature` field is for reuse and similarity search.

Suggested shape:

```json
{
  "failure_type": "boundary_violation",
  "primary_paths": ["systems/body_registry.py"],
  "probe_checks": [],
  "risk_flags": ["mother_system_path_in_body_candidate"],
  "similarity_keys": [
    "boundary_violation:systems/body_registry.py",
    "body_candidate:mixed_agent_and_mother_paths"
  ]
}
```

Common `failure_type` values:

```text
boundary_violation
probe_failure
watch_window_failure
execution_failure
rollback_failure
insufficient_evidence
```

## 12. Index Requirements

Mem should eventually index governance events by:

- `id`
- `event_type`
- `decision`
- `task_id`
- `body_id`
- `source_actor`
- `git_lineage.candidate_commit`
- `git_lineage.rollback_commit`
- `git_lineage.changed_files`
- `evolution_boundary.violations`
- `failure_signature.failure_type`
- `failure_signature.similarity_keys`
- `created_at`

## 13. Relationship To Event / Scene / Arc / Epoch

`governance_event` is not a replacement for timeline memory.

Recommended relationship:

- Governance events are atomic audit records.
- Important governance events may later be summarized into normal `Event` / `Scene` / `Arc` memory.
- Raw governance event history must remain available for audit even after summary compression.

Example:

```text
governance_event: boundary_defer
  -> later summarized into
Event: Candidate body rejected due to mother-system path violation
```

## 14. Minimal v0.1 Compliance

An implementation is governance-event v0.1 compliant if it can:

- create a valid `governance_event`
- serialize and deserialize it without field loss
- represent `boundary_defer`
- represent `execution_outcome`
- represent `watch_window_rollback`
- expose changed files and violations for indexing

## 15. Minimal Repository

The current v0.1 repository is intentionally small and append-only.

Package location:

```text
Mem/src/memai/
```

Files:

```text
Mem/src/memai/governance.py
Mem/src/memai/governance_repository.py
```

`GovernanceEventRepository` supports:

- append-only JSONL persistence
- idempotent append by event id
- list all events
- list latest N events
- query by `event_type`
- query by `decision`
- query by `task_id`
- query by `body_id`
- query by `candidate_commit`
- query by `rollback_commit`
- query by `changed_file`
- query by `violation`
- query by `failure_type`
- query by `similarity_key`
- retrieve ranked failure samples by changed files, failure type, and similarity keys

This repository is a Mem-side governance memory primitive. It does not execute switch, rollback, upgrade, or lifecycle transitions.

## 16. Failure Sample Retrieval

`GovernanceFailureSampleQuery` supports:

- `changed_files`
- optional `failure_type`
- optional `similarity_keys`
- optional result `limit`

`GovernanceFailureSample` returns:

- the matched governance event
- a simple relevance score
- matched files
- matched similarity keys
- risk flags copied from the failure signature

Current scoring is intentionally simple:

- matched changed file or primary path increases relevance
- matched similarity key increases relevance more strongly
- explicit failure type filtering narrows the sample set

This is enough for early supervisor evidence gathering, but it is not yet a mature risk model.

## 17. Supervisor Evidence Summary

`GovernanceEvidenceSummary` provides the first supervisor-facing compression layer.

It returns:

- compact natural-language summary
- relevant governance event ids
- normalized risk flags
- suggested governance posture, such as `defer`, `approve_with_watch`, or `record_only`
- confidence score
- underlying failure samples

The current implementation is deterministic and intentionally conservative. It does not require a model call.

## 18. Model Configuration Boundary

VoidCube CLI is the user-facing entry for configuring Mem's LLM.

The canonical config path is:

```yaml
memory:
  provider: mem
  llm:
    provider: openrouter
    model: google/gemini-2.5-flash
    api_key_env: OPENROUTER_API_KEY
    base_url: https://openrouter.ai/api/v1
    provider_profile: openai
    roles:
      extraction:
        model: google/gemini-2.5-flash
      governance_summary:
        model: google/gemini-2.5-flash
      governance_reasoner:
        provider: deepseek
        model: deepseek-reasoner
```

Rules:

- `memory.provider` identifies the external memory plugin, such as `mem`.
- `memory.llm.provider` identifies the model provider used by Mem.
- Mem parses this block through `MemModelConfig`.
- Mem can resolve role-specific overrides through `MemModelConfigSet`.
- Explicit Mem CLI flags still override saved config for tests and experiments.
- Retired `memory.model` / plugin-level `memory.provider` fields are not LLM config.
- Saved VoidCube config must use `memory.llm.*` for Mem / API-B model selection.

## 19. Next Implementation Step

Use role-specific model configuration in more Mem subsystems:

- extraction already resolves the `extraction` role when the LLM backend is used
- scholar / summarization should resolve `summarization`
- governance evidence summary should resolve `governance_summary`
- optional model-assisted governance should resolve `governance_reasoner`
- future embedding / similarity search should resolve `embedding`

Provider profiles and prompt packs already exist, but they are not yet a complete model configuration system.
