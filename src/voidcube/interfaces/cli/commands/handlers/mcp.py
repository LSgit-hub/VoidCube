"""Interactive MCP server configuration command with explicit runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class McpCommandPorts:
    load_config: Callable[[], MutableMapping[str, Any]]
    save_config: Callable[[MutableMapping[str, Any]], None]
    probe_tools: Callable[[str, Mapping[str, Any]], Sequence[Mapping[str, Any]]]
    emit: Callable[[str], None]


def handle_mcp_command(
    request: ParsedCliCommand,
    *,
    ports: McpCommandPorts,
) -> None:
    """Manage the slash-command MCP server configuration surface."""
    args = request.arguments.split()
    subcommand = args[0] if args else "help"
    if subcommand == "list":
        _display_servers(ports=ports)
    elif subcommand == "add":
        _add_server(args, ports=ports)
    elif subcommand == "remove":
        _remove_server(args, ports=ports)
    elif subcommand == "test":
        _test_server(args, ports=ports)
    else:
        _display_help(ports.emit)


def _display_help(emit: Callable[[str], None]) -> None:
    emit("\n  MCP 服务器管理命令 (/mcp)")
    emit("")
    emit("  用法:")
    emit("    /mcp                  — 显示此帮助")
    emit("    /mcp list             — 列出已配置的 MCP 服务器")
    emit("    /mcp add <name> <url> — 添加 MCP 服务器")
    emit("    /mcp remove <name>    — 删除 MCP 服务器")
    emit("    /mcp test <name>      — 测试 MCP 服务器连接")
    emit("")


def _display_servers(*, ports: McpCommandPorts) -> None:
    emit = ports.emit
    emit("\n  已配置的 MCP 服务器:")
    emit("")
    servers = ports.load_config().get("mcp_servers", {})
    if not servers:
        emit("    暂无配置的 MCP 服务器")
        emit("")
        emit("    使用 /mcp add <name> <url> 添加服务器")
    else:
        for name, server in servers.items():
            emit(f"    [{name}]")
            emit(f"        URL: {server.get('url', 'N/A')}")
            emit(f"        类型: {server.get('type', 'http')}")
            if server.get("command"):
                emit(f"        命令: {server.get('command')}")
            emit("")
    emit("")


def _add_server(args: list[str], *, ports: McpCommandPorts) -> None:
    emit = ports.emit
    if len(args) < 3:
        emit("\n  ❌ 参数不足")
        emit("    用法: /mcp add <name> <url>")
        emit("")
        return

    name, url = args[1:3]
    emit(f"\n  添加 MCP 服务器: {name}")
    emit(f"  URL: {url}")
    emit("")
    try:
        config = ports.load_config()
        servers = config.setdefault("mcp_servers", {})
        servers[name] = {"url": url, "type": "http"}
        ports.save_config(config)
        emit(f"    ✅ MCP 服务器 '{name}' 添加成功")
        emit("    重启会话后生效")
    except Exception as exc:
        emit(f"    ❌ 添加失败: {exc}")
    emit("")


def _remove_server(args: list[str], *, ports: McpCommandPorts) -> None:
    emit = ports.emit
    if len(args) < 2:
        emit("\n  ❌ 参数不足")
        emit("    用法: /mcp remove <name>")
        emit("")
        return

    name = args[1]
    emit(f"\n  删除 MCP 服务器: {name}")
    emit("")
    try:
        config = ports.load_config()
        servers = config.get("mcp_servers", {})
        if name in servers:
            del servers[name]
            ports.save_config(config)
            emit(f"    ✅ MCP 服务器 '{name}' 删除成功")
            emit("    重启会话后生效")
        else:
            emit(f"    ❌ 未找到 MCP 服务器 '{name}'")
    except Exception as exc:
        emit(f"    ❌ 删除失败: {exc}")
    emit("")


def _test_server(args: list[str], *, ports: McpCommandPorts) -> None:
    emit = ports.emit
    if len(args) < 2:
        emit("\n  ❌ 参数不足")
        emit("    用法: /mcp test <name>")
        emit("")
        return

    name = args[1]
    emit(f"\n  测试 MCP 服务器: {name}")
    emit("")
    try:
        servers = ports.load_config().get("mcp_servers", {})
        if name not in servers:
            emit(f"    ❌ 未找到 MCP 服务器 '{name}'")
            emit("")
            return

        server_config = servers[name]
        url = server_config.get("url")
        emit(f"    正在连接到: {url}")
        tools = list(ports.probe_tools(name, server_config))
        if tools:
            emit("    ✅ 连接成功")
            emit(f"    可用工具: {len(tools)} 个")
            for tool in tools[:5]:
                emit(f"      - {tool.get('name', 'Unknown')}")
            if len(tools) > 5:
                emit(f"      ... 还有 {len(tools) - 5} 个工具")
        else:
            emit("    ⚠️ 连接成功但未返回工具列表")
    except Exception as exc:
        emit(f"    ❌ 连接失败: {exc}")
    emit("")
