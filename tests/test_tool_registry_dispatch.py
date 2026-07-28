import json

import pytest

from tools.registry import ToolRegistry


pytestmark = pytest.mark.smoke


@pytest.mark.unit
def test_dispatch_filters_unexpected_context_kwargs_for_args_handler():
    registry = ToolRegistry()

    def handler(args):
        return json.dumps({"success": True, "host": args["host"]})

    registry.register(name="ping_like", handler=handler)

    result = json.loads(
        registry.dispatch(
            "ping_like",
            {"host": "example.com"},
            task_id="task-123",
            user_task="diagnose agent",
        )
    )

    assert result["success"] is True
    assert result["host"] == "example.com"


@pytest.mark.unit
def test_dispatch_preserves_supported_context_kwargs_for_args_handler():
    registry = ToolRegistry()

    def handler(args, task_id=None):
        return json.dumps({"success": True, "task_id": task_id, "path": args["path"]})

    registry.register(name="read_like", handler=handler)

    result = json.loads(
        registry.dispatch(
            "read_like",
            {"path": "logs/app.log"},
            task_id="task-456",
            user_task="unused",
        )
    )

    assert result["success"] is True
    assert result["task_id"] == "task-456"
    assert result["path"] == "logs/app.log"


@pytest.mark.unit
def test_dispatch_filters_context_kwargs_for_keyword_handler():
    registry = ToolRegistry()

    def handler(path):
        return json.dumps({"success": True, "path": path})

    registry.register(name="keyword_only_tool", handler=handler)

    result = json.loads(
        registry.dispatch(
            "keyword_only_tool",
            {"path": "README.md"},
            task_id="task-789",
            user_task="unused",
        )
    )

    assert result["success"] is True
    assert result["path"] == "README.md"


@pytest.mark.unit
def test_definitions_and_availability_honor_check_functions():
    registry = ToolRegistry()
    registry.register(
        name="ready_tool",
        toolset="demo",
        schema={"name": "ready_tool", "parameters": {}},
        handler=lambda: "ready",
        check_fn=lambda: True,
    )
    registry.register(
        name="blocked_tool",
        toolset="demo",
        schema={"name": "blocked_tool", "parameters": {}},
        handler=lambda: "blocked",
        check_fn=lambda: False,
    )

    definitions = registry.get_definitions(["ready_tool", "blocked_tool"])
    available, unavailable = registry.check_tool_availability()

    assert [item["function"]["name"] for item in definitions] == ["ready_tool"]
    assert available == [
        {"name": "demo", "tools": ["ready_tool"], "available": True}
    ]
    assert unavailable == [
        {
            "name": "demo",
            "tools": ["blocked_tool"],
            "available": False,
            "missing_vars": [],
        }
    ]
    assert registry.get_toolset_requirements() == {"demo": False}


@pytest.mark.unit
def test_unregister_removes_tool_metadata():
    registry = ToolRegistry()
    registry.register(
        name="temporary_tool",
        toolset="demo",
        handler=lambda: "ok",
        check_fn=lambda: True,
    )

    assert registry.unregister("temporary_tool") is True
    assert registry.has_tool("temporary_tool") is False
    assert registry.get_toolset_requirements() == {}
