# Chronicle Scholar LM Design Docs

This directory contains the v1 design specifications for the Mem memory framework.

Inside VoidCube, this framework is not just an optional memory helper. It is the design base for the long-term memory and soul-side governance layer.

In the current VoidCube baseline, that also means:

- VoidCube acts as a mother system
- the dual body architecture is really two child Agent slots
- Mem helps the mother system preserve identity continuity and decide which improved child Agent may safely face the user

Its job is not to analyze personality or preserve full transcripts. Its job is to organize long-running interaction history into a structured, revisable, compressible timeline, and to provide the durable memory substrate that governance protocols can rely on.

Core principles:
- Time is the primary index.
- Narrative structure is more important than raw detail accumulation.
- Compression is preferred over deletion.
- Revision is explicit and versioned.
- Evidence and uncertainty must remain visible.

In VoidCube terms, these docs mainly cover:
- how Mem stores long-term memory truth,
- how Mem preserves identity continuity evidence,
- how memory stewardship and supervisor governance share one soul domain, and
- how downstream governance can rely on audit-safe memory rather than raw transcript drift.

They also support one practical project goal:

- keep the mother system able to improve child Agents without losing the soul truth that must survive body replacement

Documents:
- `docs/01-system-constitution.md`: role, constraints, epistemic rules, and system boundaries.
- `docs/02-schema-v1.md`: formal memory object schema for `Event`, `Scene`, `Arc`, and `Epoch`.
- `docs/03-mainline-sideline-rules.md`: scoring and routing rules for mainlines, sidelines, dormant lines, and noise.
- `docs/04-compression-revision-rules.md`: lifecycle rules for compression, revision, supersession, and controlled forgetting.
- `docs/05-query-interface.md`: retrieval interfaces, ranking flow, and response shapes.
- `docs/06-prompts-and-evaluation.md`: prompt framework and benchmark plan.
- `docs/07-profile-and-fact-memory.md`: design for stable non-timeline memory such as preferences, constraints, definitions, and durable facts.
- `docs/08-uncertainty-and-conflict-rules.md`: rules for certainty state, conflict tracking, supersession, and audit-safe retrieval.
- `docs/09-query-planner.md`: planning layer for turning natural requests into structured query execution plans.
- `docs/10-governance-event-schema.md`: governance event schema for body switching, self-evolution decisions, rollback, and failure samples.
- `docs/MemAI v0.2 设计路线图.md`: roadmap for the v0.2 evolution of the memory framework.

Recommended implementation order:
1. Temporal normalization
2. Event extraction
3. Scene building
4. Arc binding
5. Compression and revision
6. Query layer
7. Prompt refinement and benchmark tuning
8. Profile and fact memory
9. Governance event schema and indexing for VoidCube self-evolution
