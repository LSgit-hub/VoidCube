#!/usr/bin/env python3
"""
Voidcube Agent CLI - Interactive Terminal Interface

A focused command-line interface for the Voidcube Agent.
Features ASCII art branding, interactive REPL, toolset selection, and rich formatting.

Usage:
    python cli.py                          # Start interactive mode with all tools
    python cli.py --toolsets web,terminal  # Start with specific toolsets
    python cli.py --skills VoidCube-agent-dev,github-auth
    python cli.py -q "your question"       # Single query mode
    python cli.py --list-tools             # List available tools and exit
"""

import logging
import os
import shutil
import sys
import json
import atexit
import time
import uuid
import warnings

# Suppress firecrawl "Field name 'json' shadows an attribute in parent" warnings
warnings.filterwarnings(
    "ignore",
    message=r"Field name \"json\" in \".*\" shadows an attribute in parent \"BaseModel\"",
    category=UserWarning
)

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    # 设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'

    # 重新配置标准输出编码
    try:
        import io
        if sys.stdout.encoding != 'utf-8':
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

    # 设置控制台代码页为 UTF-8
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable, Mapping, Sequence, TYPE_CHECKING

from agent.error_classifier import summarize_api_error
from VoidCube_app.application import ApplicationRuntime
from VoidCube_app.contracts.events import MessageDelta
from VoidCube_app.configuration import (
    get_application_config,
    reload_application_config,
)
from VoidCube_app.config import save_config_value
from VoidCube_app.gateway import (
    is_gateway_running as _is_gateway_running,
    register_session as _register_gateway_session,
)
from VoidCube_app.interaction_contract import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ClarificationDecision,
    ClarificationRequest,
    ClarificationStatus,
)
from VoidCube_app.session_identity import generate_session_id, resolve_session_identity
from VoidCube_app.session_lifecycle import (
    SessionHydration,
    SessionLifecycleState,
    SessionTitleStatus,
)
from VoidCube_app.tool_events import ToolEvent
from VoidCube_cli.tui_application import install_resize_reflow_cleanup
from VoidCube_cli.chat_block_store import ChatBlockStore
from VoidCube_cli.cli_tui_host_assembly_runtime import (
    CliTuiCompositionPorts,
    CliTuiExtensionPorts,
    CliTuiHostAssemblyPorts,
    CliTuiHostAssemblyRuntime,
    CliTuiInputPorts,
    CliTuiModalNavigationPorts,
    CliTuiModalPorts,
    CliTuiModalStatePorts,
    CliTuiModalStateRuntime,
    CliTuiPastePorts,
)
from VoidCube_cli.cli_tui_indicator_assembly_runtime import (
    CliTuiIndicatorAssemblyPorts,
    CliTuiIndicatorAssemblyRuntime,
)
from VoidCube_cli.scheduled_executor import (
    ScheduledTaskExecutorPorts,
    ScheduledTaskExecutorRuntime,
)
from VoidCube_cli.background_task_runtime import (
    BackgroundTaskPorts,
    BackgroundTaskRuntime,
    BackgroundTaskSnapshot,
    BackgroundTaskState,
)
from VoidCube_cli.cli_interactive_lifecycle_assembly_runtime import (
    CliInteractiveLifecycleAssemblyPorts,
    CliInteractiveLifecycleAssemblyRuntime,
)
from VoidCube_cli.cli_interactive_state_runtime import (
    CliInteractiveStatePorts,
    CliInteractiveStateRuntime,
)
from VoidCube_cli.cli_interactive_registration_runtime import (
    CliInteractiveRegistrationPorts,
    CliInteractiveRegistrationRuntime,
)
from VoidCube_cli.cli_history_display_runtime import (
    CliHistoryDisplayPorts,
    CliHistoryDisplayRuntime,
)
from VoidCube_cli.cli_status_bar_runtime import (
    CliStatusBarPorts,
    CliStatusBarRuntime,
)
from VoidCube_cli.cli_middle_status_runtime import (
    CliMiddleStatusPorts,
    CliMiddleStatusRuntime,
)
from VoidCube_cli.cli_subagent_observability_runtime import (
    CliSubagentObservabilityPorts,
    CliSubagentObservabilityRuntime,
)
from VoidCube_cli.cli_status_snapshot_runtime import (
    CliStatusSnapshotPorts,
    CliStatusSnapshotRuntime,
)
from VoidCube_cli.cli_git_status_runtime import (
    CliGitStatusPorts,
    CliGitStatusRuntime,
)
from VoidCube_cli.cli_background_response_runtime import (
    CliBackgroundResponsePorts,
    CliBackgroundResponseRuntime,
)
from VoidCube_cli.cli_command_availability_runtime import (
    CliCommandAvailabilityPorts,
    CliCommandAvailabilityRuntime,
)
from VoidCube_cli.cli_tui_image_indicator_runtime import (
    CliTuiImageIndicatorPorts,
)
from VoidCube_cli.cli_voice_status_runtime import (
    CliVoiceStatusPorts,
    CliVoiceStatusRuntime,
)
from VoidCube_cli.cli_exit_summary_runtime import (
    CliExitSummaryPorts,
    CliExitSummaryRuntime,
)
from VoidCube_cli.cli_btw_runtime import CliBtwPorts, CliBtwRuntime
from VoidCube_cli.cli_dynamic_command_runtime import (
    CliDynamicCommandPorts,
    CliDynamicCommandRuntime,
)
from VoidCube_cli.cli_turn_agent_route_runtime import (
    CliTurnAgentRoutePorts,
    CliTurnAgentRouteRuntime,
)
from VoidCube_cli.cli_runtime_credentials import (
    CliRuntimeCredentialsPorts,
    CliRuntimeCredentialsRuntime,
)
from VoidCube_cli.cli_agent_initialization_runtime import (
    CliAgentInitializationPorts,
    CliAgentInitializationRuntime,
)
from VoidCube_cli.execution_lease_validator import validate_execution_lease
from VoidCube_cli.cli_session_browser_runtime import (
    CliSessionBrowserPorts,
    CliSessionBrowserRuntime,
)
from VoidCube_cli.cli_model_picker_runtime import (
    CliModelPickerPorts,
    CliModelPickerRuntime,
)
from VoidCube_cli.cli_session_resume_runtime import (
    CliSessionResumePorts,
    CliSessionResumeRuntime,
)
from VoidCube_cli.cli_single_query_resume_runtime import (
    CliSingleQueryResumePorts,
    CliSingleQueryResumeRuntime,
)
from VoidCube_cli.cli_session_lifecycle_runtime import (
    CliSessionLifecyclePorts,
    CliSessionLifecycleRuntime,
)
from VoidCube_cli.cli_agent_turn_call_runtime import (
    CliAgentTurnCallPorts,
)
from VoidCube_cli.cli_turn_input_preparation_runtime import (
    CliTurnInputPreparationPorts,
)
from VoidCube_cli.cli_chat_error_runtime import (
    CliChatErrorPorts,
    CliChatErrorRuntime,
)
from VoidCube_cli.cli_startup_runtime import CliStartupPorts, CliStartupRuntime
from VoidCube_cli.cli_lifecycle_guards import (
    CliLifecycleGuardPorts,
    CliLifecycleGuardRuntime,
)
from VoidCube_cli.enter_keybinding_runtime import (
    EnterKeybindingPorts,
    EnterKeybindingRuntime,
)
from VoidCube_cli.voice_keybinding_runtime import (
    VoiceKeybindingPorts,
    VoiceKeybindingRuntime,
)
from VoidCube_cli.suspend_keybinding_runtime import (
    SuspendKeybindingPorts,
    SuspendKeybindingRuntime,
)
from VoidCube_cli.tui_dynamic_text_runtime import (
    TuiDynamicTextPorts,
    TuiDynamicTextRuntime,
)
from VoidCube_cli.cli_tui_prompt_runtime import (
    CliTuiPromptPorts,
    CliTuiPromptRuntime,
)
from VoidCube_cli.cli_tui_layout_metrics_runtime import (
    CliTuiLayoutMetricsPorts,
    CliTuiLayoutMetricsRuntime,
)
from VoidCube_cli.pending_input_runtime import (
    PendingInputExecutionPorts,
    PendingInputRuntime,
)
from VoidCube_cli.turn_postprocessing_runtime import (
    TurnPostprocessingPorts,
)
from VoidCube_cli.cli_chat_finalization_runtime import (
    CliChatFinalizationPorts,
    CliChatFinalizationRuntime,
)
from VoidCube_cli.cli_session_teardown_runtime import (
    CliSessionTeardownPorts,
    CliSessionTeardownRuntime,
)
from VoidCube_cli.turn_result_application_runtime import (
    TurnResultApplicationPorts,
)
from VoidCube_cli.turn_execution_runtime import (
    TurnExecutionPorts,
)
from VoidCube_cli.tui_teardown import TuiTeardownPorts, run_tui_teardown
from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState
from VoidCube_cli.voice_recording_runtime import (
    VoiceRecordingPorts,
    start_terminal_voice_recording,
    stop_terminal_voice_recording,
)
from VoidCube_app.voice_session_runtime import VoiceSessionRuntime
from VoidCube_app.autonomous_execution_runtime import (
    AutonomousExecutionRuntime,
    AutonomousExecutionRuntimePorts,
)
from VoidCube_app.contracts.scheduler import SchedulerEvent, TurnLane, TurnRequest
from VoidCube_app.turn_scheduler import CancellationToken, TurnScheduler
from VoidCube_cli.turn_scheduler_runtime import (
    CliTurnSchedulerPorts,
    CliTurnSchedulerRuntime,
)
from VoidCube_cli.cli_agent_turn_executor_runtime import (
    CliAgentTurnExecutorPorts,
    CliAgentTurnExecutorRuntime,
    CliAgentTurnResult,
)
from VoidCube_cli.autonomous_execution_output import run_autonomous_operation_silently
from VoidCube_cli.autonomous_execution_host import AutonomousExecutionHost
from VoidCube_cli.scheduler_display_projector import SchedulerDisplayProjector
from VoidCube_cli.scheduled_execution_host import ScheduledExecutionHost
from VoidCube_cli.terminal_text_layout import (
    display_width as _terminal_display_width,
    pad_to_width as _terminal_pad_to_width,
    trim_to_width as _terminal_trim_to_width,
)
from VoidCube_cli.chat_render_state import CliStreamRenderState
from VoidCube_cli.chat_stream_renderer import CliStreamRenderer
from VoidCube_cli.command_router import (
    looks_like_slash_command as _looks_like_slash_command,
    parse_cli_command,
)
from VoidCube_cli.command_handlers.registry import (
    autonomous_command_ports_for_host,
    exit_autonomous_gate_fast_for_host,
    install_cli_command_execution,
    reload_mcp_for_host,
    render_tools_for_host,
    render_toolsets_for_host,
)
from VoidCube_cli.command_handlers.reasoning import parse_reasoning_config
from VoidCube_cli.command_handlers.fast import parse_service_tier_config
from VoidCube_cli.interaction_adapter import (
    approval_choices as _approval_choices_view,
    approval_display_fragments as _approval_display_fragments_view,
    approval_sink as _approval_sink_view,
    clarification_sink as _clarification_sink_view,
    handle_approval_selection as _handle_approval_selection_view,
)
from VoidCube_cli.tool_event_adapter import project_tool_event as _project_tool_event_view

if TYPE_CHECKING:
    from run_agent import AIAgent  # noqa: F401 — only for static type-checkers

from VoidCube_cli.autonomous_events import (
    AutonomousPanelEventPorts,
    append_autonomous_execution_event as _append_autonomous_execution_event_view,
)
from VoidCube_cli.autonomous_presence import (
    refresh_gateway_cli_presence as _refresh_gateway_cli_presence_view,
    ensure_supervisor_task_session as _ensure_supervisor_task_session_view,
    push_cli_agent_scene as _push_cli_agent_scene,
)
from VoidCube_cli.autonomous_panel import (
    AutonomousPanelRenderPorts,
    AutonomousPanelStatePorts,
    get_autonomous_execution_panel_fragments as _get_autonomous_execution_panel_fragments_view,
    has_visible_autonomous_work as _has_visible_autonomous_work_view,
)
from VoidCube_cli.autonomous_runtime_host import (
    autonomous_executor_runtime as _autonomous_executor_runtime_view,
)
from VoidCube_cli.autonomous_status_host import (
    initialize_autonomous_status_caches as _initialize_autonomous_status_caches_view,
    refresh_autonomous_observation_surfaces as _refresh_autonomous_observation_surfaces_view,
    refresh_autonomous_gateway_status as _refresh_autonomous_gateway_status_view,
    refresh_gateway_autonomous_execute_snapshot as _refresh_gateway_autonomous_execute_snapshot_view,
    refresh_supervisor_status as _refresh_supervisor_status_view,
    supervisor_activity_snapshot as _supervisor_activity_snapshot_view,
)

logger = logging.getLogger(__name__)


def _background_completion_outcome(result: Optional[Dict[str, Any]]) -> tuple[bool, str, str]:
    response = str((result or {}).get("final_response") or "")
    error = str((result or {}).get("error") or "").strip()
    if result is None and not error:
        error = "API-A returned no result"
    if not response and error:
        response = f"Error: {error}"
    return not bool(error), response, error


# Suppress startup messages for clean CLI experience
os.environ["VOIDCUBE_QUIET"] = "1"  # Our own modules

# Initialize i18n (internationalization) early
try:
    from VoidCube_cli.i18n import init_i18n, t
    init_i18n()
except Exception:
    from VoidCube_cli.i18n import t as _fallback_t
    def t(key, default=None, **kwargs):
        return default or key

# prompt_toolkit for fixed input area TUI
from prompt_toolkit.patch_stdout import patch_stdout
try:
    from prompt_toolkit.cursor_shapes import CursorShape
    _STEADY_CURSOR = CursorShape.BLOCK  # Non-blinking block cursor
except (ImportError, AttributeError):
    _STEADY_CURSOR = None  # type: ignore[assignment]
import threading
import queue

# Lazy import for agent.usage_pricing — defers ~180ms (openai + usage_pricing import chain)
_format_duration_compact = None

def _lazy_import_usage_pricing():
    global _format_duration_compact
    if _format_duration_compact is None:
        from agent.usage_pricing import format_duration_compact as _FDC

        _format_duration_compact = _FDC


def _format_duration_compact_lazy(elapsed_seconds):
    _lazy_import_usage_pricing()
    return _format_duration_compact(elapsed_seconds)


from VoidCube_cli.banner import build_compact_banner
from VoidCube_cli.cli_ui import (
    ChatConsole as _BaseChatConsole,
    _accent_hex,
    _rich_text_from_ansi,
    _cprint,
    _ACCENT,
)
from VoidCube_cli.cli_handlers import (
    _setup_worktree,
    _cleanup_worktree,
    _prune_stale_worktrees,
    _git_repo_root,
    _git_head_commit,
    _git_improvement_diff,
)
from VoidCube_cli.attachments import (
    _collect_query_images,
    _format_image_attachment_badges,
    _should_auto_attach_clipboard_image_on_paste,
    _termux_example_image_path,
)

_COMMAND_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class ChatConsole(_BaseChatConsole):
    """Compatibility export bound to this module's patchable emitter."""

    def __init__(self):
        super().__init__(emit=_cprint)


# Load .env from ~/.VoidCube/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from VoidCube_core.constants import get_VoidCube_home
from VoidCube_app.environment import load_VoidCube_dotenv

_VoidCube_home = get_VoidCube_home()
_project_env = Path(__file__).resolve().parents[1] / '.env'
load_VoidCube_dotenv(VoidCube_home=_VoidCube_home, project_env=_project_env)


# =============================================================================
# Configuration Loading
# =============================================================================

def _load_prefill_messages(file_path: str) -> List[Dict[str, Any]]:
    """Load ephemeral prefill messages from a JSON file.
    
    The file should contain a JSON array of {role, content} dicts, e.g.:
        [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]
    
    Relative paths are resolved from ~/.VoidCube/.
    Returns an empty list if the path is empty or the file doesn't exist.
    """
    if not file_path:
        return []
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = _VoidCube_home / path
    if not path.exists():
        logger.warning("Prefill messages file not found: %s", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("Prefill messages file must contain a JSON array: %s", path)
            return []
        return data
    except Exception as e:
        logger.warning("Failed to load prefill messages from %s: %s", path, e)
        return []


def _get_chrome_debug_candidates(system: str) -> list[str]:
    """Return likely browser executables for local CDP auto-launch."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add_candidate(path: str | None) -> None:
        if not path:
            return
        normalized = os.path.normcase(os.path.normpath(path))
        if normalized in seen:
            return
        if os.path.isfile(path):
            candidates.append(path)
            seen.add(normalized)

    def _add_from_path(*names: str) -> None:
        for name in names:
            _add_candidate(shutil.which(name))

    if system == "Darwin":
        for app in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ):
            _add_candidate(app)
    elif system == "Windows":
        _add_from_path(
            "chrome.exe", "msedge.exe", "brave.exe", "chromium.exe",
            "chrome", "msedge", "brave", "chromium",
        )

        for base in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not base:
                continue
            for parts in (
                ("Google", "Chrome", "Application", "chrome.exe"),
                ("Chromium", "Application", "chrome.exe"),
                ("Chromium", "Application", "chromium.exe"),
                ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                ("Microsoft", "Edge", "Application", "msedge.exe"),
            ):
                _add_candidate(os.path.join(base, *parts))
    else:
        _add_from_path(
            "google-chrome", "google-chrome-stable", "chromium-browser",
            "chromium", "brave-browser", "microsoft-edge",
        )

    return candidates


def _resolve_cli_provider_config(
    config: Dict[str, Any],
    requested_provider: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    """Resolve the active provider and its unified provider entry."""
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    provider_key = str(requested_provider or runtime.get("active_provider") or "").strip()
    provider_config = providers.get(provider_key)
    return provider_key, dict(provider_config) if isinstance(provider_config, dict) else {}


def load_cli_config() -> Dict[str, Any]:
    """Load the canonical user configuration."""
    from VoidCube_app.config import load_config

    return reload_application_config(load_config)


def _get_cli_config():
    """Lazy-load and cache the CLI configuration (called automatically on first access)."""
    from VoidCube_app.config import load_config

    return get_application_config(load_config)


# Module __getattr__ for transparent lazy config access.
# After first access, stashes the result as CLI_CONFIG in the module namespace
# so subsequent lookups avoid __getattr__ overhead entirely.
def __getattr__(name: str):
    if name == "CLI_CONFIG":
        cfg = _get_cli_config()
        # Cache directly in module namespace for zero-overhead subsequent access
        globals()["CLI_CONFIG"] = cfg
        return cfg
    raise AttributeError(f"module 'cli' has no attribute {name!r}")

def _init_cli_runtime():
    """Apply config-dependent CLI setup (deferred to avoid import-time config load)."""
    # Initialize centralized logging
    try:
        from VoidCube_core.logging import setup_logging
        setup_logging(mode="cli")
    except Exception:
        pass
    cfg = _get_cli_config()
    # Validate config structure — print warnings before user hits cryptic errors
    try:
        from VoidCube_app.config import print_config_warnings
        print_config_warnings()
    except Exception:
        pass
    # Initialize tool preview length from config
    try:
        from agent.display import set_tool_preview_max_len
        _tpl = cfg.get("display", {}).get("tool_preview_length", 0)
        set_tool_preview_max_len(int(_tpl) if _tpl else 0)
    except Exception:
        pass

# Neuter AsyncHttpxClientWrapper.__del__ before any AsyncOpenAI clients are
# created.  The SDK's __del__ schedules aclose() on asyncio.get_running_loop()
# which, during CLI idle time, finds prompt_toolkit's event loop and tries to
# close TCP transports bound to dead worker loops — producing
# "Event loop is closed" / "Press ENTER to continue..." errors.
# NOTE: Deferred to first AIAgent creation to avoid importing openai (~125ms) at
# module load time. The monkey-patch must be applied before any OpenAI client is
# instantiated, which happens during AIAgent.__init__ — calling it lazily there
# is early enough.
_neutered_async_httpx = False

def _ensure_async_httpx_neutered():
    """Apply OpenAI SDK monkey-patch exactly once, before first client creation."""
    global _neutered_async_httpx
    if _neutered_async_httpx:
        return
    _neutered_async_httpx = True
    try:
        from agent.auxiliary_client import neuter_async_httpx_del
        neuter_async_httpx_del()
    except Exception:
        pass

from rich import box as rich_box
from rich.console import Console
from rich.markup import escape as _escape
from rich.panel import Panel

from VoidCube_cli.banner import build_welcome_banner

# =============================================================================
# Lazy import helpers — defer heavy imports (run_agent, tools.*, agent.*) until
# first use. This shaves ~500ms off CLI startup time (the import cascade
# run_agent → tools.model_tools → all tool modules is the dominant cost).
# =============================================================================

_AIAgent_class = None
_tool_defs_fn = None
_validate_toolset_fn = None
_cleanup_all_terminals_fn = None
_cleanup_all_browsers_fn = None
_set_sudo_password_callback_fn = None
_set_approval_sink_fn = None
_set_secret_capture_callback_fn = None
_prompt_for_secret_fn = None


def _get_AIAgent():
    """Lazy-import AIAgent class (defers ~251ms of import chain).

    Always returns the local AIAgent class. The current CLI/API-A session
    is the canonical executor; the gateway is used for observability and
    governance coordination, not as an agent conversation proxy.
    """
    global _AIAgent_class
    if _AIAgent_class is None:
        _ensure_async_httpx_neutered()
        from run_agent import AIAgent as _AIAgent
        _AIAgent_class = _AIAgent
    return _AIAgent_class


def _register_with_gateway(session_id: str, model: str, provider: str) -> bool:
    """Register this CLI session through the shared Gateway client."""
    from memai.domain.scope import CLI_WORKSPACE_ID, DEFAULT_OWNER_ID

    return _register_gateway_session(
        session_id,
        model,
        provider,
        source="cli",
        owner_id=DEFAULT_OWNER_ID,
        workspace_id=CLI_WORKSPACE_ID,
    )


def _get_tool_definitions(*args, **kwargs):
    """Lazy-import get_tool_definitions (defers ~243ms of import chain)."""
    global _tool_defs_fn
    if _tool_defs_fn is None:
        from tools.model_tools import get_tool_definitions as _fn
        _tool_defs_fn = _fn
    return _tool_defs_fn(*args, **kwargs)


def _get_validate_toolset(name: str) -> bool:
    global _validate_toolset_fn
    if _validate_toolset_fn is None:
        from tools.toolsets import validate_toolset as _fn
        _validate_toolset_fn = _fn
    return _validate_toolset_fn(name)


def _get_cleanup_all_terminals():
    global _cleanup_all_terminals_fn
    if _cleanup_all_terminals_fn is None:
        from tools.terminal_tool import cleanup_all_environments as _fn
        _cleanup_all_terminals_fn = _fn
    return _cleanup_all_terminals_fn()


def _get_cleanup_all_browsers():
    global _cleanup_all_browsers_fn
    if _cleanup_all_browsers_fn is None:
        from tools.browser_tool import _emergency_cleanup_all_sessions as _fn
        _cleanup_all_browsers_fn = _fn
    return _cleanup_all_browsers_fn()


def _get_set_sudo_password_callback(cb):
    global _set_sudo_password_callback_fn
    if _set_sudo_password_callback_fn is None:
        from tools.terminal_tool import set_sudo_password_callback as _fn
        _set_sudo_password_callback_fn = _fn
    return _set_sudo_password_callback_fn(cb)


def _get_set_approval_sink(sink):
    global _set_approval_sink_fn
    if _set_approval_sink_fn is None:
        from tools.terminal_tool import set_approval_sink as _fn
        _set_approval_sink_fn = _fn
    return _set_approval_sink_fn(sink)


def _get_set_secret_capture_callback():
    global _set_secret_capture_callback_fn
    if _set_secret_capture_callback_fn is None:
        from tools.skills_tool import set_secret_capture_callback as _fn
        _set_secret_capture_callback_fn = _fn
    return _set_secret_capture_callback_fn


def _get_prompt_for_secret():
    global _prompt_for_secret_fn
    if _prompt_for_secret_fn is None:
        from VoidCube_cli.callbacks import prompt_for_secret as _fn
        _prompt_for_secret_fn = _fn
    return _prompt_for_secret_fn


# Guard to prevent cleanup from running multiple times on exit
_cleanup_done = False
# Weak reference to the active AIAgent for memory provider shutdown at exit
_active_agent_ref = None

def _run_cleanup():
    """Run resource cleanup exactly once."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    try:
        _get_cleanup_all_terminals()
    except Exception:
        pass
    try:
        _get_cleanup_all_browsers()
    except Exception:
        pass
    try:
        from tools.mcp_tool import shutdown_mcp_servers
        shutdown_mcp_servers()
    except Exception:
        pass
    # Close cached auxiliary LLM clients (sync + async) so that
    # AsyncHttpxClientWrapper.__del__ doesn't fire on a closed event loop
    # and trigger prompt_toolkit's "Press ENTER to continue..." handler.
    try:
        from agent.auxiliary_client import shutdown_cached_clients
        shutdown_cached_clients()
    except Exception:
        pass
    # Shut down memory provider (on_session_end + shutdown_all) at actual
    # session boundary — NOT per-turn inside run_conversation().
    try:
        from VoidCube_cli.plugins import invoke_hook as _invoke_hook
        _invoke_hook("on_session_finalize", session_id=_active_agent_ref.session_id if _active_agent_ref else None, platform="cli")
    except Exception:
        pass
    try:
        if _active_agent_ref and hasattr(_active_agent_ref, 'shutdown_memory_provider'):
            _active_agent_ref.shutdown_memory_provider(
                getattr(_active_agent_ref, 'conversation_history', None) or []
            )
    except Exception:
        pass


# ============================================================================
# ASCII Art & Branding
# ============================================================================

# Color palette (hex colors for Rich markup):
# - Gold: #FFD700 (headers, highlights)
# - Amber: #FFBF00 (secondary highlights)
# - Bronze: #CD7F32 (tertiary elements)
# - Light: #FFF8DC (text)
# - Dim: #B8860B (muted text)

# ANSI building blocks for conversation display
_ACCENT_ANSI_DEFAULT = "\033[1;38;2;255;215;0m"  # True-color #FFD700 bold — fallback
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RST = "\033[0m"


# ASCII Art - VOIDCUBE-AGENT logo (full width, single line - requires ~95 char terminal)
VOIDCUBE_AGENT_LOGO = ""

VOIDCUBE_HERO = ""



# ============================================================================
# Skill Slash Commands — dynamic commands generated from installed skills
# ============================================================================

# Lazy import for skill commands — defers tools.skills_tool → VoidCube_app.config
# (~62ms import chain) until first skill command is processed.
_skill_commands_cache = None
_skill_cmd_imports = None


def _get_skill_commands():
    """Lazy-load and cache the skill commands dictionary."""
    global _skill_commands_cache, _skill_cmd_imports
    if _skill_commands_cache is None:
        from agent.skill_commands import (
            scan_skill_commands as _sc,
            build_skill_invocation_message as _bi,
            build_preloaded_skills_prompt as _bl,
        )
        _skill_cmd_imports = (_bi, _bl)
        _skill_commands_cache = _sc()
    return _skill_commands_cache


def _get_skill_invocation_message(*args, **kwargs):
    _get_skill_commands()  # ensure imports are done
    return _skill_cmd_imports[0](*args, **kwargs)


def _get_preloaded_skills_prompt(*args, **kwargs):
    _get_skill_commands()
    return _skill_cmd_imports[1](*args, **kwargs)


def _get_plugin_cmd_handler_names() -> set:
    """Return plugin command names (without slash prefix) for dispatch matching."""
    try:
        from VoidCube_cli.plugins import get_plugin_manager
        return set(get_plugin_manager()._plugin_commands.keys())  # type: ignore[attr-defined]
    except Exception:
        return set()


def _parse_skills_argument(skills: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize a CLI skills flag into a deduplicated list of skill identifiers."""
    if not skills:
        return []

    if isinstance(skills, str):
        raw_values = [skills]
    elif isinstance(skills, (list, tuple)):
        raw_values = [str(item) for item in skills if item is not None]
    else:
        raw_values = [str(skills)]

    parsed: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for part in raw.split(","):
            normalized = part.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            parsed.append(normalized)
    return parsed


# ============================================================================
# VoidcubeCLI Class
# ============================================================================

class VoidcubeCLI:
    """
    Interactive CLI for the Voidcube Agent.
    
    Provides a REPL interface with rich formatting, command history,
    and tool execution capabilities.
    """

    def _voice_state(self) -> CliVoiceRuntimeState:
        """Return this host's voice state, including minimal test hosts."""
        state = self.__dict__.get("_voice_runtime_state")
        if state is None:
            state = CliVoiceRuntimeState()
            self._voice_runtime_state = state
        return state

    @property
    def session_id(self) -> str:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.session_id
        return str(self.__dict__.get("_session_id", "") or "")

    @session_id.setter
    def session_id(self, value: str) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            runtime.state.session_id = str(value or "")
        else:
            self.__dict__["_session_id"] = str(value or "")

    @property
    def session_start(self) -> datetime:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.session_start
        return self.__dict__["_session_start"]

    @session_start.setter
    def session_start(self, value: datetime) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            runtime.state.session_start = value
        else:
            self.__dict__["_session_start"] = value

    @property
    def conversation_history(self) -> List[Dict[str, Any]]:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.conversation_history
        return self.__dict__.setdefault("_conversation_history", [])

    @conversation_history.setter
    def conversation_history(self, value: Sequence[Mapping[str, Any]]) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            runtime.replace_history(value)
        else:
            self.__dict__["_conversation_history"] = (
                value if isinstance(value, list) else [dict(message) for message in value]
            )

    @property
    def _pending_title(self) -> str | None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.pending_title
        return self.__dict__.get("_pending_title_fallback")

    @_pending_title.setter
    def _pending_title(self, value: str | None) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            runtime.set_pending_title(value)
        else:
            self.__dict__["_pending_title_fallback"] = value

    @property
    def _session_hydration(self) -> SessionHydration | None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.session_hydration
        return self.__dict__.get("_session_hydration_fallback")

    @_session_hydration.setter
    def _session_hydration(self, value: SessionHydration | None) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            if value is None:
                runtime.clear_session_hydration()
            else:
                runtime.set_session_hydration(value)
        else:
            self.__dict__["_session_hydration_fallback"] = value

    @property
    def _agent_running(self) -> bool:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.agent_running
        return bool(self.__dict__.get("_agent_running_fallback", False))

    @_agent_running.setter
    def _agent_running(self, value: bool) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            runtime.set_agent_running(value)
        else:
            self.__dict__["_agent_running_fallback"] = bool(value)

    @property
    def _pending_input(self):
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime.state.pending_input_queue
        fallback = self.__dict__.get("_pending_input_fallback")
        if fallback is None:
            fallback = queue.Queue()
            self.__dict__["_pending_input_fallback"] = fallback
        return fallback

    @_pending_input.setter
    def _pending_input(self, value) -> None:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            runtime.state.pending_input_queue = value
        else:
            self.__dict__["_pending_input_fallback"] = value

    def _initialize_application_runtime(self, session_identity) -> None:
        self.session_id = session_identity.session_id
        self._application_runtime = ApplicationRuntime.create(
            session_id=self.session_id,
            session_start=self.session_start,
            conversation_history=self.__dict__.pop("_conversation_history", ()),
            resumed=session_identity.resumed,
            event_sink=self._handle_application_event,
        )
        self.__dict__.pop("_session_id", None)
        self.__dict__.pop("_session_start", None)

    def _ensure_application_runtime(self) -> ApplicationRuntime:
        runtime = self.__dict__.get("_application_runtime")
        if runtime is not None:
            return runtime
        runtime = ApplicationRuntime.create(
            session_id=self.session_id,
            session_start=self.__dict__.get("_session_start", datetime.now()),
            conversation_history=self.conversation_history,
            resumed=False,
            event_sink=self._handle_application_event,
        )
        pending_title = self.__dict__.pop("_pending_title_fallback", None)
        hydration = self.__dict__.pop("_session_hydration_fallback", None)
        agent_running = self.__dict__.pop("_agent_running_fallback", False)
        pending_input = self.__dict__.pop("_pending_input_fallback", None)
        if pending_title is not None:
            runtime.set_pending_title(pending_title)
        if hydration is not None:
            runtime.set_session_hydration(hydration)
        runtime.set_agent_running(agent_running)
        if pending_input is not None:
            runtime.state.pending_input_queue = pending_input
        self.__dict__["_application_runtime"] = runtime
        self.__dict__.pop("_session_id", None)
        self.__dict__.pop("_session_start", None)
        return runtime

    def _chat_blocks(self) -> ChatBlockStore:
        """Return the renderer-neutral store for this CLI host."""
        store = self.__dict__.get("_chat_block_store")
        if store is None:
            store = ChatBlockStore()
            session_id = str(getattr(self, "session_id", "") or "").strip()
            if session_id:
                store.bind_session(session_id)
            self.__dict__["_chat_block_store"] = store
        return store

    def __init__(
        self,
        model: Optional[str] = None,
        toolsets: Optional[List[str]] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_turns: Optional[int] = None,
        verbose: bool = False,
        compact: bool = False,
        resume: Optional[str] = None,
        checkpoints: bool = False,
        pass_session_id: bool = False,
    ):
        """
        Initialize the Voidcube CLI.

        Args:
            model: Model to use (default: from the active provider config)
            toolsets: List of toolsets to enable (default: all)
            provider: Inference provider ("auto", "openrouter", "nous", "zai", "kimi-coding", "minimax", "minimax-cn")
            api_key: API key (default: from environment)
            base_url: API base URL (default: OpenRouter)
            max_turns: Maximum tool-calling iterations shared with subagents (default: 90)
            verbose: Enable verbose logging
            compact: Use compact display mode
            resume: Session ID to resume (restores conversation history from SQLite)
            pass_session_id: Include the session ID in the agent's system prompt
        """
        # Initialize Rich console
        self.console = Console()
        self.config = CLI_CONFIG
        display_config = CLI_CONFIG.get("display") or {}
        self.compact = compact if compact is not None else display_config.get("compact", False)
        # tool_progress: "off", "new", "all", "verbose" (from config.yaml display section)
        # YAML 1.1 parses bare `off` as boolean False — normalise to string.
        _raw_tp = display_config.get("tool_progress", "all")
        self.tool_progress_mode = "off" if _raw_tp is False else str(_raw_tp)
        # resume_display: "full" (show history) | "minimal" (one-liner only)
        self.resume_display = display_config.get("resume_display", "full")
        # bell_on_complete: play terminal bell (\a) when agent finishes a response
        self.bell_on_complete = display_config.get("bell_on_complete", False)
        # show_reasoning: display model thinking/reasoning before the response
        self.show_reasoning = display_config.get("show_reasoning", False)
        self.verbose = verbose if verbose is not None else (self.tool_progress_mode == "verbose")
        
        # streaming: stream tokens to the terminal as they arrive (display.streaming in config.yaml)
        self.streaming_enabled = display_config.get("streaming", False)

        # Inline diff previews for write actions (display.inline_diffs in config.yaml)
        self._inline_diffs_enabled = display_config.get("inline_diffs", True)

        self._stream_render_state = CliStreamRenderState()
        self._stream_renderer = CliStreamRenderer(
            self._stream_render_state,
            emit_line=lambda text: _cprint(text),
            should_emit=self._should_emit_scrollback_output,
            show_reasoning=lambda: self.show_reasoning,
            verbose=lambda: self.verbose,
        )
        self._pending_edit_snapshots: Dict[str, Any] = {}
        
        # Configuration priority: CLI args > active provider config.
        _active_provider, _active_provider_cfg = _resolve_cli_provider_config(
            CLI_CONFIG,
            provider,
        )
        _config_model = str(_active_provider_cfg.get("selected_model") or "").strip()
        self.model = model or _config_model
        if not self.model:
            _base_url = _active_provider_cfg.get("base_url", "")
            if "localhost" in _base_url or "127.0.0.1" in _base_url:
                from VoidCube_app.runtime_provider import _auto_detect_local_model
                _detected = _auto_detect_local_model(_base_url)
                if _detected:
                    self.model = _detected
        self._model_is_default = not model and not _config_model

        self._explicit_api_key = api_key
        self._explicit_base_url = base_url

        # Provider selection is resolved lazily at use-time via _ensure_runtime_credentials().
        self.requested_provider = _active_provider
        self._provider_source: Optional[str] = None
        self.provider = self.requested_provider or ""
        self.acp_command: Optional[str] = None
        self.acp_args: list[str] = []
        self.base_url = (
            base_url
            or _active_provider_cfg.get("base_url", "")
        ) or None
        # Match key to resolved base_url: OpenRouter URL → prefer OPENROUTER_API_KEY,
        # custom endpoint → prefer OPENAI_API_KEY (issue #560).
        # Note: _ensure_runtime_credentials() re-resolves this before first use.
        if api_key:
            self.api_key = api_key
        else:
            self.api_key = ""
        # Max turns priority: CLI arg > agent config > env var > default.
        _configured_max_turns = CLI_CONFIG["agent"].get("max_turns")
        if max_turns is not None:  # CLI arg was explicitly set
            self.max_turns = max_turns
        elif _configured_max_turns:
            self.max_turns = _configured_max_turns
        elif os.getenv("VOIDCUBE_MAX_ITERATIONS"):
            self.max_turns = int(os.getenv("VOIDCUBE_MAX_ITERATIONS"))
        else:
            self.max_turns = 90
        
        # Parse and validate toolsets
        self.enabled_toolsets = toolsets
        if toolsets and "all" not in toolsets and "*" not in toolsets:
            # Validate each toolset — MCP server names are added by
            # _get_platform_tools() but aren't registered in TOOLSETS yet
            # (that happens later in _sync_mcp_toolsets), so exclude them.
            mcp_names = set((CLI_CONFIG.get("mcp_servers") or {}).keys())
            invalid = [t for t in toolsets if not _get_validate_toolset(t) and t not in mcp_names]
            if invalid:
                self.console.print(f"[bold red]Warning: Unknown toolsets: {', '.join(invalid)}[/]")
        
        # Filesystem checkpoints: CLI flag > config
        cp_cfg = CLI_CONFIG.get("checkpoints", {})
        if isinstance(cp_cfg, bool):
            cp_cfg = {"enabled": cp_cfg}
        self.checkpoints_enabled = checkpoints or cp_cfg.get("enabled", False)
        self.checkpoint_max_snapshots = cp_cfg.get("max_snapshots", 50)
        self.pass_session_id = pass_session_id
        
        # Ephemeral system prompt: env var takes precedence, then config
        self.system_prompt = (
            os.getenv("VOIDCUBE_EPHEMERAL_SYSTEM_PROMPT", "")
            or CLI_CONFIG["agent"].get("system_prompt", "")
        )
        self.personalities = CLI_CONFIG["agent"].get("personalities", {})
        
        # Ephemeral prefill messages (few-shot priming, never persisted)
        self.prefill_messages = _load_prefill_messages(
            CLI_CONFIG["agent"].get("prefill_messages_file", "")
        )
        
        # Reasoning config (OpenRouter reasoning effort level)
        self.reasoning_config = parse_reasoning_config(
            CLI_CONFIG["agent"].get("reasoning_effort", "")
        )
        self.service_tier = parse_service_tier_config(
            CLI_CONFIG["agent"].get("service_tier", "")
        )
        
        # OpenRouter provider routing preferences
        pr = CLI_CONFIG.get("provider_routing", {}) or {}
        self._provider_sort = pr.get("sort")
        self._providers_only = pr.get("only")
        self._providers_ignore = pr.get("ignore")
        self._providers_order = pr.get("order")
        self._provider_require_params = pr.get("require_parameters", False)
        self._provider_data_collection = pr.get("data_collection")
        
        # Fallback provider chain — tried in order when primary fails after retries.
        # Supports new list format (fallback_providers) and legacy single-dict (fallback_model).
        fb = CLI_CONFIG.get("fallback_providers") or CLI_CONFIG.get("fallback_model") or []
        # Normalize legacy single-dict to a one-element list
        if isinstance(fb, dict):
            fb = [fb] if fb.get("provider") and fb.get("model") else []
        self._fallback_model = fb

        # Optional cheap-vs-strong routing for simple turns
        self._smart_model_routing = CLI_CONFIG.get("smart_model_routing", {}) or {}
        self._active_agent_route_signature = None

        # Agent will be initialized on first use
        self.agent: Optional[AIAgent] = None
        self._app = None  # prompt_toolkit Application (set in run())
        
        # Conversation state
        self.conversation_history: List[Dict[str, Any]] = []
        self.session_start = datetime.now()
        # Renderer-neutral records for the active CLI session. ApplicationRuntime
        # remains the canonical owner of conversation and turn state.
        self._chat_block_store = ChatBlockStore()
        # Interactive and autonomous Hosts index sessions. Scheduled work is
        # deliberately ephemeral and does not need session search or /title.
        self._session_db = None
        try:
            from VoidCube_core.state import SessionDB
            self._session_db = SessionDB()
        except Exception as e:
            logger.warning("Failed to initialize SessionDB — session will NOT be indexed for search: %s", e)
        
        session_identity = resolve_session_identity(
            requested_session_id=resume,
            auto_resume_enabled=bool(
                CLI_CONFIG["display"].get("auto_resume_last_session", False)
            ),
            session_index=self._session_db,
            session_start=self.session_start,
            interactive_source="cli",
            autonomous_source="cli_supervisor_task_lane",
        )
        self._initialize_application_runtime(session_identity)
        if session_identity.resume_lookup_error:
            logger.warning(
                "Failed to auto-resume last session: %s",
                session_identity.resume_lookup_error,
            )
        elif resume is None and self._application_runtime.state.resumed:
            logger.info("Auto-resuming last session: %s", self.session_id)
        
        # History file for persistent input recall across sessions
        self._history_file = _VoidCube_home / ".VoidCube_history"
        self._last_invalidate: float = 0.0  # throttle UI repaints
        self._app = None

        # ── Per-instance render caches (avoid disk I/O & subprocess on hot path) ──
        self._config_cache: Dict[str, Any] | None = None
        self._config_cache_ts: float = 0.0
        self._ascii_fallback: bool | None = None  # cached once, never changes mid-session
        self._tool_definitions_cache: tuple[tuple[str, ...], list[dict[str, Any]]] | None = None

        # State shared by interactive run() and single-query chat mode.
        # These must exist before any direct chat() call because single-query
        # mode does not go through run().
        self._autonomous_gate_active: bool = False
        self._autonomous_activation_pending: bool = False
        self._autonomous_mode_lock = threading.Lock()
        self._should_exit = False
        self._clarify_state = None
        self._clarify_freetext = False
        self._clarify_deadline = 0
        self._sudo_state = None
        self._sudo_deadline = 0
        self._modal_input_snapshot = None
        self._approval_state = None
        self._approval_deadline = 0
        self._approval_lock = threading.Lock()
        self._model_picker_state = None
        self._secret_state = None
        self._secret_deadline = 0
        self._spinner_text: str = ""  # thinking spinner text for TUI
        self._tool_start_time: float = 0.0  # monotonic timestamp when current tool started (for live elapsed)
        self._current_tool_name: str = ""  # function_name of currently running tool ("" when idle)
        self._last_scrollback_tool: str = ""  # last tool name printed to scrollback (for "new" dedup)
        self._command_running = False
        self._command_status = ""
        install_cli_command_execution(
            self,
            emit=_cprint,
            translate=t,
            chat_console_factory=ChatConsole,
            compact_banner_factory=build_compact_banner,
            skill_commands=_get_skill_commands,
            autonomous_command_ports=autonomous_command_ports_for_host(
                self,
                event_ports=self._autonomous_panel_event_ports(),
                emit=_cprint,
                refresh_gateway_cli_presence=lambda *, force=False: _refresh_gateway_cli_presence_view(
                    self,
                    force=force,
                    is_gateway_running=_is_gateway_running,
                    register_with_gateway=_register_with_gateway,
                    push_cli_agent_scene=_push_cli_agent_scene,
                    monotonic_time=time.monotonic,
                ),
                interrupt_current_task=self._interrupt_autonomous_task,
                push_cli_agent_scene=_push_cli_agent_scene,
                thread_factory=threading.Thread,
            ),
        )
        self._attached_images: list[Path] = []
        self._image_counter = 0
        self.preloaded_skills: list[str] = []
        self._startup_skills_line_shown = False

        # Direct chat() calls need voice state before interactive run() starts.
        self._voice_runtime_state = CliVoiceRuntimeState()

        # Status bar visibility (toggled via /statusbar)
        self._status_bar_visible = True

        # Background task tracking is owned by the explicit runtime state.
        self._background_task_state = BackgroundTaskState()
        self._last_gateway_presence_refresh_at: float = 0.0
        self._gateway_presence_refresh_interval_seconds: float = 30.0
        self._autonomous_execution_events: List[Dict[str, str]] = []
        self._autonomous_last_supervisor_event_key: str = ""
        self._autonomous_execution_host = None
        self._autonomous_execution_thread = None
        self._autonomous_execution_stop = threading.Event()
        self._scheduler_display_projector = SchedulerDisplayProjector()
        self._turn_scheduler_runtime = self._build_turn_scheduler_runtime()
        self._scheduled_execution_host = None
        self._scheduled_execution_gate = threading.Lock()
        self._scheduled_execution_active = False
        self._scheduled_companion_active = False
        self._scheduled_executor_runtime = self._create_scheduled_executor_runtime()
        _initialize_autonomous_status_caches_view(self)

    def _quiet_autonomous_cprint(self, *args: Any, **kwargs: Any) -> None:
        """Keep autonomous execution out of the user's scrollback."""
        del args, kwargs

    def _build_turn_scheduler_runtime(self) -> CliTurnSchedulerRuntime:
        scheduler = TurnScheduler(
            autonomous_gate_active=False,
            event_sink=self._handle_scheduler_event,
        )

        def execute_request(
            host: Any,
            request: TurnRequest,
            token: CancellationToken,
        ) -> Any:
            return host._execute_agent_turn_request(request, token)

        runtime = CliTurnSchedulerRuntime(
            scheduler,
            CliTurnSchedulerPorts(
                session_id=lambda host: str(getattr(host, "session_id", "") or ""),
                tool_policy=lambda host, payload, lane: host._turn_tool_policy(
                    payload,
                    lane,
                ),
                execute_user=execute_request,
                execute_autonomous=execute_request,
                cancel_user=lambda host, _request_id: host._cancel_agent_for_scheduler(),
                cancel_autonomous=lambda host, _request_id: host._cancel_agent_for_scheduler(),
            ),
            asynchronous=True,
            thread_factory=threading.Thread,
        )
        scheduler.set_executor(runtime)
        return runtime

    def _handle_scheduler_event(self, event: SchedulerEvent) -> None:
        projector = self.__dict__.get("_scheduler_display_projector")
        if projector is None:
            projector = SchedulerDisplayProjector()
            self.__dict__["_scheduler_display_projector"] = projector
        projector.accept(event)
        self._invalidate(min_interval=0.0)

    def _scheduler_display_snapshot(self):
        projector = self.__dict__.get("_scheduler_display_projector")
        if projector is None:
            projector = SchedulerDisplayProjector()
            self.__dict__["_scheduler_display_projector"] = projector
        return projector.snapshot(
            lambda: self._scheduler_runtime().scheduler.snapshot()
        )

    def _scheduler_display_events(self) -> tuple[dict[str, Any], ...]:
        projector = self.__dict__.get("_scheduler_display_projector")
        return projector.event_dicts() if projector is not None else ()

    def _cancel_agent_for_scheduler(self) -> None:
        agent = getattr(self, "agent", None)
        if agent is None:
            return
        try:
            agent.interrupt(None)
        except Exception:
            logger.debug("Agent cancellation callback failed", exc_info=True)

    def _turn_tool_policy(
        self,
        payload: Any,
        lane: TurnLane,
    ) -> dict[str, Any]:
        policy: dict[str, Any] = {"agent_role": lane.value}
        if lane is not TurnLane.SUPERVISOR_TASK:
            return policy
        return policy

    def _direct_turn_request(self, message: Any, images: list | None) -> TurnRequest:
        payload = (message, images)
        return TurnRequest(
            request_id=f"direct-{uuid.uuid4()}",
            lane=TurnLane.USER_CHAT,
            session_id=str(self.session_id or "direct-session"),
            prompt=payload,
            tool_policy=self._turn_tool_policy(payload, TurnLane.USER_CHAT),
            source=TurnLane.USER_CHAT.value,
        )

    def _scheduler_runtime(self) -> CliTurnSchedulerRuntime:
        runtime = self.__dict__.get("_turn_scheduler_runtime")
        if runtime is None:
            runtime = self._build_turn_scheduler_runtime()
            self._turn_scheduler_runtime = runtime
        return runtime

    def _create_scheduled_executor_runtime(self) -> ScheduledTaskExecutorRuntime:
        """Assemble scheduled execution from explicit CLI-owned state ports."""
        return ScheduledTaskExecutorRuntime(
            ScheduledTaskExecutorPorts(
                autonomous_mode_active=lambda: bool(
                    self._autonomous_gate_active
                    or self._autonomous_activation_pending
                ),
                autonomous_mode_lock=self._autonomous_mode_lock,
                execution_gate=self._scheduled_execution_gate,
                get_session_id=lambda: str(self.session_id or ""),
                set_execution_active=lambda active: setattr(
                    self, "_scheduled_execution_active", bool(active)
                ),
                set_companion_active=lambda active: setattr(
                    self, "_scheduled_companion_active", bool(active)
                ),
                cancel_background_task=lambda task_id, reason: self._background_task_runtime().ports.state.cancel(
                    task_id, reason
                ),
                start_background_task=self._start_scheduled_execution_task,
            )
        )

    def _should_emit_scrollback_output(self) -> bool:
        """Return whether this host may write into the user's main CLI transcript."""
        return True

    def _ensure_scheduled_execution_host(self):
        host = getattr(self, "_scheduled_execution_host", None)
        if host is not None:
            return host
        host = ScheduledExecutionHost(
            ensure_credentials=self._ensure_runtime_credentials,
            resolve_agent_route=self._resolve_scheduled_worker_route,
            create_agent=self._create_scheduled_agent,
            completion_outcome=_background_completion_outcome,
            announce_start=self._announce_scheduled_execution_start,
            render_completion=self._render_scheduled_execution_completion,
            invalidate=lambda: self._invalidate(min_interval=0),
        )
        self._scheduled_execution_host = host
        return host

    def _create_scheduled_agent(
        self,
        turn_route: dict[str, Any],
        task_id: str,
        request_overrides: dict[str, Any],
        persist_session: bool,
    ) -> Any:
        return self._create_background_agent(
            turn_route,
            task_id,
            request_overrides,
            persist_session,
            scheduled=True,
        )

    def _resolve_scheduled_worker_route(
        self,
        prompt: str,
        worker_role: str,
    ) -> dict[str, Any]:
        base_route = self._resolve_turn_agent_config(prompt)
        if not worker_role:
            return base_route

        from VoidCube_app.companion_workers import resolve_companion_worker_route
        from VoidCube_app.config import load_config
        from VoidCube_app.runtime_provider import resolve_runtime_provider

        route = resolve_companion_worker_route(
            config=load_config(),
            requested_role=worker_role,
            base_route=base_route,
            resolve_provider=resolve_runtime_provider,
        )
        if self.service_tier:
            from VoidCube_app.models import resolve_fast_mode_overrides

            route["request_overrides"] = resolve_fast_mode_overrides(
                route.get("model")
            )
        configured_toolsets = route.get("enabled_toolsets")
        if configured_toolsets is not None:
            mcp_names = set((self.config.get("mcp_servers") or {}).keys())
            invalid = [
                toolset
                for toolset in configured_toolsets
                if not _get_validate_toolset(toolset) and toolset not in mcp_names
            ]
            if invalid:
                raise ValueError(
                    f"worker role '{worker_role}' has unknown toolsets: "
                    + ", ".join(invalid)
                )
        return route

    @staticmethod
    def _scheduled_execution_display_text(value: str, *, limit: int = 200) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 3)].rstrip() + "..."

    def _announce_scheduled_execution_start(
        self,
        _task_num: int,
        _task_id: str,
        _prompt: str,
        task_label: str,
    ) -> None:
        if task_label.startswith(("自主指令 · ", "自主媒体 · ")):
            _append_autonomous_execution_event_view(
                event_ports=self._autonomous_panel_event_ports(),
                message=f"开始执行: {task_label}",
                tone="info",
                stage="companion_started",
            )
            self._invalidate(min_interval=0)
            return
        if self._should_emit_scrollback_output():
            label = task_label.removeprefix("定时任务 · ")
            _cprint(f"  ◇ API-A 定时任务  {label}")

    def _render_scheduled_execution_completion(
        self,
        success: bool,
        response: str,
        error: str,
        _task_num: int,
        task_label: str,
        _response_title: str | None,
        _prompt: str,
    ) -> None:
        summary = self._scheduled_execution_display_text(
            (response or "执行完成") if success else error or response or "执行失败"
        )
        if task_label.startswith(("自主指令 · ", "自主媒体 · ")):
            _append_autonomous_execution_event_view(
                event_ports=self._autonomous_panel_event_ports(),
                message=f"{'执行完成' if success else '执行失败'}: {summary}",
                tone="success" if success else "error",
                stage="companion_completed" if success else "companion_failed",
                visible_seconds=12.0,
            )
            self._invalidate(min_interval=0)
            return
        if self._should_emit_scrollback_output():
            _cprint(f"  {'✓' if success else '!'} API-A 定时任务  {summary}")

    def _start_scheduled_execution_task(self, prompt: str, **kwargs: Any) -> bool:
        """Run scheduled work through the isolated scheduled Host runtime."""
        return self._ensure_scheduled_execution_host().start(
            prompt,
            **kwargs,
        )

    def _ensure_autonomous_execution_host(self):
        host = getattr(self, "_autonomous_execution_host", None)
        if host is not None:
            return host

        session_start = datetime.now()
        session_id = ""
        if self._session_db is not None:
            try:
                sessions = self._session_db.list_sessions_rich(
                    limit=20,
                    exclude_id_prefixes=["scheduled_"],
                )
                session_id = next(
                    (
                        str(session.get("id") or "").strip()
                        for session in sessions
                        if session.get("source") == "cli_supervisor_task_lane"
                        and session.get("ended_at") is None
                        and str(session.get("id") or "").strip()
                    ),
                    "",
                )
            except Exception:
                logger.debug("Could not resolve autonomous session", exc_info=True)
        session_id = session_id or generate_session_id(session_start)
        autonomous_session_db = self._session_db
        if self._session_db is not None:
            try:
                from VoidCube_core.state import SessionDB

                autonomous_session_db = SessionDB()
            except Exception:
                logger.debug(
                    "Could not open autonomous SessionDB connection",
                    exc_info=True,
                )
        host = AutonomousExecutionHost(
            session_id=session_id,
            session_start=session_start,
            model=str(self.model or ""),
            provider=str(self.provider or ""),
            session_db=autonomous_session_db,
            scheduler_runtime=self._scheduler_runtime(),
            execute_turn=self._execute_autonomous_agent_turn,
            invalidate=lambda: self._invalidate(min_interval=0.5),
            tool_event_sink=self._project_autonomous_tool_event,
            panel_event_ports=self._autonomous_panel_event_ports,
        )
        _ensure_supervisor_task_session_view(host, logger_debug=logger.debug)
        self._autonomous_execution_host = host
        return host

    def _project_autonomous_tool_event(
        self,
        owner: AutonomousExecutionHost,
        event: ToolEvent,
    ) -> None:
        def append_event(
            _host: Any,
            message: str,
            *,
            tone: str = "info",
            stage: str = "",
        ) -> None:
            _append_autonomous_execution_event_view(
                event_ports=owner._autonomous_panel_event_ports(),
                message=message,
                tone=tone,
                stage=stage,
            )

        _project_tool_event_view(
            owner,
            event,
            append_autonomous_event=append_event,
            emit_line=self._quiet_autonomous_cprint,
        )

    def _execute_autonomous_agent_turn(
        self,
        owner: AutonomousExecutionHost,
        request: TurnRequest,
        cancellation: CancellationToken,
    ) -> Optional[str]:
        result = self._agent_turn_executor_runtime(owner).execute(request, cancellation)
        return result.response if isinstance(result, CliAgentTurnResult) else result

    def _autonomous_execution_runtime(self):
        execution_host = self._ensure_autonomous_execution_host()
        return _autonomous_executor_runtime_view(
            execution_host,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=self._quiet_autonomous_cprint,
        )

    def _autonomous_execution_lifecycle(self) -> AutonomousExecutionRuntime:
        runtime = self.__dict__.get("_autonomous_execution_lifecycle_runtime")
        if runtime is not None:
            return runtime

        def ensure_stop_event() -> threading.Event:
            event = getattr(self, "_autonomous_execution_stop", None)
            if event is None:
                event = threading.Event()
                self._autonomous_execution_stop = event
            return event

        def refresh_statuses(execution_host: Any) -> None:
            _refresh_supervisor_status_view(execution_host)
            _refresh_autonomous_gateway_status_view(execution_host)
            _refresh_gateway_autonomous_execute_snapshot_view(execution_host)
            _refresh_gateway_cli_presence_view(
                execution_host,
                force=False,
                is_gateway_running=_is_gateway_running,
                register_with_gateway=_register_with_gateway,
                push_cli_agent_scene=_push_cli_agent_scene,
                monotonic_time=time.monotonic,
            )

        def get_pending_input(execution_host: Any) -> object | None:
            try:
                return execution_host._pending_input.get_nowait()
            except Exception:
                return None

        def can_poll_workflow(execution_host: Any) -> bool:
            return not getattr(self, "_scheduled_execution_active", False) and not getattr(
                execution_host,
                "_agent_running",
                False,
            )

        def deactivate_execution_host(execution_host: Any | None) -> bool:
            if execution_host is None:
                return False
            execution_host._autonomous_gate_active = False
            return True

        def interrupt_running_agent(execution_host: Any | None) -> None:
            try:
                if execution_host and execution_host.agent and execution_host._agent_running:
                    execution_host.agent.interrupt()
            except Exception:
                pass

        def interrupt_current_task() -> None:
            try:
                self._autonomous_execution_runtime().interrupt_current_task(
                    reason="自主链路已停止；当前链路项被用户中断。",
                    source="autonomous_execution_stop",
                    timeout=5,
                )
            except Exception:
                pass

        def signal_stop() -> None:
            ensure_stop_event().set()

        runtime = AutonomousExecutionRuntime(
            AutonomousExecutionRuntimePorts(
                get_execution_host=lambda: getattr(
                    self,
                    "_autonomous_execution_host",
                    None,
                ),
                ensure_execution_host=self._ensure_autonomous_execution_host,
                get_execution_thread=lambda: getattr(
                    self,
                    "_autonomous_execution_thread",
                    None,
                ),
                store_execution_thread=lambda thread: setattr(
                    self,
                    "_autonomous_execution_thread",
                    thread,
                ),
                ensure_stop_event=ensure_stop_event,
                execution_active=lambda: bool(self._autonomous_gate_active),
                set_execution_active=lambda host, active: setattr(
                    host,
                    "_autonomous_gate_active",
                    active,
                ),
                build_executor_runtime=lambda _host: self._autonomous_execution_runtime(),
                refresh_statuses=refresh_statuses,
                can_poll_workflow=can_poll_workflow,
                get_pending_input=get_pending_input,
                execute_pending_input=lambda host, pending: run_autonomous_operation_silently(
                    lambda: host._execute_pending_input(
                        pending,
                        app=None,
                    )
                ),
                invalidate=lambda: self._invalidate(min_interval=0.5),
                report_error=lambda error: logger.debug(
                    "Autonomous execution loop error: %s",
                    error,
                ),
                publish_idle_scene=lambda host: _push_cli_agent_scene(
                    "idle",
                    session_id=getattr(host, "session_id", None),
                    agent_role="supervisor_task",
                ),
                deactivate_execution_host=deactivate_execution_host,
                interrupt_running_agent=interrupt_running_agent,
                interrupt_current_task=interrupt_current_task,
                signal_stop=signal_stop,
                thread_factory=threading.Thread,
            )
        )
        self._autonomous_execution_lifecycle_runtime = runtime
        return runtime

    def _start_autonomous_execution(self) -> bool:
        """Start the API-A autonomous execution loop."""
        self._scheduler_runtime().enable_autonomous()
        return self._autonomous_execution_lifecycle().start()

    def _stop_autonomous_execution(self, *, interrupt: bool = False) -> None:
        self._scheduler_runtime().cancel_autonomous()
        self._autonomous_execution_lifecycle().stop(interrupt=interrupt)

    def _interrupt_autonomous_task(
        self,
        *,
        reason: str,
        source: str,
        timeout: float = 5,
    ) -> bool:
        execution_host = getattr(self, "_autonomous_execution_host", None)
        if execution_host is None:
            return _autonomous_executor_runtime_view(
                self,
                push_cli_agent_scene=_push_cli_agent_scene,
                git_head_commit=_git_head_commit,
                git_improvement_diff=_git_improvement_diff,
                cprint=_cprint,
            ).interrupt_current_task(reason=reason, source=source, timeout=timeout)
        return _autonomous_executor_runtime_view(
            execution_host,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=self._quiet_autonomous_cprint,
        ).interrupt_current_task(reason=reason, source=source, timeout=timeout)

    def _invalidate(self, min_interval: float = 0.25) -> None:
        """Throttled UI repaint — prevents terminal blinking on slow/SSH connections."""
        import time as _time
        now = _time.monotonic()
        if hasattr(self, "_app") and self._app and (now - self._last_invalidate) >= min_interval:
            self._last_invalidate = now
            self._app.invalidate()

    def _status_bar_context_style(self, percent_used: Optional[int]) -> str:
        if percent_used is None:
            return "class:status-bar-dim"
        # Color scheme: 0-60% green, 60-80% yellow, 80%+ red
        if percent_used >= 80:
            return "class:status-bar-critical"
        if percent_used >= 60:
            return "class:status-bar-warn"
        return "class:status-bar-good"

    def _build_context_bar(self, percent_used: Optional[int], width: int = 10) -> str:
        safe_percent = max(0, min(100, percent_used or 0))
        filled = round((safe_percent / 100) * width)
        return f"[{('█' * filled) + ('░' * max(0, width - filled))}]"

    def _get_status_bar_snapshot(self) -> Dict[str, Any]:
        """Build the session status snapshot through explicit data ports."""
        agent = getattr(self, "agent", None)

        def _agent_usage() -> dict[str, Any]:
            return {
                field: getattr(agent, field, 0) or 0
                for field in CliStatusSnapshotRuntime._USAGE_FIELDS
            }

        def _context_usage() -> dict[str, Any]:
            compressor = getattr(agent, "context_compressor", None)
            if not compressor:
                return {}
            return {
                "context_tokens": getattr(compressor, "last_prompt_tokens", 0) or 0,
                "context_length": getattr(compressor, "context_length", 0) or 0,
                "compressions": getattr(compressor, "compression_count", 0) or 0,
            }

        return CliStatusSnapshotRuntime(
            CliStatusSnapshotPorts(
                configured_model=lambda: self.model,
                active_model=lambda: getattr(agent, "model", None),
                session_start=lambda: self.session_start,
                now=datetime.now,
                agent_usage=_agent_usage,
                context_usage=_context_usage,
                subagent_snapshot=self._get_subagent_observability_snapshot,
                format_duration=_format_duration_compact_lazy,
            )
        ).snapshot()

    def _get_subagent_display_managers(self) -> list[Any]:
        agent = getattr(self, "agent", None)
        if not agent:
            return []
        managers: list[Any] = []
        manager_map = getattr(agent, "_subagent_display_managers", None)
        if isinstance(manager_map, dict):
            for manager in manager_map.values():
                if manager is not None and manager not in managers:
                    managers.append(manager)
        history = getattr(agent, "_subagent_display_history", None)
        if isinstance(history, dict):
            for manager in reversed(tuple(history.values())):
                if manager is not None and manager not in managers:
                    managers.append(manager)
        single = getattr(agent, "_subagent_display_manager", None)
        if single is not None and single not in managers:
            managers.append(single)
        return managers

    def _get_subagent_observability_snapshot(self) -> Dict[str, Any]:
        return CliSubagentObservabilityRuntime(
            CliSubagentObservabilityPorts(
                display_managers=self._get_subagent_display_managers,
            )
        ).snapshot()

    @staticmethod
    def _status_bar_display_width(text: str) -> int:
        """Return terminal cell width for status-bar text.

        len() is not enough for prompt_toolkit layout decisions because some
        glyphs can render wider than one Python codepoint. Keeping the status
        bar within the real display width prevents it from wrapping onto a
        second line and leaving behind duplicate rows.
        """
        return _terminal_display_width(text)

    @classmethod
    def _trim_status_bar_text(cls, text: str, max_width: int) -> str:
        """Trim status-bar text to a single terminal row."""
        return _terminal_trim_to_width(text, max_width)

    @classmethod
    def _pad_status_bar_text(cls, text: str, width: int) -> str:
        """Pad text to an exact display width using terminal cell width."""
        return _terminal_pad_to_width(text, width)

    def _tui_layout_metrics_runtime(self) -> CliTuiLayoutMetricsRuntime:
        return CliTuiLayoutMetricsRuntime(
            CliTuiLayoutMetricsPorts(
                agent_running=lambda: bool(getattr(self, "_agent_running", False)),
                spinner_visible=lambda: bool(getattr(self, "_spinner_text", "")),
            )
        )

    def _autonomous_panel_render_ports(self) -> AutonomousPanelRenderPorts:
        layout_metrics = self._tui_layout_metrics_runtime()
        return AutonomousPanelRenderPorts(
            terminal_width=layout_metrics.terminal_width,
            trim_status_bar_text=self._trim_status_bar_text,
            pad_status_bar_text=self._pad_status_bar_text,
        )

    def _autonomous_panel_state_ports(self) -> AutonomousPanelStatePorts:
        def snapshot():
            owner = getattr(self, "_autonomous_execution_host", None)
            return owner.snapshot() if owner is not None else None

        def companion_tasks():
            owner = getattr(self, "_scheduled_execution_host", None)
            if owner is None:
                return ()
            return tuple(
                task
                for task in owner.snapshot().active_tasks
                if str(getattr(task, "prompt_preview", "") or "").startswith(
                    ("自主指令 · ", "自主媒体 · ")
                )
            )

        def pending_input_nonempty() -> bool:
            state = snapshot()
            return bool(state and state.pending_input_count)

        return AutonomousPanelStatePorts(
            gate_active=lambda: bool(self._autonomous_gate_active),
            session_id=lambda: snapshot().session_id if snapshot() else str(self.session_id or ""),
            current_task=lambda: snapshot().current_task if snapshot() else None,
            current_task_started_at=lambda: (
                snapshot().current_task_started_at if snapshot() else 0.0
            ),
            agent_running=lambda: bool(snapshot() and snapshot().agent_running),
            last_agent_turn_result=lambda: (
                snapshot().last_agent_turn_result if snapshot() else None
            ),
            pending_input_nonempty=pending_input_nonempty,
            execution_events=lambda: list(
                getattr(self, "_autonomous_execution_events", []) or []
            ),
            spinner_text=lambda: snapshot().spinner_text if snapshot() else "",
            companion_tasks=companion_tasks,
        )

    def _autonomous_panel_event_ports(self) -> AutonomousPanelEventPorts:
        return AutonomousPanelEventPorts(
            gate_active=lambda: bool(self._autonomous_gate_active),
            execution_events=lambda: list(
                getattr(self, "_autonomous_execution_events", []) or []
            ),
            set_execution_events=lambda events: setattr(
                self,
                "_autonomous_execution_events",
                list(events),
            ),
            trim_status_bar_text=self._trim_status_bar_text,
            last_supervisor_event_key=lambda: str(
                getattr(self, "_autonomous_last_supervisor_event_key", "") or ""
            ),
            set_last_supervisor_event_key=lambda value: setattr(
                self,
                "_autonomous_last_supervisor_event_key",
                str(value or ""),
            ),
        )

    @staticmethod
    def _get_tui_terminal_width(default: tuple[int, int] = (80, 24)) -> int:
        """Expose the layout metric to the autonomous panel adapter."""
        return CliTuiLayoutMetricsRuntime.terminal_width(default)

    def _get_voice_status_fragments(self, width: Optional[int] = None):
        """Build voice status fragments through the display runtime."""
        layout_metrics = self._tui_layout_metrics_runtime()
        return CliVoiceStatusRuntime(
            CliVoiceStatusPorts(
                terminal_width=layout_metrics.terminal_width,
                minimal_chrome=layout_metrics.minimal_chrome,
                recording=lambda: bool(self._voice_state().recording),
                processing=lambda: bool(self._voice_state().processing),
                continuous=lambda: bool(self._voice_state().continuous),
            )
        ).build(width)

    def _build_status_bar_text(self, width: Optional[int] = None) -> str:
        """Return a compact one-line session status string for the TUI footer.
        Format: model_name  percentage% (e.g., deepseek-v4-flash  60%)
        """
        try:
            snapshot = self._get_status_bar_snapshot()
            if width is None:
                width = self._get_tui_terminal_width()
            percent = snapshot["context_percent"]
            percent_label = f"{percent}%" if percent is not None else "--"

            # Simplified format: model name + percentage
            text = f"{snapshot['model_short']}  {percent_label}"
            return self._trim_status_bar_text(text, width)
        except Exception:
            return f"{self.model if getattr(self, 'model', None) else 'Voidcube'}"

    _autonomous_gate_last_event_ts: str = ""

    _current_autonomous_task: Dict[str, Any] | None = None
    _current_autonomous_task_started_at: float = 0.0
    _last_agent_turn_result: Dict[str, Any] | None = None
    _current_autonomous_task_run_id: str = ""

    def _pending_input_runtime(self) -> PendingInputRuntime:
        runtime = self.__dict__.get("_pending_input_runtime_instance")
        if runtime is not None:
            return runtime

        def invalidate_app(app: Any | None) -> None:
            if app is None:
                return
            try:
                app.invalidate()
            except Exception:
                pass

        def exit_app(app: Any | None) -> None:
            if app is not None and getattr(app, "is_running", False):
                app.exit()

        def reset_turn_state() -> None:
            self._spinner_text = ""
            self._tool_start_time = 0.0
            self._current_tool_name = ""
            self._last_scrollback_tool = ""

        runtime = PendingInputRuntime(
            PendingInputExecutionPorts(
                should_emit_scrollback=self._should_emit_scrollback_output,
                process_command=self.process_command,
                set_should_exit=lambda value: setattr(self, "_should_exit", bool(value)),
                reset_turn_state=reset_turn_state,
                submit_turn=lambda payload, app, on_finished: self._submit_turn_via_scheduler(
                    payload,
                    app,
                    on_finished=on_finished,
                ),
                invalidate_app=invalidate_app,
                exit_app=exit_app,
                voice_restart_ready=lambda: bool(
                    self._voice_state().mode
                    and self._voice_state().continuous
                    and not self._voice_state().recording
                ),
                restart_voice_recording=self._voice_start_recording,
                enqueue_pending_input=self._pending_input.put,
                render_markup=lambda text: ChatConsole().print(text),
                emit=_cprint,
            )
        )
        self._pending_input_runtime_instance = runtime
        return runtime

    def _execute_pending_input(self, user_input: Any, *, app=None) -> bool:
        """Execute one queued prompt/command through the pending-input runtime."""
        return self._pending_input_runtime().execute(user_input, app=app)

    def _submit_turn_via_scheduler(
        self,
        payload: Any,
        app: Any | None,
        *,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        del app
        runtime = self._scheduler_runtime()
        return runtime.submit_user(self, payload, on_finished=on_finished)

    @staticmethod
    def _use_ascii_fallback() -> bool:
        """Detect terminals that may not render emoji correctly (e.g. legacy conhost)."""
        import sys, os
        # Electron xterm renders color emoji as oversized fallback glyphs. The
        # one-cell status symbols remain aligned when the desktop PTY uses the
        # existing ASCII presentation, regardless of its parent terminal.
        if os.environ.get("VOIDCUBE_DESKTOP") == "1":
            return True
        if sys.platform != "win32":
            return False
        if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"):
            return False
        if os.environ.get("ConEmuANSI") or os.environ.get("ConEmuTask"):
            return False
        return True

    def _use_ascii_fallback_cached(self) -> bool:
        """Cached version — terminal type never changes mid-session."""
        if self._ascii_fallback is None:
            self._ascii_fallback = self._use_ascii_fallback()
        return self._ascii_fallback

    def _cached_load_config(self, ttl: float = 30.0) -> Dict[str, Any]:
        """Return cached config, refreshing from disk every *ttl* seconds.

        Also invalidates early when the config-watcher thread (in ``run()``)
        detects a file change via ``_config_mtime``.
        """
        import time
        now = time.time()
        config_mtime = getattr(self, '_config_mtime', 0.0)
        if (
            self._config_cache is not None
            and (now - self._config_cache_ts) < ttl
            and config_mtime <= self._config_cache_ts
        ):
            return self._config_cache
        from VoidCube_app.config import load_config
        self._config_cache = load_config()
        self._config_cache_ts = now
        return self._config_cache

    def _get_middle_status_fragments(self, is_active: bool = False) -> list[tuple[str, str]]:
        """Build middle status fragments through the display runtime."""
        return CliMiddleStatusRuntime(
            CliMiddleStatusPorts(
                supervisor_snapshot=lambda: _supervisor_activity_snapshot_view(self),
                memory_llm=lambda: (
                    self._cached_load_config().get("memory", {}).get("llm", {})
                ),
                ascii_mode=self._use_ascii_fallback_cached,
                subagent_snapshot=self._get_subagent_observability_snapshot,
                scheduler_snapshot=self._scheduler_display_snapshot,
            )
        ).build()

    def _get_git_status_simple(self) -> list[tuple[str, str]]:
        """Build compact git status through the cached display runtime."""
        runtime = self.__dict__.get("_git_status_runtime_instance")
        if runtime is None:
            def _git_display_factory() -> Any:
                from VoidCube_cli.git_display import GitDisplay

                return GitDisplay()

            runtime = CliGitStatusRuntime(
                CliGitStatusPorts(
                    git_display_factory=_git_display_factory,
                    clock=time.time,
                    thread_factory=threading.Thread,
                )
            )
            self._git_status_runtime_instance = runtime
        return runtime.build()
    
    def _get_status_bar_fragments(self):
        """Build the status bar through the display-only runtime."""
        layout_metrics = self._tui_layout_metrics_runtime()
        is_active = (
            getattr(self, "_spinner_text", "") != ""
            or getattr(self, "_tool_start_time", 0) > 0
            or getattr(self, "_command_running", False)
            or self._stream_render_state.started
            or self._scheduler_display_snapshot().active is not None
        )
        return CliStatusBarRuntime(
            CliStatusBarPorts(
                status_bar_visible=lambda: self._status_bar_visible,
                model_picker_open=lambda: bool(getattr(self, "_model_picker_state", None)),
                snapshot=self._get_status_bar_snapshot,
                terminal_width=layout_metrics.terminal_width,
                agent_active=lambda: is_active,
                middle_fragments=self._get_middle_status_fragments,
                git_fragments=self._get_git_status_simple,
                fallback_text=lambda: self._build_status_bar_text(),
                closing=lambda: bool(getattr(self, "_should_exit", False)),
            )
        ).build()

    def _on_thinking(self, text: str) -> None:
        """Called by agent when thinking starts/stops. Updates TUI spinner."""
        if not text:
            self._flush_reasoning_preview(force=True)
        # Start marquee refresh when thinking starts (let spinner_text control the loop)
        if text and not getattr(self, '_marquee_refresh_running', False):
            import threading
            def _marquee_refresh():
                self._marquee_refresh_running = True
                while getattr(self, '_spinner_text', ''):
                    self._invalidate(min_interval=0.0)
                    import time
                    time.sleep(0.1)
                self._marquee_refresh_running = False
            threading.Thread(target=_marquee_refresh, daemon=True).start()
        self._spinner_text = text or ""
        self._tool_start_time = 0.0  # clear tool timer when switching to thinking
        self._invalidate()

    # ── Streaming display ────────────────────────────────────────────────

    def _current_reasoning_callback(self):
        """Return the active reasoning display callback for the current mode."""
        if self.show_reasoning and self.streaming_enabled:
            return self._stream_reasoning_delta
        if self.verbose and not self.show_reasoning:
            return self._on_reasoning
        return None

    def _flush_reasoning_preview(self, *, force: bool = False) -> None:
        """Flush buffered reasoning text at natural boundaries."""
        self._stream_renderer.flush_reasoning_preview(force=force)

    def _stream_reasoning_delta(self, text: str) -> None:
        """Stream reasoning tokens through the CLI renderer."""
        self._stream_renderer.stream_reasoning_delta(text)

    def _close_reasoning_box(self) -> None:
        """Close the live reasoning box if it's open."""
        self._stream_renderer.close_reasoning_box()

    def _stream_delta(self, text) -> None:
        """Render one text delta or an intermediate tool-turn boundary."""
        self._stream_renderer.stream_delta(text)

    def _flush_stream(self) -> None:
        """Emit any remaining partial line from the stream buffer and close the box."""
        self._stream_renderer.flush_stream()

    def _command_spinner_frame(self) -> str:
        """Return the current spinner frame for slow slash commands."""
        import time as _time

        frame_idx = int(_time.monotonic() * 10) % len(_COMMAND_SPINNER_FRAMES)
        return _COMMAND_SPINNER_FRAMES[frame_idx]

    def _ensure_runtime_credentials(self) -> bool:
        """
        Ensure runtime credentials are resolved before agent use.
        Re-resolves provider credentials so key rotation and token refresh
        are picked up without restarting the CLI.
        Returns True if credentials are ready, False on auth failure.
        """
        resolution = CliRuntimeCredentialsRuntime(
            CliRuntimeCredentialsPorts(
                requested_provider=self.requested_provider,
                explicit_api_key=self._explicit_api_key,
                explicit_base_url=self._explicit_base_url,
                current={
                    "model": self.model,
                    "api_key": self.api_key,
                    "base_url": self.base_url,
                    "provider": self.provider,
                    "command": self.acp_command,
                    "args": list(self.acp_args or []),
                },
            )
        ).resolve()
        if not resolution.ready:
            message = resolution.error or "Provider credentials are unavailable."
            if message.startswith("Provider resolver returned") or message.startswith("No model selected"):
                print(f"\n⚠️  {message}")
            else:
                try:
                    ChatConsole().print(f"[bold red]{message}[/]")
                except Exception:
                    print(message)
            return False

        previous_model = self.model
        self.provider = resolution.provider
        self.acp_command = resolution.command
        self.acp_args = list(resolution.args)
        self._credential_pool = resolution.credential_pool
        self._provider_source = resolution.source
        self.api_key = resolution.api_key
        self.base_url = resolution.base_url
        self.model = resolution.model
        if (
            resolution.model_changed
            and not self._model_is_default
            and previous_model != self.model
        ):
            self.console.print(
                f"[yellow]⚠️  Normalized model '{previous_model}' to '{self.model}' "
                f"for {self.provider}.[/]"
            )

        # AIAgent/OpenAI client holds auth at init time, so rebuild if key,
        # routing, or the effective model changed.
        if (
            resolution.credentials_changed
            or resolution.routing_changed
            or resolution.model_changed
        ) and self.agent is not None:
            self.agent = None
            self._active_agent_route_signature = None

        return True

    def _resolve_turn_agent_config(self, user_message: str) -> dict:
        """Resolve one turn's model route through the routing runtime."""
        return CliTurnAgentRouteRuntime(
            CliTurnAgentRoutePorts(
                smart_model_routing=self._smart_model_routing,
                runtime_credentials={
                    "model": self.model,
                    "api_key": self.api_key,
                    "base_url": self.base_url,
                    "provider": self.provider,
                    "command": self.acp_command,
                    "args": list(self.acp_args or []),
                    "credential_pool": getattr(self, "_credential_pool", None),
                },
                service_tier=getattr(self, "service_tier", None),
            )
        ).resolve(user_message)

    def _effective_system_prompt(self) -> str | None:
        """Combine the configured prompt with the current session goal."""
        from VoidCube_cli.session_goal_runtime import get_goal, goal_prompt

        parts = [self.system_prompt or "", goal_prompt(get_goal(self))]
        prompt = "\n\n".join(part for part in parts if part).strip()
        return prompt or None

    def _init_agent(
        self,
        *,
        model_override: Optional[str] = None,
        runtime_override: Optional[dict] = None,
        route_label: Optional[str] = None,
        request_overrides: dict | None = None,
        enabled_toolsets_override: Optional[list[str]] = None,
    ) -> bool:
        """
        Initialize the agent on first use.
        When resuming a session, restores conversation history from SQLite.
        
        Returns:
            bool: True if successful, False otherwise
        """
        if self.agent is not None:
            return True

        if not self._ensure_runtime_credentials():
            return False

        # Initialize SQLite session store for CLI sessions (if not already done in __init__)
        if self._session_db is None:
            try:
                from VoidCube_core.state import SessionDB
                self._session_db = SessionDB()
            except Exception as e:
                logger.warning("SQLite session store not available — session will NOT be indexed: %s", e)
        
        # Single-query callers do not run the interactive preload path.
        if (
            self._ensure_application_runtime().state.resumed
            and self._session_db
            and not self.conversation_history
        ):
            hydration, loaded_now = self._hydrate_resumed_session()
            if not CliSingleQueryResumeRuntime(
                CliSingleQueryResumePorts(
                    session_id=lambda: self.session_id,
                    accent_color=_accent_hex,
                    escape=_escape,
                    translate=t,
                    emit=ChatConsole().print,
                )
            ).report(hydration, loaded_now):
                return False
        
        try:
            runtime = runtime_override or {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "provider": self.provider,
                "command": self.acp_command,
                "args": list(self.acp_args or []),
                "credential_pool": getattr(self, "_credential_pool", None),
            }
            effective_model = model_override or self.model
            self.agent = CliAgentInitializationRuntime(
                CliAgentInitializationPorts(
                    agent_factory=_get_AIAgent(),
                    runtime=runtime,
                    model=effective_model,
                    max_iterations=self.max_turns,
                    enabled_toolsets=(
                        self.enabled_toolsets
                        if enabled_toolsets_override is None
                        else enabled_toolsets_override
                    ),
                    verbose_logging=self.verbose,
                    quiet_mode=not self.verbose,
                    ephemeral_system_prompt=self._effective_system_prompt(),
                    prefill_messages=self.prefill_messages or None,
                    reasoning_config=self.reasoning_config,
                    service_tier=self.service_tier,
                    request_overrides=request_overrides,
                    providers_allowed=self._providers_only,
                    providers_ignored=self._providers_ignore,
                    providers_order=self._providers_order,
                    provider_sort=self._provider_sort,
                    provider_require_parameters=self._provider_require_params,
                    provider_data_collection=self._provider_data_collection,
                    session_id=self.session_id,
                    platform="cli",
                    session_db=self._session_db,
                    clarification_sink=self._ensure_application_runtime().clarification_sink(
                        self._clarification_sink
                    ),
                    reasoning_callback=self._current_reasoning_callback(),
                    fallback_model=self._fallback_model,
                    thinking_callback=self._on_thinking,
                    checkpoints_enabled=self.checkpoints_enabled,
                    checkpoint_max_snapshots=self.checkpoint_max_snapshots,
                    pass_session_id=self.pass_session_id,
                    tool_event_sink=self._ensure_application_runtime().tool_event_sink,
                    stream_delta_callback=(
                        self._ensure_application_runtime().message_delta_sink
                        if self.streaming_enabled
                        else None
                    ),
                    tool_gen_callback=self._on_tool_gen_start if self.streaming_enabled else None,
                    autonomous_task_provider=lambda: self._current_autonomous_task,
                    validate_execution_lease=validate_execution_lease,
                )
            ).create()
            # Store reference for atexit memory provider shutdown
            global _active_agent_ref
            _active_agent_ref = self.agent
            # Route agent status output through prompt_toolkit so ANSI escape
            # sequences aren't garbled by patch_stdout's StdoutProxy (#2262).
            self.agent._print_fn = _cprint  # type: ignore[assignment]
            self._active_agent_route_signature = (
                effective_model,
                runtime.get("provider"),
                runtime.get("base_url"),
                runtime.get("command"),
                tuple(runtime.get("args") or ()),
            )

            application_runtime = self._ensure_application_runtime()
            pending_title = application_runtime.state.pending_title
            if pending_title and self._session_db:
                try:
                    title_result = application_runtime.set_session_title(
                        repository=self._session_db,
                        raw_title=pending_title,
                    )
                    if title_result.status is SessionTitleStatus.UPDATED:
                        _cprint(f"  Session title applied: {title_result.title}")
                    elif title_result.status is SessionTitleStatus.CONFLICT:
                        _cprint(f"  Could not apply pending title: {title_result.error}")
                    else:
                        _cprint("  Could not apply pending title: session is not persisted")
                except Exception as e:
                    _cprint(f"  Could not apply pending title: {e}")
                application_runtime.clear_pending_title()

            # ── Gateway observability ───────────────────────────────────
            # The interactive CLI remains the canonical API-A runtime for
            # direct user turns, and it also hosts the supervisor_task lane
            # when autonomous chain work is claimed here. We still
            # register the session with Gateway for observability, but we do
            # NOT rewrite the CLI agent's base_url to Gateway here; doing so
            # would proxy the live CLI session through the daemon-side 6080
            # body service and reintroduce the split execution path we are
            # actively removing.
            if _is_gateway_running():
                _register_with_gateway(
                    self.session_id,
                    effective_model,
                    runtime.get("provider", ""),
                )

            return True
        except Exception as e:
            if self._should_emit_scrollback_output():
                ChatConsole().print(f"[bold red]Failed to initialize agent: {e}[/]")
            return False
    
    def show_banner(self):
        """Display the welcome banner."""
        self.console.clear()

        # Get context length for display before branching so it remains
        # available to the low-context warning logic in compact mode too.
        ctx_len = None
        if hasattr(self, 'agent') and self.agent and hasattr(self.agent, 'context_compressor'):
            ctx_len = self.agent.context_compressor.context_length
        
        # Auto-compact for narrow terminals — the full banner with hero art
        # + tool list needs ~80 columns minimum to render without wrapping.
        term_width = shutil.get_terminal_size().columns
        use_compact = self.compact or term_width < 80
        
        if use_compact:
            self.console.print(build_compact_banner())
            self._show_status()
        else:
            # Get tools for display
            tools = self._get_cached_tool_definitions()
            
            # Get terminal working directory (where commands will execute)
            cwd = os.getenv("TERMINAL_CWD", os.getcwd())
            
            # Build and display the banner
            build_welcome_banner(
                console=self.console,
                model=self.model,
                cwd=cwd,
                tools=tools,
                enabled_toolsets=self.enabled_toolsets,
                session_id=self.session_id,
                context_length=ctx_len,
                conversation_history=self.conversation_history,
            )
        
        # Warn about very low context lengths (common with local servers)
        if ctx_len and ctx_len <= 8192:
            self.console.print()
            self.console.print(
                f"[yellow]⚠️  Context length is only {ctx_len:,} tokens — "
                f"this is likely too low for agent use with tools.[/]"
            )
            self.console.print(
                "[dim]   Voidcube needs 16k–32k minimum. Tool schemas + system prompt alone use ~4k–8k.[/]"
            )
            base_url = getattr(self, "base_url", "") or ""
            if "11434" in base_url or "ollama" in base_url.lower():
                self.console.print(
                    "[dim]   Ollama fix: OLLAMA_CONTEXT_LENGTH=32768 ollama serve[/]"
                )
            elif "1234" in base_url:
                self.console.print(
                    "[dim]   LM Studio fix: Set context length in model settings → reload model[/]"
                )
            else:
                self.console.print(
                    "[dim]   Fix: Set model.context_length in config.yaml, or increase your server's context setting[/]"
                )

        # Warn if the configured model is a Nous Voidcube LLM (not agentic)
        model_name = getattr(self, "model", "") or ""
        if "VoidCube" in model_name.lower():
            self.console.print()
            self.console.print(
                "[bold yellow]⚠  Voidcube 3 & 4 models are NOT agentic and are not "
                "designed for use with Voidcube Agent.[/]"
            )
            self.console.print(
                "[dim]   They lack tool-calling capabilities required for agent workflows. "
                "Consider using an agentic model (GPT, Gemini, DeepSeek, Qwen, etc.).[/]"
            )
            self.console.print(
                "[dim]   Switch with: /model sonnet  or  /model gpt5[/]"
            )

        self.console.print()

    def _get_cached_tool_definitions(self) -> list[dict[str, Any]]:
        """Resolve tool schemas once per enabled-toolset configuration."""
        key = tuple(str(item) for item in (self.enabled_toolsets or ()))
        cached = self._tool_definitions_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        definitions = _get_tool_definitions(
            enabled_toolsets=list(key),
            quiet_mode=True,
        )
        self._tool_definitions_cache = (key, definitions)
        return definitions

    def _hydrate_resumed_session(self) -> tuple[SessionHydration, bool]:
        """Return one cached hydration result for the selected session."""
        return self._ensure_application_runtime().load_session_hydration(
            repository=self._session_db,
            session_id=self.session_id,
        )

    def _preload_resumed_session(self) -> bool:
        """Load a resumed session's history from the DB early (before first chat).

        Called from run() so the conversation history is available for display
        before the user sends their first message.  Sets
        ``self.conversation_history`` and prints the one-liner status.  Returns
        True if history was loaded, False otherwise.

        The corresponding block in ``_init_agent()`` reuses the cached outcome.
        """
        return CliSessionResumeRuntime(
            CliSessionResumePorts(
                resumed=lambda: self._ensure_application_runtime().state.resumed,
                repository_available=lambda: self._session_db is not None,
                session_id=lambda: self.session_id,
                hydrate=self._hydrate_resumed_session,
                accent_color=_accent_hex,
                translate=t,
                emit=self.console.print,
            )
        ).preload()

    def _display_resumed_history(self):
        """Render the resumed history through the dedicated display runtime."""
        self._chat_blocks().hydrate_history(self.conversation_history)
        CliHistoryDisplayRuntime(
            CliHistoryDisplayPorts(
                conversation_history=lambda: self.conversation_history,
                resume_display=lambda: self.resume_display,
                terminal_width=lambda: shutil.get_terminal_size((80, 24)).columns,
                translate=t,
                emit=_cprint,
                emit_blank_line=lambda: print(),
            )
        ).run()

    def _try_attach_clipboard_image(self) -> bool:
        """Check clipboard for an image and attach it if found.

        Saves the image to ~/.VoidCube/images/ and appends the path to
        ``_attached_images``.  Returns True if an image was attached.
        """
        from VoidCube_cli.clipboard import save_clipboard_image

        img_dir = get_VoidCube_home() / "images"
        self._image_counter += 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path = img_dir / f"clip_{ts}_{self._image_counter}.png"

        if save_clipboard_image(img_path):
            self._attached_images.append(img_path)
            return True
        self._image_counter -= 1
        return False

    def _preprocess_images_with_vision(self, text: str, images: list, *, announce: bool = True) -> str:
        """Analyze attached images via the vision tool and return enriched text.

        Instead of embedding raw base64 ``image_url`` content parts in the
        conversation (which only works with vision-capable models), this
        pre-processes each image through the auxiliary vision model (Gemini
        Flash) and prepends the descriptions to the user's message — the
        same approach the messaging gateway uses.

        The local file path is included so the agent can re-examine the
        image later with ``vision_analyze`` if needed.
        """
        import asyncio as _asyncio
        import json as _json
        from tools.vision_tools import vision_analyze_tool

        analysis_prompt = (
            "Describe everything visible in this image in thorough detail. "
            "Include any text, code, data, objects, people, layout, colors, "
            "and any other notable visual information."
        )

        enriched_parts = []
        for img_path in images:
            if not img_path.exists():
                continue
            size_kb = img_path.stat().st_size // 1024
            if announce:
                _cprint(f"  {_DIM}👁️  analyzing {img_path.name} ({size_kb}KB)...{_RST}")
            try:
                result_json: str = _asyncio.run(
                    vision_analyze_tool(image_path=str(img_path), prompt=analysis_prompt)
                )
                result = _json.loads(result_json)
                if result.get("success"):
                    description = result.get("analysis", "")
                    enriched_parts.append(
                        f"[The user attached an image. Here's what it contains:\n{description}]\n"
                        f"[If you need a closer look, use vision_analyze with "
                        f"image_path: {img_path}]"
                    )
                    if announce:
                        _cprint(f"  {_DIM}✓ image analyzed{_RST}")
                else:
                    enriched_parts.append(
                        f"[The user attached an image but it couldn't be analyzed. "
                        f"You can try examining it with vision_analyze using "
                        f"image_path: {img_path}]"
                    )
                    if announce:
                        _cprint(f"  {_DIM}⚠ vision analysis failed — path included for retry{_RST}")
            except Exception as e:
                enriched_parts.append(
                    f"[The user attached an image but analysis failed ({e}). "
                    f"You can try examining it with vision_analyze using "
                    f"image_path: {img_path}]"
                )
                if announce:
                    _cprint(f"  {_DIM}⚠ vision analysis error — path included for retry{_RST}")

        # Combine: vision descriptions first, then the user's original text
        user_text = text if isinstance(text, str) and text else ""
        if enriched_parts:
            prefix = "\n\n".join(enriched_parts)
            return f"{prefix}\n\n{user_text}" if user_text else prefix
        return user_text or "What do you see in this image?"

    def _show_status(self):
        """Show compact startup status line."""
        # Get tool count
        tools = self._get_cached_tool_definitions()
        tool_count = len(tools) if tools else 0

        # Format model name (shorten if needed)
        model_short = self.model.split("/")[-1] if "/" in self.model else self.model
        if len(model_short) > 30:
            model_short = model_short[:27] + "..."

        # Get API status indicator
        if self.api_key:
            api_indicator = "[green bold]●[/]"
        else:
            api_indicator = "[red bold]●[/]"

        separator_color, accent_color, label_color = "#B8860B", "#FFBF00", "#4dd0e1"
        toolsets_info = ""
        if self.enabled_toolsets and "all" not in self.enabled_toolsets:
            toolsets_info = f" [dim {separator_color}]·[/] [{label_color}]toolsets: {', '.join(self.enabled_toolsets)}[/]"

        provider_info = f" [dim {separator_color}]·[/] [dim]provider: {self.provider}[/]"
        if self._provider_source:
            provider_info += f" [dim {separator_color}]·[/] [dim]auth: {self._provider_source}[/]"

        self.console.print(
            f"  {api_indicator} [{accent_color}]{model_short}[/] "
            f"[dim {separator_color}]·[/] [bold {label_color}]{tool_count} tools[/]"
            f"{toolsets_info}{provider_info}"
        )

    def _command_availability_runtime(
        self,
    ) -> Optional[CliCommandAvailabilityRuntime]:
        try:
            from VoidCube_app.models import model_supports_fast_mode
        except Exception:
            return None
        return CliCommandAvailabilityRuntime(
            CliCommandAvailabilityPorts(
                model=lambda: getattr(
                    getattr(self, "agent", None),
                    "model",
                    None,
                )
                or getattr(self, "model", None),
                supports_fast_mode=model_supports_fast_mode,
            )
        )

    def _fast_command_available(self) -> bool:
        runtime = self._command_availability_runtime()
        return runtime.fast_available() if runtime is not None else False

    def _command_available(self, slash_command: str) -> bool:
        runtime = self._command_availability_runtime()
        if runtime is None:
            return slash_command != "/fast"
        return runtime.available(slash_command)

    def _session_browser_runtime(self) -> CliSessionBrowserRuntime:
        runtime = self.__dict__.get("_session_browser_runtime_instance")
        if runtime is not None:
            return runtime

        def list_sessions(**kwargs: Any) -> Sequence[Mapping[str, Any]]:
            session_db = getattr(self, "_session_db", None)
            if session_db is None:
                return []
            return session_db.list_sessions_rich(**kwargs)

        from VoidCube_cli.entrypoint_session import _relative_time

        runtime = CliSessionBrowserRuntime(
            CliSessionBrowserPorts(
                list_sessions=list_sessions,
                active_session_id=lambda: str(getattr(self, "session_id", "") or ""),
                relative_time=_relative_time,
                translate=t,
                emit=print,
            )
        )
        self._session_browser_runtime_instance = runtime
        return runtime

    def _list_recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent CLI sessions for in-chat browsing/resume affordances."""
        return self._session_browser_runtime().list_recent(limit=limit)

    def _show_recent_sessions(self, *, reason: str = "history", limit: int = 8) -> bool:
        """Render recent sessions inline from the active chat TUI.

        Returns True when something was shown, False if no session list was available.
        """
        return self._session_browser_runtime().show_recent(
            reason=reason,
            limit=limit,
        )

    def _apply_session_lifecycle_state(self, state: SessionLifecycleState) -> None:
        """Apply shared session state and synchronize the active Agent runtime."""
        CliSessionLifecycleRuntime(
            CliSessionLifecyclePorts(
                apply_shared_state=self._ensure_application_runtime().apply_session_state,
                activate_agent_session=lambda session_id, session_start: (
                    self.agent.activate_session(
                        session_id,
                        session_start=session_start,
                    )
                    if self.agent
                    else None
                ),
            )
        ).apply(state)

    def _prompt_text_input(self, prompt_text: str) -> str | None:
        """Prompt for free-text input safely inside or outside prompt_toolkit."""
        result = [None]

        def _ask():
            try:
                result[0] = input(prompt_text).strip() or None
            except EOFError:
                pass

        if self._app:
            from prompt_toolkit.application import run_in_terminal
            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            self._app.invalidate()
            try:
                run_in_terminal(_ask)
            finally:
                self._status_bar_visible = was_visible
                self._app.invalidate()
        else:
            _ask()
        return result[0]

    def _open_model_picker(self, providers: list, current_model: str, current_provider: str, user_provs=None) -> None:
        """Open prompt_toolkit-native /model picker modal."""
        self._capture_modal_input_snapshot()
        default_idx = next((i for i, p in enumerate(providers) if p.get("is_current")), 0)
        self._model_picker_state = {
            "stage": "provider",
            "providers": providers,
            "selected": default_idx,
            "current_model": current_model,
            "current_provider": current_provider,
            "user_provs": user_provs,
        }
        self._invalidate(min_interval=0.0)

    def _close_model_picker(self) -> None:
        self._model_picker_state = None
        self._restore_modal_input_snapshot()
        self._invalidate(min_interval=0.0)

    def _apply_model_switch_result(self, result, persist_global: bool) -> None:
        if not result.success:
            _cprint(f"  ✗ {result.error_message}")
            return

        old_model = self.model
        self.model = result.new_model
        self.provider = result.target_provider
        self.requested_provider = result.target_provider
        if result.api_key:
            self.api_key = result.api_key
            self._explicit_api_key = result.api_key
        if result.base_url:
            self.base_url = result.base_url
            self._explicit_base_url = result.base_url
        if self.agent is not None:
            try:
                self.agent.switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                )
            except Exception as exc:
                _cprint(f"  ⚠ Agent swap failed ({exc}); change applied to next session.")

        self._pending_model_switch_note = (
            f"[Note: model was just switched from {old_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            f"Adjust your self-identification accordingly.]"
        )

        provider_label = result.provider_label or result.target_provider
        _cprint(f"  ✓ Model switched: {result.new_model}")
        _cprint(f"    Provider: {provider_label}")

        mi = result.model_info
        if mi:
            context_window = getattr(mi, 'context_window', None) or getattr(mi, 'context_length', None)
            if context_window:
                _cprint(f"    Context: {context_window:,} tokens")
            max_output = getattr(mi, 'max_output', None) or getattr(mi, 'max_completion_tokens', None)
            if max_output:
                _cprint(f"    Max output: {max_output:,} tokens")
            if hasattr(mi, 'has_cost_data') and callable(mi.has_cost_data) and mi.has_cost_data():
                _cprint(f"    Cost: {mi.format_cost()}")
            if hasattr(mi, 'format_capabilities') and callable(mi.format_capabilities):
                _cprint(f"    Capabilities: {mi.format_capabilities()}")
        else:
            try:
                from agent.model_metadata import get_model_context_length
                ctx = get_model_context_length(
                    result.new_model,
                    base_url=result.base_url or self.base_url,
                    api_key=result.api_key or self.api_key,
                    provider=result.target_provider,
                )
                _cprint(f"    Context: {ctx:,} tokens")
            except Exception:
                pass

        if result.warning_message:
            _cprint(f"    ⚠ {result.warning_message}")
        if persist_global:
            try:
                from VoidCube_app.config import (
                    load_config,
                    save_config,
                    set_active_provider,
                    set_provider_model,
                )
                cfg = load_config()
                cfg = set_provider_model(cfg, result.target_provider, result.new_model, make_active=True)
                cfg = set_active_provider(cfg, result.target_provider)
                save_config(cfg)
            except Exception as exc:
                _cprint(f"    ⚠ Failed to save config: {exc}")
                return
            _cprint(t('    Saved to config.yaml'))
        else:
            _cprint(t("    (session only — won't persist after restart)"))

    def _handle_model_picker_selection(self, persist_global: bool = True) -> None:
        def switch_model(**kwargs: Any) -> Any:
            from VoidCube_cli.model_switch import switch_model as _switch_model

            return _switch_model(**kwargs)

        CliModelPickerRuntime(
            CliModelPickerPorts(
                state=lambda: self._model_picker_state,
                set_state=lambda value: setattr(self, "_model_picker_state", value),
                close_picker=self._close_model_picker,
                invalidate=lambda: self._invalidate(min_interval=0.0),
                switch_model=switch_model,
                apply_switch_result=self._apply_model_switch_result,
                current_provider=lambda: self.provider,
                current_model=lambda: self.model,
                current_base_url=lambda: self.base_url or "",
                current_api_key=lambda: self.api_key or "",
            )
        ).submit(persist_global=persist_global)

    def _should_handle_model_command_inline(self, text: str, has_images: bool = False) -> bool:
        """Return True when /model should be handled immediately on the UI thread."""
        if not text or has_images or not _looks_like_slash_command(text):
            return False
        try:
            from VoidCube_cli.commands import resolve_command
            base = text.split(None, 1)[0].lower().lstrip('/')
            cmd = resolve_command(base)
            return bool(cmd and cmd.name == "model")
        except Exception:
            return False


    def process_command(self, command: str) -> bool:
        """
        Process a slash command.
        
        Args:
            command: The command string (starting with /)
            
        Returns:
            bool: True to continue, False to exit
        """
        request = parse_cli_command(command)
        if self._command_blocked_during_turn(request.canonical):
            _cprint("  Command unavailable while a turn is active.")
            return True
        builtin = self._builtin_command_executor.execute(request)
        if builtin.handled:
            return builtin.continue_running

        _skcmds = _get_skill_commands()
        def _emit_dynamic_markup(text: str) -> None:
            console = getattr(self, "console", None)
            if console is not None:
                console.print(text)
            else:
                ChatConsole().print(text)

        return CliDynamicCommandRuntime(
            CliDynamicCommandPorts(
                custom_commands=self.config.get("quick_commands", {}),
                plugin_names=_get_plugin_cmd_handler_names(),
                skill_commands=_skcmds,
                get_plugin_handler=lambda name: __import__(
                    "VoidCube_cli.plugins", fromlist=["get_plugin_command_handler"]
                ).get_plugin_command_handler(name),
                build_skill_message=lambda name, arguments, task_id: _get_skill_invocation_message(
                    name, arguments, task_id=task_id
                ),
                session_id=lambda: self.session_id,
                enqueue_pending_input=lambda message: (
                    self._pending_input.put(message)
                    if hasattr(self, "_pending_input")
                    else None
                ),
                emit=_cprint,
                emit_markup=_emit_dynamic_markup,
            )
        ).run(request)

    def _command_blocked_during_turn(self, command_name: str) -> bool:
        """Prevent session/model mutations from racing an active turn."""
        protected_commands = {
            "new",
            "clear",
            "resume",
            "branch",
            "undo",
            "retry",
            "model",
            "provider",
            "config",
            "api",
            "goal",
        }
        if command_name not in protected_commands:
            return False
        runtime = self.__dict__.get("_turn_scheduler_runtime")
        if runtime is None:
            return False
        try:
            return runtime.scheduler.snapshot().active is not None
        except Exception:
            return True

    def _record_supervisor_ui_activity_safe(self, event_type: str, *, scene: str = "idle", summary: str = "") -> None:
        """Non-fatal UI activity recording — best-effort, never throws."""
        try:
            self._record_supervisor_ui_activity(event_type, scene=scene, summary=summary)
        except Exception:
            pass

    def _background_task_runtime(self) -> BackgroundTaskRuntime:
        runtime = self.__dict__.get("_background_task_runtime_instance")
        if runtime is not None:
            return runtime
        state = self.__dict__.get("_background_task_state")
        if state is None:
            state = BackgroundTaskState()
            self._background_task_state = state
        runtime = BackgroundTaskRuntime(
            BackgroundTaskPorts(
                state=state,
                ensure_credentials=self._ensure_runtime_credentials,
                resolve_agent_route=self._resolve_turn_agent_config,
                create_agent=self._create_background_agent,
                announce_start=self._announce_background_start,
                render_completion=self._render_background_completion,
                set_thinking=self._set_background_thinking,
                invalidate=lambda: self._invalidate(min_interval=0),
                bell_on_complete=self._bell_background_completion,
                completion_outcome=_background_completion_outcome,
            )
        )
        self._background_task_runtime_instance = runtime
        return runtime

    def _has_running_background_tasks(self) -> bool:
        return self._background_task_runtime().ports.state.has_running_tasks()

    def _list_background_tasks(self) -> tuple[BackgroundTaskSnapshot, ...]:
        return self._background_task_runtime().ports.state.active_snapshots()

    def _create_background_agent(
        self,
        turn_route: dict[str, Any],
        task_id: str,
        request_overrides: dict[str, Any],
        persist_session: bool,
        *,
        scheduled: bool | None = None,
    ) -> Any:
        runtime = turn_route["runtime"]
        minimal_scheduled_host = (
            str(task_id or "").startswith("scheduled_")
            if scheduled is None
            else bool(scheduled)
        )
        route_toolsets = turn_route.get("enabled_toolsets")
        return CliAgentInitializationRuntime(
            CliAgentInitializationPorts(
                agent_factory=_get_AIAgent(),
                runtime=runtime,
                model=turn_route["model"],
                max_iterations=self.max_turns,
                enabled_toolsets=(
                    self.enabled_toolsets
                    if route_toolsets is None
                    else list(route_toolsets)
                ),
                verbose_logging=False,
                quiet_mode=True,
                ephemeral_system_prompt=None,
                prefill_messages=None,
                reasoning_config=self.reasoning_config,
                service_tier=self.service_tier,
                request_overrides=request_overrides or None,
                providers_allowed=self._providers_only,
                providers_ignored=self._providers_ignore,
                providers_order=self._providers_order,
                provider_sort=self._provider_sort,
                provider_require_parameters=self._provider_require_params,
                provider_data_collection=self._provider_data_collection,
                session_id=task_id,
                platform="cli",
                session_db=None if minimal_scheduled_host else self._session_db,
                clarification_sink=None,
                reasoning_callback=None,
                fallback_model=(
                    None
                    if turn_route.get("worker_provider_explicit")
                    else self._fallback_model
                ),
                thinking_callback=None,
                checkpoints_enabled=False,
                checkpoint_max_snapshots=0,
                pass_session_id=False,
                tool_event_sink=None,
                stream_delta_callback=None,
                tool_gen_callback=None,
                persist_session=persist_session,
                skip_memory=True if minimal_scheduled_host else None,
                skip_context_files=True if minimal_scheduled_host else None,
            )
        ).create()

    def _announce_background_start(
        self,
        task_num: int,
        task_id: str,
        prompt: str,
        task_label: str,
    ) -> None:
        _cprint(
            f"  🔄 {task_label} #{task_num} started: \"{prompt[:60]}"
            f"{'...' if len(prompt) > 60 else ''}\""
        )
        _cprint(f"  Task ID: {task_id}")
        _cprint("  You can continue chatting — results will appear when done.\n")

    def _set_background_thinking(self, text: str) -> None:
        if not self._agent_running:
            self._spinner_text = text
            if self._app:
                self._app.invalidate()

    def _render_background_completion(
        self,
        success: bool,
        response: str,
        error: str,
        task_num: int,
        task_label: str,
        response_title: str | None,
        prompt: str,
    ) -> None:
        CliBackgroundResponseRuntime(
            CliBackgroundResponsePorts(
                invalidate=lambda: self._app.invalidate() if self._app else None,
                sleep=time.sleep,
                emit_blank_line=lambda: print(),
                emit=_cprint,
                create_console=ChatConsole,
                rich_text_from_ansi=_rich_text_from_ansi,
            )
        ).render(
            success,
            response,
            error,
            task_num,
            task_label,
            response_title,
            prompt,
        )

    def _bell_background_completion(self) -> None:
        if self.bell_on_complete:
            sys.stdout.write("\a")
            sys.stdout.flush()

    def _start_background_agent_task(
        self,
        prompt: str,
        *,
        task_id: Optional[str] = None,
        task_label: str = "Background task",
        response_title: Optional[str] = None,
        request_timeout_seconds: Optional[float] = None,
        timeout_seconds: Optional[float] = None,
        persist_session: bool = True,
        on_complete: Optional[Callable[[bool, str, str], None]] = None,
    ) -> bool:
        return self._background_task_runtime().start(
            prompt,
            task_id=task_id,
            task_label=task_label,
            response_title=response_title,
            request_timeout_seconds=request_timeout_seconds,
            timeout_seconds=timeout_seconds,
            persist_session=persist_session,
            on_complete=on_complete,
        )

    def _start_btw_side_question(self, question: str) -> bool:
        """Start an ephemeral /btw question through the dedicated runtime."""
        return CliBtwRuntime(
            CliBtwPorts(
                ensure_credentials=self._ensure_runtime_credentials,
                resolve_agent_route=self._resolve_turn_agent_config,
                conversation_history=lambda: self.conversation_history,
                create_agent=self._create_btw_agent,
                task_id_factory=lambda: (
                    f"btw_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"
                ),
                emit=_cprint,
                invalidate=lambda: self._app.invalidate() if self._app else None,
                sleep=time.sleep,
                emit_blank_line=lambda: print(),
                create_console=ChatConsole,
                rich_text_from_ansi=_rich_text_from_ansi,
                bell=self._bell_background_completion,
                thread_factory=threading.Thread,
            )
        ).start(question)

    def _create_btw_agent(
        self,
        turn_route: Mapping[str, Any],
        task_id: str,
    ) -> Any:
        """Create the no-tools, non-persistent agent used by /btw."""
        runtime = turn_route["runtime"]
        return CliAgentInitializationRuntime(
            CliAgentInitializationPorts(
                agent_factory=_get_AIAgent(),
                runtime=runtime,
                model=turn_route["model"],
                max_iterations=8,
                enabled_toolsets=[],
                verbose_logging=False,
                quiet_mode=True,
                ephemeral_system_prompt=None,
                prefill_messages=None,
                reasoning_config=self.reasoning_config,
                service_tier=self.service_tier,
                request_overrides=turn_route.get("request_overrides"),
                providers_allowed=self._providers_only,
                providers_ignored=self._providers_ignore,
                providers_order=self._providers_order,
                provider_sort=self._provider_sort,
                provider_require_parameters=self._provider_require_params,
                provider_data_collection=self._provider_data_collection,
                session_id=task_id,
                platform="cli",
                session_db=None,
                clarification_sink=None,
                reasoning_callback=None,
                fallback_model=self._fallback_model,
                thinking_callback=None,
                checkpoints_enabled=False,
                checkpoint_max_snapshots=0,
                pass_session_id=False,
                tool_event_sink=None,
                stream_delta_callback=None,
                tool_gen_callback=None,
                persist_session=False,
                skip_memory=True,
                skip_context_files=True,
            )
        ).create()

    @staticmethod
    def _try_launch_chrome_debug(port: int, system: str) -> bool:
        """Try to launch Chrome/Chromium with remote debugging enabled.

        Uses a dedicated user-data-dir so the debug instance doesn't conflict
        with an already-running Chrome using the default profile.

        Returns True if a launch command was executed (doesn't guarantee success).
        """
        import subprocess as _sp

        candidates = _get_chrome_debug_candidates(system)

        if not candidates:
            return False

        # Dedicated profile dir so debug Chrome won't collide with normal Chrome
        data_dir = str(_VoidCube_home / "chrome-debug")
        os.makedirs(data_dir, exist_ok=True)

        chrome = candidates[0]
        try:
            _sp.Popen(
                [
                    chrome,
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={data_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                start_new_session=True,  # detach from terminal
            )
            return True
        except Exception:
            return False

    def _toggle_verbose(self):
        """Cycle tool progress mode: off → new → all → verbose → off."""
        cycle = ["off", "new", "all", "verbose"]
        try:
            idx = cycle.index(self.tool_progress_mode)
        except ValueError:
            idx = 2  # default to "all"
        self.tool_progress_mode = cycle[(idx + 1) % len(cycle)]
        self.verbose = self.tool_progress_mode == "verbose"

        if self.agent:
            self.agent.verbose_logging = self.verbose
            self.agent.quiet_mode = not self.verbose
            self.agent.reasoning_callback = self._current_reasoning_callback()

        # Use raw ANSI codes via _cprint so the output is routed through
        # prompt_toolkit's renderer.  self.console.print() with Rich markup
        # writes directly to stdout which patch_stdout's StdoutProxy mangles
        # into garbled sequences like '?[33mTool progress: NEW?[0m' (#2262).
        from VoidCube_cli.colors import Colors as _Colors
        labels = {
            "off": f"{_Colors.DIM}Tool progress: OFF{_Colors.RESET} — silent mode, just the final response.",
            "new": f"{_Colors.YELLOW}Tool progress: NEW{_Colors.RESET} — show each new tool (skip repeats).",
            "all": f"{_Colors.GREEN}Tool progress: ALL{_Colors.RESET} — show every tool call.",
            "verbose": f"{_Colors.BOLD}{_Colors.GREEN}Tool progress: VERBOSE{_Colors.RESET} — full args, results, think blocks, and debug logs.",
        }
        _cprint(labels.get(self.tool_progress_mode, ""))
        if self.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
            for noisy in (
                "openai", "openai._base_client", "httpx", "httpcore", "asyncio",
                "hpack", "grpc", "modal",
            ):
                logging.getLogger(noisy).setLevel(logging.WARNING)
        else:
            logging.getLogger().setLevel(logging.INFO)
            for quiet_logger in (
                "tools", "run_agent", "trajectory_compressor", "VoidCube_cli",
            ):
                logging.getLogger(quiet_logger).setLevel(logging.ERROR)

    def _toggle_yolo(self):
        """Toggle YOLO mode — skip all dangerous command approval prompts."""
        import os
        current = bool(os.environ.get("VOIDCUBE_YOLO_MODE"))
        if current:
            os.environ.pop("VOIDCUBE_YOLO_MODE", None)
            self.console.print("  ⚠ YOLO mode [bold red]OFF[/] — dangerous commands will require approval.")
        else:
            os.environ["VOIDCUBE_YOLO_MODE"] = "1"
            self.console.print("  🔧 YOLO mode [bold green]ON[/] — all commands auto-approved. Use with caution.")

    def _on_reasoning(self, reasoning_text: str):
        """Callback for intermediate reasoning display during tool-call loops."""
        if not reasoning_text:
            return
        self._stream_render_state.reasoning_preview_buffer += reasoning_text
        self._flush_reasoning_preview(force=False)

    def _check_config_mcp_changes(self) -> None:
        """Detect mcp_servers changes in config.yaml and auto-reload MCP connections.

        Called from process_loop every CONFIG_WATCH_INTERVAL seconds.
        Compares config.yaml mtime + mcp_servers section against the last
        known state.  When a change is detected, runs the shared MCP reload
        informs the user so they know the tool list has been refreshed.
        """
        import time
        import yaml as _yaml

        CONFIG_WATCH_INTERVAL = 5.0  # seconds between config.yaml stat() calls

        now = time.monotonic()
        if now - self._last_config_check < CONFIG_WATCH_INTERVAL:
            return
        self._last_config_check = now

        from VoidCube_core.constants import get_config_path as _get_config_path
        cfg_path = _get_config_path()
        if not cfg_path.exists():
            return

        try:
            mtime = cfg_path.stat().st_mtime
        except OSError:
            return

        if mtime == self._config_mtime:
            return  # File unchanged — fast path

        # File changed — check whether mcp_servers section changed
        self._config_mtime = mtime
        try:
            with open(cfg_path, encoding="utf-8") as f:
                new_cfg = _yaml.safe_load(f) or {}
        except Exception:
            return

        new_mcp = new_cfg.get("mcp_servers") or {}
        if new_mcp == self._config_mcp_servers:
            return  # mcp_servers unchanged (some other section was edited)

        self._config_mcp_servers = new_mcp
        # Notify user and reload.  Run in a separate thread with a hard
        # timeout so a hung MCP server cannot block the process_loop
        # indefinitely (which would freeze the entire TUI).
        print()
        print("🔄 MCP server config changed — reloading connections...")
        _reload_thread = threading.Thread(
            target=lambda: reload_mcp_for_host(self), daemon=True
        )
        _reload_thread.start()
        _reload_thread.join(timeout=30)
        if _reload_thread.is_alive():
            print("  ⚠️  MCP reload timed out (30s). Some servers may not have reconnected.")

    # ====================================================================
    # Tool-call generation indicator (shown during streaming)
    # ====================================================================

    def _on_tool_gen_start(self, tool_name: str) -> None:
        """Called when the model begins generating tool-call arguments.

        Closes any open streaming boxes (reasoning / response) exactly once,
        then prints a short status line so the user sees activity instead of
        a frozen screen while a large payload (e.g. 45 KB write_file) streams.
        """
        if not self._should_emit_scrollback_output():
            return
        if self._stream_render_state.response_box_open:
            self._flush_stream()
            self._stream_render_state.response_box_open = False
        self._close_reasoning_box()

        from agent.display import get_tool_emoji
        emoji = get_tool_emoji(tool_name, default="🔧")
        _cprint(f"  ┊ {emoji} preparing {tool_name}…")

    # ====================================================================
    # Tool event adapter (TUI state, voice cues, and inline diffs)
    # ====================================================================

    def _on_tool_event(self, event: ToolEvent) -> None:
        def append_autonomous_event(
            _host: Any,
            message: str,
            *,
            tone: str = "info",
            stage: str = "",
        ) -> None:
            _append_autonomous_execution_event_view(
                event_ports=self._autonomous_panel_event_ports(),
                message=message,
                tone=tone,
                stage=stage,
            )

        _project_tool_event_view(
            self,
            event,
            append_autonomous_event=append_autonomous_event,
            emit_line=_cprint,
        )

    def _handle_application_event(self, event) -> None:
        self._chat_blocks().consume(event)
        if isinstance(event, ToolEvent):
            self._on_tool_event(event)
        elif isinstance(event, MessageDelta):
            self._stream_delta(event.text)

    # ====================================================================
    # Voice mode methods
    # ====================================================================

    def _voice_recording_ports(self) -> VoiceRecordingPorts:
        def invalidate() -> None:
            app = getattr(self, "_app", None)
            if app:
                app.invalidate()

        return VoiceRecordingPorts(
            state=self._voice_state(),
            should_exit=lambda: self._should_exit,
            invalidate=invalidate,
            emit=lambda message: _cprint(f"{_DIM}{message}{_RST}"),
            enqueue_input=self._pending_input.put,
            clear_attached_images=self._attached_images.clear,
            voice=self._voice_session(),
        )

    def _voice_start_recording(self) -> None:
        start_terminal_voice_recording(self._voice_recording_ports())

    def _voice_stop_and_transcribe(self) -> None:
        stop_terminal_voice_recording(self._voice_recording_ports())

    def _enable_voice_mode(self):
        """Enable voice mode after checking requirements."""
        state = self._voice_state()
        if state.mode:
            _cprint(f"{_DIM}Voice mode is already enabled.{_RST}")
            return

        voice = self._voice_session()
        reqs = voice.enable()
        if not reqs.get("capture_available") or not reqs.get("stt_configured"):
            voice.disable()
            _cprint(f"\n{_ACCENT}Voice mode requirements not met:{_RST}")
            if not reqs.get("capture_available"):
                _cprint(f"  {_DIM}Install sounddevice and numpy with an available input device.{_RST}")
            if not reqs.get("stt_configured"):
                _cprint(f"  {_DIM}Configure the canonical STT provider before enabling voice mode.{_RST}")
            return

        with state.lock:
            state.mode = True

        # Voice mode instruction is injected as a user message prefix (not a
        # system prompt change) to avoid invalidating the prompt cache.  See
        # _voice_message_prefix property and its usage in _process_message().

        try:
            from VoidCube_app.config import load_config
            _raw_ptt = load_config().get("voice", {}).get("record_key", "ctrl+b")
            _ptt_key = _raw_ptt.lower().replace("ctrl+", "c-").replace("alt+", "a-")
        except Exception:
            _ptt_key = "c-b"
        _ptt_display = _ptt_key.replace("c-", "Ctrl+").upper()
        _cprint(f"\n{_ACCENT}Voice mode enabled{_RST}")
        _cprint(f"  {_DIM}{_ptt_display} to start/stop recording{_RST}")
        _cprint(f"  {_DIM}/voice tts checks terminal playback; add text to speak{_RST}")
        _cprint(f"  {_DIM}/voice off  to disable voice mode{_RST}")

    def _disable_voice_mode(self):
        """Disable voice mode, cancel any active recording, and stop TTS."""
        state = self._voice_state()
        with state.lock:
            recording = state.recording
            state.recording = False
            state.mode = False
            state.continuous = False
        if recording:
            self._voice_session().interrupt()
        self._voice_session().disable()

        _cprint(f"\n{_DIM}Voice mode disabled.{_RST}")

    def _voice_session(self) -> VoiceSessionRuntime:
        runtime = self.__dict__.get("_voice_session_runtime")
        if runtime is None:
            runtime = VoiceSessionRuntime()
            self._voice_session_runtime = runtime
        return runtime

    def _show_voice_tts_status(self):
        """Project canonical voice transport readiness into terminal text."""
        result = self._voice_session().status()
        status = result.get("status") or "unavailable"
        reason = result.get("reason") or "unknown"
        _cprint(f"{_DIM}Terminal TTS: {status} ({reason}).{_RST}")

    def _speak_voice_tts(self, text: str):
        """Start terminal TTS without blocking the prompt-toolkit thread."""
        message = str(text or "").strip()
        if not message:
            self._show_voice_tts_status()
            return

        def _speak() -> None:
            result = self._voice_session().speak(message)
            status = str(result.get("status") or "unavailable")
            if status == "complete":
                _cprint(f"{_DIM}Terminal TTS complete.{_RST}")
            else:
                reason = result.get("reason") or status
                _cprint(f"{_DIM}Terminal TTS unavailable: {reason}.{_RST}")

        threading.Thread(target=_speak, daemon=True, name="voidcube-terminal-tts").start()

    def _show_voice_status(self):
        """Show current voice mode status."""
        from VoidCube_app.config import load_config

        reqs = self._voice_session().status().get("voice", {})

        _cprint(f"\n{_BOLD}Voice Mode Status{_RST}")
        state = self._voice_state()
        _cprint(f"  Mode:      {'ON' if state.mode else 'OFF'}")
        tts_status = self._voice_session().status()
        _cprint(
            f"  TTS:       {tts_status.get('status', 'unavailable')} "
            f"({tts_status.get('reason', 'unknown')})"
        )
        _cprint(f"  Recording: {'YES' if state.recording else 'no'}")
        _raw_key = load_config().get("voice", {}).get("record_key", "ctrl+b")
        _display_key = _raw_key.replace("ctrl+", "Ctrl+").upper() if "ctrl+" in _raw_key.lower() else _raw_key
        _cprint(f"  Record key: {_display_key}")
        _cprint(f"\n  {_BOLD}Requirements:{_RST}")
        _cprint(f"    Capture: {'available' if reqs.get('capture_available') else 'unavailable'}")
        _cprint(f"    STT:     {'configured' if reqs.get('stt_configured') else 'unconfigured'}")

    def _clarification_sink(
        self,
        request: ClarificationRequest,
    ) -> ClarificationDecision:
        timeout = CLI_CONFIG.get("clarify", {}).get("timeout", 120)
        return _clarification_sink_view(
            self,
            request,
            timeout=timeout,
            notify_timeout=lambda seconds: _cprint(
                f"\n{_DIM}(clarify timed out after {seconds:g}s — agent will decide){_RST}"
            ),
        )

    def _sudo_password_callback(self) -> str:
        """
        Prompt for sudo password through the prompt_toolkit UI.
        
        Called from the agent thread when a sudo command is encountered.
        Uses the same clarify-style mechanism: sets UI state, waits on a
        queue for the user's response via the Enter key binding.
        """
        import time as _time

        timeout = 45
        response_queue: queue.Queue = queue.Queue()

        self._capture_modal_input_snapshot()
        self._sudo_state = {
            "response_queue": response_queue,
        }
        self._sudo_deadline = _time.monotonic() + timeout

        self._invalidate()

        while True:
            try:
                result = response_queue.get(timeout=1)
                self._sudo_state = None
                self._sudo_deadline = 0
                self._restore_modal_input_snapshot()
                self._invalidate()
                if result:
                    _cprint(f"\n{_DIM}  ✓ Password received (cached for session){_RST}")
                else:
                    _cprint(f"\n{_DIM}  ⏭ Skipped{_RST}")
                return result
            except queue.Empty:
                remaining = self._sudo_deadline - _time.monotonic()
                if remaining <= 0:
                    break
                self._invalidate()

        self._sudo_state = None
        self._sudo_deadline = 0
        self._restore_modal_input_snapshot()
        self._invalidate()
        _cprint(f"\n{_DIM}  ⏱ Timeout — continuing without sudo{_RST}")
        return ""

    def _approval_sink(self, request: ApprovalRequest) -> ApprovalDecision:
        return self._ensure_application_runtime().resolve_approval(
            request,
            lambda approval_request: _approval_sink_view(
                self,
                approval_request,
                timeout=60,
                notify_timeout=lambda: _cprint(
                    f"\n{_DIM}  ⏱ Timeout — denying command{_RST}"
                ),
            ),
        )

    def _approval_choices(self, command: str) -> list[str]:
        return _approval_choices_view(command)

    def _handle_approval_selection(self) -> None:
        _handle_approval_selection_view(self)

    def _get_approval_display_fragments(self):
        return _approval_display_fragments_view(self)

    def _secret_capture_callback(self, var_name: str, prompt: str, metadata=None) -> dict:
        return _get_prompt_for_secret()(self, var_name, prompt, metadata)

    def _capture_modal_input_snapshot(self) -> None:
        """Temporarily clear the input buffer and save the user's in-progress draft."""
        if self._modal_input_snapshot is not None or not getattr(self, "_app", None):
            return
        try:
            buf = self._app.current_buffer
            self._modal_input_snapshot = {
                "text": buf.text,
                "cursor_position": buf.cursor_position,
            }
            buf.reset()
        except Exception:
            self._modal_input_snapshot = None

    def _restore_modal_input_snapshot(self) -> None:
        """Restore any draft text that was present before a modal prompt opened."""
        snapshot = self._modal_input_snapshot
        self._modal_input_snapshot = None
        if not snapshot or not getattr(self, "_app", None):
            return
        try:
            buf = self._app.current_buffer
            buf.text = snapshot.get("text", "")
            buf.cursor_position = min(snapshot.get("cursor_position", 0), len(buf.text))
        except Exception:
            pass

    def _submit_secret_response(self, value: str) -> None:
        if not self._secret_state:
            return
        self._secret_state["response_queue"].put(value)
        self._secret_state = None
        self._secret_deadline = 0
        self._invalidate()

    def _cancel_secret_capture(self) -> None:
        self._submit_secret_response("")

    def _clear_secret_input_buffer(self) -> None:
        if getattr(self, "_app", None):
            try:
                self._app.current_buffer.reset()
            except Exception:
                pass

    def _initialize_autonomous_turn_agent(
        self,
        owner: AutonomousExecutionHost,
        route: Mapping[str, Any],
        toolsets: Sequence[str] | None,
    ) -> bool:
        if owner.agent is not None:
            return True
        try:
            runtime = dict(route.get("runtime") or {})
            effective_model = str(route.get("model") or self.model or "")
            owner.agent = CliAgentInitializationRuntime(
                CliAgentInitializationPorts(
                    agent_factory=_get_AIAgent(),
                    runtime=runtime,
                    model=effective_model,
                    max_iterations=self.max_turns,
                    enabled_toolsets=(
                        list(toolsets)
                        if toolsets is not None
                        else self.enabled_toolsets
                    ),
                    verbose_logging=False,
                    quiet_mode=True,
                    ephemeral_system_prompt=self.system_prompt or None,
                    prefill_messages=self.prefill_messages or None,
                    reasoning_config=self.reasoning_config,
                    service_tier=self.service_tier,
                    request_overrides=route.get("request_overrides"),
                    providers_allowed=self._providers_only,
                    providers_ignored=self._providers_ignore,
                    providers_order=self._providers_order,
                    provider_sort=self._provider_sort,
                    provider_require_parameters=self._provider_require_params,
                    provider_data_collection=self._provider_data_collection,
                    session_id=owner.session_id,
                    platform="cli",
                    session_db=owner._session_db,
                    clarification_sink=None,
                    reasoning_callback=None,
                    fallback_model=self._fallback_model,
                    thinking_callback=lambda text: setattr(
                        owner,
                        "_spinner_text",
                        str(text or ""),
                    ),
                    checkpoints_enabled=self.checkpoints_enabled,
                    checkpoint_max_snapshots=self.checkpoint_max_snapshots,
                    pass_session_id=True,
                    tool_event_sink=owner._application_runtime.tool_event_sink,
                    stream_delta_callback=None,
                    tool_gen_callback=None,
                    autonomous_task_provider=lambda: owner._current_autonomous_task,
                    validate_execution_lease=validate_execution_lease,
                )
            ).create()
            owner.agent._print_fn = self._quiet_autonomous_cprint
            owner._active_agent_route_signature = route.get("signature")
            if _is_gateway_running():
                _register_with_gateway(
                    owner.session_id,
                    effective_model,
                    str(runtime.get("provider") or ""),
                )
            return True
        except Exception:
            logger.debug("Failed to initialize autonomous Agent", exc_info=True)
            owner.agent = None
            return False

    def _agent_turn_executor_runtime(
        self,
        owner: Any | None = None,
    ) -> CliAgentTurnExecutorRuntime:
        owner = owner or self
        runtime = owner.__dict__.get("_agent_turn_executor_runtime_instance")
        if runtime is not None:
            return runtime
        autonomous_runtime = _autonomous_executor_runtime_view(
            owner,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=(
                _cprint
                if owner is self
                else self._quiet_autonomous_cprint
            ),
        )
        application_runtime = owner._ensure_application_runtime() if owner is self else owner._application_runtime
        should_emit = self._should_emit_scrollback_output if owner is self else owner._should_emit_scrollback_output

        def initialize_agent(
            route: Mapping[str, Any],
            toolsets: Sequence[str] | None,
        ) -> bool:
            if owner.agent is None and should_emit():
                _cprint(f"{_DIM}Initializing agent...{_RST}")
            if owner is not self:
                return self._initialize_autonomous_turn_agent(owner, route, toolsets)
            return self._init_agent(
                model_override=route.get("model"),
                runtime_override=route.get("runtime"),
                route_label=route.get("label"),
                request_overrides=route.get("request_overrides"),
                enabled_toolsets_override=(list(toolsets) if toolsets is not None else None),
            )

        def prepare_input_ports(
            message: Any,
            images: Sequence[Any] | None,
        ) -> CliTurnInputPreparationPorts:
            return CliTurnInputPreparationPorts(
                message=message,
                images=images,
                conversation_history=owner.conversation_history,
                preprocess_images=self._preprocess_images_with_vision,
                model=getattr(self, "model", "") or "",
                base_url=getattr(self, "base_url", "") or "",
                api_key=getattr(self, "api_key", "") or "",
                cwd=os.getcwd,
                should_emit=should_emit,
                emit=_cprint,
                begin_turn=application_runtime.begin_turn,
            )

        def record_user_message(message: Any) -> None:
            if owner is not self:
                return
            self._chat_blocks().record_user_message(
                message,
                turn_id=application_runtime.state.active_turn_id or "",
            )

        def notify_turn_started() -> None:
            if should_emit():
                ChatConsole().print(f"[#34D399]{'~' * 40}[/]")
                print(flush=True)

        def voice_prefix(message: Any) -> str:
            if (
                owner is not self
                or not self._voice_state().mode
                or not isinstance(message, str)
            ):
                return ""
            return (
                "[Voice input - respond concisely and conversationally, "
                "2-3 sentences max. No code blocks or markdown.] "
            )

        def agent_call_ports(
            message: Any,
            prefix: str,
            prior_history: Sequence[Mapping[str, Any]],
        ) -> CliAgentTurnCallPorts:
            return CliAgentTurnCallPorts(
                message=message,
                voice_prefix=prefix,
                pending_model_switch_note=lambda: getattr(
                    self,
                    "_pending_model_switch_note",
                    None,
                ),
                clear_pending_model_switch_note=lambda: setattr(
                    owner,
                    "_pending_model_switch_note",
                    None,
                ),
                prior_history=prior_history,
                session_id=owner.session_id,
                stream_callback=None,
                persist_user_message=message if prefix else None,
                new_trace_id=lambda: str(uuid.uuid4()),
                set_trace_id=lambda value: setattr(owner, "_current_trace_id", value),
                run_conversation=lambda **kwargs: owner.agent.run_conversation(**kwargs),
                summarize_error=summarize_api_error,
                log_error=lambda error: logging.error(
                    "run_conversation raised: %s",
                    error,
                    exc_info=True,
                ),
            )

        def execution_ports(run_id: str) -> TurnExecutionPorts:
            def check_autonomous_timeout() -> tuple[bool, bool]:
                timed_out_task = autonomous_runtime.current_task()
                timed_out_run_id = (
                    str(
                        (timed_out_task or {}).get("_autonomous_task_run_id") or ""
                    ).strip()
                    if isinstance(timed_out_task, dict)
                    else ""
                )
                if timed_out_run_id != run_id:
                    return False, False
                reported = autonomous_runtime.report_current_task_timeout_if_needed(
                    timeout=15,
                )
                return reported, reported and autonomous_runtime.current_task() is None

            def cleanup_async_clients() -> None:
                try:
                    from agent.auxiliary_client import cleanup_stale_async_clients

                    cleanup_stale_async_clients()
                except Exception:
                    pass

            return TurnExecutionPorts(
                interrupt_agent=lambda: owner.agent.interrupt(None),
                check_autonomous_timeout=check_autonomous_timeout,
                cleanup_async_clients=cleanup_async_clients,
                flush_stream=(self._flush_stream if owner is self else lambda: None),
                flush_output=sys.stdout.flush,
            )

        def result_ports() -> TurnResultApplicationPorts:
            return TurnResultApplicationPorts(
                conversation_history=lambda: owner.conversation_history,
                set_conversation_history=lambda history: setattr(
                    owner,
                    "conversation_history",
                    history,
                ),
                publish_usage=application_runtime.usage_sink,
                record_autonomous_result=autonomous_runtime.record_turn_result,
                record_autonomous_finished=autonomous_runtime.record_model_turn_finished,
            )

        def postprocessing_ports() -> TurnPostprocessingPorts:
            return TurnPostprocessingPorts(
                session_db=lambda: owner._session_db,
                session_id=lambda: str(owner.session_id or ""),
                voice_continuous=lambda: bool(
                    owner is self and self._voice_state().continuous
                ),
                stop_voice_continuous=lambda: setattr(
                    self._voice_state(),
                    "continuous",
                    False,
                ),
                emit=_cprint,
            )

        def synchronize_session_identity() -> None:
            agent = owner.agent
            if agent is None:
                return
            agent_session_id = str(agent.session_id or "").strip()
            if not agent_session_id or agent_session_id == application_runtime.state.session_id:
                return
            application_runtime.continue_session(
                agent_session_id,
                session_start=agent.session_start,
            )
            if owner is self:
                self._chat_blocks().bind_session(agent_session_id, clear=False)

        def handle_error(
            error: Exception,
            timeout_reported: bool,
            run_id: str,
            timeout_writeback_succeeded: bool,
        ) -> None:
            CliChatErrorRuntime(
                CliChatErrorPorts(
                    autonomous_timeout_reported=timeout_reported,
                    autonomous_task_run_id=run_id,
                    autonomous_timeout_writeback_succeeded=(
                        timeout_writeback_succeeded
                    ),
                    current_autonomous_task=autonomous_runtime.current_task,
                    set_last_agent_turn_result=(
                        autonomous_runtime.set_last_agent_turn_result
                    ),
                    should_emit=should_emit,
                    translate=t,
                    emit=print,
                )
            ).handle(error)

        runtime = CliAgentTurnExecutorRuntime(
            CliAgentTurnExecutorPorts(
                ensure_credentials=self._ensure_runtime_credentials,
                current_autonomous_task=autonomous_runtime.current_task,
                set_last_agent_turn_result=autonomous_runtime.set_last_agent_turn_result,
                agent_exists=lambda: owner.agent is not None,
                clear_agent=lambda: setattr(owner, "agent", None),
                active_route_signature=lambda: owner._active_agent_route_signature,
                resolve_route=self._resolve_turn_agent_config,
                initialize_agent=initialize_agent,
                prepare_input_ports=prepare_input_ports,
                record_user_message=record_user_message,
                notify_turn_started=notify_turn_started,
                set_agent_running=lambda value: setattr(
                    owner,
                    "_agent_running",
                    bool(value),
                ),
                active_role=lambda: str(
                    getattr(owner, "_active_chat_agent_role", "") or ""
                ),
                set_active_role=lambda value: setattr(
                    owner,
                    "_active_chat_agent_role",
                    value,
                ),
                begin_stream=(
                    self._stream_render_state.begin_turn
                    if owner is self
                    else lambda: None
                ),
                voice_prefix=voice_prefix,
                agent_call_ports=agent_call_ports,
                execution_ports=execution_ports,
                result_ports=result_ports,
                postprocessing_ports=postprocessing_ports,
                synchronize_session_identity=synchronize_session_identity,
                finish_turn=lambda applied: application_runtime.finish_turn(
                    applied.outcome,
                    history_applied=True,
                ),
                finish_failed_turn=lambda error, interrupted: application_runtime.abort_turn(
                    str(error),
                    interrupted=interrupted,
                ),
                handle_error=handle_error,
            )
        )
        owner._agent_turn_executor_runtime_instance = runtime
        return runtime

    def _present_agent_turn_result(self, result: CliAgentTurnResult) -> None:
        def bell() -> None:
            sys.stdout.write("\a")
            sys.stdout.flush()

        CliChatFinalizationRuntime(
            CliChatFinalizationPorts(
                should_emit_scrollback=self._should_emit_scrollback_output,
                show_reasoning=lambda: bool(self.show_reasoning),
                reasoning_already_shown=lambda: bool(
                    self._stream_render_state.reasoning_shown_this_turn
                ),
                terminal_width=lambda: shutil.get_terminal_size().columns,
                emit=_cprint,
                create_console=ChatConsole,
                rich_text_from_ansi=_rich_text_from_ansi,
                bell_on_complete=lambda: bool(self.bell_on_complete),
                bell=bell,
            )
        ).finalize(
            response=result.response,
            response_previewed=result.outcome.response_previewed,
            failed=result.outcome.failed,
            partial=result.outcome.partial,
            stream_started=self._stream_render_state.started,
            response_box_open=self._stream_render_state.response_box_open,
            reasoning=result.outcome.last_reasoning,
        )

    def _execute_agent_turn_request(
        self,
        request: TurnRequest,
        cancellation: CancellationToken,
    ) -> Optional[str]:
        result = self._agent_turn_executor_runtime().execute(request, cancellation)
        if isinstance(result, CliAgentTurnResult):
            self._present_agent_turn_result(result)
            return result.response
        return result

    def chat(self, message, images: list = None) -> Optional[str]:
        _get_set_secret_capture_callback()(self._secret_capture_callback)
        return self._execute_agent_turn_request(
            self._direct_turn_request(message, images),
            CancellationToken(),
        )

    def _print_exit_summary(self):
        """Render session resume information through the display runtime."""
        CliExitSummaryRuntime(
            CliExitSummaryPorts(
                conversation_history=lambda: self.conversation_history,
                session_id=lambda: self.session_id,
                session_start=lambda: self.session_start,
                now=datetime.now,
                 session_title=lambda: self._ensure_application_runtime()
                 .get_session_title(repository=self._session_db)
                 .title,
                translate=t,
                emit=print,
                emit_blank_line=lambda: print(),
            )
        ).render()

    def _tui_prompt_runtime(self) -> CliTuiPromptRuntime:
        layout_metrics = self._tui_layout_metrics_runtime()
        return CliTuiPromptRuntime(
            CliTuiPromptPorts(
                voice_recording=lambda: bool(self._voice_state().recording),
                voice_processing=lambda: bool(self._voice_state().processing),
                sudo_active=lambda: bool(self._sudo_state),
                secret_active=lambda: bool(self._secret_state),
                approval_active=lambda: bool(self._approval_state),
                clarify_freetext=lambda: bool(self._clarify_freetext),
                clarify_active=lambda: bool(self._clarify_state),
                command_running=lambda: bool(self._command_running),
                command_spinner_frame=self._command_spinner_frame,
                agent_running=lambda: bool(self._agent_running),
                voice_mode=lambda: bool(self._voice_state().mode),
                minimal_tui_chrome=layout_metrics.minimal_chrome,
                terminal_width=layout_metrics.terminal_width,
                audio_status=lambda: self._voice_session().realtime_status(),
            )
        )

    # --- Protected TUI extension hooks for wrapper CLIs ---

    def _get_extra_tui_widgets(self) -> list:
        """Return extra prompt_toolkit widgets to insert into the TUI layout.

        Wrapper CLIs can override this to inject widgets (e.g. a mini-player,
        overlay menu) into the layout without overriding ``run()``.  Widgets
        are inserted between the spacer and the status bar.

        The main CLI already projects the API-A autonomous execution lane
        through ``auto_execution_panel`` when that lane has visible work.
        Wrappers can still inject their own widgets here.
        """
        return []

    def _register_extra_tui_keybindings(self, kb, *, input_area) -> None:
        """Register extra keybindings on the TUI ``KeyBindings`` object.

        Wrapper CLIs can override this to add keybindings (e.g. transport
        controls, modal shortcuts) without overriding ``run()``.

        Parameters
        ----------
        kb : KeyBindings
            The active keybinding registry for the prompt_toolkit application.
        input_area : TextArea
            The main input widget, for wrappers that need to inspect or
            manipulate user input from a keybinding handler.
        """

    def _enter_keybinding_runtime(self) -> EnterKeybindingRuntime:
        def invalidate(event: Any) -> None:
            event.app.invalidate()

        def reset_buffer(event: Any, append_to_history: bool) -> None:
            event.app.current_buffer.reset(append_to_history=append_to_history)

        def exit_application(event: Any) -> None:
            if event.app.is_running:
                event.app.exit()

        def clear_modal_states() -> None:
            self._model_picker_state = None
            self._clarify_state = None
            self._clarify_freetext = False
            self._approval_state = None
            self._sudo_state = None
            self._secret_state = None

        def run_api_command(event: Any) -> None:
            from prompt_toolkit.application import run_in_terminal

            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            event.app.invalidate()
            try:
                run_in_terminal(lambda: self.process_command("/api"))
            finally:
                self._status_bar_visible = was_visible
                event.app.invalidate()

        def stop_daemons(keep_daemons: bool) -> None:
            global _daemons_auto_started

            if not _daemons_auto_started or keep_daemons:
                return
            try:
                from VoidCube_cli.ops.serve import stop_all

                stop_all(force=True)
                _daemons_auto_started = False
            except Exception:
                pass

        def exit_autonomous_gate_fast() -> None:
            exit_autonomous_gate_fast_for_host(
                self,
                event_ports=self._autonomous_panel_event_ports(),
                emit=_cprint,
                interrupt_current_task=self._interrupt_autonomous_task,
                push_cli_agent_scene=_push_cli_agent_scene,
            )

        return EnterKeybindingRuntime(
            EnterKeybindingPorts(
                read_text=lambda event: event.app.current_buffer.text,
                has_images=lambda: bool(self._attached_images),
                snapshot_images=lambda: list(self._attached_images),
                clear_images=self._attached_images.clear,
                reset_buffer=reset_buffer,
                invalidate=invalidate,
                sudo_state=lambda: self._sudo_state,
                set_sudo_state=lambda state: setattr(self, "_sudo_state", state),
                submit_sudo=lambda text: self._sudo_state["response_queue"].put(text),
                secret_state=lambda: self._secret_state,
                submit_secret=self._submit_secret_response,
                clear_secret_input=lambda event: event.app.current_buffer.reset(),
                approval_state=lambda: self._approval_state,
                submit_approval=self._handle_approval_selection,
                model_picker_state=lambda: self._model_picker_state,
                submit_model_picker=self._handle_model_picker_selection,
                clarify_state=lambda: self._clarify_state,
                set_clarify_state=lambda state: setattr(self, "_clarify_state", state),
                clarify_freetext=lambda: bool(self._clarify_freetext),
                set_clarify_freetext=lambda value: setattr(
                    self, "_clarify_freetext", bool(value)
                ),
                submit_clarification=lambda value: self._clarify_state["response_queue"].put(
                    value
                ),
                restore_modal_input=self._restore_modal_input_snapshot,
                clear_modal_states=clear_modal_states,
                status_bar_visible=lambda: bool(self._status_bar_visible),
                set_status_bar_visible=lambda value: setattr(
                    self, "_status_bar_visible", bool(value)
                ),
                process_command=self.process_command,
                should_handle_model_inline=self._should_handle_model_command_inline,
                set_should_exit=lambda value: setattr(self, "_should_exit", bool(value)),
                exit_application=exit_application,
                stop_daemons=stop_daemons,
                run_api_command=run_api_command,
                autonomous_gate_active=lambda: bool(self._autonomous_gate_active),
                exit_autonomous_gate_fast=exit_autonomous_gate_fast,
                enqueue_input=lambda payload: self._ensure_application_runtime().enqueue_turn_input(
                    payload
                ),
                agent_running=lambda: bool(self._agent_running),
                emit=_cprint,
            )
        )

    def _voice_keybinding_runtime(self) -> VoiceKeybindingRuntime:
        def run_background(operation: Callable[[], None]) -> None:
            threading.Thread(target=operation, daemon=True).start()

        def set_continuous(value: bool) -> None:
            state = self._voice_state()
            with state.lock:
                state.continuous = bool(value)

        def invalidate_app() -> None:
            app = getattr(self, "_app", None)
            if app:
                app.invalidate()

        return VoiceKeybindingRuntime(
            VoiceKeybindingPorts(
                voice_mode=lambda: bool(self._voice_state().mode),
                recording=lambda: bool(self._voice_state().recording),
                set_continuous=set_continuous,
                agent_running=lambda: bool(self._agent_running),
                modal_active=lambda: bool(
                    self._clarify_state or self._sudo_state or self._approval_state
                ),
                processing=lambda: bool(self._voice_state().processing),
                start_recording=self._voice_start_recording,
                stop_recording=self._voice_stop_and_transcribe,
                run_background=run_background,
                invalidate=lambda event: event.app.invalidate(),
                invalidate_app=invalidate_app,
                report_error=lambda error: _cprint(
                    f"\n{_DIM}Voice recording failed: {error}{_RST}"
                ),
            )
        )

    def _suspend_keybinding_runtime(self) -> SuspendKeybindingRuntime:
        import os
        import signal
        import sys
        from prompt_toolkit.application import run_in_terminal

        agent_name = "Voidcube Agent"
        message = f"\n{agent_name} has been suspended. Run `fg` to bring {agent_name} back."

        def suspend_process() -> None:
            os.write(1, message.encode())
            os.kill(0, signal.SIGTSTP)

        return SuspendKeybindingRuntime(
            SuspendKeybindingPorts(
                platform=lambda: sys.platform,
                emit=lambda value: _cprint(f"{_DIM}{value}{_RST}"),
                invalidate=lambda event: event.app.invalidate(),
                run_in_terminal=run_in_terminal,
                suspend_process=suspend_process,
            )
        )

    def _tui_dynamic_text_runtime(self) -> TuiDynamicTextRuntime:
        layout_metrics = self._tui_layout_metrics_runtime()
        return TuiDynamicTextRuntime(
            TuiDynamicTextPorts(
                voice_recording=lambda: bool(self._voice_state().recording),
                voice_processing=lambda: bool(self._voice_state().processing),
                sudo_active=lambda: bool(self._sudo_state),
                secret_active=lambda: bool(self._secret_state),
                approval_active=lambda: bool(self._approval_state),
                clarify_freetext=lambda: bool(self._clarify_freetext),
                clarify_active=lambda: bool(self._clarify_state),
                command_running=lambda: bool(self._command_running),
                command_spinner_frame=self._command_spinner_frame,
                command_status=lambda: self._command_status or "",
                agent_running=lambda: bool(self._agent_running),
                voice_mode=lambda: bool(self._voice_state().mode),
                spinner_text=lambda: self._spinner_text,
                tool_start_time=lambda: self._tool_start_time,
                now=time.monotonic,
                agent_spacer_height=layout_metrics.agent_spacer_height,
                spinner_height=layout_metrics.spinner_height,
                sudo_deadline=lambda: self._sudo_deadline,
                secret_deadline=lambda: self._secret_deadline,
                approval_deadline=lambda: self._approval_deadline,
                clarify_deadline=lambda: self._clarify_deadline,
                translate=t,
            )
        )

    def _cli_startup_runtime(self) -> CliStartupRuntime:
        def recent_sessions() -> list[dict[str, Any]]:
            try:
                from VoidCube_core.state import SessionDB

                return SessionDB().list_sessions_rich(
                    source="cli",
                    exclude_sources=["tool"],
                    limit=5,
                    exclude_id_prefixes=["scheduled_"],
                )
            except Exception:
                return []

        def render_history_panel(lines: list[str]) -> None:
            from rich.panel import Panel

            self.console.print(
                Panel(
                    "\n".join(lines),
                    border_style="dim",
                    padding=(0, 1),
                    height=12,
                )
            )

        def tools_count() -> int:
            try:
                tools = self._get_cached_tool_definitions()
                return len(tools) if tools else 0
            except Exception:
                return 0

        def skills_count() -> int:
            try:
                from tools.skills_tool import _find_all_skills

                skills = _find_all_skills()
                return len(skills) if skills else 0
            except Exception:
                return 0

        return CliStartupRuntime(
            CliStartupPorts(
                terminal_lines=lambda: shutil.get_terminal_size().lines,
                write_blank_lines=lambda count: print(
                    "\n" * count,
                    end="",
                    flush=True,
                ),
                show_banner=self.show_banner,
                resumed=lambda: self._ensure_application_runtime().state.resumed,
                preload_resumed_session=self._preload_resumed_session,
                display_resumed_history=self._display_resumed_history,
                recent_sessions=recent_sessions,
                terminal_width=lambda: shutil.get_terminal_size((80, 24)).columns,
                render_history_panel=render_history_panel,
                tools_count=tools_count,
                skills_count=skills_count,
                session_id=lambda: self.session_id,
                preloaded_skills=lambda: self.preloaded_skills,
                startup_skills_line_shown=lambda: bool(
                    self._startup_skills_line_shown
                ),
                set_startup_skills_line_shown=lambda value: setattr(
                    self, "_startup_skills_line_shown", bool(value)
                ),
                accent_hex=_accent_hex,
                emit=self.console.print,
            )
        )

    def _cli_lifecycle_guards(self) -> CliLifecycleGuardRuntime:
        import asyncio
        import os
        import signal

        def report_stdin_unavailable() -> None:
            print(
                "Error: stdin (fd 0) is not available.\n"
                "This can happen with certain Python installations "
                "(e.g. uv-managed cPython on macOS).\n"
                "Try reinstalling Python via pyenv or Homebrew, then re-run: /api"
            )

        return CliLifecycleGuardRuntime(
            CliLifecycleGuardPorts(
                install_signal=signal.signal,
                sigint=signal.SIGINT,
                sigint_ignore=signal.SIG_IGN,
                sigterm=signal.SIGTERM,
                sighup=getattr(signal, "SIGHUP", None),
                get_running_loop=asyncio.get_running_loop,
                new_event_loop=asyncio.new_event_loop,
                set_event_loop=asyncio.set_event_loop,
                fstat_stdin=lambda: os.fstat(0),
                report_stdin_unavailable=report_stdin_unavailable,
                cleanup_after_stdin_failure=_run_cleanup,
                print_exit_summary=self._print_exit_summary,
                log_signal=lambda signum: logger.debug(
                    "Received signal %s, triggering graceful shutdown",
                    signum,
                ),
            )
        )

    def _tui_teardown_ports(self) -> TuiTeardownPorts:
        def interrupt_running_agent() -> None:
            # The agent thread is daemon-backed, but interruption prevents
            # needless API work and lets its conversation cleanup finish.
            if self.agent and self._agent_running:
                try:
                    self.agent.interrupt()
                except Exception:
                    pass

        def close_voice_session() -> None:
            runtime = self.__dict__.get("_voice_session_runtime")
            if runtime is not None:
                runtime.close()

        def unregister_tool_callbacks() -> None:
            _get_set_sudo_password_callback(None)
            _get_set_approval_sink(None)
            _get_set_secret_capture_callback()(None)

        def invoke_session_end(**kwargs: Any) -> None:
            from VoidCube_cli.plugins import invoke_hook as _invoke_hook

            _invoke_hook("on_session_end", **kwargs)

        session_teardown = CliSessionTeardownRuntime(
            CliSessionTeardownPorts(
                repository=lambda: self._session_db,
                session_id=lambda: self.agent.session_id if self.agent else "",
                agent_available=lambda: self.agent is not None,
                agent_running=lambda: bool(self._agent_running),
                model=lambda: getattr(self.agent, "model", None),
                platform=lambda: getattr(self.agent, "platform", None),
                end_session=lambda repository, session_id, reason: repository.end_session(
                    session_id, reason
                ),
                invoke_session_end=invoke_session_end,
                log_debug=lambda message, error: logger.debug(message, error),
            )
        )

        return TuiTeardownPorts(
            shutdown_scheduler=lambda: self._scheduler_runtime().shutdown(
                reason="cli_exit",
                wait_timeout=5.0,
            ),
            stop_autonomous=lambda: self._stop_autonomous_execution(
                interrupt=True
            ),
            interrupt_agent=interrupt_running_agent,
            interrupt_voice=lambda: self._voice_session().interrupt(),
            close_voice_session=close_voice_session,
            unregister_tool_callbacks=unregister_tool_callbacks,
            close_session=session_teardown.close_session,
            finish_interrupted_session=session_teardown.finish_interrupted_session,
            run_global_cleanup=_run_cleanup,
            print_exit_summary=self._print_exit_summary,
        )

    def run(self):
        """Run the interactive CLI loop with persistent input at bottom."""
        self._cli_startup_runtime().run()
        
        from VoidCube_core.constants import get_config_path as _get_config_path
        _cfg_path = _get_config_path()
        run_state = CliInteractiveStateRuntime(
            CliInteractiveStatePorts(
                config_path=_cfg_path,
                config_mcp_servers=self.config.get("mcp_servers") or {},
            )
        ).initialize()
        self._ensure_application_runtime().reset_input_queues()
        self._agent_running = run_state.agent_running
        self._should_exit = run_state.should_exit
        self._config_mtime = run_state.config_mtime
        self._config_mcp_servers = run_state.config_mcp_servers
        self._last_config_check = run_state.last_config_check
        self._clarify_state = run_state.clarify_state
        self._clarify_freetext = run_state.clarify_freetext
        self._clarify_deadline = run_state.clarify_deadline
        self._sudo_state = run_state.sudo_state
        self._sudo_deadline = run_state.sudo_deadline
        self._modal_input_snapshot = run_state.modal_input_snapshot
        self._approval_state = run_state.approval_state
        self._approval_deadline = run_state.approval_deadline
        self._approval_lock = run_state.approval_lock
        self._secret_state = run_state.secret_state
        self._secret_deadline = run_state.secret_deadline
        self._attached_images = run_state.attached_images
        self._image_counter = run_state.image_counter
        self._voice_runtime_state = run_state.voice_runtime_state

        from VoidCube_cli.plugins import get_plugin_manager
        from VoidCube_app.config import load_config

        registrations = CliInteractiveRegistrationRuntime(
            CliInteractiveRegistrationPorts(
                register_plugin_cli=lambda: setattr(
                    get_plugin_manager(), "_cli_ref", self
                ),
                reset_command_lifecycle=self._command_busy_lifecycle.reset,
                register_sudo_password_callback=_get_set_sudo_password_callback,
                register_approval_sink=_get_set_approval_sink,
                register_secret_capture_callback=_get_set_secret_capture_callback(),
                sudo_password_callback=self._sudo_password_callback,
                approval_sink=self._approval_sink,
                secret_capture_callback=self._secret_capture_callback,
                create_enter_runtime=self._enter_keybinding_runtime,
                create_voice_runtime=self._voice_keybinding_runtime,
                create_suspend_runtime=self._suspend_keybinding_runtime,
                create_dynamic_text_runtime=self._tui_dynamic_text_runtime,
                load_voice_record_key=lambda: load_config()
                .get("voice", {})
                .get("record_key", "ctrl+b"),
            )
        ).prepare()
        dynamic_text_runtime = registrations.dynamic_text
        prompt_runtime = self._tui_prompt_runtime()
        layout_metrics = self._tui_layout_metrics_runtime()
        modal_state_runtime = CliTuiModalStateRuntime(
            CliTuiModalStatePorts(
                clarify_state=lambda: self._clarify_state,
                clarify_freetext_active=lambda: bool(self._clarify_freetext),
                sudo_state=lambda: self._sudo_state,
                secret_state=lambda: self._secret_state,
                approval_state=lambda: self._approval_state,
                model_picker_state=lambda: self._model_picker_state,
            )
        )

        indicator_ports = CliTuiIndicatorAssemblyRuntime(
            CliTuiIndicatorAssemblyPorts(
                dynamic_text=dynamic_text_runtime,
                layout_input_rule_height=layout_metrics.input_rule_height,
                image=CliTuiImageIndicatorPorts(
                    attached_images=lambda: list(self._attached_images),
                    image_counter=lambda: self._image_counter,
                    format_badges=_format_image_attachment_badges,
                ),
                voice_fragments=self._get_voice_status_fragments,
                voice_visible=lambda: self._voice_state().mode,
                autonomous_fragments=lambda: _get_autonomous_execution_panel_fragments_view(
                    self,
                    state_ports=self._autonomous_panel_state_ports(),
                    render_ports=self._autonomous_panel_render_ports(),
                ),
                autonomous_visible=lambda: (
                    layout_metrics.extended_panels_visible()
                    and _has_visible_autonomous_work_view(
                        self,
                        state_ports=self._autonomous_panel_state_ports(),
                    )
                ),
                status_fragments=self._get_status_bar_fragments,
                status_visible=lambda: (
                    self._status_bar_visible
                    and layout_metrics.status_bar_visible()
                ),
            )
        ).build()

        app = CliTuiHostAssemblyRuntime(
            CliTuiHostAssemblyPorts(
                registrations=registrations,
                paste=CliTuiPastePorts(
                    should_attach_clipboard_image=_should_auto_attach_clipboard_image_on_paste,
                    attach_clipboard_image=self._try_attach_clipboard_image,
                    paste_directory=_VoidCube_home / "pastes",
                    timestamp=lambda: datetime.now().strftime("%H%M%S"),
                    invalidate=lambda event: event.app.invalidate(),
                ),
                modal_navigation=modal_state_runtime.modal_navigation_ports(
                    invalidate=lambda: self._invalidate(min_interval=0.0),
                ),
                normal_input_active=modal_state_runtime.normal_input_active,
                input=CliTuiInputPorts(
                    history_path=str(self._history_file),
                    prompt_fragments=prompt_runtime.fragments,
                    prompt_text=prompt_runtime.text,
                    command_available=self._command_available,
                    command_running=lambda: bool(self._command_running),
                    password_mask_active=modal_state_runtime.password_mask_active,
                    input_locked=modal_state_runtime.input_locked,
                ),
                placeholder_text=dynamic_text_runtime.placeholder,
                modal=modal_state_runtime.modal_widget_ports(
                    approval_fragments=self._get_approval_display_fragments,
                ),
                indicators=indicator_ports,
                extensions=CliTuiExtensionPorts(
                    register_extra_keybindings=self._register_extra_tui_keybindings,
                    composition=CliTuiCompositionPorts(
                        cursor=_STEADY_CURSOR,
                        store_application=lambda application: setattr(
                            self, "_app", application
                        ),
                        install_resize_cleanup=install_resize_reflow_cleanup,
                    ),
                    extra_widgets=self._get_extra_tui_widgets,
                ),
            )
        ).build()

        lifecycle_guards = self._cli_lifecycle_guards()

        def report_unusable_stdin(error: BaseException) -> None:
            print(
                f"\nError: stdin is not usable ({error}).\n"
                "This can happen with certain Python installations (e.g. uv-managed cPython on macOS).\n"
                "Try reinstalling Python via pyenv or Homebrew, then re-run: /api"
            )

        def refresh_gateway_presence(force: bool) -> None:
            _refresh_gateway_cli_presence_view(
                self,
                force=force,
                is_gateway_running=_is_gateway_running,
                register_with_gateway=_register_with_gateway,
                push_cli_agent_scene=_push_cli_agent_scene,
                monotonic_time=time.monotonic,
            )

        def refresh_observation_surfaces(refresh_gateway: Callable[[], None]) -> None:
            _refresh_autonomous_observation_surfaces_view(
                self,
                refresh_gateway_cli_presence=refresh_gateway,
            )

        CliInteractiveLifecycleAssemblyRuntime(
            CliInteractiveLifecycleAssemblyPorts(
                application=app,
                lifecycle_guards=lifecycle_guards,
                agent_running=lambda: self._agent_running,
                check_config_changes=self._check_config_mcp_changes,
                refresh_observation_surfaces=refresh_observation_surfaces,
                refresh_gateway_presence=refresh_gateway_presence,
                autonomous_gate_active=lambda: self._autonomous_gate_active,
                start_autonomous_execution=lambda: self._start_autonomous_execution(),
                application_ready=lambda: bool(self._app),
                invalidate=lambda interval: self._invalidate(min_interval=interval),
                enqueue_pending_input=self._pending_input.put,
                stop_requested=lambda: self._should_exit,
                presence_refresh_needed=lambda: (
                    self._agent_running
                    or self._command_running
                    or self._stream_render_state.started
                    or self._get_subagent_observability_snapshot().get("active")
                ),
                command_running=lambda: self._command_running,
                poll_scheduled_workflow=self._scheduled_executor_runtime.poll_workflow,
                get_pending_input=lambda timeout: self._pending_input.get(timeout=timeout),
                empty_input=queue.Empty,
                execute_input=lambda user_input: self._execute_pending_input(user_input, app=app),
                report_input_error=lambda error: print(f"Error: {error}"),
                sleep=time.sleep,
                monotonic_time=time.monotonic,
                thread_factory=threading.Thread,
                register_exit_cleanup=atexit.register,
                cleanup=_run_cleanup,
                stdout_context=patch_stdout,
                report_unusable_stdin=report_unusable_stdin,
                request_stop=lambda: setattr(self, "_should_exit", True),
                teardown=lambda: run_tui_teardown(self._tui_teardown_ports()),
            )
        ).run()


# ============================================================================
# Main Entry Point
# ============================================================================

# Track whether daemons were auto-started (so we can offer to stop on exit)
_daemons_auto_started = False


def _handle_serve_command(action: str) -> None:
    """Handle ``voidcube stop`` / ``voidcube status`` subcommands.

    ``start`` and ``foreground`` are also available but are normally
    handled automatically by the interactive entry path.
    """
    valid = {"start", "stop", "status", "foreground"}
    action_lower = action.strip().lower() if action else "status"

    if action_lower not in valid:
        print(f"Invalid serve action: {action!r}")
        print(f"Usage: voidcube <stop|status|start|foreground>")
        return

    try:
        from VoidCube_cli.ops.serve import start_all, stop_all, print_status
    except ImportError as exc:
        print(f"Failed to import serve module: {exc}")
        print("Ensure VoidCube is installed correctly (pip install -e .)")
        return

    if action_lower == "start":
        start_all(foreground=False)
    elif action_lower == "foreground":
        start_all(foreground=True)
    elif action_lower == "stop":
        stop_all()
    elif action_lower == "status":
        print_status()


def _auto_start_daemons() -> None:
    """Start Gateway → Memory → Supervisor if not already running.

    Called transparently when entering interactive mode (skipped for -q).
    Sets the module-level ``_daemons_auto_started`` flag so the exit
    handler can offer to stop them.
    """
    global _daemons_auto_started

    try:
        from VoidCube_cli.ops.serve import ensure_running, print_status
    except ImportError:
        return  # serve module not available — silently skip

    print("\n  Auto-starting VoidCube daemons (Gateway -> Memory -> Supervisor)...\n")
    result = ensure_running(silent=False)
    print()

    any_started = any(info.get("started") for info in result.values())
    if any_started:
        print_status()
        _daemons_auto_started = True


def _maybe_stop_daemons_on_exit(force: bool = False) -> None:
    """Called at process exit: stop daemons that were auto-started.

    When ``force=True`` (from /quit): stop immediately, no output.
    When ``force=False`` (from EOF or normal exit): stop silently
    without prompting — ``input()`` is unreliable inside atexit handlers
    because stdin may already be closed after TUI teardown.
    """
    global _daemons_auto_started
    if not _daemons_auto_started:
        return

    try:
        from VoidCube_cli.ops.serve import stop_all
    except ImportError:
        return

    stop_all(force=True)
    _daemons_auto_started = False


def main(
    query: Optional[str] = None,
    q: Optional[str] = None,
    image: Optional[str] = None,
    toolsets: Optional[str] = None,
    skills: Optional[str | list[str] | tuple[str, ...]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_turns: Optional[int] = None,
    verbose: bool = False,
    quiet: bool = False,
    compact: bool = False,
    list_tools: bool = False,
    list_toolsets: bool = False,
    resume: Optional[str] = None,
    worktree: bool = False,
    w: bool = False,
    checkpoints: bool = False,
    pass_session_id: bool = False,
    version: bool = False,
    serve: Optional[str] = None,
):
    """
    Voidcube Agent CLI - Interactive AI Assistant
    
    Args:
        query: Single query to execute (then exit). Alias: -q
        q: Shorthand for --query
        image: Optional local image path to attach to a single query
        toolsets: Comma-separated list of toolsets to enable (e.g., "web,terminal")
        skills: Comma-separated or repeated list of skills to preload for the session
        model: Model to use (default: from the active provider config)
        provider: Inference provider ("auto", "openrouter", "nous", "zai", "kimi-coding", "minimax", "minimax-cn")
        api_key: API key for authentication
        base_url: Base URL for the API
        max_turns: Maximum tool-calling iterations (default: 60)
        verbose: Enable verbose logging
        compact: Use compact display mode
        list_tools: List available tools and exit
        list_toolsets: List available toolsets and exit
        resume: Resume a previous session by its ID (e.g., 20260225_143052_a1b2c3)
        worktree: Run in an isolated git worktree (for parallel agents). Alias: -w
        w: Shorthand for --worktree
        version: Show version information and exit
    
    Examples:
        python cli.py                            # Start interactive mode
        python cli.py --toolsets web,terminal    # Use specific toolsets
        python cli.py --skills VoidCube-agent-dev,github-auth
        python cli.py -q "What is Python?"       # Single query mode
        python cli.py -q "Describe this" --image ~/storage/shared/Pictures/cat.png
        python cli.py --list-tools               # List tools and exit
        python cli.py --resume 20260225_143052_a1b2c3  # Resume session
        python cli.py -w                         # Start in isolated git worktree
        python cli.py -w -q "Fix issue #123"     # Single query in worktree
        python cli.py --version                  # Show version and exit
    """
    if version:
        from VoidCube_cli import __version__

        print(f"VoidCube CLI v{__version__}")
        print("轻量安装·快速配置·友好交互 — 服务器运维与部署智能体")
        print("项目地址: https://gitee.com/LSgit-hub/voidcub-CLI")
        return

    # ── serve command ─────────────────────────────────────────────────
    if serve is not None:
        _handle_serve_command(serve)
        return

    # Deferred runtime initialization: logging, config, and tool preview.
    # Moved out of module-level to avoid ~300ms of import-chain cost at startup.
    _init_cli_runtime()

    # Ensure CLI_CONFIG is cached in module globals so bare-name references
    # in main(), VoidcubeCLI.__init__, and class methods resolve correctly.
    globals()["CLI_CONFIG"] = _get_cli_config()

    # Signal to terminal_tool that we're in interactive mode
    # This enables interactive sudo password prompts with timeout
    os.environ["VOIDCUBE_INTERACTIVE"] = "1"

    # Skip worktree for list commands (they exit immediately)
    if not list_tools and not list_toolsets:
        # ── Git worktree isolation (#652) ──
        # Create an isolated worktree so this agent instance doesn't collide
        # with other agents working on the same repo.
        use_worktree = worktree or w or CLI_CONFIG.get("worktree", False)
        wt_info = None
        if use_worktree:
            # Prune stale worktrees from crashed/killed sessions
            _repo = _git_repo_root()
            if _repo:
                _prune_stale_worktrees(_repo)
            wt_info = _setup_worktree()
            if wt_info:
                os.environ["TERMINAL_CWD"] = wt_info["path"]
                atexit.register(_cleanup_worktree, wt_info)
            else:
                # Worktree was explicitly requested but setup failed —
                # don't silently run without isolation.
                return
    else:
        wt_info = None
    
    # Handle query shorthand
    query = query or q

    # ── Auto-start daemons (interactive mode only) ─────────────────────
    # Single-query (-q), list commands, and other short-lived operations
    # skip the daemon lifecycle to keep startup fast.
    #
    # When VOIDCUBE_DAEMONS_STARTED=1 (set by voidcube.py), daemons were
    # already started by the wrapper — skip the start but still register
    # cleanup so /quit and atexit can stop them. The desktop shell owns its
    # service processes independently, so its embedded CLI neither starts
    # nor stops them.
    is_interactive = query is None and not list_tools and not list_toolsets
    daemons_already_started = os.environ.get("VOIDCUBE_DAEMONS_STARTED") == "1"
    desktop_manages_services = (
        os.environ.get("VOIDCUBE_DESKTOP_MANAGED_SERVICES") == "1"
    )
    if os.environ.get("VOIDCUBE_DESKTOP") == "1":
        from VoidCube_cli.execution_context import (
            clear_execution_context,
            publish_execution_context,
        )

        publish_execution_context(wt_info)
        atexit.register(clear_execution_context, os.getpid())
    if is_interactive and not desktop_manages_services:
        if daemons_already_started:
            # Daemons were started by voidcube.py — we still own cleanup
            global _daemons_auto_started
            _daemons_auto_started = True
            atexit.register(_maybe_stop_daemons_on_exit)
        else:
            _auto_start_daemons()
            atexit.register(_maybe_stop_daemons_on_exit)

    # Parse toolsets - handle both string and tuple/list inputs
    # Parse the explicitly selected toolsets when provided.
    toolsets_list = None
    if toolsets:
        if isinstance(toolsets, str):
            toolsets_list = [t.strip() for t in toolsets.split(",")]
        elif isinstance(toolsets, (list, tuple)):
            # Fire may pass multiple --toolsets as a tuple
            toolsets_list = []
            for t in toolsets:
                if isinstance(t, str):
                    toolsets_list.extend([x.strip() for x in t.split(",")])
                else:
                    toolsets_list.append(str(t))
    else:
        # Use the shared resolver so MCP servers are included at runtime
        from VoidCube_cli.tools_config import _get_platform_tools
        toolsets_list = sorted(_get_platform_tools(CLI_CONFIG, "cli"))
    
    parsed_skills = _parse_skills_argument(skills)

    # Create CLI instance
    cli = VoidcubeCLI(
        model=model,
        toolsets=toolsets_list,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        max_turns=max_turns,
        verbose=verbose,
        compact=compact,
        resume=resume,
        checkpoints=checkpoints,
        pass_session_id=pass_session_id,
    )

    if parsed_skills:
        skills_prompt, loaded_skills, missing_skills = _get_preloaded_skills_prompt(
            parsed_skills,
            task_id=cli.session_id,
        )
        if missing_skills:
            missing_display = ", ".join(missing_skills)
            raise ValueError(f"Unknown skill(s): {missing_display}")
        if skills_prompt:
            cli.system_prompt = "\n\n".join(
                part for part in (cli.system_prompt, skills_prompt) if part
            ).strip()
            cli.preloaded_skills = loaded_skills

    # Inject language preference based on current locale
    lang_prompt = _get_language_preference_prompt()
    if lang_prompt:
        cli.system_prompt = "\n\n".join(
            part for part in (cli.system_prompt, lang_prompt) if part
        ).strip()

    # Inject worktree context into agent's system prompt
    if wt_info:
        wt_note = (
            f"\n\n[System note: You are working in an isolated git worktree at "
            f"{wt_info['path']}. Your branch is `{wt_info['branch']}`. "
            f"Changes here do not affect the main working tree or other agents. "
            f"Remember to commit and push your changes, and create a PR if appropriate. "
            f"The original repo is at {wt_info['repo_root']}.]"
        )
        cli.system_prompt = (cli.system_prompt or "") + wt_note
    
    # Handle list commands (don't init agent for these)
    if list_tools:
        cli.show_banner()
        render_tools_for_host(cli, emit=print, translate=t)
        sys.exit(0)
    
    if list_toolsets:
        cli.show_banner()
        render_toolsets_for_host(cli, emit=print, translate=t)
        sys.exit(0)
    
    # Register cleanup for single-query mode (interactive mode registers in run())
    atexit.register(_run_cleanup)
    
    # Handle single query mode
    if query or image:
        query, single_query_images = _collect_query_images(query, image)
        if quiet:
            # Quiet mode: suppress banner, spinner, tool previews.
            # Only print the final response and parseable session info.
            cli.tool_progress_mode = "off"
            if cli._ensure_runtime_credentials():
                effective_query = query
                if single_query_images:
                    effective_query = cli._preprocess_images_with_vision(
                        query,
                        single_query_images,
                        announce=False,
                    )
                turn_route = cli._resolve_turn_agent_config(effective_query)
                if turn_route["signature"] != cli._active_agent_route_signature:
                    cli.agent = None
                if cli._init_agent(
                    model_override=turn_route["model"],
                    runtime_override=turn_route["runtime"],
                    route_label=turn_route["label"],
                    request_overrides=turn_route.get("request_overrides"),
                ):
                    cli.agent.quiet_mode = True
                    cli.agent.suppress_status_output = True
                    result = cli.agent.run_conversation(
                        user_message=effective_query,
                        conversation_history=cli.conversation_history,
                    )
                    response = result.get("final_response", "") if isinstance(result, dict) else str(result)
                    if response:
                        print(response)
                    print(f"\nsession_id: {cli.session_id}")
                    
                    # Ensure proper exit code for automation wrappers
                    sys.exit(1 if isinstance(result, dict) and result.get("failed") else 0)
            
            # Exit with error code if credentials or agent init fails
            sys.exit(1)
        else:
            cli.show_banner()
            _query_label = query or ("[image attached]" if single_query_images else "")
            if _query_label:
                cli.console.print(f"[bold blue]Query:[/] {_query_label}")
            cli.chat(query, images=single_query_images or None)
            cli._print_exit_summary()
        return
    
    # Run interactive mode
    cli.run()


def _get_language_preference_prompt() -> str:
    """Return a language preference injection based on the current i18n locale.

    When the locale is zh_CN (Simplified Chinese), injects a directive
    instructing the model to respond in Chinese.  Falls back gracefully
    if i18n is not initialized or the locale file is missing.
    """
    try:
        from VoidCube_cli.i18n import get_i18n
        i18n = get_i18n()
        locale = i18n.get_current_locale()
    except Exception:
        return ""

    # Locale→language preference mapping
    LOCALE_LANG_PROMPTS = {
        "zh_CN": (
            "## 语言偏好 (Language Preference)\n"
            "请始终使用**简体中文**回复用户。代码注释、技术解释和对话都应使用中文。\n"
            "代码本身（变量名、函数名等）保留英文。技术术语若没有通用中文译名可保留英文原文。"
        ),
    }
    return LOCALE_LANG_PROMPTS.get(locale, "")


if __name__ == "__main__":
    import fire

    fire.Fire(main)
