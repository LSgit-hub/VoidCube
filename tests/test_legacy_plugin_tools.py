from __future__ import annotations

import json
from pathlib import Path


def test_plugin_discovery_registers_executable_legacy_tools():
    from tools.model_tools import get_tool_definitions

    expected = {
        "http_request": "http_request",
        "browser_legacy": "browser",
        "append_file": "append_file",
        "git": "git_manage",
    }
    for toolset, tool_name in expected.items():
        names = {
            item["function"]["name"]
            for item in get_tool_definitions([toolset], quiet_mode=True)
        }
        assert tool_name in names


def test_append_file_plugin_writes_content_without_read_modify_write(tmp_path):
    from tools.model_tools import handle_function_call

    path = tmp_path / "append.log"
    first = json.loads(
        handle_function_call(
            "append_file",
            {"path": str(path), "content": "one"},
            task_id="plugin-test",
            user_task="test append plugin",
        )
    )
    second = json.loads(
        handle_function_call(
            "append_file",
            {"path": str(path), "content": "two"},
            task_id="plugin-test",
            user_task="test append plugin",
        )
    )

    assert first["success"] is True
    assert second["success"] is True
    assert path.read_text(encoding="utf-8") == "one\ntwo"


def test_git_plugin_is_read_only_for_supported_actions():
    from tools.model_tools import handle_function_call

    result = json.loads(
        handle_function_call(
            "git_manage",
            {"action": "status"},
            task_id="plugin-test",
            user_task="test git plugin",
        )
    )

    assert result["success"] is True
    assert " M " in result["output"] or result["output"].strip() == ""
