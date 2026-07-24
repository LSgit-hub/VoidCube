"""Model identifier handling.

Provider model IDs are authoritative values returned by each live ``/models``
endpoint. VoidCube therefore preserves them instead of guessing vendor
prefixes or translating version names with local compatibility tables.
"""

from __future__ import annotations


_AGGREGATOR_PROVIDERS: frozenset[str] = frozenset({"openrouter", "nous"})


def normalize_model_for_provider(model_input: str, target_provider: str) -> str:
    """Return the provider-supplied model ID unchanged apart from whitespace."""
    return str(model_input or "").strip()
