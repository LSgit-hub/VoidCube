from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_model_tools_defers_discovery_until_first_api_call():
    probe = """
from tools.registry import registry
import tools.model_tools as model_tools

assert registry.list_tools() == []
names = set(model_tools.get_all_tool_names())
assert {"terminal", "read_file", "write_file", "patch", "search_files"} <= names
"""

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_run_agent_defers_runtime_only_integrations():
    probe = """
import sys
import run_agent

assert "openai" not in sys.modules
assert "tools.terminal_tool" not in sys.modules
assert "agent.auxiliary_client" not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_terminal_tool_loads_only_the_selected_environment():
    probe = """
import os
import sys
import tools.terminal_tool as terminal_tool

assert "tools.environments.local" not in sys.modules
assert "tools.environments.docker" not in sys.modules
assert "tools.environments.managed_modal" not in sys.modules
environment = terminal_tool._create_environment_once("local", "", os.getcwd(), 1)
assert environment.__class__.__name__ == "LocalEnvironment"
assert "tools.environments.local" in sys.modules
assert "tools.environments.docker" not in sys.modules
assert "tools.environments.managed_modal" not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_static_toolsets_only_reference_registered_tools():
    from tools.model_tools import get_all_tool_names
    from tools.toolsets import TOOLSETS, resolve_toolset

    registered = set(get_all_tool_names())
    missing = {
        toolset: sorted(set(resolve_toolset(toolset)) - registered)
        for toolset in TOOLSETS
        if set(resolve_toolset(toolset)) - registered
    }

    assert missing == {}


def test_windows_uiautomation_toolset_exposes_all_registered_tools():
    if sys.platform != "win32":
        return

    from tools.model_tools import get_tool_definitions

    names = {
        item["function"]["name"]
        for item in get_tool_definitions(["uiautomation"], quiet_mode=True)
    }

    assert len(names) == 17
    assert {"uia_find_window", "uia_click_button", "uia_scroll"} <= names
