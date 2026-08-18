"""Interactive MCP tool filter UI for the CLI boundary."""

from __future__ import annotations

from typing import Any, Callable, Set


def configure_mcp_tools(
    config: dict[str, Any],
    *,
    checklist: Callable[..., Set[int]],
    probe_tools: Callable[[], dict[str, list[tuple[str, str]]]],
    print_info: Callable[[str], None],
    print_error: Callable[[str], None],
    print_warning: Callable[[str], None],
    print_success: Callable[[str], None],
    save: Callable[[dict[str, Any]], Any],
    print_line: Callable[[str], None],
    style: Callable[[str, Any], str],
    dim_style: Any,
    yellow_style: Any,
    green_style: Any,
) -> None:
    """Discover configured MCP servers and persist per-tool exclusions."""
    mcp_servers = config.get("mcp_servers") or {}
    if not mcp_servers:
        print_info("No MCP servers configured.")
        return

    enabled_names = [
        name for name, server in mcp_servers.items()
        if isinstance(server, dict)
        and server.get("enabled", True) not in (False, "false", "0", "no", "off")
    ]
    if not enabled_names:
        print_info("All MCP servers are disabled.")
        return

    print_line("")
    print_line(style("  Discovering tools from MCP servers...", yellow_style))
    print_line(style(
        f"  Connecting to {len(enabled_names)} server(s): {', '.join(enabled_names)}",
        dim_style,
    ))
    try:
        server_tools = probe_tools()
    except Exception as exc:
        print_error(f"Failed to probe MCP servers: {exc}")
        return
    if not server_tools:
        print_warning("Could not discover tools from any MCP server.")
        print_info("Check that server commands/URLs are correct and dependencies are installed.")
        return

    for name in (name for name in enabled_names if name not in server_tools):
        print_warning(f"  Could not connect to '{name}'")
    total_tools = sum(len(items) for items in server_tools.values())
    print_line(style(
        f"  Found {total_tools} tool(s) across {len(server_tools)} server(s)",
        green_style,
    ))
    print_line("")

    changed = False
    for server_name, tools in server_tools.items():
        if not tools:
            print_info(f"  {server_name}: no tools found")
            continue
        server_cfg = mcp_servers.get(server_name, {})
        tool_cfg = server_cfg.get("tools") or {}
        includes = tool_cfg.get("include") or []
        excludes = tool_cfg.get("exclude") or []
        labels = []
        names = [name for name, _description in tools]
        for name, description in tools:
            short = description[:70] + "..." if len(description) > 70 else description
            labels.append(f"{name}  ({short})" if short else name)

        selected: Set[int] = set()
        for index, name in enumerate(names):
            if includes:
                if name in includes:
                    selected.add(index)
            elif not excludes or name not in excludes:
                selected.add(index)

        chosen = checklist(
            f"MCP Server: {server_name}  ({len(tools)} tools)",
            labels,
            selected,
            cancel_returns=selected,
        )
        if chosen == selected:
            print_info(f"  {server_name}: no changes")
            continue

        new_excludes = [name for index, name in enumerate(names) if index not in chosen]
        server_cfg = mcp_servers.setdefault(server_name, {})
        tool_cfg = server_cfg.setdefault("tools", {})
        if new_excludes:
            tool_cfg["exclude"] = new_excludes
            tool_cfg.pop("include", None)
        else:
            tool_cfg.pop("exclude", None)
            tool_cfg.pop("include", None)
        print_success(
            f"  {server_name}: {len(chosen)} enabled, {len(tools) - len(chosen)} disabled"
        )
        changed = True

    if changed:
        save(config)
        print_success("  MCP tool configuration changed")
    else:
        print_line(style("  No changes to MCP tools", dim_style))


__all__ = ["configure_mcp_tools"]
