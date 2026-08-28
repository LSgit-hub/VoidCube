"""Execute one agent conversation call through explicit CLI ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True, slots=True)
class CliAgentTurnCallPorts:
    """Agent call inputs and host-owned turn state supplied by the CLI."""

    message: Any
    voice_prefix: str
    pending_model_switch_note: Callable[[], Optional[str]]
    clear_pending_model_switch_note: Callable[[], None]
    prior_history: Sequence[Mapping[str, Any]]
    session_id: str
    stream_callback: Any
    persist_user_message: Any
    new_trace_id: Callable[[], str]
    set_trace_id: Callable[[str], None]
    run_conversation: Callable[..., Mapping[str, Any] | None]
    summarize_error: Callable[[Exception], str]
    log_error: Callable[[Exception], None]
    attachments: Sequence[Mapping[str, Any]] = ()


class CliAgentTurnCallRuntime:
    """Own one agent invocation and its transport-level error projection."""

    def __init__(self, ports: CliAgentTurnCallPorts) -> None:
        self.ports = ports

    def run(self) -> Mapping[str, Any] | None:
        ports = self.ports
        agent_message = (
            f"{ports.voice_prefix}{ports.message}"
            if ports.voice_prefix
            else ports.message
        )
        pending_note = ports.pending_model_switch_note()
        if pending_note:
            agent_message = f"{pending_note}\n\n{agent_message}"
            ports.clear_pending_model_switch_note()

        try:
            trace_id = ports.new_trace_id()
            ports.set_trace_id(trace_id)
            return ports.run_conversation(
                user_message=agent_message,
                conversation_history=list(ports.prior_history),
                stream_callback=ports.stream_callback,
                task_id=ports.session_id,
                trace_id=trace_id,
                persist_user_message=ports.persist_user_message,
                attachments=[
                    dict(attachment)
                    for attachment in ports.attachments
                    if isinstance(attachment, Mapping)
                ],
            )
        except Exception as error:
            ports.log_error(error)
            summary = ports.summarize_error(error)
            return {
                "final_response": f"Error: {summary}",
                "messages": [],
                "api_calls": 0,
                "completed": False,
                "failed": True,
                "error": summary,
            }
