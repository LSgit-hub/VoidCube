"""UI-independent orchestration for one admitted Agent turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

TExecution = TypeVar("TExecution")
TApplication = TypeVar("TApplication")
TPostprocessed = TypeVar("TPostprocessed")
TResult = TypeVar("TResult")


@dataclass(frozen=True, slots=True)
class SingleTurnExecutorPorts(Generic[TExecution, TApplication, TPostprocessed, TResult]):
    """Explicit lifecycle operations supplied by an Agent adapter."""

    execute: Callable[[], TExecution]
    apply_result: Callable[[TExecution], TApplication]
    postprocess: Callable[[TApplication], TPostprocessed]
    finish: Callable[[TApplication, TPostprocessed], None]
    finalize: Callable[[TApplication, TPostprocessed], TResult]


class SingleTurnExecutor(Generic[TExecution, TApplication, TPostprocessed, TResult]):
    """Sequence one turn's execution and result lifecycle without UI state."""

    def __init__(self, ports: SingleTurnExecutorPorts[TExecution, TApplication, TPostprocessed, TResult]):
        self.ports = ports

    def execute(self) -> TResult:
        execution = self.ports.execute()
        applied = self.ports.apply_result(execution)
        postprocessed = self.ports.postprocess(applied)
        self.ports.finish(applied, postprocessed)
        return self.ports.finalize(applied, postprocessed)


__all__ = ["SingleTurnExecutor", "SingleTurnExecutorPorts"]
