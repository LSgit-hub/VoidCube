"""Provider-neutral model identifier handling."""

from __future__ import annotations


AGGREGATOR_PROVIDERS: frozenset[str] = frozenset({"openrouter", "nous"})


def normalize_model_for_provider(model_input: str, target_provider: str) -> str:
    """Preserve provider-supplied model IDs apart from surrounding whitespace."""
    return str(model_input or "").strip()
