"""Operational command handlers with explicit runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class StopCommandPorts:
    list_processes: Callable[[], Sequence[Mapping[str, object]]]
    kill_all: Callable[[], int]
    emit: Callable[[str], None]
    no_running_message: str
    stopping_message: Callable[[int], str]
    stopped_message: Callable[[int], str]


@dataclass(frozen=True, slots=True)
class CancelCommandPorts:
    agent_running: Callable[[], bool]
    interrupt_agent: Callable[[], None]
    emit: Callable[[str], None]
    cancel_scheduler: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class DoctorCommandPorts:
    run_diagnosis: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ApiCommandPorts:
    run_wizard: Callable[[], None]


@dataclass(frozen=True, slots=True)
class DebugCommandPorts:
    run_debug_share: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ReloadMcpCommandPorts:
    run_reload: Callable[[], None]


@dataclass(frozen=True, slots=True)
class McpReloadRuntimePorts:
    server_names: Callable[[], set[str]]
    shutdown_servers: Callable[[], None]
    discover_tools: Callable[[], Sequence[Mapping[str, Any]]]
    command_running: Callable[[], bool]
    refresh_agent_tools: Callable[[], int]
    append_reload_note: Callable[[str], None]
    persist_reload_note: Callable[[], None]
    emit: Callable[[str], None]


def handle_api_command(
    request: ParsedCliCommand,
    *,
    ports: ApiCommandPorts,
) -> None:
    """Delegate API configuration without owning credentials or persistence."""
    del request
    ports.run_wizard()


def handle_doctor_command(
    request: ParsedCliCommand,
    *,
    ports: DoctorCommandPorts,
) -> None:
    """Delegate the diagnostic operation without owning its external probes."""
    del request
    ports.run_diagnosis()


def handle_debug_command(
    request: ParsedCliCommand,
    *,
    ports: DebugCommandPorts,
) -> None:
    """Delegate debug-report collection and upload to its operation owner."""
    del request
    ports.run_debug_share()


def handle_reload_mcp_command(
    request: ParsedCliCommand,
    *,
    ports: ReloadMcpCommandPorts,
) -> None:
    """Delegate MCP connection lifecycle work to its runtime operation."""
    del request
    ports.run_reload()


def reload_mcp_servers(*, ports: McpReloadRuntimePorts) -> None:
    """Reconnect MCP servers and synchronize the active agent's tool state."""
    try:
        old_servers = ports.server_names()
        if not ports.command_running():
            ports.emit("🔄 Reloading MCP servers...")

        ports.shutdown_servers()
        new_tools = list(ports.discover_tools())
        connected_servers = ports.server_names()
        added = connected_servers - old_servers
        removed = old_servers - connected_servers
        reconnected = connected_servers & old_servers

        if reconnected:
            ports.emit(f"  ♻️  Reconnected: {', '.join(sorted(reconnected))}")
        if added:
            ports.emit(f"  ➕ Added: {', '.join(sorted(added))}")
        if removed:
            ports.emit(f"  ➖ Removed: {', '.join(sorted(removed))}")
        if not connected_servers:
            ports.emit("  No MCP servers connected.")
        else:
            ports.emit(
                f"  🔧 {len(new_tools)} tool(s) available from "
                f"{len(connected_servers)} server(s)"
            )

        agent_tool_count = ports.refresh_agent_tools()
        ports.append_reload_note(
            _reload_note(
                added=added,
                removed=removed,
                reconnected=reconnected,
                mcp_tool_count=len(new_tools),
            )
        )
        ports.persist_reload_note()
        ports.emit(f"  ✅ Agent updated — {agent_tool_count} tool(s) available")
    except Exception as exc:
        ports.emit(f"  ❌ MCP reload failed: {exc}")


def _reload_note(
    *,
    added: set[str],
    removed: set[str],
    reconnected: set[str],
    mcp_tool_count: int,
) -> str:
    changes: list[str] = []
    if added:
        changes.append(f"Added servers: {', '.join(sorted(added))}")
    if removed:
        changes.append(f"Removed servers: {', '.join(sorted(removed))}")
    if reconnected:
        changes.append(f"Reconnected servers: {', '.join(sorted(reconnected))}")
    detail = ". ".join(changes) + ". " if changes else ""
    tool_summary = (
        f"{mcp_tool_count} MCP tool(s) now available"
        if mcp_tool_count
        else "No MCP tools available"
    )
    return (
        "[SYSTEM: MCP servers have been reloaded. "
        f"{detail}{tool_summary}. The tool list for this conversation has been updated accordingly.]"
    )


def handle_stop_command(
    request: ParsedCliCommand,
    *,
    ports: StopCommandPorts,
) -> None:
    del request
    running_count = sum(
        process.get("status") == "running"
        for process in ports.list_processes()
    )
    if not running_count:
        ports.emit(ports.no_running_message)
        return
    ports.emit(ports.stopping_message(running_count))
    ports.emit(ports.stopped_message(ports.kill_all()))


def handle_cancel_command(
    request: ParsedCliCommand,
    *,
    ports: CancelCommandPorts,
) -> None:
    """Cancel the active user turn through an explicit CLI command."""
    del request
    if ports.cancel_scheduler is not None and ports.cancel_scheduler():
        ports.emit("  Cancellation requested for the active user turn.")
        return
    if not ports.agent_running():
        ports.emit("  No active user turn to cancel.")
        return
    try:
        ports.interrupt_agent()
    except Exception as exc:
        ports.emit(f"  Failed to cancel the active user turn: {exc}")
        return
    ports.emit("  Cancellation requested for the active user turn.")
