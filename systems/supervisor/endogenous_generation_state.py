"""Single owner for the latest endogenous LM generation snapshot."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(slots=True)
class LmGenerationStateOwner:
    """Own the mutable diagnostic snapshot exposed to read-only projections."""

    _context: dict[str, Any] = field(default_factory=dict)
    _proposals: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        context_snapshot: Mapping[str, Any] | None,
        proposals: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self._context = deepcopy(dict(context_snapshot or {}))
        self._proposals = [
            deepcopy(dict(item))
            for item in proposals
            if isinstance(item, Mapping)
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "context": deepcopy(self._context),
            "proposals": deepcopy(self._proposals),
        }


__all__ = ["LmGenerationStateOwner"]
