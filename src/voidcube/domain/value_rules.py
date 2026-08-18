"""Pure normalization rules shared across application and adapters."""

from __future__ import annotations

from typing import Any


TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


def is_truthy_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


__all__ = ["TRUTHY_STRINGS", "is_truthy_value"]
