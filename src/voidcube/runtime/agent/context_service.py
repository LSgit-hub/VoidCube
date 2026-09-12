"""Coordinate context compaction without reaching into an Agent instance."""

from __future__ import annotations

import logging
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...application.ports import EventPort, MemoryPort
from ...domain.agent.context_engine import ContextEngine
from ...domain.agent.effect_outcomes import EffectOutcome, failed_effect, require_effect_outcome
from ...domain.events import ContextCompressed
from ...infrastructure.providers.model_metadata import (
    estimate_messages_tokens_rough,
    estimate_tokens_rough,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextBindings:
    """Session and presentation adapters supplied by the composition root."""

    session_id: Callable[[], str]
    system_prompt: Callable[[str | None, bool], str]
    continue_session: Callable[[str], EffectOutcome]
    todo_snapshot: Callable[[], str]
    reset_pressure: Callable[[str], None]
    reset_file_reads: Callable[[str], None]
    warn: Callable[[str], None]


class ContextService:
    def __init__(
        self, *, engine: ContextEngine, bindings: ContextBindings,
        memory: MemoryPort | None = None, events: EventPort | None = None,
    ) -> None:
        self.engine = engine
        self.bindings = bindings
        self.memory = memory
        self.events = events

    def compress(
        self, messages: list[dict[str, Any]], system_message: str | None,
        *, approx_tokens: int | None = None, task_id: str = "default",
        focus_topic: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        before_count = len(messages)
        original_messages = deepcopy(messages)
        previous_session_id = self.bindings.session_id()
        memory_outcome = EffectOutcome(status="skipped", details={"reason": "no_memory"})
        if self.memory is not None:
            try:
                self.memory.on_pre_compress(messages)
                memory_outcome = EffectOutcome(status="succeeded")
            except Exception as exc:
                memory_outcome = failed_effect(exc)
                logger.warning("Memory pre-compress hook failed: %s", exc, exc_info=True)

        compressed = self.engine.compress(
            messages, current_tokens=approx_tokens or 0, focus_topic=focus_topic or "",
        )
        # The built-in engine returns the original history when there is
        # nothing to compact. Do not duplicate todos or rotate session IDs.
        if compressed == original_messages:
            return messages, self.bindings.system_prompt(system_message, False)

        compressed = list(compressed)
        todo_snapshot = self.bindings.todo_snapshot()
        if todo_snapshot:
            compressed.append({"role": "user", "content": todo_snapshot})
        new_system_prompt = self.bindings.system_prompt(system_message, True)
        try:
            continuation = require_effect_outcome(
                self.bindings.continue_session(new_system_prompt),
                effect="context session continuation",
            )
        except Exception as exc:
            continuation = failed_effect(exc)
        if continuation.status in {"failed", "degraded"}:
            logger.warning("Context session continuation failed: %s", continuation.error)

        estimate = estimate_tokens_rough(new_system_prompt) + estimate_messages_tokens_rough(compressed)
        self.engine.last_prompt_tokens = estimate
        self.engine.last_completion_tokens = 0
        self.engine.last_total_tokens = estimate
        session_id = self.bindings.session_id()
        if self.engine.threshold_tokens > 0 and estimate / self.engine.threshold_tokens < 0.85:
            self.bindings.reset_pressure(session_id or "default")
        try:
            self.bindings.reset_file_reads(task_id)
        except Exception:
            logger.debug("Could not reset file-read cache after compression", exc_info=True)

        if self.engine.compression_count >= 2:
            self.bindings.warn(
                f"Session compressed {self.engine.compression_count} times — "
                "accuracy may degrade. Consider /new to start fresh."
            )
        if self.events is not None:
            try:
                outcome = require_effect_outcome(self.events.emit(ContextCompressed(
                    session_id=session_id,
                    before_messages=before_count,
                    after_messages=len(compressed),
                    details={
                        "previous_session_id": previous_session_id,
                        "estimated_tokens": estimate,
                        "memory": memory_outcome.as_dict(),
                        "continuation": continuation.as_dict(),
                    },
                )), effect="context event publication")
                if outcome.status in {"failed", "degraded"}:
                    logger.warning("Context event publication failed: %s", outcome.error)
            except Exception:
                logger.warning("Context event publication failed", exc_info=True)
        logger.info(
            "Context compressed: session=%s messages=%d->%d tokens=~%d",
            session_id, before_count, len(compressed), estimate,
        )
        return compressed, new_system_prompt
