#!/usr/bin/env python3
"""
Model Tools Module

Thin orchestration layer over the tool registry. Each tool file in
self-registers its schema, handler, and metadata via tools.registry.register().
Tool discovery is deferred until the first public API call so importing the
agent or gateway does not eagerly load every tool integration.

Public API (signatures preserved from the original 2,400-line version):
    get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode) -> list
    handle_function_call(function_name, function_args, task_id, user_task) -> str
    get_all_tool_names() -> list
    get_toolset_for_tool(name) -> str
    get_available_toolsets() -> dict
    check_tool_availability(quiet) -> tuple
"""

import hashlib
import json
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple, Set

from .registry import registry
from .toolsets import resolve_toolset, validate_toolset
from voidcube.runtime.agent.tool_execution import classify_tool_result
from ...domain.contracts.execution import ExecutionState

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Discovery  (importing each module triggers its registry.register calls)
# =============================================================================

def _discover_tools():
    """Import all tool modules to trigger their registry.register() calls.

    Wrapped in a function so import errors in optional tools don't prevent the
    rest from loading.
    """
    _modules = [
        "voidcube.extensions.tools.web.web_tools",
        "voidcube.infrastructure.execution.terminal_tool",
        "voidcube.extensions.tools.files.file_tools",
        "voidcube.extensions.tools.media.vision_tools",
        "voidcube.extensions.skills.tool",
        "voidcube.extensions.skills.manager",
        "voidcube.extensions.tools.browser.browser_tool",
        "voidcube.extensions.tools.media.media_tool",
        "voidcube.extensions.tools.todo_tool",
        "voidcube.extensions.tools.mail_tools",
        "voidcube.extensions.tools.session_search_tool",
        "voidcube.extensions.tools.mixture_of_agents_tool",
        "voidcube.extensions.tools.clarify_tool",
        "voidcube.extensions.tools.scheduled_task_tool",
        "voidcube.infrastructure.execution.code_execution_tool",
        "voidcube.extensions.tools.delegate_tool",
        "voidcube.infrastructure.execution.process_registry",

        # Media generation tools (image/video)
        "voidcube.extensions.tools.media.media_generation_tool",
        # Ops: Server operations tools (registered via ops_register)
        "voidcube.extensions.tools.ops_register",
        # Bootstrap / environment dependency checker
        "voidcube.extensions.tools.dependency_checker",
    ]
    import importlib
    for mod_name in _modules:
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            logger.warning("Could not import tool module %s: %s", mod_name, e)


_tools_discovered = False
_tool_discovery_lock = threading.Lock()


def ensure_tools_discovered() -> None:
    """Discover built-in, MCP, and plugin tools exactly once on first use."""
    global _tools_discovered
    if _tools_discovered:
        return

    with _tool_discovery_lock:
        if _tools_discovered:
            return

        _discover_tools()

        try:
            from voidcube.extensions.tools.mcp.mcp_tool import discover_mcp_tools
            discover_mcp_tools()
        except (ImportError, RuntimeError) as e:
            logger.debug("MCP tool discovery failed: %s", e)

        try:
            from ..plugins.cli_adapter import discover_plugins
            discover_plugins()
        except (ImportError, RuntimeError) as e:
            logger.debug("Plugin discovery failed: %s", e)

        _tools_discovered = True

# Resolved tool names from the last get_tool_definitions() call.
# Used by code_execution_tool to know which tools are available in this session.
_last_resolved_tool_names: List[str] = []


# =============================================================================
# get_tool_definitions  (the main schema provider)
# =============================================================================

def get_tool_definitions(
    enabled_toolsets: Optional[List[str]] = None,
    disabled_toolsets: Optional[List[str]] = None,
    quiet_mode: bool = False,
) -> List[Dict[str, Any]]:
    """
    Get tool definitions for model API calls with toolset-based filtering.

    All tools must be part of a toolset to be accessible.

    Args:
        enabled_toolsets: Only include tools from these toolsets.
        disabled_toolsets: Exclude tools from these toolsets (if enabled_toolsets is None).
        quiet_mode: Suppress status prints.

    Returns:
        Filtered list of OpenAI-format tool definitions.
    """
    ensure_tools_discovered()

    # Determine which tool names the caller wants
    tools_to_include: Set[str] = set()

    if enabled_toolsets is not None:
        for toolset_name in enabled_toolsets:
            try:
                canonical_name = validate_toolset(toolset_name)
            except ValueError:
                if not quiet_mode:
                    print(f"⚠️  Unknown toolset: {toolset_name}")
                continue
            resolved = resolve_toolset(canonical_name)
            tools_to_include.update(resolved)
            if not quiet_mode:
                print(f"✅ Enabled toolset '{canonical_name}': {', '.join(resolved) if resolved else 'no tools'}")

    elif disabled_toolsets:
        from .toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

        for toolset_name in disabled_toolsets:
            try:
                canonical_name = validate_toolset(toolset_name)
            except ValueError:
                if not quiet_mode:
                    print(f"⚠️  Unknown toolset: {toolset_name}")
                continue
            resolved = resolve_toolset(canonical_name)
            tools_to_include.difference_update(resolved)
            if not quiet_mode:
                print(f"🚫 Disabled toolset '{canonical_name}': {', '.join(resolved) if resolved else 'no tools'}")
    else:
        from .toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

    # Plugin-registered tools are now resolved through the normal toolset
    # path — validate_toolset() / resolve_toolset() / get_all_toolsets()
    # all check the tool registry for plugin-provided toolsets.  No bypass
    # needed; plugins respect enabled_toolsets / disabled_toolsets like any
    # other toolset.

    # Ask the registry for schemas (only returns tools whose check_fn passes)
    filtered_tools = registry.get_definitions(tools_to_include, quiet=quiet_mode)

    # The set of tool names that actually passed check_fn filtering.
    # Use this (not tools_to_include) for any downstream schema that references
    # other tools by name — otherwise the model sees tools mentioned in
    # descriptions that don't actually exist, and hallucinates calls to them.
    available_tool_names = {t["function"]["name"] for t in filtered_tools}

    # Rebuild execute_code schema to only list sandbox tools that are actually
    # available.  Without this, the model sees "web_search is available in
    # execute_code" even when the API key isn't configured or the toolset is
    # disabled.
    if "execute_code" in available_tool_names:
        from ...infrastructure.execution.code_execution_tool import SANDBOX_ALLOWED_TOOLS, build_execute_code_schema
        sandbox_enabled = set(SANDBOX_ALLOWED_TOOLS) & available_tool_names
        dynamic_schema = build_execute_code_schema(sandbox_enabled)
        for i, td in enumerate(filtered_tools):
            if td.get("function", {}).get("name") == "execute_code":
                filtered_tools[i] = {"type": "function", "function": dynamic_schema}
                break

    # Strip web tool cross-references from browser_navigate description when
    # web_search / web_extract are not available.  The static schema says
    # "prefer web_search or web_extract" which causes the model to hallucinate
    # those tools when they're missing.
    if "browser_navigate" in available_tool_names:
        web_tools_available = {"web_search", "web_extract"} & available_tool_names
        if not web_tools_available:
            for i, td in enumerate(filtered_tools):
                if td.get("function", {}).get("name") == "browser_navigate":
                    desc = td["function"].get("description", "")
                    desc = desc.replace(
                        " For simple information retrieval, prefer web_search or web_extract (faster, cheaper).",
                        "",
                    )
                    filtered_tools[i] = {
                        "type": "function",
                        "function": {**td["function"], "description": desc},
                    }
                    break

    if not quiet_mode:
        if filtered_tools:
            tool_names = [t["function"]["name"] for t in filtered_tools]
            print(f"🛠️  Final tool selection ({len(filtered_tools)} tools): {', '.join(tool_names)}")
        else:
            print("🛠️  No tools selected (all filtered out or unavailable)")

    global _last_resolved_tool_names
    _last_resolved_tool_names = [t["function"]["name"] for t in filtered_tools]

    return filtered_tools


# =============================================================================
# handle_function_call  (the main dispatcher)
# =============================================================================

# Tools whose execution is intercepted by the Agent runtime.
# because they need agent-level state (for example TodoStore).
# The registry still holds their schemas; dispatch just returns a stub error
# so if something slips through, the LLM sees a sensible message.
_AGENT_LOOP_TOOLS = {"todo", "memory", "session_search", "delegate_task"}
_READ_SEARCH_TOOLS = {"read_file", "search_files"}


# =========================================================================
# Tool argument type coercion
# =========================================================================

def coerce_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce tool call arguments to match their JSON Schema types.

    LLMs frequently return numbers as strings (``"42"`` instead of ``42``)
    and booleans as strings (``"true"`` instead of ``true``).  This compares
    each argument value against the tool's registered JSON Schema and attempts
    safe coercion when the value is a string but the schema expects a different
    type.  Original values are preserved when coercion fails.

    Handles ``"type": "integer"``, ``"type": "number"``, ``"type": "boolean"``,
    and union types (``"type": ["integer", "string"]``).
    """
    if not args or not isinstance(args, dict):
        return args

    schema = registry.get_schema(tool_name)
    if not schema:
        return args

    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        return args

    for key, value in args.items():
        if not isinstance(value, str):
            continue
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")
        if not expected:
            continue
        coerced = _coerce_value(value, expected)
        if coerced is not value:
            args[key] = coerced

    return args


def _coerce_value(value: str, expected_type):
    """Attempt to coerce a string *value* to *expected_type*.

    Returns the original string when coercion is not applicable or fails.
    """
    if isinstance(expected_type, list):
        # Union type — try each in order, return first successful coercion
        for t in expected_type:
            result = _coerce_value(value, t)
            if result is not value:
                return result
        return value

    if expected_type in ("integer", "number"):
        return _coerce_number(value, integer_only=(expected_type == "integer"))
    if expected_type == "boolean":
        return _coerce_boolean(value)
    return value


def _coerce_number(value: str, integer_only: bool = False):
    """Try to parse *value* as a number.  Returns original string on failure."""
    try:
        f = float(value)
    except (ValueError, OverflowError):
        return value
    # Guard against inf/nan before int() conversion
    if f != f or f == float("inf") or f == float("-inf"):
        return f
    # If it looks like an integer (no fractional part), return int
    if f == int(f):
        return int(f)
    if integer_only:
        # Schema wants an integer but value has decimals — keep as string
        return value
    return f


def _coerce_boolean(value: str):
    """Try to parse *value* as a boolean.  Returns original string on failure."""
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return value


def handle_function_call(
    function_name: str,
    function_args: Dict[str, Any],
    task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_task: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
    main_runtime: Optional[Dict[str, Any]] = None,
    dynamic_handler: Optional[Any] = None,
    dynamic_effect: Optional[str] = None,
) -> str:
    """
    Main function call dispatcher that routes calls to the tool registry.

    Args:
        function_name: Name of the function to call.
        function_args: Arguments for the function.
        task_id: Unique identifier for terminal/browser session isolation.
        user_task: The user's original task (for browser_snapshot context).
        enabled_tools: Tool names enabled for this session.  When provided,
                       execute_code uses this list to determine which sandbox
                       tools to generate.  Falls back to the process-global
                       ``_last_resolved_tool_names`` when omitted.

    Returns:
        Function result as a JSON string.
    """
    ensure_tools_discovered()

    # Coerce string arguments to their schema-declared types (e.g. "42"→42)
    function_args = coerce_tool_args(function_name, function_args)

    # Notify the read-loop tracker when a non-read/search tool runs,
    # so the *consecutive* counter resets (reads after other work are fine).
    if function_name not in _READ_SEARCH_TOOLS:
        try:
            from voidcube.extensions.tools.files.file_tools import notify_other_tool_call
            notify_other_tool_call(task_id or "default")
        except ImportError:
            pass  # file_tools may not be loaded yet

    prepared_action = None
    journal = None
    try:
        if function_name in _AGENT_LOOP_TOOLS:
            return json.dumps({"error": f"{function_name} must be handled by the agent loop"})

        try:
            from ..plugins.cli_adapter import invoke_hook
            invoke_hook(
                "pre_tool_call",
                tool_name=function_name,
                args=function_args,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
            )
        except (ImportError, RuntimeError):
            pass

        effect = dynamic_effect or registry.get_effect(function_name)
        if effect != "read_only":
            from ...infrastructure.persistence.action_journal import get_action_journal

            journal = get_action_journal()
            lease = (
                dict(main_runtime.get("execution_lease") or {})
                if isinstance(main_runtime, dict)
                else {}
            )
            lease_validator = (
                main_runtime.get("validate_execution_lease")
                if isinstance(main_runtime, dict)
                else None
            )
            autonomous_task = (
                main_runtime.get("autonomous_task")
                if isinstance(main_runtime, dict)
                else None
            )
            if autonomous_task is not None:
                if not callable(lease_validator):
                    raise ValueError(
                        "stale_execution_lease: trusted lease validator unavailable"
                    )
                if not lease:
                    raise ValueError("stale_execution_lease: execution lease required")
                lease_validator(
                    task_id=str(autonomous_task.get("task_id") or task_id or ""),
                    generation=int(lease.get("generation") or 0),
                    attempt_id=str(lease.get("attempt_id") or ""),
                    owner_session_id=str(
                        lease.get("owner_session_id") or session_id or ""
                    ),
                )
                current_task_id = str(
                    autonomous_task.get("task_id") or task_id or ""
                )

                def _lease_is_abandoned(action: dict[str, Any]) -> bool:
                    if str(action.get("task_id") or "") != current_task_id:
                        return False
                    if not all(
                        (
                            action.get("lease_generation") is not None,
                            action.get("attempt_id"),
                            action.get("owner_session_id"),
                        )
                    ):
                        return False
                    try:
                        lease_validator(
                            task_id=current_task_id,
                            generation=int(action["lease_generation"]),
                            attempt_id=str(action["attempt_id"]),
                            owner_session_id=str(action["owner_session_id"]),
                        )
                    except Exception as exc:
                        return getattr(exc, "code", None) == "stale_execution_lease"
                    return False

                journal.recover_abandoned_dispatched(
                    is_abandoned=_lease_is_abandoned
                )
            if lease and str(lease.get("state") or "") != "active":
                raise ValueError("stale_execution_lease: side effect dispatch rejected")
            prepared_action = journal.prepare(
                tool_name=function_name,
                arguments=function_args,
                effect=effect,
                task_id=task_id,
                lease_generation=(
                    int(lease.get("generation") or 0) if lease else None
                ),
                attempt_id=str(lease.get("attempt_id") or "") or None,
                call_id=tool_call_id,
                operation_id=str(function_args.get("operation_id") or "") or None,
                owner_session_id=(
                    str(lease.get("owner_session_id") or session_id or "") or None
                ),
            )
            if not journal.claim_dispatch(
                prepared_action.action_id,
                reason="tool_dispatch_started",
                lease_generation=(
                    int(lease.get("generation") or 0) if lease else None
                ),
                attempt_id=str(lease.get("attempt_id") or "") or None,
                owner_session_id=(
                    str(lease.get("owner_session_id") or session_id or "") or None
                ),
            ):
                record = journal.get(prepared_action.action_id) or {}
                return json.dumps(
                    {
                        "error": "duplicate_or_in_flight_action",
                        "action_id": prepared_action.action_id,
                        "state": str(record.get("state") or "unknown"),
                    },
                    ensure_ascii=False,
                )

        if dynamic_handler is not None:
            result = dynamic_handler(function_name, function_args)
        elif function_name == "execute_code":
            # Prefer the caller-provided list so subagents can't overwrite
            # the parent's tool set via the process-global.
            sandbox_enabled = enabled_tools if enabled_tools is not None else _last_resolved_tool_names
            result = registry.dispatch(
                function_name, function_args,
                raise_exceptions=True,
                task_id=task_id,
                enabled_tools=sandbox_enabled,
                main_runtime=main_runtime,
            )
        else:
            result = registry.dispatch(
                function_name, function_args,
                raise_exceptions=True,
                task_id=task_id,
                user_task=user_task,
                main_runtime=main_runtime,
            )

        if prepared_action is not None and journal is not None:
            result_state, _ = classify_tool_result(result)
            action_state = {
                ExecutionState.SUCCEEDED: "succeeded",
                ExecutionState.FAILED: "failed",
                ExecutionState.CANCELLED: "cancelled",
                ExecutionState.TIMED_OUT: "timed_out",
                ExecutionState.UNKNOWN: "unknown",
            }[result_state]
            is_error = result_state is not ExecutionState.SUCCEEDED
            journal.record_outcome(
                prepared_action.action_id,
                action_state,
                reason="tool_dispatch_finished",
                error_code=(
                    f"tool_{result_state.value}" if is_error else None
                ),
                error_summary=str(result)[:1000] if is_error else None,
                evidence={
                    "result_hash": hashlib.sha256(str(result).encode()).hexdigest(),
                    "state": result_state.value,
                },
            )

        try:
            from ..plugins.cli_adapter import invoke_hook
            invoke_hook(
                "post_tool_call",
                tool_name=function_name,
                args=function_args,
                result=result,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
            )
        except (ImportError, RuntimeError):
            pass

        return result

    except Exception as e:
        if prepared_action is not None and journal is not None:
            try:
                journal.transition(
                    prepared_action.action_id,
                    "unknown",
                    reason="dispatcher_exception_after_dispatch",
                    error_code=type(e).__name__,
                    error_summary=str(e),
                )
            except (KeyError, ValueError):
                pass
        error_msg = f"Error executing {function_name}: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg}, ensure_ascii=False)


# =============================================================================
# Public registry query functions
# =============================================================================

def get_all_tool_names() -> List[str]:
    """Return all registered tool names."""
    ensure_tools_discovered()
    return registry.get_all_tool_names()


def get_toolset_for_tool(tool_name: str) -> Optional[str]:
    """Return the toolset a tool belongs to."""
    ensure_tools_discovered()
    return registry.get_toolset_for_tool(tool_name)


def get_available_toolsets() -> Dict[str, dict]:
    """Return toolset availability info for UI display."""
    ensure_tools_discovered()
    return registry.get_available_toolsets()


def check_tool_availability(quiet: bool = False) -> Tuple[List[str], List[dict]]:
    """Return (available_toolsets, unavailable_info)."""
    ensure_tools_discovered()
    return registry.check_tool_availability(quiet=quiet)
