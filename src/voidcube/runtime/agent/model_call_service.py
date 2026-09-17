"""Single model-call boundary used by the turn orchestrator."""

from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from ...infrastructure.llm.request import ChatRequestConfig, build_chat_completion_kwargs
from ...infrastructure.llm.stream_response import StreamChunkUpdate


class ModelTransport(Protocol):
    def stream(
        self, request: dict[str, Any], *,
        on_update: Callable[[StreamChunkUpdate], None],
        on_first_delta: Callable[[], None] | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """Response and request metadata for one transport attempt."""

    request_kwargs: dict[str, Any]
    response: Any
    duration_seconds: float


class ModelCallService:
    """Build and execute one model request without owning retry policy."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def call(
        self,
        *,
        config: ChatRequestConfig,
        messages: Sequence[dict[str, Any]],
        transport: ModelTransport,
        on_update: Callable[[StreamChunkUpdate], None],
        on_first_delta: Callable[[], None] | None = None,
        before_send: Callable[[dict[str, Any]], None] | None = None,
    ) -> ModelCallResult:
        request_kwargs = build_chat_completion_kwargs(config, messages)
        if before_send is not None:
            before_send(request_kwargs)
        started = self._clock()
        response = transport.stream(
            request_kwargs,
            on_update=on_update,
            on_first_delta=on_first_delta,
        )
        return ModelCallResult(
            request_kwargs=request_kwargs,
            response=response,
            duration_seconds=max(0.0, self._clock() - started),
        )


__all__ = ["ModelCallResult", "ModelCallService"]
