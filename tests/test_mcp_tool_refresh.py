from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import voidcube.extensions.tools.mcp.mcp_tool as mcp_tool
from voidcube.extensions.tools.registry import registry


pytestmark = [pytest.mark.smoke, pytest.mark.unit]


@pytest.mark.asyncio
async def test_dynamic_refresh_unregisters_stale_tools(monkeypatch):
    server = mcp_tool.MCPServerTask("refresh-test")
    server.session = SimpleNamespace(
        list_tools=AsyncMock(return_value=SimpleNamespace(tools=[]))
    )
    server._registered_tool_names = ["mcp_refresh_test_old"]
    registry.register(name="mcp_refresh_test_old", handler=lambda: "old")

    def register_replacement(name, active_server, config):
        assert name == "refresh-test"
        assert active_server is server
        assert config == {}
        registry.register(name="mcp_refresh_test_new", handler=lambda: "new")
        return ["mcp_refresh_test_new"]

    monkeypatch.setattr(mcp_tool, "_register_server_tools", register_replacement)
    try:
        await server._refresh_tools()

        assert registry.has_tool("mcp_refresh_test_old") is False
        assert registry.has_tool("mcp_refresh_test_new") is True
        assert server._registered_tool_names == ["mcp_refresh_test_new"]
    finally:
        registry.unregister("mcp_refresh_test_old")
        registry.unregister("mcp_refresh_test_new")
