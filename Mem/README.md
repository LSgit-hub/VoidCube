# MemAI

MemAI is a time-first memory management toolkit for long-running language model interactions.

## VoidCube Integration Position

MemAI is VoidCube's active long-term memory domain layer. The Memory Service owns
HTTP, Tier 1 SQLite state, maintenance scheduling, backup/restore, and the single
Tier 1 to Tier 2 transaction. MemAI owns `Event`, `Scene`, `Arc`, `Epoch`,
extraction, hierarchy construction, query semantics, and maintenance policy.

The active integration contract is defined in
[`docs/mem-integration-contract.md`](../docs/mem-integration-contract.md). Neither
layer may maintain a second bridge or a second long-term truth store.

VoidCube stores Memory runtime data under
`VOIDCUBE_HOME/runtime/memory/`:

- `memory.db`: Tier 1, archive, Tier 2, and compression-quality audit data;
- `backups/`: validated online SQLite backups with bounded rotation;
- `exports/`: explicit versioned JSON exports.

Tier 1 relevance decay is based on elapsed time and a persisted
`last_decay_at` anchor. Compression is accepted only after event coverage,
source backlink completeness, compression ratio, and degraded fraction pass the
configured gate. A rejected batch stays active in Tier 1 and its evidence is
written to `compression_quality_audit`.

VoidCube exposes one bounded `/recall` path across active Tier 1 turns and
structured Tier 2 memory. It performs multilingual concept-term planning,
time/topic filtering, mixed relevance/recency/importance ranking, near-duplicate
suppression, per-session diversity, and a strict context budget. Every result
retains source-turn and score evidence. This is intentionally described as
hybrid recall rather than vector semantic search: there is no embedding column
or chat-model-generated pseudo-vector path. A future embedding index still
requires a protocol covering model/version, write, backfill, and invalidation.

The first implementation pass in this repository focuses on a `Chronicle Scholar LM` design:
- structured memory objects: `Event`, `Scene`, `Arc`, `Epoch`
- explicit time normalization
- event extraction from mixed Chinese and English transcripts
- scene construction from ordered events
- evidence-aware serialization and storage

## Project Layout

- `docs/`: design specifications and rules
- `src/memai/`: implementation code
- `tests/`: smoke tests for core behavior
- `examples/`: runnable example
- `benchmarks/fixtures/`: starter benchmark fixtures
- `benchmarks/provider_contracts/`: transport-level provider compatibility fixtures

## Quick Start

```bash
python -m pip install -e .[dev]
pytest
python examples/demo.py
```

## Current Scope

This v0.1 codebase implements the first executable layer of the design docs:
- schema models from `docs/02-schema-v1.md`
- temporal normalization for common Chinese and English time expressions
- pluggable event extraction backends
- pluggable scholar backends for scene, arc, and revision generation
- temporal scorer interface ready for future sequence or transformer modules
- pluggable compression policy for long-range memory aging
- prompt pack registry for externalized LM behavior tuning
- heuristic scene building
- arc binding and epoch aggregation
- time-first query engine and CLI entrypoint
- persistent memory state and incremental updates
- benchmark runner with multi-fixture scoring

The remaining MemAI domain priorities are stronger arc/epoch scoring, richer
benchmark datasets, and evidence-driven revision quality. Cross-service
governance and execution closure is tracked by the root VoidCube architecture,
not redefined in this subproject.

The architecture now also includes a `TemporalScorer` insertion point, so a future time-series Transformer can be attached as a scoring module without replacing the rest of the memory pipeline.

The scorer contract is now explicit through `TemporalSequenceRequest` and `TemporalSequencePrediction`, which define the handoff boundary for a future Transformer-based time model.

Compression policy is also modularized, so future adaptive retention or learned forgetting policies can be attached without rewriting maintenance logic.

The benchmark runner now scores more than simple count floors. In addition to topic and structure coverage, it also reports metrics such as:
- `structure_integrity`
- `evidence_integrity`
- `range_query_quality`
- `chapter_query_quality`
- `revision_precision`
- `interpretation_restraint`

Expectation fixtures remain backward compatible, and can optionally add richer probes such as `range_query_checks`, `chapter_query_checks`, `revision_probe`, `forbidden_topics`, and `forbidden_summary_terms`.

The starter fixture set now includes:
- mixed-language extraction smoke tests
- chapter-growth continuity checks
- relative-time and revision-propagation probes
- interpretation-restraint probes

## CLI

```bash
memai ingest benchmarks/fixtures/sample_transcript.json
memai query benchmarks/fixtures/sample_transcript.json --query-type theme --theme memory-system
memai query benchmarks/fixtures/sample_transcript.json --query-type chapter --start 2026-03-01 --end 2026-03-31
memai maintain benchmarks/fixtures/sample_transcript.json --reference-time 2027-03-31T00:00:00Z
memai revise benchmarks/fixtures/sample_transcript.json --target-id event:0 --revision-type factual_revision --reason "polish wording" --summary "..."
memai state-init state.json benchmarks/fixtures/sample_transcript.json
memai state-update state.json more_turns.json
memai state-query state.json --query-type theme --theme memory-system
memai benchmark --fixture benchmarks/fixtures
memai benchmark-prompt-packs --fixture benchmarks/fixtures --prompt-packs default,conservative,high-recall,scholar-heavy
memai benchmark-provider-contracts --fixture benchmarks/provider_contracts
```

`revise` supports either a concrete memory id or a selector like `event:0`, `scene:0`, `arc:0`, or `epoch:0`.

Query commands also support richer retrieval controls such as:

```bash
memai query transcript.json --query-type range --start 2026-03-01 --end 2026-03-31 --theme memory-system --detail-level brief --max-results 3
memai query transcript.json --query-type theme --theme memory-system --include-superseded
memai state-query state.json --query-type active --status-filter active,dormant --max-results 5
```

Useful query flags:
- `--status-filter active,dormant`
- `--detail-level brief|standard|deep`
- `--max-results 10`
- `--include-superseded`
- `--no-evidence`

`state-update` performs an incremental rebuild around newly added turns instead of blindly recomputing the entire history.

`state-update` now also returns a diff report with human-readable change explanations so you can see which lines strengthened, became dormant, or formed new chapters.

That diff report also includes a structured `mainline_report`, so downstream tools can consume promoted mainlines, dormant lines, reactivated lines, and new chapters without parsing prose.

Any transcript-building command can switch to an LLM extraction backend with flags like:

```bash
memai ingest transcript.json --backend llm --model gpt-4o-mini --api-key-env OPENAI_API_KEY
```

If your provider is only partially OpenAI-compatible, you can select a capability profile or override the chat endpoint path:

```bash
memai ingest transcript.json --backend llm --provider-profile legacy-compatible
memai ingest transcript.json --backend llm --base-url https://example.com/api --chat-completions-path /custom/chat
```

The compatibility layer can now adapt a few more common provider quirks:

```bash
memai ingest transcript.json --backend llm --provider-profile developer-role
memai ingest transcript.json --backend llm --provider-profile user-only
memai ingest transcript.json --backend llm --provider-profile text-choice
memai ingest transcript.json --backend llm --response-format-style json_object_string
memai ingest transcript.json --backend llm --system-prompt-style inline_user
memai ingest transcript.json --backend llm --response-content-style output_text
memai ingest transcript.json --backend llm --provider-profile-file config/provider-profiles.json --provider-profile vendor-gateway
```

Built-in provider profiles:
- `openai`: standard OpenAI-compatible chat completions behavior
- `generic`: same transport shape as `openai`, useful as a neutral default
- `legacy-compatible`: omits `response_format`
- `developer-role`: sends the instruction prompt with the `developer` role
- `user-only`: inlines the instruction prompt into the user message
- `text-choice`: reads model text from `choices[0].text`
- `output-text`: reads model text from `output[*].content[*].text` or top-level `output_text`

Provider behavior can also be configured with environment variables:
- `OPENAI_PROVIDER_PROFILE`
- `OPENAI_PROVIDER_PROFILE_FILE`
- `OPENAI_CHAT_COMPLETIONS_PATH`
- `OPENAI_SYSTEM_PROMPT_STYLE`
- `OPENAI_RESPONSE_FORMAT_STYLE`
- `OPENAI_RESPONSE_CONTENT_STYLE`

When Mem is used inside VoidCube, the preferred user-facing configuration entry is
the VoidCube CLI. The saved configuration should keep the memory plugin and the
Mem LLM separate:

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
      governance_reasoner:
        provider: deepseek
        model: deepseek-reasoner
```

`memory.provider` selects the memory plugin. `memory.llm.*` configures the model
Mem uses for LLM-backed memory work. `memory.llm.roles.*` can override that
default for a specific Mem role. Explicit `memai` CLI flags still override saved
config for tests and temporary experiments.

Custom provider profiles can be stored in JSON so you can add new adapters without editing Python code:

```json
{
  "profiles": {
    "vendor-gateway": {
      "extends": "legacy-compatible",
      "chat_completions_path": "/vendor/chat",
      "system_prompt_style": "developer",
      "response_format_style": "json_object_string",
      "response_content_style": "choices_text"
    }
  }
}
```

`extends` can reuse any built-in profile and override only the fields that differ.

You can also point the LLM pipeline at a custom prompt pack directory:

```bash
memai ingest transcript.json --backend llm --prompt-pack-dir src/memai/prompts/default
```

Or choose a built-in prompt pack variant:

```bash
memai ingest transcript.json --backend llm --prompt-pack conservative
memai ingest transcript.json --backend llm --prompt-pack high-recall
memai ingest transcript.json --backend llm --prompt-pack scholar-heavy
```

The default prompt pack contains task-specific prompts for:
- `extractor.events`
- `scholar.scene`
- `scholar.arc`
- `scholar.revision`

Built-in prompt pack variants:
- `default`: balanced behavior for general development
- `conservative`: stricter, lower-recall, more evidence-first behavior
- `high-recall`: broader capture of emerging signals and tentative developments
- `scholar-heavy`: richer historical framing for scene, arc, and revision tasks

You can compare prompt packs directly with the benchmark matrix command. This is useful when you want to evaluate which prompt style gives the best balance for your fixtures.

You can also run provider transport contract fixtures to regression-test OpenAI-compatible adapters without making live network calls:

```bash
memai benchmark-provider-contracts --fixture benchmarks/provider_contracts
```

Those contract fixtures can also reference a relative `provider_profile_file`, so the same custom JSON profile used at runtime can be validated in regression tests.

## Backend Hook

You can swap the extraction backend in Python code:

```python
from memai import ChroniclePipeline, EventExtractor, HeuristicEventExtractionBackend, LLMEventExtractionBackend

# default
pipeline = ChroniclePipeline(event_extractor=EventExtractor(backend=HeuristicEventExtractionBackend()))

# LM-backed contract
# client must implement: extract_events(turns) -> list[dict]
# pipeline = ChroniclePipeline(
#     event_extractor=EventExtractor(backend=LLMEventExtractionBackend(client)),
#     scholar_backend=LLMScholarBackend(client),
# )
```

## Modular Expansion

The current build remains focused on memory management, but the internal shape is now modular enough for future adapters.

- `MemorySignal` and `ModalityAdapter` define a canonical bridge from other modalities into the memory pipeline.
- `TextTurnAdapter` is the first adapter and shows how text turns map into normalized memory signals.
- `AudioSegmentAdapter` and `ImageCaptionAdapter` are placeholder adapters that show how non-text inputs can be normalized before entering the memory pipeline.
- Future `audio`, `image`, or `video` adapters can emit the same signal shape before entering the memory pipeline.
