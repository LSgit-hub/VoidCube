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
