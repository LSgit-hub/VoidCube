"""UI-independent receipt returned when turn input is queued."""

from __future__ import annotations

from enum import Enum


class TurnInputRoute(str, Enum):
    NEXT_TURN = "next_turn"
