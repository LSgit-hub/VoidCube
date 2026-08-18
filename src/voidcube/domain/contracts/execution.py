"""Canonical execution outcomes shared by tools and process adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ExecutionState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


class Retryability(str, Enum):
    SAFE = "safe"
    RECONCILE_FIRST = "reconcile_first"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StructuredError:
    code: str
    summary: str


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: str
    reference: str
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: ExecutionState
    exit_code: int | None = None
    error: StructuredError | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    retryability: Retryability = Retryability.UNKNOWN
    started_at: datetime | None = None
    finished_at: datetime | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def state_from_exit_code(exit_code: int | None) -> ExecutionState:
    """Map a command exit code without interpreting missing evidence as failure."""
    if exit_code is None:
        return ExecutionState.UNKNOWN
    if exit_code == 0:
        return ExecutionState.SUCCEEDED
    return ExecutionState.FAILED


TERMINAL_EXECUTION_STATES = frozenset(ExecutionState)


__all__ = [
    "EvidenceRef",
    "ExecutionResult",
    "ExecutionState",
    "Retryability",
    "StructuredError",
    "TERMINAL_EXECUTION_STATES",
    "state_from_exit_code",
    "utc_now",
]
