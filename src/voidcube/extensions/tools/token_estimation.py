"""Tool schema token estimation policy.

This module intentionally performs discovery lazily.  The CLI may import tool
configuration while rendering help, but token counting should only happen when
the checklist needs a status line.
"""

from __future__ import annotations

import json
from typing import Any


class ToolTokenEstimator:
    """Memoized estimator with injectable registry and encoder factories."""

    def __init__(self) -> None:
        self._cache: dict[str, int] | None = None

    def estimate(
        self,
        *,
        encoding_factory: Any | None = None,
        registry: Any | None = None,
    ) -> dict[str, int]:
        if self._cache is not None:
            return self._cache
        try:
            if encoding_factory is None:
                import tiktoken

                encoding_factory = lambda: tiktoken.get_encoding("cl100k_base")
            encoder = encoding_factory()
        except Exception:
            self._cache = {}
            return self._cache

        try:
            if registry is None:
                from . import model_tools  # noqa: F401
                from .registry import registry as tool_registry

                registry = tool_registry
            names = registry.get_all_tool_names()
        except Exception:
            self._cache = {}
            return self._cache

        counts: dict[str, int] = {}
        for name in names:
            schema = registry.get_schema(name)
            if schema:
                payload = json.dumps({"type": "function", "function": schema})
                counts[name] = len(encoder.encode(payload))
        self._cache = counts
        return counts


_DEFAULT_ESTIMATOR = ToolTokenEstimator()


def estimate_tool_tokens(**kwargs: Any) -> dict[str, int]:
    return _DEFAULT_ESTIMATOR.estimate(**kwargs)


__all__ = ["ToolTokenEstimator", "estimate_tool_tokens"]
