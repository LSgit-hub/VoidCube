"""Post-process one model-turn outcome before presentation and requeueing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from VoidCube_app.turn_contract import TurnOutcome
from VoidCube_app.turn_queue import TurnInterrupt, resolve_interrupted_followup


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


@dataclass(frozen=True, slots=True)
class TurnPostprocessingResult:
    response: str
    turn_result: dict[str, Any]
    pending_message: Any = None


class TurnPostprocessingRuntime:
    """Own title, failure, voice-error and interrupted-follow-up transitions."""

    def __init__(self, ports: TurnPostprocessingPorts) -> None:
        self.ports = ports

    def process(
        self,
        *,
        outcome: TurnOutcome,
        message: str,
        conversation_history: Sequence[dict[str, Any]],
        turn_result: dict[str, Any],
        turn_interrupt: TurnInterrupt | None,
    ) -> TurnPostprocessingResult:
        response = outcome.response
        result = dict(turn_result)

        if outcome.usable:
            try:
                from agent.title_generator import maybe_auto_title

                maybe_auto_title(
                    self.ports.session_db(),
                    self.ports.session_id(),
                    message,
                    response,
                    list(conversation_history),
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

        pending_message = None
        if outcome.interrupted:
            pending_message = resolve_interrupted_followup(
                turn_interrupt,
                outcome.interrupt_message,
            )
            if response and pending_message:
                response = response + "\n\n---\n_[Interrupted - processing new message]_"

        return TurnPostprocessingResult(
            response=response,
            turn_result=result,
            pending_message=pending_message,
        )
