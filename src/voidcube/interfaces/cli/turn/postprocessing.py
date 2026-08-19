"""Post-process one model-turn outcome before presentation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ....application.session_title import maybe_auto_title
from ....domain.contracts.turn import TurnOutcome


_DIM = "\033[2m"
_RST = "\033[0m"


@dataclass(frozen=True, slots=True)
class TurnPostprocessingPorts:
    """Session/title and voice transitions supplied by the CLI host."""

    session_db: Callable[[], Any]
    session_id: Callable[[], str]
    voice_continuous: Callable[[], bool]
    stop_voice_continuous: Callable[[], None]
    emit: Callable[[str], None]
    title_generator: Callable[..., None] = maybe_auto_title


@dataclass(frozen=True, slots=True)
class TurnPostprocessingResult:
    response: str
    turn_result: dict[str, Any]


class TurnPostprocessingRuntime:
    """Own title, failure and voice-error transitions."""

    def __init__(self, ports: TurnPostprocessingPorts) -> None:
        self.ports = ports

    def process(
        self,
        *,
        outcome: TurnOutcome,
        message: str,
        conversation_history: Sequence[dict[str, Any]],
        turn_result: dict[str, Any],
    ) -> TurnPostprocessingResult:
        response = outcome.response
        result = dict(turn_result)

        if outcome.usable:
            try:
                from voidcube.infrastructure.providers.auxiliary_client import call_llm

                self.ports.title_generator(
                    self.ports.session_db(),
                    self.ports.session_id(),
                    message,
                    response,
                    list(conversation_history),
                    llm_call=call_llm,
                )
            except Exception:
                pass

        if (outcome.failed or outcome.partial) and not response:
            response = outcome.response_or_error()
            result["response"] = response
            if self.ports.voice_continuous():
                self.ports.stop_voice_continuous()
                self.ports.emit(
                    f"\n{_DIM}Continuous voice mode stopped due to error.{_RST}"
                )

        return TurnPostprocessingResult(
            response=response,
            turn_result=result,
        )
