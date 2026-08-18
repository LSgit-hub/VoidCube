"""Provider-facing prompt policy constants."""

from __future__ import annotations

# Models that require the provider's developer role instead of system role.
DEVELOPER_ROLE_MODELS: tuple[str, ...] = ("gpt-5",)

__all__ = ["DEVELOPER_ROLE_MODELS"]

