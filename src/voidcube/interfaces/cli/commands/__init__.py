"""Canonical CLI command composition and port registration."""

from .registry import (
    autonomous_command_ports_for_host,
    exit_autonomous_gate_fast_for_host,
    install_cli_command_execution,
    reload_mcp_for_host,
    render_tools_for_host,
    render_toolsets_for_host,
)

__all__ = [
    "autonomous_command_ports_for_host",
    "exit_autonomous_gate_fast_for_host",
    "install_cli_command_execution",
    "reload_mcp_for_host",
    "render_tools_for_host",
    "render_toolsets_for_host",
]
