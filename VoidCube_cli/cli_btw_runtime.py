"""Run and render an ephemeral /btw side question through explicit ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Thread
from typing import Any

from rich import box as rich_box
from rich.panel import Panel


@dataclass(frozen=True, slots=True)
class CliBtwPorts:
    """Credential, agent and terminal operations supplied by the CLI host."""

    ensure_credentials: Callable[[], bool]
    resolve_agent_route: Callable[[str], Mapping[str, Any]]
    conversation_history: Callable[[], Sequence[Mapping[str, Any]]]
    create_agent: Callable[[Mapping[str, Any], str], Any]
    task_id_factory: Callable[[], str]
    emit: Callable[[str], None]
    invalidate: Callable[[], None]
    sleep: Callable[[float], None]
    emit_blank_line: Callable[[], None]
    create_console: Callable[[], Any]
    rich_text_from_ansi: Callable[[str], Any]
    bell: Callable[[], None]
    thread_factory: Callable[..., Thread] = Thread


class CliBtwRuntime:
    """Own ephemeral side-question execution and its terminal presentation."""

    _PROMPT_PREFIX = (
        "[Ephemeral /btw side question. Answer using the conversation "
        "context. No tools available. Be direct and concise.]\n\n"
    )

    def __init__(self, ports: CliBtwPorts) -> None:
        self.ports = ports

    def start(self, question: str) -> bool:
        ports = self.ports
        task_id = ports.task_id_factory()
        if not ports.ensure_credentials():
            ports.emit("  (>_<) Cannot start /btw: no valid credentials.")
            return False

        route = ports.resolve_agent_route(question)
        history = list(ports.conversation_history())
        preview = question[:60] + ("..." if len(question) > 60 else "")
        ports.emit(f'  💬 /btw: "{preview}"')

        def run() -> None:
            try:
                agent = ports.create_agent(route, task_id)
                result = agent.run_conversation(
                    user_message=self._PROMPT_PREFIX + question,
                    conversation_history=history,
                    task_id=task_id,
                )
                response = (result.get("final_response") or "") if result else ""
                if not response and result and result.get("error"):
                    response = f"Error: {result['error']}"
                self._render_response(response)
                ports.bell()
            except Exception as error:
                self._render_error(error)
            finally:
                ports.invalidate()

        ports.thread_factory(
            target=run,
            daemon=True,
            name=f"btw-{task_id}",
        ).start()
        return True

    def _render_response(self, response: str) -> None:
        ports = self.ports
        ports.invalidate()
        ports.sleep(0.05)
        ports.emit_blank_line()
        if response:
            color = "#4F6D4A"
            ports.create_console().print(
                Panel(
                    ports.rich_text_from_ansi(response),
                    title=f"[{color} bold]> /btw[/]",
                    title_align="left",
                    border_style=color,
                    box=rich_box.HORIZONTALS,
                    padding=(1, 2),
                )
            )
        else:
            ports.emit("  💬 /btw: (no response)")

    def _render_error(self, error: Exception) -> None:
        ports = self.ports
        ports.invalidate()
        ports.sleep(0.05)
        ports.emit_blank_line()
        ports.emit(f"  ❌ /btw failed: {error}")
