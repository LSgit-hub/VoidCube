# Mem Module Architecture Analysis Report

## 1. Overview

The `Mem` module (`memai`) is a **chronicle-based memory pipeline** for an AI agent. It transforms raw conversation transcripts into structured, queryable, and maintainable long-term memory. The system follows a layered architecture inspired by narrative chronicle theory: turns → events → scenes → arcs → epochs.

**Scale (2026-07-19)**: 27 source files (~9,086 lines), 18 test files (~2,571 lines), 108 passing tests.

---

## 2. Core Architecture Layers

### Layer 1: Raw Input (TranscriptTurn)
- **Schema**: `TranscriptTurn` — turn_id, speaker, text, timestamp
- **Input formats**: JSON files, dicts, direct construction
- **Pipeline entry**: `ChroniclePipeline.ingest()` accepts `Sequence[TranscriptTurn]`

### Layer 2: Event Extraction
- **Module**: `extraction.py`
- **Key class**: `EventExtractor` uses `TemporalNormalizer` for time parsing
- **Backends**:
  - `HeuristicEventExtractionBackend` — rule-based extraction (default)
  - `LLMEventExtractionBackend` — LLM-assisted extraction with fallback
- **Output**: `Event` objects with kind (decision/progress/blocker/shift/completion/conflict/correction), topics, entities, importance, confidence, novelty, impact_scope

### Layer 3: Scene Building
- **Module**: `scene_builder.py`
- **Clustering logic**: Events on the same day with shared topics OR within 6 hours
- **Scholar integration**: `HeuristicScholarBackend.summarize_scene()` or `LLMScholarBackend`
- **Output**: `Scene` objects with goals, key events, turning points, open questions

### Layer 4: Arc Binding
- **Module**: `arc_binder.py`
- **Clustering logic**: Scenes sharing topics OR within 21 days
- **Temporal scoring**: `HeuristicTemporalScorer` evaluates continuity, impact, goal coherence
- **Classification**: Scores ≥0.70 → MAIN, ≥0.40 → SIDE, <0.40 → UNDETERMINED
- **Dormancy detection**: 30+ days since last scene → DORMANT
- **Output**: `Arc` objects with state machine (EMERGING→ACTIVE→STALLED/DORMANT→RESOLVED)

### Layer 5: Epoch Building
- **Module**: `epoch_builder.py`
- **Logic**: Groups arcs into epoch-level summaries when temporal spread exceeds thresholds
- **Output**: `Epoch` objects with themes, major arcs, chapter shifts, long-term effects

### Layer 6: Profile Memory Extraction
- **Module**: `extraction.py` (ProfileMemoryExtractor)
- **Pattern matching**: Extracts preferences, constraints, definitions, facts from text
- **Languages**: English and Chinese (e.g., "指的是", "必须", "默认")
- **Conflict resolution**: `normalize_profile_memories()` detects contradictory values for same (subject, predicate) pairs
- **Certainty states**: OBSERVED, INFERRED, PENDING_VERIFICATION, DISPUTED, CONFIRMED

---

## 3. Query System

### Query Engine (`query.py`)
- Full CRUD-like interface over all memory layers
- Supports filtering by status, certainty, time ranges, topics, entities
- Methods: `query_events`, `query_scenes`, `query_arcs`, `query_epochs`, `query_profiles`
- Evidence tracing: `trace_evidence_for` links back to source turns

### Query Planner (`query_planner.py`)
- Intent classification: 5 modes:
  1. `explain_memory` → audit_first strategy
  2. `retrieve_stable_context` → stable_context_first strategy
  3. `trace_theme` → theme_first strategy
  4. `inspect_current_state` → state_first strategy
  5. Default → timeline_first strategy
- Temporal scope resolution via `TemporalNormalizer`
- Uncertainty flags propagation

### Answer Assembler (`answer_assembler.py`)
- Transforms query results into structured answers
- Strategy-specific assembly methods (timeline_first, theme_first, state_first, audit_first, stable_context_first)
- **Full i18n support**: Chinese localization of all answer sections
- Language detection: Chinese character presence → "zh", else "en"
- Unknown/uncertainty reporting

---

## 4. Governance System

### Governance Events (`governance.py`)
- Event types: CANDIDATE_REVIEW, PROBE_APPROVAL, SWITCH_APPROVAL, SELF_EVOLUTION_*, EXECUTION_OUTCOME, ROLLBACK_OUTCOME, MEMORY_MAINTENANCE
- Decisions: APPROVE, APPROVE_WITH_WATCH, DEFER, REJECT, CANCEL, PAUSE, ROLLBACK_REQUIRED, COMPLETED, FAILED, RECORD_ONLY
- Risk levels: LOW, MEDIUM, HIGH, CRITICAL, UNKNOWN
- Failure types: BOUNDARY_VIOLATION, PROBE_FAILURE, WATCH_WINDOW_FAILURE, EXECUTION_FAILURE, ROLLBACK_FAILURE, INSUFFICIENT_EVIDENCE

### Governance Repository (`governance_repository.py`)
- JSONL-based append-only log
- Failure sample similarity search (cosine on hash fingerprints)
- Evidence summarization for supervisor decisions

### Pipeline Integration
- `ChroniclePipeline.record_governance_event()`, `query_governance_events()`, `query_failure_samples()`, `summarize_governance_context()`
- Lazy initialization of `GovernanceEventRepository`
- Default path: `~/.VoidCube/soul/mem_governance.jsonl`

---

## 5. LLM Integration

### LLM Client (`llm_client.py`)
- OpenAI-compatible API abstraction
- Thread-safe JSON completion (`safe_complete_json`)
- Provider capabilities resolution (JSON-only vs chat models)
- Model configuration via `model_config.py` with VoidCube integration
- Built-in provider profiles: openai, deepseek, openrouter, ollama

### Protocol (`llm_protocol.py`)
- Version: `memai.llm.v1`
- Task schemas for: extractor.events, scholar.scene, scholar.arc, scholar.revision
- Payload building/unwrapping with multiple response format compatibility
- Flexible response parsing: result/output/response keys, protocol-stripped payloads

### Scholar Backends (`scholar.py`)
- `HeuristicScholarBackend`: Rule-based scene/arc analysis (default)
- `LLMScholarBackend`: LLM-enhanced with heuristic fallback
- Sanitization layer: `_coerce_text`, `_coerce_string_list`, `_coerce_float`, `_coerce_enum_value`

---

## 6. Maintenance & Compression

### Compression Policy (`compression_policy.py`)
- `CompressionPolicy` protocol with `decide()` method
- `HeuristicCompressionPolicy`: Rule-based compression decisions
- Adaptive: `AdaptiveCompressionPolicyAdapter` + `AdaptiveCompressionClient` for LLM-driven policy
- Actions: COMPRESS, ARCHIVE, PRUNE, RETAIN

### Maintenance Engine (`maintenance.py`)
- Rebuilds scenes/arcs/epochs after modifications
- Revision records with full audit trail
- `MemoryMaintenanceEngine.apply_plan()` and `revise_by_id()`

### Diff Engine (`diffing.py`)
- `MemoryDiffEngine` compares two `PipelineResult` states
- `MemoryDiffReport`: Added/removed events, scenes, arcs, epochs, profile memories
- Tracks status transitions and importance/confidence changes

---

## 7. Benchmarking System (`benchmarking.py`)

### Multiple Benchmark Types:
1. **Standard Benchmark**: Transcript + expectation JSON → pipeline → metric comparison
2. **Planner Benchmark**: Tests query planner correctness
3. **Provider Contract Benchmark**: Validates LLM provider compatibility
4. **Prompt Pack Matrix**: Compares different prompt packs across fixtures

### Metrics:
- Structural accuracy (events, scenes, arcs match)
- Classification quality (arc state, status)
- Temporal precision
- Profile memory extraction quality

---

## 8. Data Models (Schema)

All core types inherit from `BaseMemoryUnit` with common fields:
- id, type, title, summary, timespan_start/end, time_precision
- importance, confidence, status, main_or_side
- topics, entities, evidence_refs, parent_ids, child_ids
- compression_level, timestamps (created/updated/last_reviewed)

**Hierarchy**:
```
TranscriptTurn → Event → Scene → Arc → Epoch
                         ↕
                   ProfileMemory (flat key-value triples)
```

**Enums**:
- TimePrecision: EXACT, DAY, WEEK, MONTH, APPROX
- Status: ACTIVE, DORMANT, CLOSED, SUPERSEDED
- ArcState: EMERGING, ACTIVE, STALLED, DORMANT, RESOLVED
- EventKind: DECISION, PROGRESS, BLOCKER, SHIFT, COMPLETION, CONFLICT, CORRECTION
- ImpactScope: LOCAL, THREAD, ARC, EPOCH
- MainOrSide: MAIN, SIDE, UNDETERMINED
- MemoryKind: PREFERENCE, CONSTRAINT, DEFINITION, FACT
- CertaintyState: OBSERVED, INFERRED, PENDING_VERIFICATION, DISPUTED, CONFIRMED

---

## 9. Key Design Patterns

1. **Strategy Pattern**: ScholarBackend (Heuristic/LLM), TemporalScorer, CompressionPolicy, EventExtractionBackend
2. **Pipeline Pattern**: ChroniclePipeline chains extractors/builders in fixed order
3. **Protocol/Interface**: Multiple Protocols (ScholarBackend, TemporalScorer, CompressionPolicy, ModalityAdapter)
4. **Factory Pattern**: Pipeline components injected via constructor, defaults provided
5. **Observer Pattern**: Governance events logged for audit trail
6. **Builder Pattern**: SceneBuilder, ArcBinder, EpochBuilder each implement build() methods
7. **Double Dispatch**: AnswerAssembler dispatches by strategy type
8. **Fallback Chain**: LLM → Heuristic fallback in scholar and extraction backends

---

## 10. Temporal Normalization

`TemporalNormalizer` handles both English and Chinese expressions:
- Exact dates (ISO format)
- Relative: today/yesterday/tomorrow, 今天/昨天/明天
- Days ago/later: X days ago, X天前, in X days, X天后
- Weeks: this week/last week, 本周/这周/上周, X weeks ago, X周前
- Months: this month/last month, 本月/上个月, X months ago, X个月前
- Vague: recently/lately, 最近/前阵子/近期 (→ 14-day window)

Returns `TemporalSpan` with computed start/end and precision/confidence.

---

## 11. Observations & Strengths

1. **Well-layered architecture**: Clear separation between extraction, building, querying, and governance
2. **Dual backend support**: Every LLM-dependent component has a heuristic fallback
3. **Comprehensive i18n**: Chinese and English support throughout (temporal, extraction, answers)
4. **Governance-first design**: Built-in audit trail, risk assessment, and rollback mechanisms
5. **Protocol-based LLM integration**: Standardized JSON protocol with flexible response parsing
6. **Benchmark-driven development**: Multiple benchmark types ensure quality
7. **Compression-aware**: Built-in policies for memory lifecycle management
8. **108 passing tests**: Good test coverage across all modules

---

## 12. Potential Improvement Areas

1. **No vector embedding support**: All queries are keyword/filter-based; semantic search would benefit large memory stores
2. **Single-threaded pipeline**: Could parallelize event extraction for large transcripts
3. **JSONL governance store**: No indexing; linear scan for failure sample queries
4. **Heuristic thresholds hardcoded**: Scene clustering (6h, same day), arc clustering (21 days), dormancy (30 days) — could be configurable
5. **No incremental ingestion**: Pipeline re-processes all turns each time
6. **Limited modality support**: AudioSegmentAdapter and ImageCaptionAdapter defined but minimal integration
7. **No distributed storage**: JSONFileMemoryStore is file-based only
