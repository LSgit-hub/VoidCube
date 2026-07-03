# Soul Layer

## 1. Position

This document defines Mem as the soul layer of VoidCube.

It answers five practical questions:

- what the soul is in this system
- how memory and governance coexist inside Mem
- what the soul layer's memory stewardship and supervisor governance roles mean
- which inputs and outputs belong to the soul layer
- how soul-side governance relates to gateway, CLI, and executors

## 2. Definition

In VoidCube, the soul is not:

- the current process
- the current model context
- the current active body

The soul is the long-lived identity and governance substrate that persists across body replacement.

Today, Mem already carries the key soul responsibilities:

- long-term memory
- identity continuity
- evolution history
- body lineage
- switch and rollback records
- governance decisions

So Mem is not just a memory backend. It is the identity and governance core of the system.

In the current VoidCube architecture, this soul-side governance does not exist for its own sake. It exists to support the continuous improvement, replacement, and safe activation of better Agent bodies for the user-facing system.

Another baseline framing is now important:

- VoidCube acts as the mother system
- the dual body slots act as two child Agent instances
- Mem does not deliver the whole mother system to the user
- Mem helps the mother system decide which improved child Agent may become user-facing

## 3. Internal Roles

The soul layer is best understood as three internal roles inside the same Mem domain.

### 3.1 Memory Core

Responsible for durable state:

- user long-term memory
- agent long-term memory
- constitutional rules
- evolution events
- body switch history
- rollback history

### 3.2 Governor Engine

Responsible for structured governance decisions.

At the current stage, this is primarily deterministic rather than fully model-driven.

It evaluates:

- whether a shell body may become `candidate`
- whether a `candidate` may enter `probe`
- whether a `probe` body may become `active`
- whether the current switch should be rolled back
- whether a retired body may be recycled to `shell`
- whether a queued self-evolution task may be approved, deferred, cancelled, or paused

This is the core governance role inside the soul layer.

### 3.3 Governance Audit Store

Responsible for preserving soul-side traceability:

- review requests
- decisions
- execution outcomes
- watch-window observations
- rollback causes

The current implementation already writes governance history into soul-side storage.

## 4. Modes

Mem has two main operating roles:

- memory stewardship
- supervisor governance

These are not two separate systems. They are two responsibilities of the same soul layer.

In VoidCube architecture terms, they share one API-B governance and memory domain.

Historical docs often called them `Memory Mode` and `Governor Mode`. In the current baseline, the runtime should be understood as a continuous background governance system with a shared soul-side memory/governance domain, not as two time-window-driven modes.

That is also why this side of the system uses a separately configured model/API capability:

- the Agent-side working model handles task execution and short-term work memory
- the Mem-side model handles long-term memory compression, organization, interpretation, and governance decisions

## 5. Memory Stewardship Role

### 5.1 Purpose

The baseline responsibility of the soul layer.

### 5.2 Responsibilities

- write long-term memory
- search long-term memory
- compress history
- update identity description
- preserve evolution archives
- prepare memory-side summaries for governance use

### 5.3 Typical Inputs

- `memory_write`
- `memory_search`
- `memory_update`
- `identity_update`
- `history_compress`

### 5.4 Constraints

This role must not directly approve:

- `probe` entry
- `active` promotion
- rollback
- retired-body recycling

It may summarize facts, but it must not replace governance.

## 6. Supervisor Governance Role

### 6.1 Purpose

This governance role is responsible whenever the system enters body governance or self-evolution governance scenarios:

- candidate review
- probe approval
- body switch
- rollback
- post-switch observation
- self-evolution task planning
- execution release / defer decisions

### 6.2 Current Implementation Direction

At the current phase, supervisor governance is taking shape as a structured protocol rather than a free-form model persona.

The implemented path now includes:

- dual body slots
- structured probe reports
- deterministic switch approval
- active body pointer updates
- isolated runtime per body slot
- automatic watch-window supervision
- automatic recycle or rollback after observation
- explicit task states for self-evolution governance

This means soul-side governance already exists in executable protocol form.

### 6.3 Typical Inputs

- `body_upgrade_request`
- `health_review_request`
- `switch_request`
- `rollback_request`
- `post_switch_review`
- `self_evolution_plan_request`
- `execution_review`

### 6.4 Typical Outputs

- `approve`
- `reject`
- `approve_with_watch`
- `rollback_required`
- `defer`
- `cancel`
- `pause`
- `request_more_evidence`

### 6.5 Constraints

This governance role must:

- produce structured outputs
- preserve evidence summaries
- preserve auditability
- separate judgment from mechanical execution

This governance role must not directly perform body actions itself. It decides. Deterministic executors act.

## 7. Governance Capability Requirement

VoidCube does require a soul-side governance capability.

What is not mandatory is a separate free-form supervisor model detached from protocol.

The current recommended structure is:

- `Mem = soul layer`
- `Governor Engine = required governance capability inside the soul layer`
- `LLM = brain / work reasoning / optional governance reasoning support`

So the architectural requirement is not "a special supervisor persona must exist as a fourth being".

The actual requirement is:

- governance authority must exist,
- it must live in the soul-side domain,
- it must be audit-safe, and
- it must remain separate from mechanical execution.

That governance authority mainly serves one upgrade target:

- the Agent body and its future replacement

It is not primarily about self-upgrading the memory system, executor, or gateway first.

## 8. Optional Future Reasoner

A model-assisted governance reasoner can still exist later, but it should be layered above the deterministic governor instead of replacing the governance protocol.

Recommended future split:

- `Governor Engine`
  - deterministic
  - protocol-safe
  - primary authority for switch, rollback, and execution-window decisions
- `Governor Reasoner`
  - optional model-assisted analysis
  - used for ambiguous cases
  - used for rule proposal, anomaly interpretation, and long-term evolution analysis

This keeps identity continuity anchored in protocol rather than model drift.

## 9. Soul Inputs And Outputs

### 9.1 Inputs

Soul-layer inputs should be structured events, not loose prose.

Recommended sources:

- active body
- candidate body
- gateway
- lifecycle executor
- self-learning system

In practice, these body-originated events should still be attributable to a concrete child Agent slot with its own isolated `worktree`, `runtime`, `logs`, and metadata.

For user-originated operations, CLI should enter through gateway or controlled protocol rather than become a second governance truth source.

### 9.2 Minimum Request Shape

Requests should include:

- `request_id`
- `mode`
- `event_type`
- `body_id`
- `source_actor`
- `summary`
- `evidence`
- `constraints`
- `timestamp`

### 9.3 Minimum Response Shape

Responses should include:

- `decision`
- `confidence`
- `risk_level`
- `reasoning_summary`
- `required_actions`
- `watch_window_hint`
- `writeback_events`

## 10. Decisions Reserved For Supervisor Governance

Only supervisor governance may decide:

- whether a body may enter `probe`
- whether a `probe` body may become `active`
- whether rollback is required
- whether a retired body may be recycled
- whether a body switch preserves identity continuity
- whether a planned self-evolution task may enter execution

These decisions must not be made by:

- the active body alone
- the gateway alone
- the CLI alone
- the executor alone

## 11. Automatic Governance State

The current architecture supports an automatic governance state as part of supervisor governance.

Its current shape is:

- switch approval creates a watch window
- governance state triggers a background watch-window task
- the task evaluates health during the observation period
- if healthy and window expires cleanly, the retired body is recycled
- if unhealthy, rollback is triggered automatically
- in self-evolution governance cycles, governance may also release, defer, cancel, or pause queued work

This is the first practical form of soul-side self-regulation in the project.

## 12. Current Soul Facts Reflected In Runtime

At the current stage, the following facts are already represented in implementation:

- active body pointer
- per-slot launch target
- per-slot runtime identity
- probe report persistence
- governance history persistence
- automatic watch-window outcome recording
- governance decisions separated from executor actions

This matters because the soul layer is no longer only conceptual. It is now partially embodied in executable protocol.

## 13. Conclusion

Mem as soul layer is now best defined as:

- memory organ
- identity organ
- governance organ

The supervisor is not a required fourth metaphysical layer.

But soul-side governance is a required architectural capability.

In the current architecture, that capability is carried by Mem-aligned governance protocol:

- memory core
- deterministic governor
- audit history
- watch-window supervision
- rollback authority
- execution-window decisions

If a model-assisted governance reasoner appears later, it should be an enhancement to this foundation, not a replacement for it.

## 14. Related Docs

- project constitution: [constitution.md](../../docs/constitution.md)
- architecture baseline: [voidcube架构基线.md](../../docs/voidcube架构基线.md)
- switch protocol: [switch-protocol.md](../../docs/switch-protocol.md)
- body lifecycle: [body-lifecycle.md](../../docs/body-lifecycle.md)
- state boundary: [state-boundary.md](../../docs/state-boundary.md)
