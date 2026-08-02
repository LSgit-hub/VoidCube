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
from typing import List, Dict, Any, Optional, Callable, TYPE_CHECKING

from agent.error_classifier import summarize_api_error
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
from VoidCube_app.session_identity import resolve_session_identity
from VoidCube_app.session_lifecycle import (
    SessionHydration,
    SessionHydrationStatus,
    SessionLifecycleState,
    SessionTitleStatus,
    hydrate_session,
    set_session_title,
)
from VoidCube_app.turn_contract import begin_turn, normalize_turn_outcome
from VoidCube_app.tool_events import ToolEvent
from VoidCube_app.turn_queue import (
    TurnInterruptReason,
    TurnInputRoute,
    cancel_turn,
    interrupt_text,
    normalize_busy_input_mode,
    resolve_interrupted_followup,
)
from VoidCube_cli.turn_queue_adapter import (
    InterruptPollStatus,
    enqueue_turn_input,
    poll_interrupt_input,
    requeue_interrupted_inputs,
)
from VoidCube_cli.tui_layout import build_tui_layout_children
from VoidCube_cli.tui_application import (
    create_tui_application,
    install_resize_reflow_cleanup,
)
from VoidCube_cli.tui_keybindings import (
    install_history_navigation_keybindings,
    install_text_editing_keybindings,
)
from VoidCube_cli.tui_modal_navigation import (
    ModalNavigationPorts,
    install_modal_navigation_keybindings,
)
from VoidCube_cli.tui_modal_widgets import (
    ModalWidgetPorts,
    build_modal_widgets,
)
from VoidCube_cli.tui_indicator_widgets import (
    IndicatorWidgetPorts,
    build_indicator_widgets,
)
from VoidCube_cli.tui_input_widgets import (
    InputWidgetPorts,
    build_input_area,
    install_placeholder_processor,
)
from VoidCube_cli.scheduled_task_polling import start_scheduled_task_polling
from VoidCube_cli.scheduled_executor import (
    ScheduledTaskExecutorPorts,
    ScheduledTaskExecutorRuntime,
)
from VoidCube_cli.background_task_runtime import (
    BackgroundTaskPorts,
    BackgroundTaskRuntime,
    BackgroundTaskState,
)
from VoidCube_cli.tui_refresh_loop import start_tui_refresh_loop
from VoidCube_cli.input_process_loop import start_input_process_loop
from VoidCube_cli.tui_teardown import TuiTeardownPorts, run_tui_teardown
from VoidCube_cli.voice_runtime_state import CliVoiceRuntimeState
from VoidCube_cli.voice_recording_runtime import (
    VoiceRecordingPorts,
    start_terminal_voice_recording,
    stop_terminal_voice_recording,
)
from VoidCube_cli.voice_tts_adapter import VoiceTtsAdapter
from VoidCube_cli.embedded_autonomous_host import (
    EmbeddedAutonomousHostPorts,
    ensure_embedded_autonomous_component_host,
)
from VoidCube_cli.embedded_autonomous_runtime import (
    EmbeddedAutonomousComponentRuntime,
    EmbeddedAutonomousRuntimePorts,
)
from VoidCube_cli.chat_render_state import CliStreamRenderState
from VoidCube_cli.chat_stream_renderer import CliStreamRenderer
from VoidCube_cli.command_router import (
    looks_like_slash_command as _looks_like_slash_command,
    parse_cli_command,
    resolve_dynamic_command,
)
from VoidCube_cli.command_handlers.registry import (
    autonomous_command_ports_for_host,
    exit_autonomous_gate_fast_for_host,
    force_quit_autonomous_gate_for_host,
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

from VoidCube_cli.autonomous_executor import (
    autonomous_task_run_id_for_message,
)
from VoidCube_cli.autonomous_events import (
    append_autonomous_execution_event as _append_autonomous_execution_event_view,
)
from VoidCube_cli.autonomous_presence import (
    refresh_gateway_cli_presence as _refresh_gateway_cli_presence_view,
    ensure_supervisor_task_session as _ensure_supervisor_task_session_view,
    push_cli_agent_scene as _push_cli_agent_scene,
)
from VoidCube_cli.autonomous_panel import (
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
from prompt_toolkit.layout import Layout, HSplit, Window, FormattedTextControl, ConditionalContainer
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.key_binding import KeyBindings
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
    _format_process_notification,
    _setup_worktree,
    _cleanup_worktree,
    _prune_stale_worktrees,
    _git_repo_root,
    _git_head_commit,
    _git_improvement_diff,
)
from VoidCube_cli.attachments import (
    _collect_query_images,
    _detect_file_drop,
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
    return _register_gateway_session(
        session_id,
        model,
        provider,
        source="cli",
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
    def _voice_lock(self):
        return self._voice_state().lock

    @property
    def _voice_mode(self):
        return self._voice_state().mode

    @_voice_mode.setter
    def _voice_mode(self, value):
        self._voice_state().mode = value

    @property
    def _voice_recording(self):
        return self._voice_state().recording

    @_voice_recording.setter
    def _voice_recording(self, value):
        self._voice_state().recording = value

    @property
    def _voice_processing(self):
        return self._voice_state().processing

    @_voice_processing.setter
    def _voice_processing(self, value):
        self._voice_state().processing = value

    @property
    def _voice_continuous(self):
        return self._voice_state().continuous

    @_voice_continuous.setter
    def _voice_continuous(self, value):
        self._voice_state().continuous = value

    @property
    def _voice_stop_continuous(self):
        return self._voice_state().stop_continuous

    @_voice_stop_continuous.setter
    def _voice_stop_continuous(self, value):
        self._voice_state().stop_continuous = value

    @property
    def _no_speech_count(self):
        return self._voice_state().no_speech_count

    @_no_speech_count.setter
    def _no_speech_count(self, value):
        self._voice_state().no_speech_count = value
    
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
        # busy_input_mode: "interrupt" (Enter interrupts current run) or "queue" (Enter queues for next turn)
        _bim = display_config.get("busy_input_mode", "interrupt")
        self.busy_input_mode = normalize_busy_input_mode(_bim).value

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
        self._resumed = False
        # Initialize SQLite session store early so /title works before first message
        self._session_db = None
        try:
            from VoidCube_core.state import SessionDB
            self._session_db = SessionDB()
        except Exception as e:
            logger.warning("Failed to initialize SessionDB — session will NOT be indexed for search: %s", e)
        
        # Deferred title: stored in memory until the session is created in the DB
        self._pending_title: Optional[str] = None
        
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
        self.session_id = session_identity.session_id
        self._resumed = session_identity.resumed
        self._session_hydration: SessionHydration | None = None
        if session_identity.resume_lookup_error:
            logger.warning(
                "Failed to auto-resume last session: %s",
                session_identity.resume_lookup_error,
            )
        elif resume is None and self._resumed:
            logger.info("Auto-resuming last session: %s", self.session_id)
        
        # History file for persistent input recall across sessions
        self._history_file = _VoidCube_home / ".VoidCube_history"
        self._last_invalidate: float = 0.0  # throttle UI repaints
        self._app = None

        # ── Per-instance render caches (avoid disk I/O & subprocess on hot path) ──
        self._config_cache: Dict[str, Any] | None = None
        self._config_cache_ts: float = 0.0
        self._git_status_cache: list | None = None
        self._git_status_cache_ts: float = 0.0
        self._git_status_refreshing: bool = False  # guard against concurrent git subprocess refresh
        self._ascii_fallback: bool | None = None  # cached once, never changes mid-session

        # State shared by interactive run() and single-query chat mode.
        # These must exist before any direct chat() call because single-query
        # mode does not go through run().
        self._agent_running = False
        self._autonomous_gate_active: bool = False
        self._pending_input: queue.Queue = queue.Queue()
        self._interrupt_queue: queue.Queue = queue.Queue()
        self._should_exit = False
        self._last_ctrl_c_time = 0
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
                emit=_cprint,
                refresh_gateway_cli_presence=lambda *, force=False: _refresh_gateway_cli_presence_view(
                    self,
                    force=force,
                    is_gateway_running=_is_gateway_running,
                    register_with_gateway=_register_with_gateway,
                    push_cli_agent_scene=_push_cli_agent_scene,
                    monotonic_time=time.monotonic,
                ),
                interrupt_current_task=self._interrupt_autonomous_component_task,
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
        self._background_tasks = self._background_task_state.tasks
        self._background_task_info = self._background_task_state.info
        self._last_gateway_presence_refresh_at: float = 0.0
        self._gateway_presence_refresh_interval_seconds: float = 30.0
        self._autonomous_execution_events: List[Dict[str, str]] = []
        self._autonomous_last_supervisor_event_key: str = ""
        self._autonomous_parent_host = None
        self._autonomous_component_host = None
        self._autonomous_component_thread = None
        self._autonomous_component_stop = threading.Event()
        self._api_a_execution_gate = threading.Lock()
        self._scheduled_execution_active = False
        self._scheduled_executor_runtime = self._create_scheduled_executor_runtime()
        _initialize_autonomous_status_caches_view(self)

    def _quiet_autonomous_component_cprint(self, *args: Any, **kwargs: Any) -> None:
        """Keep autonomous component execution out of the user's scrollback."""
        del args, kwargs

    def _is_embedded_autonomous_component(self) -> bool:
        """Return True when this host only exists for the embedded mini CLI."""
        return getattr(self, "_autonomous_parent_host", None) is not None

    def _create_scheduled_executor_runtime(self) -> ScheduledTaskExecutorRuntime:
        """Assemble scheduled execution from explicit CLI-owned state ports."""
        return ScheduledTaskExecutorRuntime(
            ScheduledTaskExecutorPorts(
                is_embedded_component=self._is_embedded_autonomous_component,
                auto_task_running=lambda: bool(
                    getattr(
                        getattr(self, "_autonomous_component_host", None),
                        "_agent_running",
                        False,
                    )
                ),
                manual_background_task_running=lambda: any(
                    thread.is_alive()
                    for thread in self._background_tasks.values()
                    if callable(getattr(thread, "is_alive", None))
                ),
                agent_running=lambda: bool(self._agent_running),
                command_running=lambda: bool(self._command_running),
                execution_gate=self._api_a_execution_gate,
                get_session_id=lambda: str(self.session_id or ""),
                set_execution_active=lambda active: setattr(
                    self, "_scheduled_execution_active", bool(active)
                ),
                start_background_task=self._start_background_agent_task,
            )
        )

    def _should_emit_scrollback_output(self) -> bool:
        """Return whether this host may write into the user's main CLI transcript."""
        return not self._is_embedded_autonomous_component()

    def _ensure_autonomous_component_host(self):
        def create_component_host():
            return type(self)(
                model=getattr(self, "model", None),
                toolsets=getattr(self, "enabled_toolsets", None),
                provider=getattr(self, "requested_provider", None) or getattr(self, "provider", None),
                api_key=getattr(self, "_explicit_api_key", None),
                base_url=getattr(self, "_explicit_base_url", None),
                max_turns=getattr(self, "max_turns", None),
                verbose=getattr(self, "verbose", False),
                compact=True,
                checkpoints=getattr(self, "checkpoints_enabled", False),
                pass_session_id=True,
            )

        return ensure_embedded_autonomous_component_host(
            EmbeddedAutonomousHostPorts(
                get_component_host=lambda: getattr(self, "_autonomous_component_host", None),
                create_component_host=create_component_host,
                set_component_active=lambda host, active: setattr(
                    host, "_autonomous_gate_active", active
                ),
                bind_component_parent=lambda host: setattr(
                    host, "_autonomous_parent_host", self
                ),
                ensure_task_session=lambda host: _ensure_supervisor_task_session_view(
                    host, logger_debug=logger.debug
                ),
                store_component_host=lambda host: setattr(
                    self, "_autonomous_component_host", host
                ),
            )
        )

    def _autonomous_component_runtime(self):
        component_host = self._ensure_autonomous_component_host()
        return _autonomous_executor_runtime_view(
            component_host,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=self._quiet_autonomous_component_cprint,
        )

    def _embedded_autonomous_runtime(self) -> EmbeddedAutonomousComponentRuntime:
        runtime = self.__dict__.get("_embedded_autonomous_runtime_instance")
        if runtime is not None:
            return runtime

        def ensure_stop_event() -> threading.Event:
            event = getattr(self, "_autonomous_component_stop", None)
            if event is None:
                event = threading.Event()
                self._autonomous_component_stop = event
            return event

        def refresh_statuses(component_host: Any) -> None:
            _refresh_supervisor_status_view(component_host)
            _refresh_autonomous_gateway_status_view(component_host)
            _refresh_gateway_autonomous_execute_snapshot_view(component_host)
            _refresh_gateway_cli_presence_view(
                component_host,
                force=False,
                is_gateway_running=_is_gateway_running,
                register_with_gateway=_register_with_gateway,
                push_cli_agent_scene=_push_cli_agent_scene,
                monotonic_time=time.monotonic,
            )

        def get_pending_input(component_host: Any) -> object | None:
            try:
                return component_host._pending_input.get_nowait()
            except Exception:
                return None

        def can_poll_workflow(component_host: Any) -> bool:
            return not getattr(self, "_scheduled_execution_active", False) and not getattr(
                component_host,
                "_agent_running",
                False,
            )

        def deactivate_component_host(component_host: Any | None) -> bool:
            if component_host is None:
                return False
            component_host._autonomous_gate_active = False
            return True

        def interrupt_running_agent(component_host: Any | None) -> None:
            try:
                if component_host and component_host.agent and component_host._agent_running:
                    component_host.agent.interrupt()
            except Exception:
                pass

        def interrupt_current_task() -> None:
            try:
                self._autonomous_component_runtime().interrupt_current_task(
                    reason="自主链路已停止；当前链路项被用户中断。",
                    source="embedded_component_stop",
                    timeout=5,
                )
            except Exception:
                pass

        def signal_stop() -> None:
            ensure_stop_event().set()

        runtime = EmbeddedAutonomousComponentRuntime(
            EmbeddedAutonomousRuntimePorts(
                get_component_host=lambda: getattr(
                    self,
                    "_autonomous_component_host",
                    None,
                ),
                ensure_component_host=self._ensure_autonomous_component_host,
                get_component_thread=lambda: getattr(
                    self,
                    "_autonomous_component_thread",
                    None,
                ),
                store_component_thread=lambda thread: setattr(
                    self,
                    "_autonomous_component_thread",
                    thread,
                ),
                ensure_stop_event=ensure_stop_event,
                parent_component_active=lambda: bool(self._autonomous_gate_active),
                set_component_active=lambda host, active: setattr(
                    host,
                    "_autonomous_gate_active",
                    active,
                ),
                build_executor_runtime=lambda _host: self._autonomous_component_runtime(),
                refresh_statuses=refresh_statuses,
                can_poll_workflow=can_poll_workflow,
                get_pending_input=get_pending_input,
                execute_pending_input=lambda host, pending: host._execute_pending_input(
                    pending,
                    app=None,
                ),
                invalidate=lambda: self._invalidate(min_interval=0.5),
                report_error=lambda error: logger.debug(
                    "Autonomous execution component loop error: %s",
                    error,
                ),
                publish_idle_scene=lambda host: _push_cli_agent_scene(
                    "idle",
                    session_id=getattr(host, "session_id", None),
                    agent_role="supervisor_task",
                ),
                deactivate_component_host=deactivate_component_host,
                interrupt_running_agent=interrupt_running_agent,
                interrupt_current_task=interrupt_current_task,
                signal_stop=signal_stop,
                thread_factory=threading.Thread,
            )
        )
        self._embedded_autonomous_runtime_instance = runtime
        return runtime

    def _start_autonomous_execution_component(self) -> bool:
        """Start the embedded API-A autonomous execution component."""
        return self._embedded_autonomous_runtime().start()

    def _stop_autonomous_execution_component(self, *, interrupt: bool = False) -> None:
        self._embedded_autonomous_runtime().stop(interrupt=interrupt)

    def _interrupt_autonomous_component_task(
        self,
        *,
        reason: str,
        source: str,
        timeout: float = 5,
    ) -> bool:
        component_host = getattr(self, "_autonomous_component_host", None)
        if component_host is None:
            return _autonomous_executor_runtime_view(
                self,
                push_cli_agent_scene=_push_cli_agent_scene,
                git_head_commit=_git_head_commit,
                git_improvement_diff=_git_improvement_diff,
                cprint=_cprint,
            ).interrupt_current_task(reason=reason, source=source, timeout=timeout)
        return _autonomous_executor_runtime_view(
            component_host,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=self._quiet_autonomous_component_cprint,
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
        # Prefer the agent's model name — it updates on fallback.
        # self.model reflects the originally configured model and never
        # changes mid-session, so the TUI would show a stale name after
        # _try_activate_fallback() switches provider/model.
        agent = getattr(self, "agent", None)
        model_name = (getattr(agent, "model", None) or self.model or "unknown")
        model_short = model_name.split("/")[-1] if "/" in model_name else model_name
        if model_short.endswith(".gguf"):
            model_short = model_short[:-5]
        if len(model_short) > 26:
            model_short = f"{model_short[:23]}..."

        elapsed_seconds = max(0.0, (datetime.now() - self.session_start).total_seconds())
        snapshot = {
            "model_name": model_name,
            "model_short": model_short,
            "duration": _format_duration_compact_lazy(elapsed_seconds),
            "context_tokens": 0,
            "context_length": None,
            "context_percent": None,
            "session_input_tokens": 0,
            "session_output_tokens": 0,
            "session_cache_read_tokens": 0,
            "session_cache_write_tokens": 0,
            "session_prompt_tokens": 0,
            "session_completion_tokens": 0,
            "session_total_tokens": 0,
            "session_api_calls": 0,
            "compressions": 0,
            "subagent": self._get_subagent_observability_snapshot(),
        }

        if not agent:
            return snapshot

        snapshot["session_input_tokens"] = getattr(agent, "session_input_tokens", 0) or 0
        snapshot["session_output_tokens"] = getattr(agent, "session_output_tokens", 0) or 0
        snapshot["session_cache_read_tokens"] = getattr(agent, "session_cache_read_tokens", 0) or 0
        snapshot["session_cache_write_tokens"] = getattr(agent, "session_cache_write_tokens", 0) or 0
        snapshot["session_prompt_tokens"] = getattr(agent, "session_prompt_tokens", 0) or 0
        snapshot["session_completion_tokens"] = getattr(agent, "session_completion_tokens", 0) or 0
        snapshot["session_total_tokens"] = getattr(agent, "session_total_tokens", 0) or 0
        snapshot["session_api_calls"] = getattr(agent, "session_api_calls", 0) or 0

        compressor = getattr(agent, "context_compressor", None)
        if compressor:
            context_tokens = getattr(compressor, "last_prompt_tokens", 0) or 0
            context_length = getattr(compressor, "context_length", 0) or 0
            snapshot["context_tokens"] = context_tokens
            snapshot["context_length"] = context_length or None
            snapshot["compressions"] = getattr(compressor, "compression_count", 0) or 0
            if context_length:
                snapshot["context_percent"] = max(0, min(100, round((context_tokens / context_length) * 100)))

        return snapshot

    @staticmethod
    def _normalize_subagent_status(task: Any) -> str:
        status = getattr(task, "status", "")
        value = getattr(status, "value", status)
        return str(value or "").strip().lower()

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
        single = getattr(agent, "_subagent_display_manager", None)
        if single is not None and single not in managers:
            managers.append(single)
        return managers

    @classmethod
    def _truncate_subagent_preview(cls, text: str, limit: int) -> str:
        value = " ".join(str(text or "").strip().split())
        if len(value) <= limit:
            return value
        if limit <= 3:
            return value[:limit]
        return value[: limit - 3] + "..."

    def _get_subagent_observability_snapshot(self) -> Dict[str, Any]:
        snapshot = {
            "active": False,
            "foreground_count": 0,
            "background_count": 0,
            "total_count": 0,
            "counts_label": "0",
            "focus_task_id": "",
            "focus_tool": "",
            "focus_preview": "",
            "compact_preview": "",
        }
        managers = self._get_subagent_display_managers()
        if not managers:
            return snapshot

        foreground_tasks = []
        background_tasks = []
        try:
            for manager in managers:
                foreground_tasks.extend(list(manager.list_tasks(include_background=False) or []))
                background_tasks.extend(list(manager.list_background_tasks() or []))
        except Exception:
            return snapshot

        terminal_statuses = {"completed", "failed", "interrupted", "cancelled"}

        def _is_active(task: Any) -> bool:
            return self._normalize_subagent_status(task) not in terminal_statuses

        active_foreground = [task for task in foreground_tasks if _is_active(task)]
        active_background = [task for task in background_tasks if _is_active(task)]
        if not active_foreground and not active_background:
            return snapshot

        active_foreground.sort(key=lambda task: getattr(task, "task_index", 0))
        active_background.sort(key=lambda task: getattr(task, "task_index", 0))
        focus_task = active_foreground[0] if active_foreground else active_background[0]

        focus_task_id = str(getattr(focus_task, "task_id", "") or "").strip()
        focus_tool = str(getattr(focus_task, "current_tool", "") or "").strip()
        focus_preview_source = (
            focus_tool
            or str(getattr(focus_task, "current_tool_preview", "") or "").strip()
            or str(getattr(focus_task, "current_thinking", "") or "").strip()
            or str(getattr(focus_task, "goal_preview", "") or "").strip()
            or str(getattr(focus_task, "goal", "") or "").strip()
        )
        focus_preview = self._truncate_subagent_preview(focus_preview_source, 32)
        compact_preview = self._truncate_subagent_preview(focus_preview_source, 18)
        foreground_count = len(active_foreground)
        background_count = len(active_background)
        total_count = foreground_count + background_count
        counts_label = (
            f"{foreground_count}+{background_count}"
            if background_count > 0
            else str(foreground_count)
        )

        snapshot.update(
            {
                "active": True,
                "foreground_count": foreground_count,
                "background_count": background_count,
                "total_count": total_count,
                "counts_label": counts_label,
                "focus_task_id": focus_task_id,
                "focus_tool": focus_tool,
                "focus_preview": focus_preview,
                "compact_preview": compact_preview,
            }
        )
        return snapshot

    @staticmethod
    def _status_bar_display_width(text: str) -> int:
        """Return terminal cell width for status-bar text.

        len() is not enough for prompt_toolkit layout decisions because some
        glyphs can render wider than one Python codepoint. Keeping the status
        bar within the real display width prevents it from wrapping onto a
        second line and leaving behind duplicate rows.
        """
        try:
            from prompt_toolkit.utils import get_cwidth
            return get_cwidth(text or "")
        except Exception:
            return len(text or "")

    @classmethod
    def _trim_status_bar_text(cls, text: str, max_width: int) -> str:
        """Trim status-bar text to a single terminal row."""
        if max_width <= 0:
            return ""
        try:
            from prompt_toolkit.utils import get_cwidth
        except Exception:
            get_cwidth = None  # type: ignore[assignment]

        if cls._status_bar_display_width(text) <= max_width:
            return text

        ellipsis = "..."
        ellipsis_width = cls._status_bar_display_width(ellipsis)
        if max_width <= ellipsis_width:
            return ellipsis[:max_width]

        out = []
        width = 0
        for ch in text:
            ch_width = get_cwidth(ch) if get_cwidth else len(ch)
            if width + ch_width + ellipsis_width > max_width:
                break
            out.append(ch)
            width += ch_width
        return "".join(out).rstrip() + ellipsis

    @classmethod
    def _pad_status_bar_text(cls, text: str, width: int) -> str:
        """Pad text to an exact display width using terminal cell width."""
        text = cls._trim_status_bar_text(text, width)
        pad = max(0, width - cls._status_bar_display_width(text))
        if pad <= 0:
            return text
        return text + (" " * pad)

    @staticmethod
    def _get_tui_terminal_width(default: tuple[int, int] = (80, 24)) -> int:
        """Return the live prompt_toolkit width, falling back to ``shutil``.

        The TUI layout can be narrower than ``shutil.get_terminal_size()`` reports,
        especially on Termux/mobile shells, so prefer prompt_toolkit's width whenever
        an app is active.
        """
        try:
            from prompt_toolkit.application import get_app
            return get_app().output.get_size().columns
        except Exception:
            return shutil.get_terminal_size(default).columns

    def _use_minimal_tui_chrome(self, width: Optional[int] = None) -> bool:
        """Hide low-value chrome on narrow/mobile terminals to preserve rows."""
        if width is None:
            width = self._get_tui_terminal_width()
        return width < 64

    def _tui_input_rule_height(self, position: str, width: Optional[int] = None) -> int:
        """Return the visible height for the top/bottom input separator rules."""
        if position not in {"top", "bottom"}:
            raise ValueError(f"Unknown input rule position: {position}")
        if position == "top":
            return 1
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _agent_spacer_height(self, width: Optional[int] = None) -> int:
        """Return the spacer height shown above the status bar while the agent runs."""
        if not getattr(self, "_agent_running", False):
            return 0
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _spinner_widget_height(self, width: Optional[int] = None) -> int:
        """Return the visible height for the spinner/status text line above the status bar."""
        if not getattr(self, "_spinner_text", ""):
            return 0
        return 0 if self._use_minimal_tui_chrome(width=width) else 1

    def _get_voice_status_fragments(self, width: Optional[int] = None):
        """Return the voice status bar fragments for the interactive TUI."""
        width = width or self._get_tui_terminal_width()
        compact = self._use_minimal_tui_chrome(width=width)
        if self._voice_recording:
            if compact:
                return [("class:voice-status-recording", " ● REC ")]
            return [("class:voice-status-recording", " ● REC  Ctrl+B to stop ")]
        if self._voice_processing:
            if compact:
                return [("class:voice-status", " ◉ STT ")]
            return [("class:voice-status", " ◉ Transcribing... ")]
        if compact:
            return [("class:voice-status", " 🎤 Ctrl+B ")]
        cont = " | Continuous" if self._voice_continuous else ""
        return [("class:voice-status", f" 🎤 Voice mode{cont}  —  Ctrl+B to record ")]

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

    def _execute_pending_input(self, user_input: Any, *, app=None) -> bool:
        """Execute one queued prompt/command using the same path as the interactive loop."""
        if not user_input:
            return False

        should_emit_scrollback = self._should_emit_scrollback_output()
        submit_images = []
        if isinstance(user_input, tuple):
            user_input, submit_images = user_input

        _file_drop = _detect_file_drop(user_input) if isinstance(user_input, str) else None
        if _file_drop:
            _drop_path = _file_drop["path"]
            _remainder = _file_drop["remainder"]
            if _file_drop["is_image"]:
                submit_images.append(_drop_path)
                user_input = _remainder or f"[User attached image: {_drop_path.name}]"
                if should_emit_scrollback:
                    _cprint(f"  📎 Auto-attached image: {_drop_path.name}")
            else:
                if should_emit_scrollback:
                    _cprint(f"  📄 Detected file: {_drop_path.name}")
                user_input = f"[User attached file: {_drop_path}]"
                if _remainder:
                    user_input += f"\n{_remainder}"

        if not _file_drop and isinstance(user_input, str) and _looks_like_slash_command(user_input):
            if should_emit_scrollback:
                _cprint(f"\n>️  {user_input}")
            logger.info("CLI command executed: %s", user_input)
            if not self.process_command(user_input):
                self._should_exit = True
                try:
                    if app and getattr(app, "is_running", False):
                        app.exit()
                except Exception:
                    pass
            return False

        import re as _re

        _paste_ref_re = _re.compile(r'\[Pasted text #\d+: \d+ lines \u2192 (.+?)\]')
        paste_refs = list(_paste_ref_re.finditer(user_input)) if isinstance(user_input, str) else []
        if paste_refs:
            def _expand_ref(match):
                path = Path(match.group(1))
                return path.read_text(encoding="utf-8") if path.exists() else match.group(0)

            expanded = _paste_ref_re.sub(_expand_ref, user_input)
            total_lines = expanded.count('\n') + 1
            n_pastes = len(paste_refs)
            if should_emit_scrollback:
                _user_bar = f"[#34D399]{'~' * 40}[/]"
                print()
                ChatConsole().print(_user_bar)
                split_parts = _paste_ref_re.split(user_input)
                visible_user_text = " ".join(
                    split_parts[i].strip() for i in range(0, len(split_parts), 2) if split_parts[i].strip()
                )
                if visible_user_text:
                    ChatConsole().print(
                        f"[bold {_accent_hex()}]\u25cf[/] [bold]{_escape(visible_user_text)}[/] "
                        f"[dim]({n_pastes} pasted block{'s' if n_pastes > 1 else ''}, {total_lines} lines total)[/]"
                    )
                else:
                    ChatConsole().print(
                        f"[bold {_accent_hex()}]\u25cf[/] [bold]{_escape(f'[Pasted text: {total_lines} lines]')}[/]"
                    )
            user_input = expanded
        else:
            if should_emit_scrollback:
                _user_bar = f"[#34D399]{'~' * 40}[/]"
                if isinstance(user_input, str) and '\n' in user_input:
                    first_line = user_input.split('\n')[0]
                    line_count = user_input.count('\n') + 1
                    print()
                    ChatConsole().print(_user_bar)
                    ChatConsole().print(
                        f"[bold {_accent_hex()}]●[/] [bold]{_escape(first_line)}[/] "
                        f"[dim](+{line_count - 1} lines)[/]"
                    )
                else:
                    print()
                    ChatConsole().print(_user_bar)
                    ChatConsole().print(f"[bold {_accent_hex()}]●[/] [bold]{_escape(str(user_input))}[/]")

        if submit_images and should_emit_scrollback:
            n = len(submit_images)
            _cprint(f"  {_DIM}📎 {n} image{'s' if n > 1 else ''} attached{_RST}")

        self._agent_running = True
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

        _sanitized = str(user_input).encode('ascii', errors='replace').decode('ascii')
        logger.info(
            "User input received: %s (images: %d)",
            _sanitized[:100] + "..." if len(_sanitized) > 100 else _sanitized,
            len(submit_images) if submit_images else 0,
        )

        try:
            self.chat(user_input, images=submit_images or None)
        finally:
            self._agent_running = False
            self._spinner_text = ""
            self._tool_start_time = 0.0
            self._current_tool_name = ""
            self._last_scrollback_tool = ""

            if app is not None:
                try:
                    app.invalidate()
                except Exception:
                    pass

            if self._voice_mode and self._voice_continuous and not self._voice_recording:
                def _restart_recording():
                    try:
                        self._voice_start_recording()
                        if app is not None:
                            app.invalidate()
                    except Exception as exc:
                        _cprint(f"{_DIM}Voice auto-restart failed: {exc}{_RST}")

                threading.Thread(target=_restart_recording, daemon=True).start()

            try:
                from tools.process_registry import process_registry
                while not process_registry.completion_queue.empty():
                    evt = process_registry.completion_queue.get_nowait()
                    _evt_sid = evt.get("session_id", "")
                    if evt.get("type") == "completion" and process_registry.is_completion_consumed(_evt_sid):
                        continue
                    _synth = _format_process_notification(evt)
                    if _synth:
                        self._pending_input.put(_synth)
            except Exception:
                pass

        return True

    @staticmethod
    def _use_ascii_fallback() -> bool:
        """Detect terminals that may not render emoji correctly (e.g. legacy conhost)."""
        import sys, os
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
        """Build middle status bar section: supervisor's model + supervisor scene.

        The "memory model" is the supervisor's working LLM (configured via
        memory.llm.* in config.yaml).  Its marquee and context% reflect the
        **supervisor's** activity, not the agent's memory-tool calls.

        - Marquee ON  → supervisor scene is NOT "idle" (planning / learning /
          memory / execution)
        - Context %   → supervisor's own MemAI token accumulator, reported via
          /ui/state.mem_usage
        """
        frags: list[tuple[str, str]] = []
        ascii_mode = self._use_ascii_fallback_cached()

        # ── Fetch supervisor state once (cached 5s) ──
        scene = "idle"
        sup_active = False
        mem_usage: Dict[str, Any] = {}
        try:
            supervisor_snapshot = _supervisor_activity_snapshot_view(self)
            scene = str(supervisor_snapshot.get("scene") or "idle").strip() or "idle"
            sup_active = bool(supervisor_snapshot.get("is_active"))
            mem_usage = dict(supervisor_snapshot.get("mem_usage") or {})
        except Exception:
            pass

        # ── Supervisor's model (memory.llm) ──
        try:
            config = self._cached_load_config()
            memory_config = config.get("memory", {})
            mem_llm = memory_config.get("llm", {})
            mem_model = mem_llm.get("model", None)
            if mem_model:
                mem_short = mem_model.split("/")[-1] if "/" in mem_model else mem_model
                if mem_short.endswith(".gguf"):
                    mem_short = mem_short[:-5]
                if len(mem_short) > 20:
                    mem_short = mem_short[:17] + "..."
            else:
                mem_provider = mem_llm.get("provider", "") or "Mem"
                mem_short = mem_provider if len(mem_provider) <= 12 else mem_provider[:9] + "..."

            icon = "[M]" if ascii_mode else "🧠"
            frags.append(("bg:#1a1a2e #6B7280", icon))

            # Marquee: supervisor's model lights up when supervisor is working
            mem_color = "#7CC9A0"  # mint green
            if sup_active and len(mem_short) > 0:
                import time
                marquee_speed = 9
                marquee_pos = int(time.time() * marquee_speed) % (len(mem_short) + 4)
                for i, char in enumerate(mem_short):
                    if i == marquee_pos - 1:
                        frags.append(("bg:#1a1a2e #FFFFFF bold", char))
                    elif i == marquee_pos:
                        frags.append(("bg:#1a1a2e #C0FFC0 bold", char))
                    elif i == marquee_pos + 1:
                        frags.append(("bg:#1a1a2e #80C080 bold", char))
                    else:
                        frags.append((f"bg:#1a1a2e {mem_color} bold", char))
            else:
                frags.append((f"bg:#1a1a2e {mem_color} bold", mem_short))

            # Context %: from supervisor's own token accumulator
            mem_pct = mem_usage.get("context_percent") if mem_usage else None
            if mem_pct is not None and mem_pct > 0:
                if mem_pct >= 80:
                    mem_pct_color = "#FF6B6B"
                elif mem_pct >= 60:
                    mem_pct_color = "#FFD700"
                else:
                    mem_pct_color = "#8FBC8F"
                frags.append((f"bg:#1a1a2e {mem_pct_color} bold", f" {mem_pct}%"))
            else:
                frags.append(("bg:#1a1a2e #6B7280", " --"))
        except Exception:
            pass

        # ── Supervisor scene (reuse `sup` from memory-model section above) ──
        # Per architectural baseline §3.4/§3.6, the supervisor (API-B) only
        # manages the task list and runs endogenous drive — it never
        # executes learning or body-upgrade code.  Scenes that imply
        # execution (`learning`, `execution`) are API-A territory and are
        # not legal values for the supervisor's `scene` field; they remain
        # in the map below as defensive fallbacks (the Agent may surface
        # its own scene in the future) but the supervisor will never
        # emit them.
        try:
            if scene:
                if ascii_mode:
                    scene_icons = {
                        "idle": "(-)", "planning": "(?)", "memory": "(M)",
                        "drive": "(D)", "handoff": "(>)", "maintenance": "(M)",
                        "body_switch": "(S)",
                    }
                else:
                    scene_icons = {
                        "idle": "💤", "planning": "🤔", "memory": "🧠",
                        "drive": "💡", "handoff": "📤", "maintenance": "🔧",
                        "body_switch": "🔄",
                    }
                scene_colors = {
                    "idle": "#8B8682", "planning": "#E07362", "memory": "#7CC9A0",
                    "drive": "#E2B04A", "handoff": "#A78BFA", "maintenance": "#60A5FA",
                    "body_switch": "#C084FC",
                }
                icon = scene_icons.get(scene, "●")
                color = scene_colors.get(scene, "#9CA3AF")
                if frags:
                    frags.append(("bg:#1a1a2e #4B5563", " · "))
                frags.append((f"bg:#1a1a2e {color}", icon))

                # compact scene label
                scene_labels = {
                    "idle": "辅助", "planning": "规划", "memory": "记忆",
                    "drive": "驱动", "handoff": "交接", "maintenance": "维护",
                    "body_switch": "切换",
                }
                label = scene_labels.get(scene, scene)
                frags.append((f"bg:#1a1a2e {color}", label))

                # error indicator
                error_count = sup.get("error_count", 0)
                if error_count > 0:
                    frags.append(("bg:#1a1a2e #FF6B6B bold", f" !{error_count}"))
            else:
                # Supervisor not reachable — show offline indicator
                if frags:
                    frags.append(("bg:#1a1a2e #4B5563", " · "))
                icon = "[x]" if ascii_mode else "⚙️"
                frags.append(("bg:#1a1a2e #6B7280", icon))
                frags.append(("bg:#1a1a2e #6B7280", "离线"))
        except Exception:
            pass

        try:
            subagent = self._get_subagent_observability_snapshot()
            if subagent.get("active"):
                if frags:
                    frags.append(("bg:#1a1a2e #4B5563", " · "))
                icon = "[SA]" if ascii_mode else "🧩"
                frags.append(("bg:#1a1a2e #F59E0B", icon))
                frags.append(("bg:#1a1a2e #F59E0B bold", f" {subagent.get('counts_label', '0')}"))
                compact_preview = str(subagent.get("compact_preview") or "").strip()
                if compact_preview:
                    frags.append(("bg:#1a1a2e #94A3B8", f" {compact_preview}"))
        except Exception:
            pass

        return frags

    def _get_git_status_simple(self) -> list[tuple[str, str]]:
        """简洁的Git状态显示，返回片段列表，支持高亮数字。

        Git status is cached for 60 s and runs in a background thread so
        it NEVER blocks the UI thread — spawning ``git status`` as a
        subprocess is the #2 hot-path bottleneck after ``load_config()``,
        and on Windows each subprocess spawn is especially expensive.
        """
        import time
        import threading

        now = time.time()
        # Serve from cache while it's fresh
        if self._git_status_cache is not None and (now - self._git_status_cache_ts) < 60.0:
            return self._git_status_cache  # type: ignore[return-value]

        # Prevent concurrent background refreshes
        if getattr(self, "_git_status_refreshing", False):
            return self._git_status_cache or []
        self._git_status_refreshing = True

        def _refresh():
            try:
                from VoidCube_cli.git_display import GitDisplay
                git_display = GitDisplay()
                status = git_display.runner.get_status()

                if not status.is_repo:
                    self._git_status_cache = []
                    self._git_status_cache_ts = time.time()
                    return

                frags = []

                # Git <branch>
                frags.append(("bg:#1a1a2e #58A6FF", "Git "))
                frags.append(("bg:#1a1a2e #9CA3AF", "<"))
                frags.append(("bg:#1a1a2e #58A6FF bold", status.branch))
                frags.append(("bg:#1a1a2e #9CA3AF", ">"))

                # 暂存
                if status.staged:
                    frags.append(("bg:#1a1a2e #9CA3AF", "  暂存 "))
                    frags.append(("bg:#1a1a2e #FFFFFF bold", str(len(status.staged))))

                # 更改
                changes = len(status.modified) + len(status.deleted) + len(status.untracked)
                if changes > 0:
                    frags.append(("bg:#1a1a2e #9CA3AF", "  更改 "))
                    frags.append(("bg:#1a1a2e #FFFFFF bold", str(changes)))

                # 检查远程 — use GitRunner for PowerShell/Win compat
                try:
                    code, out, _ = git_display.runner._run(["remote"])
                    if code == 0 and out.strip():
                        remotes = out.strip().splitlines()
                        remote_str = ",".join(remotes)
                        frags.append(("bg:#1a1a2e #9CA3AF", "  <"))
                        frags.append(("bg:#1a1a2e #8B949E", remote_str))
                        frags.append(("bg:#1a1a2e #9CA3AF", ">"))
                except:
                    pass

                self._git_status_cache = frags
                self._git_status_cache_ts = time.time()
            except Exception:
                self._git_status_cache = []
                self._git_status_cache_ts = time.time()
            finally:
                self._git_status_refreshing = False
                # Cache updated in-place; the next status-bar render will
                # pick up the new fragments without any explicit invalidate.
                # (Do NOT call self._invalidate() from a daemon thread —
                #  prompt_toolkit's Application is not thread-safe.)

        threading.Thread(target=_refresh, daemon=True, name="git-status-refresh").start()
        # Return stale cache while refresh runs; empty list on first call
        return self._git_status_cache or []
    
    def _get_status_bar_fragments(self):
        if not self._status_bar_visible or getattr(self, '_model_picker_state', None):
            return []
        try:
            snapshot = self._get_status_bar_snapshot()
            width = self._get_tui_terminal_width()
            
            percent = snapshot["context_percent"]
            
            # Simplified format: model name + percentage with color
            # Color scheme: 0-60% green, 60-80% yellow, 80%+ red
            if percent is not None:
                if percent >= 80:
                    percent_color = "#FF6B6B"
                elif percent >= 60:
                    percent_color = "#FFD700"
                else:
                    percent_color = "#8FBC8F"
                percent_label = f"{percent}%"
            else:
                percent_color = "#8B8682"
                percent_label = "--"
            
            # Check if agent is active (thinking, running tool, streaming, or command)
            is_active = (
                getattr(self, '_spinner_text', '') != '' or  # thinking
                getattr(self, '_tool_start_time', 0) > 0 or  # tool running
                getattr(self, '_command_running', False) or  # command running
                self._stream_render_state.started           # streaming
            )
            
            # Marquee effect when agent is active - 3 character highlighting with white/gray
            model_name = snapshot["model_short"]
            
            # 构建左侧内容（模型名和百分比）
            left_frags = []
            
            if is_active and len(model_name) > 0:
                import time
                # Create marquee effect: highlight 3 characters at a time
                # that moves through the model name
                marquee_speed = 9  # characters per second (3x faster)
                marquee_pos = int(time.time() * marquee_speed) % (len(model_name) + 4)
                
                # Build model name with marquee effect
                for i, char in enumerate(model_name):
                    if i == marquee_pos - 1:
                        # Leading highlight - bright white
                        left_frags.append(('bg:#1a1a2e #FFFFFF bold', char))
                    elif i == marquee_pos:
                        # Main highlight - light gray
                        left_frags.append(('bg:#1a1a2e #C0C0C0 bold', char))
                    elif i == marquee_pos + 1:
                        # Trailing highlight - medium gray
                        left_frags.append(('bg:#1a1a2e #808080 bold', char))
                    else:
                        # Normal characters - dark blue (original color)
                        left_frags.append(('bg:#1a1a2e #1E40AF bold', char))
                
                # Add trailing space for separator
                left_frags.append(("class:status-bar", "  "))
                left_frags.append((f'bg:#1a1a2e {percent_color} bold', percent_label))
            else:
                model_color = "#1E40AF"  # Original dark blue color
                left_frags = [
                    (f'bg:#1a1a2e {model_color} bold', model_name),
                    ("class:status-bar", "  "),
                    (f'bg:#1a1a2e {percent_color} bold', percent_label),
                ]
            
            # 获取中间片段：记忆模型 + 监督者状态
            middle_frags = self._get_middle_status_fragments(is_active=is_active)

            # 获取 Git 状态片段
            git_frags = self._get_git_status_simple()

            if git_frags:
                # 计算各段宽度
                left_width = sum(self._status_bar_display_width(text) for _, text in left_frags)
                mid_width = sum(self._status_bar_display_width(text) for _, text in middle_frags) if middle_frags else 0
                git_width = sum(self._status_bar_display_width(text) for _, text in git_frags)

                # 布局：left | spacer | middle | spacer | git
                available = width - left_width - git_width - 6  # 6 = margins
                if mid_width > 0 and available > 20:
                    mid_pad_left = max(1, (available - mid_width) // 2)
                    mid_pad_right = max(1, available - mid_width - mid_pad_left)
                    frags = left_frags.copy()
                    frags.append(("class:status-bar", " " * mid_pad_left))
                    frags.extend(middle_frags)
                    frags.append(("class:status-bar", " " * mid_pad_right))
                    frags.extend(git_frags)
                elif mid_width > 0 and available > 0:
                    frags = left_frags.copy()
                    frags.append(("class:status-bar", "  "))
                    frags.extend(middle_frags)
                    frags.append(("class:status-bar", "  "))
                    frags.extend(git_frags)
                else:
                    padding_width = width - left_width - git_width - 4
                    if padding_width > 0:
                        frags = left_frags.copy()
                        frags.append(("class:status-bar", " " * padding_width))
                        frags.extend(git_frags)
                    else:
                        frags = left_frags.copy()
                        frags.append(("class:status-bar", "  --  "))
                        frags.extend(git_frags)

                total_width = sum(self._status_bar_display_width(text) for _, text in frags)
                if total_width > width:
                    plain_text = "".join(text for _, text in frags)
                    trimmed = self._trim_status_bar_text(plain_text, width)
                    return [("class:status-bar", trimmed)]
                return frags
            else:
                # 没有Git内容：left | spacer | middle
                all_frags = left_frags.copy()
                if middle_frags:
                    mid_width = sum(self._status_bar_display_width(text) for _, text in middle_frags)
                    left_w = sum(self._status_bar_display_width(text) for _, text in left_frags)
                    pad = width - left_w - mid_width - 4
                    if pad > 4:
                        all_frags.append(("class:status-bar", " " * (pad // 2)))
                        all_frags.extend(middle_frags)
                        all_frags.append(("class:status-bar", " " * (pad - pad // 2)))
                    elif pad > 0:
                        all_frags.append(("class:status-bar", "  "))
                        all_frags.extend(middle_frags)
                total_width = sum(self._status_bar_display_width(text) for _, text in all_frags)
                if total_width > width:
                    plain_text = "".join(text for _, text in all_frags)
                    trimmed = self._trim_status_bar_text(plain_text, width)
                    return [("class:status-bar", trimmed)]
                return all_frags
        except Exception:
            return [("class:status-bar", f" {self._build_status_bar_text()} ")]

    def _normalize_model_for_provider(self, resolved_provider: str) -> bool:
        """Normalize provider-specific model IDs and routing."""
        current_model = (self.model or "").strip()
        changed = False

        try:
            from VoidCube_app.model_normalization import (
                AGGREGATOR_PROVIDERS,
                normalize_model_for_provider,
            )

            if resolved_provider not in AGGREGATOR_PROVIDERS:
                normalized_model = normalize_model_for_provider(current_model, resolved_provider)
                if normalized_model and normalized_model != current_model:
                    if not self._model_is_default:
                        self.console.print(
                            f"[yellow]⚠️  Normalized model '{current_model}' to '{normalized_model}' for {resolved_provider}.[/]"
                        )
                    self.model = normalized_model
                    current_model = normalized_model
                    changed = True
        except Exception:
            pass

        return changed

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
        from VoidCube_app.runtime_provider import (
            resolve_runtime_provider,
            format_runtime_provider_error,
        )

        try:
            runtime = resolve_runtime_provider(
                requested=self.requested_provider,
                explicit_api_key=self._explicit_api_key,
                explicit_base_url=self._explicit_base_url,
            )
        except Exception as exc:
            message = format_runtime_provider_error(exc)
            try:
                ChatConsole().print(f"[bold red]{message}[/]")
            except Exception:
                print(message)
            return False

        api_key = runtime.get("api_key")
        base_url = runtime.get("base_url")
        resolved_provider = runtime.get("provider", "openrouter")
        resolved_acp_command = runtime.get("command")
        resolved_acp_args = list(runtime.get("args") or [])
        resolved_credential_pool = runtime.get("credential_pool")
        if not isinstance(api_key, str) or not api_key:
            # Custom / local endpoints (llama.cpp, ollama, vLLM, etc.) often
            # don't require authentication.  When a base_url IS configured but
            # no API key was found, use a placeholder so the OpenAI SDK
            # doesn't reject the request and local servers just ignore it.
            _source = runtime.get("source", "")
            _has_custom_base = isinstance(base_url, str) and base_url and "openrouter.ai" not in base_url
            if _has_custom_base:
                api_key = "no-key-required"
                logger.debug(
                    "No API key for custom endpoint %s (source=%s), "
                    "using placeholder — local servers typically ignore auth",
                    base_url, _source,
                )
            else:
                print("\n⚠️  Provider resolver returned an empty API key. "
                      "Set OPENROUTER_API_KEY or run: /api")
                return False
        if not isinstance(base_url, str) or not base_url:
            print("\n⚠️  Provider resolver returned an empty base URL. "
                  "Check your provider config or run: /api")
            return False

        credentials_changed = api_key != self.api_key or base_url != self.base_url
        routing_changed = (
            resolved_provider != self.provider
            or resolved_acp_command != self.acp_command
            or resolved_acp_args != self.acp_args
        )
        self.provider = resolved_provider
        self.acp_command = resolved_acp_command
        self.acp_args = resolved_acp_args
        self._credential_pool = resolved_credential_pool
        self._provider_source = runtime.get("source")
        self.api_key = api_key
        self.base_url = base_url

        # When a custom_provider entry carries an explicit `model` field,
        # use it as the effective model name.  Without this, running
        # `VoidCube chat --model <provider-name>` sends the provider name
        # (e.g. "my-provider") as the model string to the API instead of
        # the configured model (e.g. "qwen3.6-plus"), causing 400 errors.
        runtime_model = runtime.get("model")
        if runtime_model and isinstance(runtime_model, str):
            self.model = runtime_model

        if not self.model:
            print("\n⚠️  No model selected for the active provider. Run: /model")
            return False

        # Normalize model IDs to the selected provider's request format.
        model_changed = self._normalize_model_for_provider(resolved_provider)

        # AIAgent/OpenAI client holds auth at init time, so rebuild if key,
        # routing, or the effective model changed.
        if (credentials_changed or routing_changed or model_changed) and self.agent is not None:
            self.agent = None
            self._active_agent_route_signature = None

        return True

    def _resolve_turn_agent_config(self, user_message: str) -> dict:
        """Resolve model/runtime overrides for a single user turn."""
        from agent.smart_model_routing import resolve_turn_route
        from VoidCube_app.models import resolve_fast_mode_overrides

        route = resolve_turn_route(
            user_message,
            self._smart_model_routing,
            {
                "model": self.model,
                "api_key": self.api_key,
                "base_url": self.base_url,
                "provider": self.provider,
                "command": self.acp_command,
                "args": list(self.acp_args or []),
                "credential_pool": getattr(self, "_credential_pool", None),
            },
        )

        service_tier = getattr(self, "service_tier", None)
        if not service_tier:
            route["request_overrides"] = None
            return route

        try:
            overrides = resolve_fast_mode_overrides(route.get("model"))
        except Exception:
            overrides = None
        route["request_overrides"] = overrides
        return route

    def _init_agent(self, *, model_override: Optional[str] = None, runtime_override: Optional[dict] = None, route_label: Optional[str] = None, request_overrides: dict | None = None) -> bool:
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
        if self._resumed and self._session_db and not self.conversation_history:
            hydration, loaded_now = self._hydrate_resumed_session()
            if hydration.status is SessionHydrationStatus.MISSING:
                if loaded_now:
                    _cprint(f"\033[1;31mSession not found: {self.session_id}{_RST}")
                    _cprint(f"{_DIM}Use a session ID from a previous CLI run (VoidCube sessions list).{_RST}")
                return False
            if loaded_now and hydration.status is SessionHydrationStatus.READY:
                restored = hydration.conversation_history
                msg_count = len([m for m in restored if m.get("role") == "user"])
                title_part = ""
                if hydration.metadata and hydration.metadata.get("title"):
                    title_part = f" \"{hydration.metadata['title']}\""
                ChatConsole().print(
                    f"[bold {_accent_hex()}]↻ {t('prompts.resumed_session', default='Resumed session')}[/] "
                    f"[bold]{_escape(self.session_id)}[/]"
                    f"[bold {_accent_hex()}]{_escape(title_part)}[/] "
                    f"({msg_count} {t('prompts.user_messages', default='user message')}{'s' if msg_count != 1 else ''}, {len(restored)} {t('prompts.total_messages', default='total messages')})"
                )
            elif loaded_now:
                ChatConsole().print(
                    f"[bold {_accent_hex()}]Session {_escape(self.session_id)} found but has no messages. Starting fresh.[/]"
                )
        
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
            self.agent = _get_AIAgent()(
                model=effective_model,
                api_key=runtime.get("api_key"),
                base_url=runtime.get("base_url"),
                provider=runtime.get("provider"),
                acp_command=runtime.get("command"),
                acp_args=runtime.get("args"),
                credential_pool=runtime.get("credential_pool"),
                max_iterations=self.max_turns,
                enabled_toolsets=self.enabled_toolsets,
                verbose_logging=self.verbose,
                quiet_mode=not self.verbose,
                ephemeral_system_prompt=self.system_prompt if self.system_prompt else None,
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
                clarification_sink=self._clarification_sink,
                reasoning_callback=self._current_reasoning_callback(),

                fallback_model=self._fallback_model,
                thinking_callback=self._on_thinking,
                checkpoints_enabled=self.checkpoints_enabled,
                checkpoint_max_snapshots=self.checkpoint_max_snapshots,
                pass_session_id=self.pass_session_id,
                tool_event_sink=self._on_tool_event,
                stream_delta_callback=self._stream_delta if self.streaming_enabled else None,
                tool_gen_callback=self._on_tool_gen_start if self.streaming_enabled else None,
            )
            # Store reference for atexit memory provider shutdown
            global _active_agent_ref
            _active_agent_ref = self.agent
            # Route agent status output through prompt_toolkit so ANSI escape
            # sequences aren't garbled by patch_stdout's StdoutProxy (#2262).
            self.agent._print_fn = (  # type: ignore[assignment]
                self._quiet_autonomous_component_cprint
                if self._is_embedded_autonomous_component()
                else _cprint
            )
            self._active_agent_route_signature = (
                effective_model,
                runtime.get("provider"),
                runtime.get("base_url"),
                runtime.get("command"),
                tuple(runtime.get("args") or ()),
            )

            if self._pending_title and self._session_db:
                try:
                    title_result = set_session_title(
                        repository=self._session_db,
                        session_id=self.session_id,
                        raw_title=self._pending_title,
                    )
                    if title_result.status is SessionTitleStatus.UPDATED:
                        _cprint(f"  Session title applied: {title_result.title}")
                    elif title_result.status is SessionTitleStatus.CONFLICT:
                        _cprint(f"  Could not apply pending title: {title_result.error}")
                    else:
                        _cprint("  Could not apply pending title: session is not persisted")
                except Exception as e:
                    _cprint(f"  Could not apply pending title: {e}")
                self._pending_title = None

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
            tools = _get_tool_definitions(enabled_toolsets=self.enabled_toolsets, quiet_mode=True)
            
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
        
        # Show tool availability warnings if any tools are disabled
        self._show_tool_availability_warnings()

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

    def _hydrate_resumed_session(self) -> tuple[SessionHydration, bool]:
        """Return one cached hydration result for the selected session."""
        hydration = self._session_hydration
        loaded_now = hydration is None
        if hydration is None:
            hydration = hydrate_session(
                repository=self._session_db,
                session_id=self.session_id,
            )
            self._session_hydration = hydration
        if hydration.status is SessionHydrationStatus.READY:
            self.conversation_history = list(hydration.conversation_history)
        return hydration, loaded_now

    def _preload_resumed_session(self) -> bool:
        """Load a resumed session's history from the DB early (before first chat).

        Called from run() so the conversation history is available for display
        before the user sends their first message.  Sets
        ``self.conversation_history`` and prints the one-liner status.  Returns
        True if history was loaded, False otherwise.

        The corresponding block in ``_init_agent()`` reuses the cached outcome.
        """
        if not self._resumed or not self._session_db:
            return False

        hydration, _ = self._hydrate_resumed_session()
        if hydration.status is SessionHydrationStatus.MISSING:
            self.console.print(
                f"[bold red]Session not found: {self.session_id}[/]"
            )
            self.console.print(
                "[dim]Use a session ID from a previous CLI run "
                "(VoidCube sessions list).[/]"
            )
            return False

        if hydration.status is SessionHydrationStatus.READY:
            restored = hydration.conversation_history
            msg_count = len([m for m in restored if m.get("role") == "user"])
            title_part = ""
            if hydration.metadata and hydration.metadata.get("title"):
                title_part = f' "{hydration.metadata["title"]}"'
            accent_color = _accent_hex()
            self.console.print(
                f"[{accent_color}]↻ {t('prompts.resumed_session', default='Resumed session')} [bold]{self.session_id}[/bold]"
                f"{title_part} "
                f"({msg_count} {t('prompts.user_messages', default='user message')}{'s' if msg_count != 1 else ''}, "
                f"{len(restored)} {t('prompts.total_messages', default='total messages')})[/]"
            )
        else:
            accent_color = _accent_hex()
            self.console.print(
                f"[{accent_color}]Session {self.session_id} found but has no "
                f"messages. Starting fresh.[/]"
            )
            return False

        return True

    def _display_resumed_history(self):
        """Render a compact recap of previous conversation messages.

        Uses Rich markup with dim/muted styling so the recap is visually
        distinct from the active conversation.  Caps the display at the
        last ``MAX_DISPLAY_EXCHANGES`` user/assistant exchanges and shows
        an indicator for earlier hidden messages.
        """
        if not self.conversation_history:
            return

        # Check config: resume_display setting
        if self.resume_display == "minimal":
            return

        MAX_DISPLAY_EXCHANGES = 10   # max user+assistant pairs to show
        MAX_USER_LEN = 300           # truncate user messages
        MAX_ASST_LEN = 200           # truncate assistant text
        MAX_ASST_LINES = 3           # max lines of assistant text

        def _strip_ansi_codes(text: str) -> str:
            """Remove ANSI escape sequences from text to prevent display corruption."""
            import re
            if not text:
                return text
            ansi_escape = re.compile(
                r"\x1b"
                r"(?:"
                r"\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"     # CSI sequence
                r"|\][\s\S]*?(?:\x07|\x1b\\)"                  # OSC (BEL or ST terminator)
                r"|[PX^_][\s\S]*?(?:\x1b\\)"                   # DCS/SOS/PM/APC strings
                r"|[\x20-\x2f]+[\x30-\x7e]"                    # nF escape sequences
                r"|[\x30-\x7e]"                                 # Fp/Fe/Fs single-byte
                r")"
                r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"   # 8-bit CSI
                r"|\x9d[\s\S]*?(?:\x07|\x9c)"                   # 8-bit OSC
                r"|[\x80-\x9f]",                                # Other 8-bit C1 controls
                re.DOTALL,
            )
            return ansi_escape.sub("", text)

        def _strip_reasoning(text: str) -> str:
            """Remove <REASONING_SCRATCHPAD>...</REASONING_SCRATCHPAD> blocks
            from displayed text (reasoning model internal thoughts)."""
            import re
            cleaned = re.sub(
                r"<REASONING_SCRATCHPAD>.*?</REASONING_SCRATCHPAD>\s*",
                "", text, flags=re.DOTALL,
            )
            # Also strip unclosed reasoning tags at the end
            cleaned = re.sub(
                r"<REASONING_SCRATCHPAD>.*$",
                "", cleaned, flags=re.DOTALL,
            )
            return cleaned.strip()

        # Collect displayable entries (skip system, tool-result messages)
        entries = []  # list of (role, display_text)
        _last_asst_idx = None       # index of last assistant entry
        _last_asst_full = None      # un-truncated display text for last assistant
        first_timestamp = None       # timestamp of first message
        last_timestamp = None        # timestamp of last displayed message
        
        for msg in self.conversation_history:
            role = msg.get("role", "")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls") or []
            timestamp = msg.get("timestamp")
            
            if not first_timestamp:
                first_timestamp = timestamp

            if role == "system":
                continue
            if role == "tool":
                continue

            if role == "user":
                text = "" if content is None else str(content)
                # Handle multimodal content (list of dicts)
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            parts.append(part.get("text", ""))
                        elif isinstance(part, dict) and part.get("type") == "image_url":
                            parts.append("[image]")
                    text = " ".join(parts)
                # Strip ANSI escape codes to prevent display corruption
                text = _strip_ansi_codes(text)
                if len(text) > MAX_USER_LEN:
                    text = text[:MAX_USER_LEN] + "..."
                entries.append(("user", text, timestamp))

            elif role == "assistant":
                text = "" if content is None else str(content)
                # Strip ANSI escape codes to prevent display corruption
                text = _strip_ansi_codes(text)
                text = _strip_reasoning(text)
                parts = []
                full_parts = []  # un-truncated version
                if text:
                    full_parts.append(text)
                    lines = text.splitlines()
                    if len(lines) > MAX_ASST_LINES:
                        text = "\n".join(lines[:MAX_ASST_LINES]) + " ..."
                    if len(text) > MAX_ASST_LEN:
                        text = text[:MAX_ASST_LEN] + "..."
                    parts.append(text)
                if tool_calls:
                    tc_count = len(tool_calls)
                    # Extract tool names
                    names = []
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "unknown") if isinstance(fn, dict) else "unknown"
                        if name not in names:
                            names.append(name)
                    names_str = ", ".join(names[:4])
                    if len(names) > 4:
                        names_str += ", ..."
                    noun = "call" if tc_count == 1 else "calls"
                    tc_summary = f"[{tc_count} tool {noun}: {names_str}]"
                    parts.append(tc_summary)
                    full_parts.append(tc_summary)
                if not parts:
                    # Skip pure-reasoning messages that have no visible output
                    continue
                entries.append(("assistant", " ".join(parts), timestamp))
                _last_asst_idx = len(entries) - 1
                _last_asst_full = " ".join(full_parts)
                last_timestamp = timestamp

        if not entries:
            return

        # Determine if we need to truncate
        skipped = 0
        if len(entries) > MAX_DISPLAY_EXCHANGES * 2:
            skipped = len(entries) - MAX_DISPLAY_EXCHANGES * 2
            # Get timestamp of first displayed message
            if entries:
                last_timestamp = entries[0][2]
            entries = entries[skipped:]

        # Replace last assistant entry with full (un-truncated) text
        # so the user can see where they left off without wasting tokens.
        if _last_asst_idx is not None and _last_asst_full:
            adj_idx = _last_asst_idx - skipped
            if 0 <= adj_idx < len(entries):
                # Keep the timestamp from the original entry
                original_timestamp = entries[adj_idx][2] if len(entries[adj_idx]) > 2 else None
                entries[adj_idx] = ("assistant_last", _last_asst_full, original_timestamp)

        # Build the display using Rich
        from rich.panel import Panel
        from rich.text import Text

        _history_text_c = "#FFF8DC"
        _session_label_c = "#DAA520"
        _session_border_c = "#8B8682"
        _assistant_label_c = "#8FBC8F"

        # Simple text output without borders to avoid wrapping issues
        print()
        _term_width = shutil.get_terminal_size((80, 24)).columns
        _line_fill = _term_width - 2  # Full width minus 2 for margin
        _cprint(f"\033[38;2;218;165;32m── {t('prompts.previous_conversation', default='Previous Conversation')}{'─' * ((_term_width - 2) - 2 - len(t('prompts.previous_conversation', default='Previous Conversation')))}{_RST}")
        
        if skipped and first_timestamp:
            import datetime
            first_time = datetime.datetime.fromtimestamp(first_timestamp).strftime("%m-%d %H:%M")
            last_time = datetime.datetime.fromtimestamp(last_timestamp).strftime("%m-%d %H:%M")
            _cprint(f"     ... {skipped} {t('prompts.earlier_messages', default='earlier messages')} ({first_time} - {last_time}) ...")
        elif skipped:
            _cprint(f"     ... {skipped} {t('prompts.earlier_messages', default='earlier messages')} ...")
            print()

        for i, (role, text, timestamp) in enumerate(entries):
            if timestamp:
                import datetime
                time_str = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M")
            else:
                time_str = ""
            
            if role == "user":
                _cprint(f"  ● {t('prompts.you', default='You')}{' ' + time_str if time_str else ''}: {text}")
            elif role == "assistant_last":
                _cprint(f"  ◆ {t('prompts.voidcube', default='Voidcube')}{' ' + time_str if time_str else ''}: {text}")
            else:
                _cprint(f"  ◆ {t('prompts.voidcube', default='Voidcube')}{' ' + time_str if time_str else ''}: {text}")
            if i < len(entries) - 1:
                print()
        
        print()
        _cprint(f"\033[38;2;218;165;32m{'─' * (_term_width - 2)}{_RST}")

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
                    vision_analyze_tool(image_url=str(img_path), user_prompt=analysis_prompt)
                )
                result = _json.loads(result_json)
                if result.get("success"):
                    description = result.get("analysis", "")
                    enriched_parts.append(
                        f"[The user attached an image. Here's what it contains:\n{description}]\n"
                        f"[If you need a closer look, use vision_analyze with "
                        f"image_url: {img_path}]"
                    )
                    if announce:
                        _cprint(f"  {_DIM}✓ image analyzed{_RST}")
                else:
                    enriched_parts.append(
                        f"[The user attached an image but it couldn't be analyzed. "
                        f"You can try examining it with vision_analyze using "
                        f"image_url: {img_path}]"
                    )
                    if announce:
                        _cprint(f"  {_DIM}⚠ vision analysis failed — path included for retry{_RST}")
            except Exception as e:
                enriched_parts.append(
                    f"[The user attached an image but analysis failed ({e}). "
                    f"You can try examining it with vision_analyze using "
                    f"image_url: {img_path}]"
                )
                if announce:
                    _cprint(f"  {_DIM}⚠ vision analysis error — path included for retry{_RST}")

        # Combine: vision descriptions first, then the user's original text
        user_text = text if isinstance(text, str) and text else ""
        if enriched_parts:
            prefix = "\n\n".join(enriched_parts)
            return f"{prefix}\n\n{user_text}" if user_text else prefix
        return user_text or "What do you see in this image?"

    def _show_tool_availability_warnings(self):
        """Show warnings about disabled tools due to missing API keys."""
        try:
            from tools.model_tools import check_tool_availability
            
            available, unavailable = check_tool_availability()
            
            # Filter to only those missing API keys (not system deps)
            api_key_missing = [u for u in unavailable if u["missing_vars"]]
            
            if api_key_missing:
                self.console.print()
                self.console.print(t('some_tools_disabled_missing_api_keys'))
                for item in api_key_missing:
                    tools_str = ", ".join(item["tools"][:2])  # Show first 2 tools
                    if len(item["tools"]) > 2:
                        tools_str += f", +{len(item['tools'])-2} more"
                    self.console.print(f"   [dim]• {item['name']}[/] [dim italic]({', '.join(item['missing_vars'])})[/]")
                self.console.print(t('run_api_to_configure'))
        except Exception:
            pass  # Don't crash on import errors
    
    def _show_status(self):
        """Show compact startup status line."""
        # Get tool count
        tools = get_tool_definitions(enabled_toolsets=self.enabled_toolsets, quiet_mode=True)
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

    def _fast_command_available(self) -> bool:
        try:
            from VoidCube_app.models import model_supports_fast_mode
        except Exception:
            return False
        agent = getattr(self, "agent", None)
        model = getattr(agent, "model", None) or getattr(self, "model", None)
        return model_supports_fast_mode(model)

    def _command_available(self, slash_command: str) -> bool:
        if slash_command == "/fast":
            return self._fast_command_available()
        return True

    def _list_recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent CLI sessions for in-chat browsing/resume affordances."""
        if not self._session_db:
            return []
        try:
            sessions = self._session_db.list_sessions_rich(
                source="cli",
                exclude_sources=["tool"],
                limit=limit,
                exclude_id_prefixes=["scheduled_"],
            )
        except Exception:
            return []
        return [s for s in sessions if s.get("id") != self.session_id]

    def _show_recent_sessions(self, *, reason: str = "history", limit: int = 8) -> bool:
        """Render recent sessions inline from the active chat TUI.

        Returns True when something was shown, False if no session list was available.
        """
        sessions = self._list_recent_sessions(limit=limit)
        if not sessions:
            return False

        from VoidCube_cli.main import _relative_time

        print()
        if reason == "history":
            print(t('no_messages_in_the_current_chat_yet_here_are_recent_sessions_you_can_resume'))
        else:
            print(t('recent_sessions'))
        print()
        print(f"  #  {'Title':<30} {'Preview':<38} {'Last Active':<13} {'ID'}")
        print(f"  ─ {'─' * 30} {'─' * 38} {'─' * 13} {'─' * 24}")
        for i, session in enumerate(sessions, 1):
            title = (session.get("title") or "—")[:28]
            preview = (session.get("preview") or "")[:36]
            last_active = _relative_time(session.get("last_active"))
            print(f"  {i}  {title:<30} {preview:<38} {last_active:<13} {session['id']}")
        print()
        print(t('use_resume_session_id_or_title_to_continue_where_you_left_off'))
        print("  You can also use /resume <number> to resume by the number above!")
        print()
        return True

    def _apply_session_lifecycle_state(self, state: SessionLifecycleState) -> None:
        """Apply shared session state and synchronize the active Agent runtime."""
        self.session_id = state.session_id
        self.session_start = state.session_start
        self.conversation_history = list(state.conversation_history)
        self._pending_title = state.pending_title
        self._resumed = state.resumed
        self._session_hydration = None
        if self.agent:
            self.agent.activate_session(
                state.session_id,
                session_start=state.session_start,
            )

    def _run_curses_picker(self, title: str, items: list[str], default_index: int = 0) -> int | None:
        """Run curses_single_select via run_in_terminal so prompt_toolkit handles terminal ownership cleanly."""
        import threading
        from VoidCube_cli.curses_ui import curses_single_select

        result = [None]

        def _pick():
            result[0] = curses_single_select(title, items, default_index=default_index)

        # run_in_terminal requires an asyncio event loop — only exists in the
        # main prompt_toolkit thread.  If we're in a background thread (e.g.
        # process_loop), fall back to direct curses call.
        in_main_thread = threading.current_thread() is threading.main_thread()

        if self._app and in_main_thread:
            from prompt_toolkit.application import run_in_terminal
            was_visible = self._status_bar_visible
            self._status_bar_visible = False
            self._app.invalidate()
            try:
                run_in_terminal(_pick)
            finally:
                self._status_bar_visible = was_visible
                self._app.invalidate()
        else:
            _pick()

        return result[0]

    def _prompt_text_input(self, prompt_text: str) -> str | None:
        """Prompt for free-text input safely inside or outside prompt_toolkit."""
        result = [None]

        def _ask():
            try:
                result[0] = input(prompt_text).strip() or None
            except (KeyboardInterrupt, EOFError):
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
        state = self._model_picker_state
        if not state:
            return
        selected = state.get("selected", 0)
        stage = state.get("stage")
        if stage == "provider":
            providers = state.get("providers") or []
            if selected >= len(providers):
                self._close_model_picker()
                return
            provider_data = providers[selected]
            # Reuse the already collected list to keep the picker responsive.
            model_list = provider_data.get("models", [])
            state["stage"] = "model"
            state["provider_data"] = provider_data
            state["model_list"] = model_list
            state["selected"] = 0
            self._invalidate(min_interval=0.0)
            return
        if stage == "model":
            provider_data = state.get("provider_data") or {}
            model_list = state.get("model_list") or []
            back_idx = len(model_list)
            cancel_idx = len(model_list) + 1
            if selected == back_idx:
                state["stage"] = "provider"
                state["selected"] = next((i for i, p in enumerate(state.get("providers") or []) if p.get("slug") == provider_data.get("slug")), 0)
                self._invalidate(min_interval=0.0)
                return
            if selected >= cancel_idx:
                self._close_model_picker()
                return
            if selected < len(model_list):
                from VoidCube_cli.model_switch import switch_model
                chosen_model = model_list[selected]
                
                result = switch_model(
                    raw_input=chosen_model,
                    current_provider=self.provider or "",
                    current_model=self.model or "",
                    current_base_url=self.base_url or "",
                    current_api_key=self.api_key or "",
                    is_global=persist_global,
                    explicit_provider=provider_data.get("slug"),
                    user_providers=state.get("user_provs"),
                )
                self._close_model_picker()
                self._apply_model_switch_result(result, persist_global)
                return
            self._close_model_picker()

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
        cmd_lower = request.normalized
        builtin = self._builtin_command_executor.execute(request)
        if builtin.handled:
            return builtin.continue_running

        _skcmds = _get_skill_commands()
        from VoidCube_cli.commands import COMMANDS

        route = resolve_dynamic_command(
            request,
            quick_commands=self.config.get("quick_commands", {}),
            plugin_names=_get_plugin_cmd_handler_names(),
            skill_commands=_skcmds,
            known_commands=set(COMMANDS),
        )
        if route.kind == "quick_exec":
            import shlex
            import subprocess

            try:
                result = subprocess.run(
                    shlex.split(route.executable),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                output = result.stdout.strip() or result.stderr.strip()
                if output:
                    self.console.print(_rich_text_from_ansi(output))
                else:
                    self.console.print("[dim]Command returned no output[/]")
            except subprocess.TimeoutExpired:
                self.console.print("[bold red]Quick command timed out (30s)[/]")
            except Exception as e:
                self.console.print(f"[bold red]Quick command error: {e}[/]")
        elif route.kind == "quick_alias":
            return self.process_command(route.redirect_command)
        elif route.kind == "quick_invalid":
            if route.quick_type == "exec":
                self.console.print(
                    f"[bold red]Quick command '{request.base_token}' has no command defined[/]"
                )
            elif route.quick_type == "alias":
                self.console.print(
                    f"[bold red]Quick command '{request.base_token}' has no target defined[/]"
                )
            else:
                self.console.print(
                    f"[bold red]Quick command '{request.base_token}' has unsupported type "
                    "(supported: 'exec', 'alias')"
                )
        elif route.kind == "plugin":
            from VoidCube_cli.plugins import get_plugin_command_handler

            plugin_handler = get_plugin_command_handler(request.name)
            if plugin_handler:
                try:
                    result = plugin_handler(request.arguments)
                    if result:
                        _cprint(str(result))
                except Exception as e:
                    _cprint(f"\033[1;31mPlugin command error: {e}{_RST}")
        elif route.kind == "skill":
            msg = _get_skill_invocation_message(
                request.base_token,
                request.arguments,
                task_id=self.session_id,
            )
            if msg:
                skill_name = _skcmds[request.base_token]["name"]
                print(f"\n🔧 Loading skill: {skill_name}")
                if hasattr(self, '_pending_input'):
                    self._pending_input.put(msg)
            else:
                ChatConsole().print(
                    f"[bold red]Failed to load skill for {request.base_token}[/]"
                )
        elif route.kind == "redirect":
            return self.process_command(route.redirect_command)
        elif route.kind == "ambiguous":
            _cprint(f"{_ACCENT}Ambiguous command: {cmd_lower}{_RST}")
            _cprint(f"{_DIM}Did you mean: {', '.join(route.matches)}?{_RST}")
        else:
            _cprint(f"\033[1;31mUnknown command: {cmd_lower}{_RST}")
            _cprint(f"{_DIM}{_ACCENT}Type /help for available commands{_RST}")
        
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
            self._background_tasks = state.tasks
            self._background_task_info = state.info
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

    def _create_background_agent(
        self,
        turn_route: dict[str, Any],
        task_id: str,
        request_overrides: dict[str, Any],
        persist_session: bool,
    ) -> Any:
        runtime = turn_route["runtime"]
        return _get_AIAgent()(
            model=turn_route["model"],
            api_key=runtime.get("api_key"),
            base_url=runtime.get("base_url"),
            provider=runtime.get("provider"),
            acp_command=runtime.get("command"),
            acp_args=runtime.get("args"),
            max_iterations=self.max_turns,
            enabled_toolsets=self.enabled_toolsets,
            quiet_mode=True,
            verbose_logging=False,
            session_id=task_id,
            platform="cli",
            session_db=self._session_db,
            reasoning_config=self.reasoning_config,
            service_tier=self.service_tier,
            request_overrides=request_overrides or None,
            providers_allowed=self._providers_only,
            providers_ignored=self._providers_ignore,
            providers_order=self._providers_order,
            provider_sort=self._provider_sort,
            provider_require_parameters=self._provider_require_params,
            provider_data_collection=self._provider_data_collection,
            fallback_model=self._fallback_model,
            persist_session=persist_session,
        )

    @staticmethod
    def _announce_background_start(
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
        if self._app:
            self._app.invalidate()
            time.sleep(0.05)
        print()
        ChatConsole().print(f"[#34D399]{'~' * 40}[/]")
        if success:
            _cprint(f"  ✅ {task_label} #{task_num} complete")
        else:
            _cprint(f"  ❌ {task_label} #{task_num} failed: {error}")
        _cprint(f"  Prompt: \"{prompt[:60]}{'...' if len(prompt) > 60 else ''}\"")
        ChatConsole().print(f"[#34D399]{'~' * 40}[/]")
        if response:
            label = "> Voidcube"
            response_color = "#CD7F32"
            ChatConsole().print(
                Panel(
                    _rich_text_from_ansi(response),
                    title=f"[{response_color} bold]{response_title or (label + f' (background #{task_num})')}[/]",
                    title_align="left",
                    border_style=response_color,
                    style="#FFF8DC",
                    box=rich_box.HORIZONTALS,
                    padding=(1, 2),
                )
            )
        else:
            _cprint("  (No response generated)")

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
        """Run an ephemeral side question against a snapshot of session context.

        Snapshots the current conversation history, spawns a no-tools agent in
        a background thread, and prints the answer without persisting anything
        to the main session.
        """
        task_id = f"btw_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"

        if not self._ensure_runtime_credentials():
            _cprint("  (>_<) Cannot start /btw: no valid credentials.")
            return False

        turn_route = self._resolve_turn_agent_config(question)
        history_snapshot = list(self.conversation_history)

        preview = question[:60] + ("..." if len(question) > 60 else "")
        _cprint(f'  💬 /btw: "{preview}"')

        def run_btw():
            try:
                btw_agent = _get_AIAgent()(
                    model=turn_route["model"],
                    api_key=turn_route["runtime"].get("api_key"),
                    base_url=turn_route["runtime"].get("base_url"),
                    provider=turn_route["runtime"].get("provider"),
                    acp_command=turn_route["runtime"].get("command"),
                    acp_args=turn_route["runtime"].get("args"),
                    max_iterations=8,
                    enabled_toolsets=[],
                    quiet_mode=True,
                    verbose_logging=False,
                    session_id=task_id,
                    platform="cli",
                    reasoning_config=self.reasoning_config,
                    service_tier=self.service_tier,
                    request_overrides=turn_route.get("request_overrides"),
                    providers_allowed=self._providers_only,
                    providers_ignored=self._providers_ignore,
                    providers_order=self._providers_order,
                    provider_sort=self._provider_sort,
                    provider_require_parameters=self._provider_require_params,
                    provider_data_collection=self._provider_data_collection,
                    fallback_model=self._fallback_model,
                    session_db=None,
                    skip_memory=True,
                    skip_context_files=True,
                    persist_session=False,
                )

                btw_prompt = (
                    "[Ephemeral /btw side question. Answer using the conversation "
                    "context. No tools available. Be direct and concise.]\n\n"
                    + question
                )
                result = btw_agent.run_conversation(
                    user_message=btw_prompt,
                    conversation_history=history_snapshot,
                    task_id=task_id,
                )

                response = (result.get("final_response") or "") if result else ""
                if not response and result and result.get("error"):
                    response = f"Error: {result['error']}"

                # TUI refresh before printing
                if self._app:
                    self._app.invalidate()
                    time.sleep(0.05)
                print()

                if response:
                    _resp_color = "#4F6D4A"

                    ChatConsole().print(Panel(
                        _rich_text_from_ansi(response),
                        title=f"[{_resp_color} bold]> /btw[/]",
                        title_align="left",
                        border_style=_resp_color,
                        box=rich_box.HORIZONTALS,
                        padding=(1, 2),
                    ))
                else:
                    _cprint("  💬 /btw: (no response)")

                if self.bell_on_complete:
                    sys.stdout.write("\a")
                    sys.stdout.flush()

            except Exception as e:
                if self._app:
                    self._app.invalidate()
                    time.sleep(0.05)
                print()
                _cprint(f"  ❌ /btw failed: {e}")
            finally:
                if self._app:
                    self._invalidate(min_interval=0)

        thread = threading.Thread(target=run_btw, daemon=True, name=f"btw-{task_id}")
        thread.start()
        return True

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
        _project_tool_event_view(
            self,
            event,
            append_autonomous_event=_append_autonomous_execution_event_view,
            emit_line=_cprint,
        )

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
            voice=self._voice_tts(),
        )

    def _voice_start_recording(self) -> None:
        start_terminal_voice_recording(self._voice_recording_ports())

    def _voice_stop_and_transcribe(self) -> None:
        stop_terminal_voice_recording(self._voice_recording_ports())

    def _enable_voice_mode(self):
        """Enable voice mode after checking requirements."""
        if self._voice_mode:
            _cprint(f"{_DIM}Voice mode is already enabled.{_RST}")
            return

        voice = self._voice_tts()
        reqs = voice.enable()
        if not reqs.get("capture_available") or not reqs.get("stt_configured"):
            voice.disable()
            _cprint(f"\n{_ACCENT}Voice mode requirements not met:{_RST}")
            if not reqs.get("capture_available"):
                _cprint(f"  {_DIM}Install sounddevice and numpy with an available input device.{_RST}")
            if not reqs.get("stt_configured"):
                _cprint(f"  {_DIM}Configure the canonical STT provider before enabling voice mode.{_RST}")
            return

        with self._voice_lock:
            self._voice_mode = True

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
        with self._voice_lock:
            recording = self._voice_recording
            self._voice_recording = False
            self._voice_mode = False
            self._voice_continuous = False
        if recording:
            self._voice_tts().interrupt()
        self._voice_tts().disable()

        _cprint(f"\n{_DIM}Voice mode disabled.{_RST}")

    def _voice_tts(self) -> VoiceTtsAdapter:
        adapter = self.__dict__.get("_voice_tts_adapter")
        if adapter is None:
            adapter = VoiceTtsAdapter()
            self._voice_tts_adapter = adapter
        return adapter

    def _show_voice_tts_status(self):
        """Project canonical voice transport readiness into terminal text."""
        result = self._voice_tts().status()
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
            result = self._voice_tts().speak(message)
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

        reqs = self._voice_tts().status().get("voice", {})

        _cprint(f"\n{_BOLD}Voice Mode Status{_RST}")
        _cprint(f"  Mode:      {'ON' if self._voice_mode else 'OFF'}")
        tts_status = self._voice_tts().status()
        _cprint(
            f"  TTS:       {tts_status.get('status', 'unavailable')} "
            f"({tts_status.get('reason', 'unknown')})"
        )
        _cprint(f"  Recording: {'YES' if self._voice_recording else 'no'}")
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
        return _approval_sink_view(
            self,
            request,
            timeout=60,
            notify_timeout=lambda: _cprint(
                f"\n{_DIM}  ⏱ Timeout — denying command{_RST}"
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

    def chat(self, message, images: list = None) -> Optional[str]:
        """
        Send a message to the agent and get a response.
        
        Handles streaming output, interrupt detection (user typing while agent
        is working), and re-queueing of interrupted messages.
        
        Uses a dedicated _interrupt_queue (separate from _pending_input) to avoid
        race conditions between the process_loop and interrupt monitoring. Messages
        typed while the agent is running go to _interrupt_queue; messages typed while
        idle go to _pending_input.
        
        Args:
            message: The user's message (str or multimodal content list)
            images: Optional list of Path objects for attached images
            
        Returns:
            The agent's response, or None on error
        """
        # Single-query and direct chat callers do not go through run(), so
        # register secure secret capture here as well.
        _get_set_secret_capture_callback()(self._secret_capture_callback)

        # Refresh provider credentials if needed (handles key rotation transparently)
        if not self._ensure_runtime_credentials():
            return None
        autonomous_runtime = _autonomous_executor_runtime_view(
            self,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=_cprint,
        )
        current_autonomous_task = autonomous_runtime.current_task()
        autonomous_task_run_id = autonomous_task_run_id_for_message(
            current_autonomous_task,
            message,
        )
        if autonomous_task_run_id or current_autonomous_task is None:
            autonomous_runtime.set_last_agent_turn_result(None)

        turn_route = self._resolve_turn_agent_config(message)
        if turn_route["signature"] != self._active_agent_route_signature:
            self.agent = None

        # Initialize agent if needed
        if self.agent is None and self._should_emit_scrollback_output():
            _cprint(f"{_DIM}Initializing agent...{_RST}")
        if not self._init_agent(
            model_override=turn_route["model"],
            runtime_override=turn_route["runtime"],
            route_label=turn_route["label"],
            request_overrides=turn_route.get("request_overrides"),
        ):
            return None
        
        # Pre-process images through the vision tool (Gemini Flash) so the
        # main model receives text descriptions instead of raw base64 image
        # content — works with any model, not just vision-capable ones.
        if images:
            message = self._preprocess_images_with_vision(
                message if isinstance(message, str) else "", images
            )

        # Expand @ context references (e.g. @file:main.py, @diff, @folder:src/)
        if isinstance(message, str) and "@" in message:
            try:
                from agent.context_references import preprocess_context_references
                from agent.model_metadata import get_model_context_length
                _ctx_len = get_model_context_length(
                    self.model, base_url=self.base_url or "", api_key=self.api_key or "")
                _ctx_result = preprocess_context_references(
                    message, cwd=os.getcwd(), context_length=_ctx_len)
                if _ctx_result.expanded or _ctx_result.blocked:
                    if _ctx_result.references:
                        if self._should_emit_scrollback_output():
                            _cprint(
                                f"  {_DIM}[@ context: {len(_ctx_result.references)} ref(s), "
                                f"{_ctx_result.injected_tokens} tokens]{_RST}")
                    for w in _ctx_result.warnings:
                        if self._should_emit_scrollback_output():
                            _cprint(f"  {_DIM}⚠ {w}{_RST}")
                    if _ctx_result.blocked:
                        return "\n".join(_ctx_result.warnings) or "Context injection refused."
                    message = _ctx_result.message
            except Exception as e:
                logging.debug("@ context reference expansion failed: %s", e)

        # Sanitize surrogate characters that can arrive via clipboard paste from
        # rich-text editors (Google Docs, Word, etc.).  Lone surrogates are invalid
        # UTF-8 and crash JSON serialization in the OpenAI SDK.
        if isinstance(message, str):
            from agent.message_sanitizer import sanitize_surrogates
            message = sanitize_surrogates(message)

        turn_input = begin_turn(self.conversation_history, message)
        self.conversation_history = list(turn_input.conversation_history)

        if self._should_emit_scrollback_output():
            ChatConsole().print(f"[#34D399]{'~' * 40}[/]")
            print(flush=True)
        
        autonomous_timeout_reported = False
        autonomous_timeout_writeback_succeeded = False
        previous_active_role = str(getattr(self, "_active_chat_agent_role", "") or "")
        self._active_chat_agent_role = "supervisor_task" if autonomous_task_run_id else "user_chat"
        try:
            # Run the conversation with interrupt monitoring
            result = None

            self._stream_render_state.begin_turn()

            stream_callback = None

            # When voice mode is active, prepend a brief instruction so the
            # model responds concisely. The prefix is API-call-local only —
            # run_conversation persists the original clean user message.
            _voice_prefix = ""
            if self._voice_mode and isinstance(message, str):
                _voice_prefix = (
                    "[Voice input — respond concisely and conversationally, "
                    "2-3 sentences max. No code blocks or markdown.] "
                )

            def run_agent():
                nonlocal result
                agent_message = _voice_prefix + message if _voice_prefix else message
                # Prepend pending model switch note so the model knows about the switch
                _msn = getattr(self, '_pending_model_switch_note', None)
                if _msn:
                    agent_message = _msn + "\n\n" + agent_message
                    self._pending_model_switch_note = None
                try:
                    # Generate per-interaction trace_id for observability (C-03)
                    self._current_trace_id = str(uuid.uuid4())
                    result = self.agent.run_conversation(
                        user_message=agent_message,
                        conversation_history=list(turn_input.prior_history),
                        stream_callback=stream_callback,
                        task_id=self.session_id,
                        trace_id=self._current_trace_id,
                        persist_user_message=message if _voice_prefix else None,
                    )
                except Exception as exc:
                    logging.error("run_conversation raised: %s", exc, exc_info=True)
                    _summary = summarize_api_error(exc)
                    result = {
                        "final_response": f"Error: {_summary}",
                        "messages": [],
                        "api_calls": 0,
                        "completed": False,
                        "failed": True,
                        "error": _summary,
                    }

            # Start agent in background thread (daemon so it cannot keep the
            # process alive when the user closes the terminal tab — SIGHUP
            # exits the main thread and daemon threads are reaped automatically).
            agent_thread = threading.Thread(target=run_agent, daemon=True)
            agent_thread.start()

            # Monitor the dedicated interrupt queue while the agent runs.
            # _interrupt_queue is separate from _pending_input, so process_loop
            # and chat() never compete for the same queue.
            # When a clarify question is active, user input is handled entirely
            # by the Enter key binding (routed to the clarify response queue),
            # so we skip interrupt processing to avoid stealing that input.
            turn_interrupt = None
            while agent_thread.is_alive():
                if autonomous_task_run_id and not autonomous_timeout_reported:
                    timed_out_task = autonomous_runtime.current_task()
                    timed_out_run_id = (
                        str((timed_out_task or {}).get("_autonomous_task_run_id") or "").strip()
                        if isinstance(timed_out_task, dict)
                        else ""
                    )
                    if timed_out_run_id == autonomous_task_run_id:
                        autonomous_timeout_reported = autonomous_runtime.report_current_task_timeout_if_needed(
                            timeout=15,
                        )
                        autonomous_timeout_writeback_succeeded = (
                            autonomous_timeout_reported
                            and autonomous_runtime.current_task() is None
                        )
                        if autonomous_timeout_reported:
                            turn_interrupt = cancel_turn(TurnInterruptReason.TIMEOUT)
                            try:
                                self.agent.interrupt(turn_interrupt.agent_message)
                            except Exception:
                                pass
                if hasattr(self, '_interrupt_queue'):
                    poll_result = poll_interrupt_input(
                        self._pending_input,
                        self._interrupt_queue,
                        timeout=0.1,
                        defer=bool(self._clarify_state or self._clarify_freetext),
                    )
                    if poll_result.status is InterruptPollStatus.DEFERRED:
                        continue
                    if poll_result.interrupt is None:
                        # Force prompt_toolkit to flush any pending stdout
                        # output from the agent thread.  Without this, the
                        # StdoutProxy buffer only flushes on renderer passes
                        # triggered by input events — on macOS this causes
                        # the CLI to appear frozen until the user types. (#1624)
                        self._invalidate(min_interval=0.15)
                        continue
                    turn_interrupt = poll_result.interrupt
                    print("\n🔧 New message detected, interrupting...")
                    self.agent.interrupt(turn_interrupt.agent_message)
                    break
                else:
                    # Fallback for non-interactive mode (e.g., single-query)
                    agent_thread.join(0.1)

            agent_thread.join()  # Ensure agent thread completes

            # Proactively clean up async clients whose event loop is dead.
            # The agent thread may have created AsyncOpenAI clients bound
            # to a per-thread event loop; if that loop is now closed, those
            # clients' __del__ would crash prompt_toolkit's loop on GC.
            try:
                from agent.auxiliary_client import cleanup_stale_async_clients
                cleanup_stale_async_clients()
            except Exception:
                pass

            # Flush any remaining streamed text and close the box
            self._flush_stream()

            # Drain any remaining agent output still in the StdoutProxy
            # buffer so tool/status lines render ABOVE our response box.
            # The flush pushes data into the renderer queue; the short
            # sleep lets the renderer actually paint it before we draw.
            import time as _time
            sys.stdout.flush()
            _time.sleep(0.15)

            outcome = normalize_turn_outcome(
                result,
                fallback_history=self.conversation_history,
            )
            self.conversation_history = list(outcome.conversation_history)
            response = outcome.response
            turn_result = outcome.observation()
            if autonomous_timeout_reported:
                turn_result.update(
                    {
                        "failed": True,
                        "interrupted": True,
                        "error": "Autonomous task timed out after 30 minutes.",
                    }
                )
            if autonomous_task_run_id and not autonomous_timeout_writeback_succeeded:
                turn_result["autonomous_task_run_id"] = autonomous_task_run_id
                autonomous_runtime.set_last_agent_turn_result(turn_result)
            elif autonomous_runtime.current_task() is None:
                autonomous_runtime.set_last_agent_turn_result(turn_result)

            if (
                getattr(self, "_autonomous_gate_active", False)
                and autonomous_runtime.current_task()
                and autonomous_task_run_id
                and not autonomous_timeout_writeback_succeeded
            ):
                if turn_result["failed"] or turn_result["partial"]:
                    _append_autonomous_execution_event_view(
                        self,
                        f"模型回合结束，但结果异常: {turn_result['error'] or 'unknown error'}",
                        tone="error",
                        stage="model_turn_finished",
                    )
                elif turn_result["interrupted"]:
                    _append_autonomous_execution_event_view(
                        self,
                        "模型回合被中断，等待下一条指令",
                        tone="warn",
                        stage="model_turn_finished",
                    )
                else:
                    _append_autonomous_execution_event_view(
                        self,
                        "模型回合完成，等待任务回写",
                        tone="success",
                        stage="model_turn_finished",
                    )

            # Auto-generate session title after first exchange (non-blocking)
            if outcome.usable:
                try:
                    from agent.title_generator import maybe_auto_title
                    maybe_auto_title(
                        self._session_db,
                        self.session_id,
                        message,
                        response,
                        self.conversation_history,
                    )
                except Exception:
                    pass

            # Handle failed or partial results (e.g., non-retryable errors, rate limits,
            # truncated output, invalid tool calls). Both "failed" and "partial" with
            # an empty final_response mean the agent couldn't produce a usable answer.
            if (outcome.failed or outcome.partial) and not response:
                response = outcome.response_or_error()
                turn_result["response"] = response
                # Stop continuous voice mode on persistent errors (e.g. 429 rate limit)
                # to avoid an infinite error → record → error loop
                if self._voice_continuous:
                    self._voice_continuous = False
                    _cprint(f"\n{_DIM}Continuous voice mode stopped due to error.{_RST}")

            # Handle interrupt - check if we were interrupted
            pending_message = None
            if outcome.interrupted:
                pending_message = resolve_interrupted_followup(
                    turn_interrupt, outcome.interrupt_message
                )
                # Add indicator that we were interrupted
                if response and pending_message:
                    response = response + "\n\n---\n_[Interrupted - processing new message]_"

            response_previewed = outcome.response_previewed

            # Display reasoning (thinking) box if enabled and available.
            # Intermediate tool turns reset stream framing but preserve this
            # user-turn-level flag so reasoning is not rendered twice.
            _reasoning_already_shown = self._stream_render_state.reasoning_shown_this_turn
            if self.show_reasoning and not _reasoning_already_shown:
                reasoning = outcome.last_reasoning
                if reasoning:
                    w = shutil.get_terminal_size().columns
                    r_label = " Reasoning "
                    r_fill = w - 2 - len(r_label)
                    r_top = f"{_DIM}┌─{r_label}{'─' * max(r_fill - 1, 0)}┐{_RST}"
                    r_bot = f"{_DIM}└{'─' * (w - 2)}┘{_RST}"
                    # Collapse long reasoning: show first 10 lines
                    lines = reasoning.strip().splitlines()
                    if len(lines) > 10:
                        display_reasoning = "\n".join(lines[:10])
                        display_reasoning += f"\n{_DIM}  ... ({len(lines) - 10} more lines){_RST}"
                    else:
                        display_reasoning = reasoning.strip()
                    if self._should_emit_scrollback_output():
                        _cprint(f"\n{r_top}\n{_DIM}{display_reasoning}{_RST}\n{r_bot}")

            if response and not response_previewed and self._should_emit_scrollback_output():
                label = "> Voidcube"
                _resp_color = "#CD7F32"
                _resp_text = "#FFF8DC"

                is_error_response = outcome.failed or outcome.partial
                already_streamed = (
                    self._stream_render_state.started
                    and self._stream_render_state.response_box_open
                    and not is_error_response
                )
                if already_streamed:
                    # Response was already streamed token-by-token with box framing;
                    # _flush_stream() already closed the box. Skip Rich Panel.
                    pass
                else:
                    _chat_console = ChatConsole()
                    _chat_console.print(Panel(
                        _rich_text_from_ansi(response),
                        title=f"[{_resp_color} bold]{label}[/]",
                        title_align="left",
                        border_style=_resp_color,
                        style=_resp_text,
                        box=rich_box.HORIZONTALS,
                        padding=(1, 2),
                    ))


            # Play terminal bell when agent finishes (if enabled).
            # Works over SSH — the bell propagates to the user's terminal.
            if self.bell_on_complete and self._should_emit_scrollback_output():
                sys.stdout.write("\a")
                sys.stdout.flush()

            # Re-queue the interrupt message (and any that arrived while we were
            # processing the first) as the next prompt for process_loop.
            # Only reached when busy_input_mode == "interrupt" (the default).
            # In "queue" mode Enter routes directly to _pending_input so this
            # block is never hit.
            if pending_message and hasattr(self, '_pending_input'):
                batch = requeue_interrupted_inputs(
                    self._pending_input,
                    self._interrupt_queue,
                    pending_message,
                )
                preview_text = interrupt_text(batch.payloads[0])
                preview = preview_text[:50] + ("..." if len(preview_text) > 50 else "")
                if len(batch.payloads) > 1:
                    print(f"\n🔧 Sending {len(batch.payloads)} messages after interrupt: '{preview}'")
                else:
                    print(f"\n🔧 Sending after interrupt: '{preview}'")
            
            return response
            
        except Exception as e:
            error_result = {
                "failed": True,
                "partial": False,
                "interrupted": False,
                "error": str(e),
                "response": "",
            }
            if autonomous_timeout_reported:
                error_result.update(
                    {
                        "interrupted": True,
                        "error": "Autonomous task timed out after 30 minutes.",
                    }
                )
            if autonomous_task_run_id and not autonomous_timeout_writeback_succeeded:
                error_result["autonomous_task_run_id"] = autonomous_task_run_id
                autonomous_runtime.set_last_agent_turn_result(error_result)
            elif autonomous_runtime.current_task() is None:
                autonomous_runtime.set_last_agent_turn_result(error_result)
            if self._should_emit_scrollback_output():
                print(f"Error: {e}")
            return None
        finally:
            self._active_chat_agent_role = previous_active_role
    
    def _print_exit_summary(self):
        """Print session resume info on exit."""
        print()
        msg_count = len(self.conversation_history)
        if msg_count > 0:
            user_msgs = len([m for m in self.conversation_history if m.get("role") == "user"])
            tool_calls = len([m for m in self.conversation_history if m.get("role") == "tool" or m.get("tool_calls")])
            elapsed = datetime.now() - self.session_start
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                duration_str = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                duration_str = f"{minutes}m {seconds}s"
            else:
                duration_str = f"{seconds}s"
            
            # Look up session title for resume-by-name hint
            session_title = None
            if self._session_db:
                try:
                    session_title = self._session_db.get_session_title(self.session_id)
                except Exception:
                    pass

            from VoidCube_cli.i18n import t
            print(t("prompts.resume_session_with", default="Resume this session with:"))
            print(f"  VoidCube --resume {self.session_id}")
            if session_title:
                print(f"  VoidCube -c \"{session_title}\"")
            print()
            print(f"{t('prompts.session', default='Session')}:        {self.session_id}")
            if session_title:
                print(f"{t('prompts.title', default='Title')}:          {session_title}")
            print(f"{t('prompts.duration', default='Duration')}:       {duration_str}")
            print(f"{t('prompts.messages', default='Messages')}:       {msg_count} ({user_msgs} user, {tool_calls} tool calls)")
        else:
            print("bye.")

    def _get_tui_prompt_symbols(self) -> tuple[str, str]:
        """Return ``(normal_prompt, state_suffix)`` for the fixed CLI style.

        ``normal_prompt`` is the configured built-in prompt symbol.
        ``state_suffix`` is what special states (sudo/secret/approval/agent)
        should render after their leading icon.

        When a profile is active (not "default"), the profile name is
        prepended to the prompt symbol: ``coder ❯`` instead of ``❯``.
        """
        symbol = "❯ "

        symbol = (symbol or "❯ ").rstrip() + " "

        # Prepend profile name when not default
        try:
            from VoidCube_cli.profiles import get_active_profile_name
            profile = get_active_profile_name()
            if profile and profile not in ("default", "custom"):
                symbol = f"{profile} {symbol}"
        except Exception:
            pass
        stripped = symbol.rstrip()
        if not stripped:
            return "❯ ", "❯ "

        parts = stripped.split()
        candidate = parts[-1] if parts else ""
        arrow_chars = ("❯", ">", "$", "#", "›", "»", "→")
        if any(ch in candidate for ch in arrow_chars):
            return symbol, candidate.rstrip() + " "

        return symbol, symbol

    def _audio_level_bar(self) -> str:
        """Return a visual audio level indicator based on current RMS."""
        _LEVEL_BARS = " ▁▂▃▄▅▆▇"
        try:
            rms = float(self._voice_tts().realtime_status().get("audio_rms", 0.0))
        except Exception:
            return ""
        level = max(0, min(7, int(rms * 7)))
        return _LEVEL_BARS[level]

    def _get_tui_prompt_fragments(self):
        """Return the prompt_toolkit fragments for the current interactive state."""
        symbol, state_suffix = self._get_tui_prompt_symbols()
        compact = self._use_minimal_tui_chrome(width=self._get_tui_terminal_width())

        def _state_fragment(style: str, icon: str, extra: str = ""):
            if compact:
                text = icon
                if extra:
                    text = f"{text} {extra.strip()}".rstrip()
                return [(style, text + " ")]
            if extra:
                return [(style, f"{icon} {extra} {state_suffix}")]
            return [(style, f"{icon} {state_suffix}")]

        if self._voice_recording:
            bar = self._audio_level_bar()
            return _state_fragment("class:voice-recording", "●", bar)
        if self._voice_processing:
            return _state_fragment("class:voice-processing", "◉")
        if self._sudo_state:
            return _state_fragment("class:sudo-prompt", "🔐")
        if self._secret_state:
            return _state_fragment("class:sudo-prompt", "🔑")
        if self._approval_state:
            return _state_fragment("class:prompt-working", "⚠")
        if self._clarify_freetext:
            return _state_fragment("class:clarify-selected", "✎")
        if self._clarify_state:
            return _state_fragment("class:prompt-working", "?")
        if self._command_running:
            return _state_fragment("class:prompt-working", self._command_spinner_frame())
        if self._agent_running:
            return _state_fragment("class:prompt-working", ">")
        if self._voice_mode:
            return _state_fragment("class:voice-prompt", "🎤")
        return [("class:prompt", symbol)]

    def _get_tui_prompt_text(self) -> str:
        """Return the visible prompt text for width calculations."""
        return "".join(text for _, text in self._get_tui_prompt_fragments())

    # --- Protected TUI extension hooks for wrapper CLIs ---

    def _get_extra_tui_widgets(self) -> list:
        """Return extra prompt_toolkit widgets to insert into the TUI layout.

        Wrapper CLIs can override this to inject widgets (e.g. a mini-player,
        overlay menu) into the layout without overriding ``run()``.  Widgets
        are inserted between the spacer and the status bar.

        The main CLI already mounts the API-A autonomous execution component
        through ``auto_execution_panel`` when that component has visible work.
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

    def run(self):
        """Run the interactive CLI loop with persistent input at bottom."""
        # Push the entire TUI to the bottom of the terminal so the banner,
        # responses, and prompt all appear pinned to the bottom — empty
        # space stays above, not below.  This prints enough blank lines to
        # scroll the cursor to the last row before any content is rendered.
        try:
            _term_lines = shutil.get_terminal_size().lines
            if _term_lines > 2:
                print("\n" * (_term_lines - 1), end="", flush=True)
        except Exception:
            pass

        self.show_banner()

        # If resuming a session, load history and display it immediately
        # so the user has context before typing their first message.
        if self._resumed:
            if self._preload_resumed_session():
                self._display_resumed_history()

        memory_model_display = None
        try:
            from VoidCube_app.config import load_config
            config = load_config()
            memory_config = config.get("memory", {})
            mem_llm = memory_config.get("llm", {})
            memory_model = mem_llm.get("model", None) or memory_config.get("model", None)
            if memory_model:
                memory_model_display = memory_model.split("/")[-1] if "/" in memory_model else memory_model
                if memory_model_display.endswith(".gguf"):
                    memory_model_display = memory_model_display[:-5]
                if len(memory_model_display) > 25:
                    memory_model_display = memory_model_display[:22] + "..."
        except Exception:
            pass
        
        if memory_model_display:
            _welcome_text = f"记忆模型: {memory_model_display}"
        else:
            try:
                from VoidCube_cli.i18n import t
                _welcome_text = t('cli.welcome', default="VoidCube 就绪")
            except Exception:
                _welcome_text = "VoidCube 就绪"
        
        recent_sessions = []
        try:
            from VoidCube_core.state import SessionDB
            db = SessionDB()
            sessions = db.list_sessions_rich(
                source="cli",
                exclude_sources=["tool"],
                limit=5,
                exclude_id_prefixes=["scheduled_"],
            )
            current_session_id = getattr(self, 'session_id', None)
            for sess in sessions:
                if sess.get("id") != current_session_id:
                    recent_sessions.append(sess)
                    if len(recent_sessions) >= 4:
                        break
        except Exception:
            pass
        
        if recent_sessions:
            history_lines = []
            history_lines.append(f"[bold {_accent_hex()}]历史会话列表[/]")
            history_lines.append("")
            term_width = shutil.get_terminal_size((80, 24)).columns
            for i, sess in enumerate(recent_sessions, 1):
                sess_id = sess.get("id", "")
                preview = sess.get("preview", "")
                title = sess.get("title", "")
                display_text = title if title else preview
                id_part = f"{i}.ID: {sess_id}"
                separator = " | "
                max_preview_length = term_width - len(id_part) - len(separator) - 4
                if len(display_text) > max_preview_length:
                    display_text = display_text[:max_preview_length - 3] + "..."
                history_lines.append(f"  {id_part}{separator}{display_text}")
            
            from rich.panel import Panel
            history_panel = Panel(
                "\n".join(history_lines),
                border_style="dim",
                padding=(0, 1),
                height=12,
            )
            self.console.print(history_panel)
        else:
            self.console.print("[dim]暂无对话历史[/]")
        
        # Get tool count by calling get_tool_definitions
        try:
            tools = _get_tool_definitions(enabled_toolsets=self.enabled_toolsets, quiet_mode=True)
            tools_count = len(tools) if tools else 0
        except Exception:
            tools_count = 0
        
        skills_count = 0
        try:
            from tools.skills_tool import _find_all_skills
            all_skills = _find_all_skills()
            skills_count = len(all_skills) if all_skills else 0
        except Exception:
            pass
        
        session_info = f"当前会话: {self.session_id}" if hasattr(self, 'session_id') and self.session_id else "当前会话: 新会话"
        self.console.print(f"[#FFF8DC]{_welcome_text} · {tools_count} 个工具 · {skills_count} 技能 · {session_info}[/]")
        if self.preloaded_skills and not self._startup_skills_line_shown:
            skills_label = ", ".join(self.preloaded_skills)
            self.console.print(
                f"[bold {_accent_hex()}]Activated skills:[/] {skills_label}"
            )
            self._startup_skills_line_shown = True
        self.console.print()
        
        # State for async operation
        self._agent_running = False
        self._pending_input = queue.Queue()     # For normal input (commands + new queries)
        self._interrupt_queue = queue.Queue()   # For messages typed while agent is running
        self._should_exit = False
        self._last_ctrl_c_time = 0  # Track double Ctrl+C for force exit

        # Give plugin manager a CLI reference so plugins can inject messages
        from VoidCube_cli.plugins import get_plugin_manager
        get_plugin_manager()._cli_ref = self

        # Config file watcher — detect mcp_servers changes and auto-reload
        from VoidCube_core.constants import get_config_path as _get_config_path
        _cfg_path = _get_config_path()
        self._config_mtime: float = _cfg_path.stat().st_mtime if _cfg_path.exists() else 0.0
        self._config_mcp_servers: dict = self.config.get("mcp_servers") or {}
        self._last_config_check: float = 0.0  # monotonic time of last check

        # Clarify tool state: interactive question/answer with the user.
        # When the agent calls the clarify tool, _clarify_state is set and
        # the prompt_toolkit UI switches to a selection mode.
        self._clarify_state = None      # dict with question, choices, selected, response_queue
        self._clarify_freetext = False  # True when user chose "Other" and is typing
        self._clarify_deadline = 0      # monotonic timestamp when the clarify times out

        # Sudo password prompt state (similar mechanism to clarify)
        self._sudo_state = None         # dict with response_queue when active
        self._sudo_deadline = 0
        self._modal_input_snapshot = None

        # Dangerous command approval state (similar mechanism to clarify)
        self._approval_state = None     # dict with command, description, choices, selected, response_queue
        self._approval_deadline = 0
        self._approval_lock = threading.Lock()  # serialize concurrent approval prompts (delegation race fix)

        # Slash command loading state
        self._command_busy_lifecycle.reset()

        # Secure secret capture state for skill setup
        self._secret_state = None       # dict with var_name, prompt, metadata, response_queue
        self._secret_deadline = 0

        # Clipboard image attachments (paste images into the CLI)
        self._attached_images: list[Path] = []
        self._image_counter = 0

        # Each interactive run gets a fresh cross-thread voice session state.
        self._voice_runtime_state = CliVoiceRuntimeState()

        # Register callbacks so terminal_tool prompts route through our UI
        _get_set_sudo_password_callback(self._sudo_password_callback)
        _get_set_approval_sink(self._approval_sink)
        _get_set_secret_capture_callback()(self._secret_capture_callback)

        # Ensure tirith security scanner is available (downloads if needed).
        # Warn the user if tirith is enabled in config but not available,
        # so they know command security scanning is degraded.
        try:
            from tools.tirith_security import ensure_installed
            tirith_path = ensure_installed(log_failures=False)
            pass
        except Exception:
            pass  # Non-fatal — fail-open at scan time if unavailable
        
        # Key bindings for the input area
        kb = KeyBindings()
        
        @kb.add('enter')
        def handle_enter(event):
            """Handle Enter key - submit input.
            
            Routes to the correct queue based on active UI state:
            - Sudo password prompt: password goes to sudo response queue
            - Approval selection: selected choice goes to approval response queue
            - Clarify freetext mode: answer goes to the clarify response queue
            - Clarify choice mode: selected choice goes to the clarify response queue
            - Agent running: goes to _interrupt_queue (chat() monitors this)
            - Agent idle: goes to _pending_input (process_loop monitors this)
            Commands (starting with /) always go to _pending_input so they're
            handled as commands, not sent as interrupt text to the agent.
            """
            # --- Sudo password prompt: submit the typed password ---
            if self._sudo_state:
                text = event.app.current_buffer.text
                self._sudo_state["response_queue"].put(text)
                self._sudo_state = None
                event.app.invalidate()
                return

            # --- Secret prompt: submit the typed secret ---
            if self._secret_state:
                text = event.app.current_buffer.text
                self._submit_secret_response(text)
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # --- Approval selection: confirm the highlighted choice ---
            if self._approval_state:
                self._handle_approval_selection()
                event.app.invalidate()
                return

            # --- Check for /api command first, before any modal handling ---
            text = event.app.current_buffer.text.strip()
            has_images = bool(self._attached_images)
            if text and text.startswith("/api"):
                # Clear all modal states before running API config wizard
                self._model_picker_state = None
                self._clarify_state = None
                self._clarify_freetext = False
                self._approval_state = None
                self._sudo_state = None
                self._secret_state = None
                self._restore_modal_input_snapshot()
                
                # Run API config wizard in terminal mode
                from prompt_toolkit.application import run_in_terminal
                was_visible = self._status_bar_visible
                self._status_bar_visible = False
                event.app.invalidate()
                
                def _run_wizard():
                    self.process_command("/api")
                
                try:
                    run_in_terminal(_run_wizard)
                finally:
                    self._status_bar_visible = was_visible
                    event.app.invalidate()
                
                event.app.current_buffer.reset(append_to_history=True)
                return

            # --- /model picker modal ---
            if self._model_picker_state:
                self._handle_model_picker_selection()
                event.app.invalidate()
                return

            # --- Clarify freetext mode: user typed their own answer ---
            if self._clarify_freetext and self._clarify_state:
                text = event.app.current_buffer.text.strip()
                if text:
                    self._clarify_state["response_queue"].put(text)
                    self._clarify_state = None
                    self._clarify_freetext = False
                    event.app.current_buffer.reset()
                    event.app.invalidate()
                return

            # --- Clarify choice mode: confirm the highlighted selection ---
            if self._clarify_state and not self._clarify_freetext:
                state = self._clarify_state
                selected = state["selected"]
                choices = state.get("choices") or []
                if selected < len(choices):
                    state["response_queue"].put(choices[selected])
                    self._clarify_state = None
                    event.app.invalidate()
                else:
                    # "Other" selected → switch to freetext
                    self._clarify_freetext = True
                    event.app.invalidate()
                return

            # --- Normal input routing ---
            if text or has_images:
                # Handle /model directly on the UI thread so interactive pickers
                # can safely use prompt_toolkit terminal handoff helpers.
                if self._should_handle_model_command_inline(text, has_images=has_images):
                    if not self.process_command(text):
                        self._should_exit = True
                        try:
                            if event.app.is_running:
                                event.app.exit()
                        except Exception:
                            pass
                    event.app.current_buffer.reset(append_to_history=True)
                    return
                
                # Handle /quit directly on the UI thread for immediate exit.
                # Force-stop auto-started daemons so the full VoidCube stack
                # shuts down cleanly instead of leaving orphaned processes.
                # Use /quit --keep-daemons to exit while leaving daemons running.
                if text.startswith("/quit"):
                    if not self.process_command(text):
                        self._should_exit = True
                        # ── Force-stop daemons (unless --keep-daemons) ──
                        keep_daemons = "--keep-daemons" in text
                        global _daemons_auto_started
                        if _daemons_auto_started and not keep_daemons:
                            try:
                                from VoidCube_cli.ops.serve import stop_all
                                stop_all(force=True)
                                _daemons_auto_started = False
                            except Exception:
                                pass  # leave flag True so atexit handler retries
                        # ───────────────────────────────────────────────
                        try:
                            if event.app.is_running:
                                event.app.exit()
                        except Exception:
                            pass  # already exiting — graceful no-op
                    event.app.current_buffer.reset(append_to_history=True)
                    return

                # Snapshot and clear attached images
                images = list(self._attached_images)
                self._attached_images.clear()
                event.app.invalidate()
                # Bundle text + images as a tuple when images are present
                payload = (text, images) if images else text

                # Keep /auto-q as a fast-path exit while allowing the main CLI
                # to remain usable during autonomous-chain execution.
                if self._autonomous_gate_active:
                    if text and _looks_like_slash_command(text):
                        _base = text.strip().lstrip("/").split()[0].lower()
                        if _base in ("auto-q", "auto-quit", "auto-stop"):
                            # ── FAST PATH: exit immediately, bypass queue ──
                            event.app.current_buffer.reset(append_to_history=True)
                            _cprint(f"  🔓 临时停用自主链路...")
                            exit_autonomous_gate_fast_for_host(
                                self,
                                emit=_cprint,
                                interrupt_current_task=self._interrupt_autonomous_component_task,
                                push_cli_agent_scene=_push_cli_agent_scene,
                            )
                            event.app.invalidate()
                            return
                is_command = bool(text and _looks_like_slash_command(text))
                input_route = enqueue_turn_input(
                    self._pending_input,
                    self._interrupt_queue,
                    payload,
                    agent_running=self._agent_running,
                    is_command=is_command,
                    busy_input_mode=self.busy_input_mode,
                )
                if input_route is TurnInputRoute.NEXT_TURN and self._agent_running and not is_command:
                    preview = text if text else f"[{len(images)} image{'s' if len(images) != 1 else ''} attached]"
                    _cprint(f"  Queued for the next turn: {preview[:80]}{'...' if len(preview) > 80 else ''}")
                event.app.current_buffer.reset(append_to_history=True)
        install_text_editing_keybindings(kb)

        install_modal_navigation_keybindings(
            kb,
            ports=ModalNavigationPorts(
                clarify_state=lambda: self._clarify_state,
                clarify_freetext_active=lambda: self._clarify_freetext,
                approval_state=lambda: self._approval_state,
                model_picker_state=lambda: self._model_picker_state,
                invalidate=lambda: self._invalidate(min_interval=0.0),
            ),
        )

        # --- History navigation: up/down browse history in normal input mode ---
        # The TextArea is multiline, so by default up/down only move the cursor.
        # Buffer.auto_up/auto_down handle both: cursor movement when multi-line,
        # history browsing when on the first/last line (or single-line input).
        install_history_navigation_keybindings(
            kb,
            normal_input_active=lambda: not self._clarify_state
            and not self._approval_state
            and not self._sudo_state
            and not self._secret_state
            and not self._model_picker_state,
        )

        @kb.add('c-c')
        def handle_ctrl_c(event):
            """Handle Ctrl+C - cancel interactive prompts, interrupt agent.

            Does NOT force-exit — use /quit to exit.
            """
            import time as _time
            now = _time.time()

            # Cancel active voice recording.
            _should_cancel_voice = False
            with cli_ref._voice_lock:
                if cli_ref._voice_recording:
                    cli_ref._voice_recording = False
                    cli_ref._voice_continuous = False
                    _should_cancel_voice = True
            if _should_cancel_voice:
                _cprint(f"\n{_DIM}Recording cancelled.{_RST}")
                threading.Thread(
                    target=cli_ref._voice_tts().interrupt,
                    daemon=True,
                ).start()
                event.app.invalidate()
                return

            # Cancel sudo prompt
            if self._sudo_state:
                self._sudo_state["response_queue"].put("")
                self._sudo_state = None
                event.app.invalidate()
                return

            # Cancel secret prompt
            if self._secret_state:
                self._cancel_secret_capture()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel approval prompt (deny)
            if self._approval_state:
                self._approval_state["response_queue"].put(ApprovalStatus.DENIED.value)
                self._approval_state = None
                event.app.invalidate()
                return

            # Cancel /model picker
            if self._model_picker_state:
                self._close_model_picker()
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Cancel clarify prompt
            if self._clarify_state:
                self._clarify_state["response_queue"].put(
                    ClarificationDecision(ClarificationStatus.CANCELLED)
                )
                self._clarify_state = None
                self._clarify_freetext = False
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Interrupt running agent
            if self._agent_running and self.agent:
                self._last_ctrl_c_time = now
                self.agent.interrupt(cancel_turn(TurnInterruptReason.USER_CANCELLED).agent_message)
                return

            # Idle: clear input if there's text, otherwise no-op
            if event.app.current_buffer.text or self._attached_images:
                event.app.current_buffer.reset()
                self._attached_images.clear()
                event.app.invalidate()

        @kb.add('c-d')
        def handle_ctrl_d(event):
            """Handle Ctrl+D — force-quit the autonomous chain, or clear input otherwise."""
            if self._autonomous_gate_active:
                # ── Autonomous chain: Ctrl+D = emergency force-quit ──
                event.app.current_buffer.reset()
                _cprint(f"\n  ⚡ Ctrl+D — 触发紧急强制退出自主链路...")
                force_quit_autonomous_gate_for_host(
                    self,
                    emit=_cprint,
                    interrupt_current_task=self._interrupt_autonomous_component_task,
                    push_cli_agent_scene=_push_cli_agent_scene,
                )
                event.app.invalidate()
                return
            # Normal mode: clear input (no exit, use /quit instead)
            if event.app.current_buffer.text or self._attached_images:
                event.app.current_buffer.reset()
                self._attached_images.clear()
                event.app.invalidate()

        @kb.add('c-z')
        def handle_ctrl_z(event):
            """Handle Ctrl+Z - suspend process to background (Unix only)."""
            import sys
            if sys.platform == 'win32':
                _cprint(f"\n{_DIM}Suspend (Ctrl+Z) is not supported on Windows.{_RST}")
                event.app.invalidate()
                return
            import os, signal as _sig
            from prompt_toolkit.application import run_in_terminal
            agent_name = "Voidcube Agent"
            msg = f"\n{agent_name} has been suspended. Run `fg` to bring {agent_name} back."
            def _suspend():
                os.write(1, msg.encode())
                os.kill(0, _sig.SIGTSTP)
            run_in_terminal(_suspend)

        # Voice push-to-talk key: configurable via config.yaml (voice.record_key)
        # Default: Ctrl+B (avoids conflict with Ctrl+R readline reverse-search)
        # Config uses "ctrl+b" format; prompt_toolkit expects "c-b" format.
        try:
            from VoidCube_app.config import load_config
            _raw_key = load_config().get("voice", {}).get("record_key", "ctrl+b")
            _voice_key = _raw_key.lower().replace("ctrl+", "c-").replace("alt+", "a-")
        except Exception:
            _voice_key = "c-b"

        @kb.add(_voice_key)
        def handle_voice_record(event):
            """Toggle voice recording when voice mode is active.

            IMPORTANT: This handler runs in prompt_toolkit's event-loop thread.
            Any blocking call here (locks, sd.wait, disk I/O) freezes the
            entire UI.  All heavy work is dispatched to daemon threads.
            """
            if not cli_ref._voice_mode:
                return
            # Always allow STOPPING a recording (even when agent is running)
            if cli_ref._voice_recording:
                # Manual stop via push-to-talk key: stop continuous mode
                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = False
                # Flag clearing is handled atomically inside _voice_stop_and_transcribe
                event.app.invalidate()
                threading.Thread(
                    target=cli_ref._voice_stop_and_transcribe,
                    daemon=True,
                ).start()
            else:
                # Guard: don't START recording during agent run or interactive prompts
                if cli_ref._agent_running:
                    return
                if cli_ref._clarify_state or cli_ref._sudo_state or cli_ref._approval_state:
                    return
                # Guard: don't start while a previous stop/transcribe cycle is
                # still running — recorder.stop() holds AudioRecorder._lock and
                # start() would block the event-loop thread waiting for it.
                if cli_ref._voice_processing:
                    return

                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = True

                # Keep capture and the canonical voice event loop off the
                # prompt_toolkit event-loop thread.
                def _start_recording():
                    try:
                        cli_ref._voice_start_recording()
                        if hasattr(cli_ref, '_app') and cli_ref._app:
                            cli_ref._app.invalidate()
                    except Exception as e:
                        _cprint(f"\n{_DIM}Voice recording failed: {e}{_RST}")

                threading.Thread(target=_start_recording, daemon=True).start()
                event.app.invalidate()
        from prompt_toolkit.keys import Keys

        @kb.add(Keys.BracketedPaste, eager=True)
        def handle_paste(event):
            """Handle terminal paste — detect clipboard images.

            When the terminal supports bracketed paste, Ctrl+V / Cmd+V
            triggers this with the pasted text. We only auto-attach a
            clipboard image for image-only/empty paste gestures so text
            pastes and dictation do not accidentally attach stale images.

            Large pastes (5+ lines) are collapsed to a file reference
            placeholder while preserving any existing user text in the
            buffer.
            """
            pasted_text = event.data or ""
            # Normalise line endings — Windows \r\n and old Mac \r both become \n
            # so the 5-line collapse threshold and display are consistent.
            pasted_text = pasted_text.replace('\r\n', '\n').replace('\r', '\n')
            if _should_auto_attach_clipboard_image_on_paste(pasted_text) and self._try_attach_clipboard_image():
                event.app.invalidate()
            if pasted_text:
                line_count = pasted_text.count('\n')
                buf = event.current_buffer
                if line_count >= 5 and not buf.text.strip().startswith('/'):
                    _paste_counter[0] += 1
                    paste_dir = _VoidCube_home / "pastes"
                    paste_dir.mkdir(parents=True, exist_ok=True)
                    paste_file = paste_dir / f"paste_{_paste_counter[0]}_{datetime.now().strftime('%H%M%S')}.txt"
                    paste_file.write_text(pasted_text, encoding="utf-8")
                    placeholder = f"[Pasted text #{_paste_counter[0]}: {line_count + 1} lines \u2192 {paste_file}]"
                    prefix = ""
                    if buf.cursor_position > 0 and buf.text[buf.cursor_position - 1] != '\n':
                        prefix = "\n"
                    _paste_just_collapsed[0] = True
                    buf.insert_text(prefix + placeholder)
                else:
                    buf.insert_text(pasted_text)

        @kb.add('c-v')
        def handle_ctrl_v(event):
            """Fallback image paste for terminals without bracketed paste.

            On Linux terminals (GNOME Terminal, Konsole, etc.), Ctrl+V
            sends raw byte 0x16 instead of triggering a paste.  This
            binding catches that and checks the clipboard for images.
            On terminals that DO intercept Ctrl+V for paste (macOS
            Terminal, iTerm2, VSCode, Windows Terminal), the bracketed
            paste handler fires instead and this binding never triggers.
            """
            if self._try_attach_clipboard_image():
                event.app.invalidate()

        @kb.add('escape', 'v')
        def handle_alt_v(event):
            """Alt+V — paste image from clipboard.

            Alt key combos pass through all terminal emulators (sent as
            ESC + key), unlike Ctrl+V which terminals intercept for text
            paste.  This is the reliable way to attach clipboard images
            on WSL2, VSCode, and any terminal over SSH where Ctrl+V
            can't reach the application for image-only clipboard.
            """
            if self._try_attach_clipboard_image():
                event.app.invalidate()
            else:
                # No image found — show a hint
                pass  # silent when no image (avoid noise on accidental press)

        # Dynamic prompt: shows Voidcube symbol when agent is working,
        # or answer prompt when clarify freetext mode is active.
        cli_ref = self

        def get_prompt():
            return cli_ref._get_tui_prompt_fragments()

        input_area = build_input_area(
            ports=InputWidgetPorts(
                history_path=str(self._history_file),
                prompt_fragments=get_prompt,
                prompt_text=self._get_tui_prompt_text,
                command_available=cli_ref._command_available,
                command_running=lambda: bool(cli_ref._command_running),
                password_mask_active=lambda: bool(cli_ref._sudo_state) or bool(cli_ref._secret_state),
            )
        )

        # Paste collapsing: detect large pastes and save to temp file
        _paste_counter = [0]
        _prev_text_len = [0]
        _prev_newline_count = [0]
        _paste_just_collapsed = [False]

        def _on_text_changed(buf):
            """Detect large pastes and collapse them to a file reference.

            When bracketed paste is available, handle_paste collapses
            large pastes directly.  This handler is a fallback for
            terminals without bracketed paste support.

            Two heuristics (either triggers collapse):
            1. Many characters added at once (chars_added > 1) — works
               when the terminal delivers the paste in one event-loop tick.
            2. Newline count jumped by 4+ in a single text-change event —
               catches terminals that feed characters individually but
               still batch newlines.  Alt+Enter only adds 1 newline per
               event so it never triggers this.
            """
            text = buf.text
            chars_added = len(text) - _prev_text_len[0]
            _prev_text_len[0] = len(text)
            if _paste_just_collapsed[0]:
                _paste_just_collapsed[0] = False
                _prev_newline_count[0] = text.count('\n')
                return
            line_count = text.count('\n')
            newlines_added = line_count - _prev_newline_count[0]
            _prev_newline_count[0] = line_count
            is_paste = chars_added > 1 or newlines_added >= 4
            if line_count >= 5 and is_paste and not text.startswith('/'):
                _paste_counter[0] += 1
                # Save to temp file
                paste_dir = _VoidCube_home / "pastes"
                paste_dir.mkdir(parents=True, exist_ok=True)
                paste_file = paste_dir / f"paste_{_paste_counter[0]}_{datetime.now().strftime('%H%M%S')}.txt"
                paste_file.write_text(text, encoding="utf-8")
                # Replace buffer with compact reference
                _paste_just_collapsed[0] = True
                buf.text = f"[Pasted text #{_paste_counter[0]}: {line_count + 1} lines \u2192 {paste_file}]"
                buf.cursor_position = len(buf.text)

        input_area.buffer.on_text_changed += _on_text_changed

        def _get_placeholder():
            if cli_ref._voice_recording:
                return "recording... Ctrl+B to stop, Ctrl+C to cancel"
            if cli_ref._voice_processing:
                return "transcribing..."
            if cli_ref._sudo_state:
                return "type password (hidden), Enter to skip"
            if cli_ref._secret_state:
                return "type secret (hidden), Enter to skip"
            if cli_ref._approval_state:
                return ""
            if cli_ref._clarify_freetext:
                return "type your answer here and press Enter"
            if cli_ref._clarify_state:
                return ""
            if cli_ref._command_running:
                frame = cli_ref._command_spinner_frame()
                status = cli_ref._command_status or "Processing command..."
                return f"{frame} {status}"
            if cli_ref._agent_running:
                return "type a message + Enter to interrupt, Ctrl+C to cancel"
            if cli_ref._voice_mode:
                return "type or Ctrl+B to record"
            return ""

        install_placeholder_processor(input_area, placeholder_text=_get_placeholder)

        # Hint line above input: shown only for interactive prompts that need
        # extra instructions (sudo countdown, approval navigation, clarify).
        # The agent-running interrupt hint is now an inline placeholder above.
        def get_hint_text():
            import time as _time

            if cli_ref._sudo_state:
                remaining = max(0, int(cli_ref._sudo_deadline - _time.monotonic()))
                return [
                    ('class:hint', '  password hidden · Enter to skip'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._secret_state:
                remaining = max(0, int(cli_ref._secret_deadline - _time.monotonic()))
                return [
                    ('class:hint', '  secret hidden · Enter to skip'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._approval_state:
                remaining = max(0, int(cli_ref._approval_deadline - _time.monotonic()))
                return [
                    ('class:hint', '  ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', f'  ({remaining}s)'),
                ]

            if cli_ref._clarify_state:
                remaining = max(0, int(cli_ref._clarify_deadline - _time.monotonic()))
                countdown = f'  ({remaining}s)' if cli_ref._clarify_deadline else ''
                if cli_ref._clarify_freetext:
                    return [
                        ('class:hint', '  type your answer and press Enter'),
                        ('class:clarify-countdown', countdown),
                    ]
                return [
                    ('class:hint', '  ↑/↓ to select, Enter to confirm'),
                    ('class:clarify-countdown', countdown),
                ]

            if cli_ref._command_running:
                frame = cli_ref._command_spinner_frame()
                return [
                    ('class:hint', f'  {frame} command in progress · input temporarily disabled'),
                ]

            return []

        def get_hint_height():
            if cli_ref._sudo_state or cli_ref._secret_state or cli_ref._approval_state or cli_ref._clarify_state or cli_ref._command_running:
                return 1
            # Keep a spacer while the agent runs on roomy terminals, but reclaim
            # the row on narrow/mobile screens where every line matters.
            return cli_ref._agent_spacer_height()

        def get_spinner_text():
            txt = cli_ref._spinner_text
            if not txt:
                return []
            # Append live elapsed timer when a tool is running
            t0 = cli_ref._tool_start_time
            if t0 > 0:
                import time as _time
                elapsed = _time.monotonic() - t0
                if elapsed >= 60:
                    _m, _s = int(elapsed // 60), int(elapsed % 60)
                    elapsed_str = f"{_m}m {_s}s"
                else:
                    elapsed_str = f"{elapsed:.1f}s"
                return [('class:hint', f'  {txt}  ({elapsed_str})')]
            return [('class:hint', f'  {txt}')]

        def get_spinner_height():
            return cli_ref._spinner_widget_height()

        indicator_widgets = build_indicator_widgets(
            ports=IndicatorWidgetPorts(
                spinner_fragments=get_spinner_text,
                spinner_height=get_spinner_height,
                hint_fragments=get_hint_text,
                hint_height=get_hint_height,
                input_rule_height=cli_ref._tui_input_rule_height,
                image_fragments=lambda: (
                    [("class:image-badge", f" {_format_image_attachment_badges(cli_ref._attached_images, cli_ref._image_counter)} ")]
                    if cli_ref._attached_images
                    else []
                ),
                images_visible=lambda: bool(cli_ref._attached_images),
                voice_fragments=cli_ref._get_voice_status_fragments,
                voice_visible=lambda: cli_ref._voice_mode,
                autonomous_fragments=lambda: _get_autonomous_execution_panel_fragments_view(cli_ref),
                autonomous_visible=lambda: _has_visible_autonomous_work_view(cli_ref),
                status_fragments=cli_ref._get_status_bar_fragments,
                status_visible=lambda: cli_ref._status_bar_visible,
            )
        )
        spinner_widget = indicator_widgets.spinner
        spacer = indicator_widgets.spacer
        input_rule_top = indicator_widgets.input_rule_top
        input_rule_bot = indicator_widgets.input_rule_bottom
        image_bar = indicator_widgets.image_bar
        voice_status_bar = indicator_widgets.voice_status_bar
        auto_execution_panel = indicator_widgets.autonomous_execution_panel
        status_bar = indicator_widgets.status_bar

        # Allow wrapper CLIs to register extra keybindings.
        self._register_extra_tui_keybindings(kb, input_area=input_area)

        # Layout: interactive prompt widgets + ruled input at bottom.
        # The sudo, approval, and clarify widgets appear above the input when
        # the corresponding interactive prompt is active.
        completions_menu = CompletionsMenu(max_height=12, scroll_offset=1)

        layout = Layout(
            HSplit(
                build_tui_layout_children(
                    sudo_widget=sudo_widget,
                    secret_widget=secret_widget,
                    approval_widget=approval_widget,
                    clarify_widget=clarify_widget,
                    model_picker_widget=model_picker_widget,
                    spinner_widget=spinner_widget,
                    spacer=spacer,
                    extra_widgets=self._get_extra_tui_widgets,
                    status_bar=status_bar,
                    auto_execution_panel=auto_execution_panel,
                    input_rule_top=input_rule_top,
                    image_bar=image_bar,
                    input_area=input_area,
                    input_rule_bot=input_rule_bot,
                    voice_status_bar=voice_status_bar,
                    completions_menu=completions_menu,
                )
            )
        )
        
        app = create_tui_application(
            layout=layout,
            key_bindings=kb,
            cursor=_STEADY_CURSOR,
        )
        self._app = app  # Store reference for interactive modal adapters

        install_resize_reflow_cleanup(app)

        spinner_thread = start_tui_refresh_loop(
            stop_requested=lambda: self._should_exit,
            application_ready=lambda: bool(self._app),
            presence_refresh_needed=lambda: (
                self._agent_running
                or self._command_running
                or self._stream_render_state.started
                or self._get_subagent_observability_snapshot().get("active")
            ),
            refresh_presence=lambda: _refresh_gateway_cli_presence_view(
                self,
                force=True,
                is_gateway_running=_is_gateway_running,
                register_with_gateway=_register_with_gateway,
                push_cli_agent_scene=_push_cli_agent_scene,
                monotonic_time=time.monotonic,
            ),
            command_running=lambda: self._command_running,
            invalidate=lambda interval: self._invalidate(min_interval=interval),
            monotonic_time=time.monotonic,
            sleep=time.sleep,
            thread_factory=threading.Thread,
        )

        scheduled_task_thread = start_scheduled_task_polling(
            stop_requested=lambda: self._should_exit,
            poll_workflow=self._scheduled_executor_runtime.poll_workflow,
            sleep=time.sleep,
            report_failure=lambda: logger.debug(
                "Scheduled task poll failed",
                exc_info=True,
            ),
            thread_factory=threading.Thread,
        )
        
        def perform_idle_maintenance() -> None:
            # Periodic background work remains owned by the CLI runtime.
            if self._agent_running:
                return
            self._check_config_mcp_changes()
            _refresh_autonomous_observation_surfaces_view(
                self,
                refresh_gateway_cli_presence=lambda: _refresh_gateway_cli_presence_view(
                    self,
                    force=False,
                    is_gateway_running=_is_gateway_running,
                    register_with_gateway=_register_with_gateway,
                    push_cli_agent_scene=_push_cli_agent_scene,
                    monotonic_time=time.monotonic,
                ),
            )
            if self._autonomous_gate_active:
                self._start_autonomous_execution_component()
                if self._app:
                    self._invalidate(min_interval=0.5)
            # Process notification delivery remains a CLI queue concern.
            try:
                from tools.process_registry import process_registry
                if not process_registry.completion_queue.empty():
                    event = process_registry.completion_queue.get_nowait()
                    session_id = event.get("session_id", "")
                    if event.get("type") != "completion" or not process_registry.is_completion_consumed(session_id):
                        synthesized = _format_process_notification(event)
                        if synthesized:
                            self._pending_input.put(synthesized)
            except Exception:
                pass

        process_thread = start_input_process_loop(
            stop_requested=lambda: self._should_exit,
            execution_gate=self._api_a_execution_gate,
            get_pending_input=lambda timeout: self._pending_input.get(timeout=timeout),
            empty_input=queue.Empty,
            requeue_input=self._pending_input.put,
            perform_idle_maintenance=perform_idle_maintenance,
            execute_input=lambda user_input: self._execute_pending_input(user_input, app=app),
            sleep=time.sleep,
            report_error=lambda error: print(f"Error: {error}"),
            thread_factory=threading.Thread,
        )
        
        # Register atexit cleanup so resources are freed even on unexpected exit
        atexit.register(_run_cleanup)
        
        # Register signal handlers for graceful shutdown on SSH disconnect / SIGTERM
        def _signal_handler(signum, frame):
            """Handle SIGHUP/SIGTERM by triggering graceful cleanup."""
            logger.debug("Received signal %s, triggering graceful shutdown", signum)
            raise KeyboardInterrupt()
        
        try:
            import signal as _signal
            _signal.signal(_signal.SIGTERM, _signal_handler)
            if hasattr(_signal, 'SIGHUP'):
                _signal.signal(_signal.SIGHUP, _signal_handler)
        except Exception:
            pass  # Signal handlers may fail in restricted environments
        
        # Install a custom asyncio exception handler that suppresses the
        # "Event loop is closed" RuntimeError from httpx transport cleanup
        # and the "0 is not registered" KeyError from broken stdin (#6393).
        # The RuntimeError fix is defense-in-depth — the primary fix is
        # neuter_async_httpx_del which disables __del__ entirely.  The
        # KeyError fix handles macOS + uv-managed Python environments where
        # fd 0 is not reliably available to the asyncio selector.
        def _suppress_closed_loop_errors(loop, context):
            exc = context.get("exception")
            if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
                return  # silently suppress
            if isinstance(exc, KeyError) and "is not registered" in str(exc):
                return  # suppress selector registration failures (#6393)
            # Fall back to default handler for everything else
            loop.default_exception_handler(context)

        # Validate stdin before launching prompt_toolkit — on macOS with
        # uv-managed Python, fd 0 can be invalid or unregisterable with the
        # asyncio selector, causing "KeyError: '0 is not registered'" (#6393).
        try:
            import os as _os
            _os.fstat(0)
        except OSError:
            print(
                "Error: stdin (fd 0) is not available.\n"
                "This can happen with certain Python installations (e.g. uv-managed cPython on macOS).\n"
                "Try reinstalling Python via pyenv or Homebrew, then re-run: /api"
            )
            _run_cleanup()
            self._print_exit_summary()
            return

        # Run the application with patch_stdout for proper output handling
        try:
            with patch_stdout():
                # Set the custom handler on prompt_toolkit's event loop
                try:
                    import asyncio as _aio
                    try:
                        _loop = _aio.get_running_loop()
                    except RuntimeError:
                        _loop = _aio.new_event_loop()
                        _aio.set_event_loop(_loop)
                    _loop.set_exception_handler(_suppress_closed_loop_errors)
                except Exception:
                    pass
                app.run()
        except (EOFError, KeyboardInterrupt, BrokenPipeError):
            pass  # Normal exit via Ctrl+C or EOF
        except (KeyError, OSError) as _stdin_err:
            # Catch selector registration failures from broken stdin (#6393).
            # This is the fallback for cases that slip past the fstat() guard.
            if "is not registered" in str(_stdin_err) or "Bad file descriptor" in str(_stdin_err):
                print(
                    f"\nError: stdin is not usable ({_stdin_err}).\n"
                    "This can happen with certain Python installations (e.g. uv-managed cPython on macOS).\n"
                    "Try reinstalling Python via pyenv or Homebrew, then re-run: /api"
                )
            else:
                raise
        finally:
            self._should_exit = True
            def interrupt_running_agent() -> None:
                # The agent thread is daemon-backed, but interruption prevents
                # needless API work and lets its conversation cleanup finish.
                if self.agent and self._agent_running:
                    try:
                        self.agent.interrupt()
                    except Exception:
                        pass

            def interrupt_voice() -> None:
                self._voice_tts().interrupt()

            def close_voice_tts() -> None:
                adapter = self.__dict__.get("_voice_tts_adapter")
                if adapter is not None:
                    adapter.close()

            def unregister_tool_callbacks() -> None:
                _get_set_sudo_password_callback(None)
                _get_set_approval_sink(None)
                _get_set_secret_capture_callback()(None)

            def close_session() -> None:
                if self._session_db and self.agent:
                    try:
                        self._session_db.end_session(self.agent.session_id, "cli_close")
                    except (Exception, KeyboardInterrupt) as error:
                        logger.debug("Could not close session in DB: %s", error)

            def finish_interrupted_session() -> None:
                # Normal completed turns already invoke this hook themselves.
                if self.agent and self._agent_running:
                    try:
                        from VoidCube_cli.plugins import invoke_hook as _invoke_hook
                        _invoke_hook(
                            "on_session_end",
                            session_id=self.agent.session_id,
                            completed=False,
                            interrupted=True,
                            model=getattr(self.agent, 'model', None),
                            platform=getattr(self.agent, 'platform', None) or "cli",
                        )
                    except Exception:
                        pass

            run_tui_teardown(
                TuiTeardownPorts(
                    stop_autonomous=lambda: self._stop_autonomous_execution_component(interrupt=True),
                    interrupt_agent=interrupt_running_agent,
                    interrupt_voice=interrupt_voice,
                    close_voice_tts=close_voice_tts,
                    unregister_tool_callbacks=unregister_tool_callbacks,
                    close_session=close_session,
                    finish_interrupted_session=finish_interrupted_session,
                    run_global_cleanup=_run_cleanup,
                    print_exit_summary=self._print_exit_summary,
                )
            )


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
    When ``force=False`` (from Ctrl+C / EOF / normal exit): stop silently
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

    try:
        stop_all(force=True)
    except KeyboardInterrupt:
        pass
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
    # cleanup so /quit and atexit can stop them.  Without this, daemons
    # started via ``voidcube`` would be orphaned on exit.
    is_interactive = query is None and not list_tools and not list_toolsets
    daemons_already_started = os.environ.get("VOIDCUBE_DAEMONS_STARTED") == "1"
    if is_interactive:
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

