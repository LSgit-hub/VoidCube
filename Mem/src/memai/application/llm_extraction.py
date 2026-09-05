"""Shared LLM-first ChroniclePipeline construction with result caching.

Both the Tier 1 → Tier 2 bridge and the memory service build the same
LLM-first pipeline. Factoring it here with a cached extraction adapter means:

- **defaulting**: LLM extraction is used whenever a Mem LLM client resolves
  (the ``extraction`` role), falling back to the heuristic backend only when
  no client/key is configured;
- **caching**: the same turn batch compresses deterministically, so the LLM
  extraction result is cached by (task, model, input hash) to bound cost.
"""

from __future__ import annotations

from typing import Any, Sequence

from memai.repository.contracts import MemoryRepository
from memai.repository.llm_cache import (
    TASK_EXTRACT,
    build_cache_key,
    open_cached,
    open_cached_with_repository,
    store_cached,
    store_cached_with_repository,
)


def _turn_input(turns: Sequence[Any]) -> str:
    return "\n".join(f"[{t.turn_id}] {t.speaker}: {t.text}" for t in turns)


class CachedLLMExtractionAdapter:
    """Adapt OpenAICompatibleLLMClient → LLMExtractionClient with caching."""

    def __init__(
        self,
        llm,
        db_path,
        *,
        model: str = "",
        repository: MemoryRepository | None = None,
    ) -> None:
        self._llm = llm
        self._db_path = str(db_path)
        self._model = model or ""
        self._repository = repository

    def extract_events(self, turns: Sequence[Any]):
        valid_turn_ids = {str(getattr(turn, "turn_id", "")) for turn in turns}
        if not valid_turn_ids:
            return []
        input_text = _turn_input(turns)
        cache_key = build_cache_key(TASK_EXTRACT, self._model, input_text)
        cached = None
        try:
            if self._repository is not None:
                cached = open_cached_with_repository(self._repository, cache_key)
            else:
                cached = open_cached(self._db_path, cache_key)
        except Exception:
            cached = None
        cached_payload = self._usable_payload(cached, valid_turn_ids)
        if cached_payload:
            return cached_payload

        # Use the client's canonical extraction protocol. It supplies the
        # registered prompt, structured turns, provider-specific request
        # shape, and tolerant JSON decoding in one place.
        result = self._llm.extract_events(turns)
        payload = self._usable_payload(result, valid_turn_ids)
        if not payload:
            return []
        try:
            if self._repository is not None:
                store_cached_with_repository(
                    self._repository,
                    cache_key=cache_key,
                    task=TASK_EXTRACT,
                    model=self._model,
                    input_text=input_text,
                    result=payload,
                )
            else:
                store_cached(
                    self._db_path,
                    cache_key=cache_key,
                    task=TASK_EXTRACT,
                    model=self._model,
                    input_text=input_text,
                    result=payload,
                )
        except Exception:
            pass
        return payload

    @staticmethod
    def _usable_payload(result: Any, valid_turn_ids: set[str]) -> list[dict[str, Any]]:
        """Keep only extraction envelopes that can reference this batch."""
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
            return []
        payload = [item for item in result if isinstance(item, dict)]
        if not payload:
            return []
        has_valid_source = any(
            isinstance(item.get("source_turns"), Sequence)
            and not isinstance(item.get("source_turns"), (str, bytes))
            and any(str(turn_id) in valid_turn_ids for turn_id in item["source_turns"])
            for item in payload
        )
        return payload if has_valid_source else []


def build_llm_first_pipeline(
    db_path,
    *,
    role: str = "extraction",
    repository: MemoryRepository | None = None,
):
    """Build a ChroniclePipeline — LLM-first with heuristic fallback.

    Returns an LLM-backed pipeline (cached extraction + LLM scholar) when a
    Mem LLM client resolves for ``role`` (falling back to the default role),
    else a plain heuristic ``ChroniclePipeline``.
    """
    from memai.pipeline import ChroniclePipeline

    try:
        from memai.model_config import resolve_mem_llm_client

        extraction_client, extraction_model = resolve_mem_llm_client(role=role)
        if extraction_client is None:
            extraction_client, extraction_model = resolve_mem_llm_client(
                role="default"
            )
        scholar_client, _ = resolve_mem_llm_client(role="default")
    except Exception:
        return ChroniclePipeline()

    if extraction_client is None:
        return ChroniclePipeline()
    if scholar_client is None:
        scholar_client = extraction_client

    try:
        from memai.extraction import EventExtractor, LLMEventExtractionBackend
        from memai.scholar import LLMScholarBackend

        extraction_backend = LLMEventExtractionBackend(
            client=CachedLLMExtractionAdapter(
                extraction_client,
                db_path,
                model=extraction_model,
                repository=repository,
            )
        )
        return ChroniclePipeline(
            event_extractor=EventExtractor(backend=extraction_backend),
            scholar_backend=LLMScholarBackend(client=scholar_client),
        )
    except Exception:
        return ChroniclePipeline()
