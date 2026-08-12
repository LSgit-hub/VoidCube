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


def test_registered_toolsets_are_statically_resolvable():
    from tools.model_tools import get_all_tool_names
    from tools.registry import registry
    from tools.toolsets import TOOLSETS, resolve_toolset

    get_all_tool_names()
    unresolved = {}
    for toolset in registry.list_toolsets():
        registered_tools = set(registry.get_toolset_tools(toolset))
        if toolset not in TOOLSETS or not registered_tools.issubset(
            resolve_toolset(toolset)
        ):
            unresolved[toolset] = sorted(registered_tools)

    assert unresolved == {}
    assert "mixture_of_agents" in resolve_toolset("moa")
    assert "mixture_of_agents" in resolve_toolset("full")


def test_learn_toolset_has_no_mutating_or_code_execution_entry_points():
    from tools.toolsets import resolve_toolset

    learn_tools = set(resolve_toolset("learn"))

    assert {
        "web_search",
        "web_extract",
        "web_crawl",
        "read_file",
        "search_files",
    } <= learn_tools
    assert learn_tools.isdisjoint(
        {
            "terminal",
            "execute_code",
            "write_file",
            "skill_manage",
            "delegate_task",
            "browser_click",
            "browser_type",
            "browser_press",
        }
    )


def test_web_toolset_exposes_registered_crawl_definition():
    from tools.model_tools import get_tool_definitions

    definitions = get_tool_definitions(enabled_toolsets=["web"], quiet_mode=True)
    by_name = {item["function"]["name"]: item for item in definitions}

    assert "web_crawl" in by_name
    assert by_name["web_crawl"]["function"]["parameters"]["required"] == ["url"]


def test_registry_has_no_legacy_default_toolset_aliases():
    from tools.model_tools import get_available_toolsets
    from tools.toolsets import resolve_toolset

    available = get_available_toolsets()

    assert "core" not in available
    assert "extended" not in available
    assert {
        "terminal",
        "read_file",
        "execute_code",
        "delegate_task",
        "check_dependencies",
    } <= set(resolve_toolset("voidcube"))




