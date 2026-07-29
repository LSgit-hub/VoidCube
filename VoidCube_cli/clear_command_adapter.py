"""Terminal-only projection for the CLI clear command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from VoidCube_cli.banner import build_compact_banner, build_welcome_banner
from VoidCube_cli.cli_ui import ChatConsole


@dataclass(frozen=True, slots=True)
class ClearBannerState:
    model: str
    cwd: str
    enabled_toolsets: tuple[str, ...]
    session_id: str
    context_length: int | None
    conversation_history: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class ClearDisplayPorts:
    tui_active: Callable[[], bool]
    clear_tui_screen: Callable[[], None]
    show_standalone_banner: Callable[[], None]
    compact: Callable[[], bool]
    terminal_width: Callable[[], int]
    banner_state: Callable[[], ClearBannerState]
    emit_tui: Callable[[str], None]
    emit_plain: Callable[[str], None]
    fresh_start_message: str
    chat_console_factory: Callable[[], Any] = ChatConsole
    compact_banner_factory: Callable[[], str] = build_compact_banner
    load_tools: Callable[[Sequence[str]], list[dict[str, object]]] = (
        lambda enabled_toolsets: _load_tools(enabled_toolsets)
    )


def render_clear_display(ports: ClearDisplayPorts) -> None:
    """Clear the active terminal surface and render its matching banner."""
    if ports.tui_active():
        ports.clear_tui_screen()
        console = ports.chat_console_factory()
        if ports.compact() or ports.terminal_width() < 80:
            console.print(ports.compact_banner_factory())
        else:
            state = ports.banner_state()
            build_welcome_banner(
                console=console,
                model=state.model,
                cwd=state.cwd,
                tools=ports.load_tools(state.enabled_toolsets),
                enabled_toolsets=list(state.enabled_toolsets),
                session_id=state.session_id,
                context_length=state.context_length,
                conversation_history=list(state.conversation_history),
            )
        ports.emit_tui(f"  {ports.fresh_start_message}\n")
        return

    ports.show_standalone_banner()
    ports.emit_plain(f"  {ports.fresh_start_message}\n")


def _load_tools(enabled_toolsets: Sequence[str]) -> list[dict[str, object]]:
    from tools.model_tools import get_tool_definitions

    return get_tool_definitions(
        enabled_toolsets=list(enabled_toolsets),
        quiet_mode=True,
    )
