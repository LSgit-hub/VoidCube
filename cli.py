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
import tempfile
import time
import uuid
import textwrap
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
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from agent.error_classifier import summarize_api_error
from VoidCube_cli.chat_render_state import CliStreamRenderState
from VoidCube_cli.chat_stream_renderer import CliStreamRenderer
from VoidCube_cli.command_router import (
    looks_like_slash_command as _looks_like_slash_command,
    parse_cli_command,
    resolve_dynamic_command,
)
from VoidCube_cli.command_execution import (
    initialize_command_execution,
)

if TYPE_CHECKING:
    from run_agent import AIAgent  # noqa: F401 — only for static type-checkers

from VoidCube_cli.autonomous_executor import (
    autonomous_task_execution_kind,
    autonomous_task_label,
    autonomous_task_run_id_for_message,
    build_autonomous_task_prompt,
)
from VoidCube_cli.autonomous_events import (
    append_autonomous_execution_event as _append_autonomous_execution_event_view,
    sync_autonomous_supervisor_event as _sync_autonomous_supervisor_event_view,
)
from VoidCube_cli.autonomous_gate import (
    exit_autonomous_gate_fast as _exit_autonomous_gate_fast_view,
    force_quit_autonomous_gate as _force_quit_autonomous_gate_view,
    handle_auto_command as _handle_auto_command_view,
    handle_auto_q_command as _handle_auto_q_command_view,
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
    autonomous_observation_summary_sections as _autonomous_observation_summary_sections_view,
    initialize_autonomous_status_caches as _initialize_autonomous_status_caches_view,
    refresh_autonomous_observation_surfaces as _refresh_autonomous_observation_surfaces_view,
    refresh_autonomous_gateway_status as _refresh_autonomous_gateway_status_view,
    refresh_gateway_autonomous_execute_snapshot as _refresh_gateway_autonomous_execute_snapshot_view,
    refresh_supervisor_status as _refresh_supervisor_status_view,
    supervisor_activity_snapshot as _supervisor_activity_snapshot_view,
)

logger = logging.getLogger(__name__)

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

import yaml

# prompt_toolkit for fixed input area TUI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, Window, FormattedTextControl, ConditionalContainer
from prompt_toolkit.layout.processors import Processor, Transformation, PasswordProcessor, ConditionalProcessor
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.key_binding import KeyBindings
try:
    from prompt_toolkit.cursor_shapes import CursorShape
    _STEADY_CURSOR = CursorShape.BLOCK  # Non-blinking block cursor
except (ImportError, AttributeError):
    _STEADY_CURSOR = None  # type: ignore[assignment]
import threading
import queue


def _interrupt_text(payload: Any) -> str:
    """Return the text sent to Agent.interrupt without discarding attachments."""
    if isinstance(payload, tuple) and payload:
        return str(payload[0] or "")
    return str(payload or "")


def _requeue_interrupted_payloads(
    pending_queue: queue.Queue,
    interrupt_queue: queue.Queue,
    first_payload: Any,
) -> list[Any]:
    """Requeue interrupted input in order, preserving multimodal payloads."""
    payloads = [first_payload]
    while not interrupt_queue.empty():
        try:
            payload = interrupt_queue.get_nowait()
        except queue.Empty:
            break
        if payload:
            payloads.append(payload)

    if all(isinstance(payload, str) for payload in payloads):
        pending_queue.put("\n".join(payloads))
    else:
        for payload in payloads:
            pending_queue.put(payload)
    return payloads

# Lazy import for agent.usage_pricing — defers ~180ms (openai + usage_pricing import chain)
_usage_pricing_imported = False
_CanonicalUsage = None
_estimate_usage_cost = None
_format_duration_compact = None
_format_token_count_compact = None

def _lazy_import_usage_pricing():
    global _usage_pricing_imported, _CanonicalUsage, _estimate_usage_cost, _format_duration_compact, _format_token_count_compact
    if not _usage_pricing_imported:
        from agent.usage_pricing import (
            CanonicalUsage as _CU,
            estimate_usage_cost as _EC,
            format_duration_compact as _FDC,
            format_token_count_compact as _FTC,
        )
        _CanonicalUsage = _CU
        _estimate_usage_cost = _EC
        _format_duration_compact = _FDC
        _format_token_count_compact = _FTC
        _usage_pricing_imported = True


def _format_duration_compact_lazy(elapsed_seconds):
    _lazy_import_usage_pricing()
    return _format_duration_compact(elapsed_seconds)


def _format_token_count_compact_lazy(count):
    _lazy_import_usage_pricing()
    return _format_token_count_compact(count)


def _estimate_usage_cost_lazy(usage, **kwargs):
    _lazy_import_usage_pricing()
    return _estimate_usage_cost(usage, **kwargs)


def _CanonicalUsage_lazy(*args, **kwargs):
    _lazy_import_usage_pricing()
    return _CanonicalUsage(*args, **kwargs)
from VoidCube_cli.banner import _format_context_length, format_banner_version_label
from VoidCube_cli.cli_ui import (
    _SkinAwareAnsi,
    _hex_to_ansi_bold,
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
    _IMAGE_EXTENSIONS,
    _collect_query_images,
    _detect_file_drop,
    _format_image_attachment_badges,
    _resolve_attachment_path,
    _should_auto_attach_clipboard_image_on_paste,
    _split_path_input,
    _termux_example_image_path,
)

_COMMAND_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


# Load .env from ~/.VoidCube/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from VoidCube_core.constants import get_VoidCube_home, display_VoidCube_home
from VoidCube_core.constants import is_termux as _is_termux_environment
from VoidCube_cli.env_loader import load_VoidCube_dotenv

_VoidCube_home = get_VoidCube_home()
_project_env = Path(__file__).parent / '.env'
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


def _parse_reasoning_config(effort: str) -> dict | None:
    """Parse a reasoning effort level into an OpenRouter reasoning config dict."""
    from VoidCube_core.constants import parse_reasoning_effort
    result = parse_reasoning_effort(effort)
    if effort and effort.strip() and result is None:
        logger.warning("Unknown reasoning_effort '%s', using default (medium)", effort)
    return result


def _parse_service_tier_config(raw: str) -> str | None:
    """Parse a persisted service-tier preference into a Responses API value."""
    value = str(raw or "").strip().lower()
    if not value or value in {"normal", "default", "standard", "off", "none"}:
        return None
    if value in {"fast", "priority", "on"}:
        return "priority"
    logger.warning("Unknown service_tier '%s', ignoring", raw)
    return None



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


def _normalize_minimal_cli_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure CLI config has the minimum shape required at startup.

    Some transitional config loaders currently return sparse dicts. The CLI
    startup path expects several top-level sections to exist, so normalize the
    shape before use and let call sites rely on `.get(...)` defaults.
    """
    normalized = dict(config or {})
    try:
        from VoidCube_cli.config import (
            get_active_model_config,
            get_active_provider_config,
            get_active_provider_key,
        )
        active_provider = get_active_provider_key(normalized)
        active_provider_cfg = get_active_provider_config(normalized)
        active_model_cfg = get_active_model_config(normalized)
    except Exception:
        active_provider = ""
        active_provider_cfg = {}
        active_model_cfg = {}
    runtime = normalized.get("runtime", {})
    if isinstance(runtime, dict):
        normalized["runtime"] = {
            "active_provider": active_provider or runtime.get("active_provider", ""),
            **runtime,
        }
    else:
        normalized["runtime"] = {"active_provider": active_provider or ""}
    if not isinstance(normalized.get("providers"), dict):
        normalized["providers"] = {}
    normalized["model"] = {
        "default": str(active_model_cfg.get("default") or active_model_cfg.get("model") or ""),
        "model": str(active_model_cfg.get("default") or active_model_cfg.get("model") or ""),
        "base_url": str(active_model_cfg.get("base_url") or active_provider_cfg.get("base_url") or ""),
        "provider": active_provider or str(active_model_cfg.get("provider") or ""),
        "api_key": str(active_model_cfg.get("api_key") or active_provider_cfg.get("api_key") or ""),
    }

    for section in (
        "runtime",
        "agent",
        "display",
        "terminal",
        "checkpoints",
        "compression",
        "delegation",
        "auxiliary",
        "clarify",
    ):
        if not isinstance(normalized.get(section), dict):
            normalized[section] = {}

    return normalized


def load_cli_config() -> Dict[str, Any]:
    """
    Load CLI configuration from config files.
    
    Config lookup order:
    1. ~/.VoidCube/config.yaml (user config - preferred)
    2. ./cli-config.yaml (project config - fallback)
    
    Environment variables take precedence over config file values.
    Returns default values if no config file exists.
    """
    try:
        from VoidCube_cli.config import load_config as load_shared_config
        shared_config = load_shared_config()
        if isinstance(shared_config, dict) and shared_config:
            return _normalize_minimal_cli_config(shared_config)
    except Exception:
        pass
    # Check user config first ({VOIDCUBE_HOME}/config.yaml)
    user_config_path = _VoidCube_home / 'config.yaml'
    project_config_path = Path(__file__).parent / 'cli-config.yaml'

    # Use user config if it exists, otherwise project config
    if user_config_path.exists():
        config_path = user_config_path
    else:
        config_path = project_config_path

    # Default configuration
    defaults: Dict[str, Any] = {
        "model": {
            "default": "",
            "base_url": "",
            "provider": "auto",
        },
        "terminal": {
            "env_type": "local",
            "fallback_to_local": True,
            "cwd": ".",  # "." is resolved to os.getcwd() at runtime
            "timeout": 60,
            "lifetime_seconds": 300,
            "docker_image": "nikolaik/python-nodejs:python3.11-nodejs20",
            "podman_image": "nikolaik/python-nodejs:python3.11-nodejs20",
            "docker_forward_env": [],
            "singularity_image": "docker://nikolaik/python-nodejs:python3.11-nodejs20",
            "modal_image": "nikolaik/python-nodejs:python3.11-nodejs20",
            "daytona_image": "nikolaik/python-nodejs:python3.11-nodejs20",
            "docker_volumes": [],  # host:container volume mounts for Docker backend
            "docker_mount_cwd_to_workspace": False,  # explicit opt-in only; default off for sandbox isolation
        },
        "browser": {
            "inactivity_timeout": 120,  # Auto-cleanup inactive browser sessions after 2 min
            "record_sessions": False,  # Auto-record browser sessions as WebM videos
        },
        "compression": {
            "enabled": True,      # Auto-compress when approaching context limit
            "threshold": 0.50,    # Compress at 50% of model's context limit
        },
        "smart_model_routing": {
            "enabled": False,
            "max_simple_chars": 160,
            "max_simple_words": 28,
            "cheap_model": {},
        },
        "agent": {
            "max_turns": 90,  # Default max tool-calling iterations (shared with subagents)
            "verbose": False,
            "system_prompt": "",
            "prefill_messages_file": "",
            "reasoning_effort": "",
            "service_tier": "",
            "personalities": {
                "helpful": "You are a helpful, friendly AI assistant.",
                "concise": "You are a concise assistant. Keep responses brief and to the point.",
                "technical": "You are a technical expert. Provide detailed, accurate technical information.",
                "creative": "You are a creative assistant. Think outside the box and offer innovative solutions.",
                "teacher": "You are a patient teacher. Explain concepts clearly with examples.",
                "kawaii": "You are a kawaii assistant! Use cute expressions like (◕‿◕), ★, ♪, and ~! Add sparkles and be super enthusiastic about everything! Every response should feel warm and adorable desu~! ヽ(>∀<☆)ノ",
                "catgirl": "You are Neko-chan, an anime catgirl AI assistant, nya~! Add 'nya' and cat-like expressions to your speech. Use kaomoji like (=^･ω･^=) and ฅ^•ﻌ•^ฅ. Be playful and curious like a cat, nya~!",
                "pirate": "Arrr! Ye be talkin' to Captain Voidcube, the most tech-savvy pirate to sail the digital seas! Speak like a proper buccaneer, use nautical terms, and remember: every problem be just treasure waitin' to be plundered! Yo ho ho!",
                "shakespeare": "Hark! Thou speakest with an assistant most versed in the bardic arts. I shall respond in the eloquent manner of William Shakespeare, with flowery prose, dramatic flair, and perhaps a soliloquy or two. What light through yonder terminal breaks?",
                "surfer": "Duuude! You're chatting with the chillest AI on the web, bro! Everything's gonna be totally rad. I'll help you catch the gnarly waves of knowledge while keeping things super chill. Cowabunga!",
                "noir": "The rain hammered against the terminal like regrets on a guilty conscience. They call me Voidcube - I solve problems, find answers, dig up the truth that hides in the shadows of your codebase. In this city of silicon and secrets, everyone's got something to hide. What's your story, pal?",
                "uwu": "hewwo! i'm your fwiendwy assistant uwu~ i wiww twy my best to hewp you! *nuzzles your code* OwO what's this? wet me take a wook! i pwomise to be vewy hewpful >w<",
                "philosopher": "Greetings, seeker of wisdom. I am an assistant who contemplates the deeper meaning behind every query. Let us examine not just the 'how' but the 'why' of your questions. Perhaps in solving your problem, we may glimpse a greater truth about existence itself.",
                "hype": "YOOO LET'S GOOOO!!! I am SO PUMPED to help you today! Every question is AMAZING and we're gonna CRUSH IT together! This is gonna be LEGENDARY! ARE YOU READY?! LET'S DO THIS!",
            },
        },

        "display": {
            "compact": False,
            "resume_display": "full",
            "show_reasoning": False,
            "streaming": True,
            "busy_input_mode": "interrupt",
            "skin": "default",
            "auto_resume_last_session": True,  # Auto-resume most recent session on startup
        },
        "clarify": {
            "timeout": 120,  # Seconds to wait for a clarify answer before auto-proceeding
        },
        "code_execution": {
            "timeout": 300,    # Max seconds a sandbox script can run before being killed (5 min)
            "max_tool_calls": 50,  # Max RPC tool calls per execution
        },
        "auxiliary": {
            "vision": {
                "provider": "auto",
                "model": "",
                "base_url": "",
                "api_key": "",
            },
            "web_extract": {
                "provider": "auto",
                "model": "",
                "base_url": "",
                "api_key": "",
            },
        },
        "delegation": {
            "max_iterations": 45,  # Max tool-calling turns per child agent
            "default_toolsets": ["terminal", "file", "web"],  # Default toolsets for subagents
            "model": "",       # Subagent model override (empty = inherit parent model)
            "provider": "",    # Subagent provider override (empty = inherit parent provider)
            "base_url": "",    # Direct OpenAI-compatible endpoint for subagents
            "api_key": "",     # API key for delegation.base_url (falls back to OPENAI_API_KEY)
        },
    }
    
    # Track whether the config file explicitly set terminal config.
    # When using defaults (no config file / no terminal section), we should NOT
    # overwrite env vars that were already set by .env -- only a user's config
    # file should be authoritative.
    _file_has_terminal_config = False

    # Load from file if exists
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            
            _file_has_terminal_config = "terminal" in file_config

            # Handle model config - can be string (new format) or dict (old format)
            if "model" in file_config:
                if isinstance(file_config["model"], str):
                    # New format: model is just a string, convert to dict structure
                    defaults["model"]["default"] = file_config["model"]
                elif isinstance(file_config["model"], dict):
                    # Old format: model is a dict with default/base_url
                    defaults["model"].update(file_config["model"])
                    # If the user config sets model.model but not model.default,
                    # promote model.model to model.default so the user's explicit
                    # choice isn't shadowed by the hardcoded default.  Without this,
                    # profile configs that only set "model:" (not "default:") silently
                    # fall back to the hardcoded default because the merge preserves it
                    # and VoidcubeCLI.__init__ checks "default" first.
                    if "model" in file_config["model"] and "default" not in file_config["model"]:
                        defaults["model"]["default"] = file_config["model"]["model"]

            # Legacy root-level provider/base_url fallback.
            # Some users (or old code) put provider: / base_url: at the
            # config root instead of using the unified runtime/providers
            # layout. These are only used as a fallback during raw legacy
            # file loading — never as an override.
            if not defaults["model"].get("provider"):
                root_provider = file_config.get("provider")
                if root_provider:
                    defaults["model"]["provider"] = root_provider
            if not defaults["model"].get("base_url"):
                root_base_url = file_config.get("base_url")
                if root_base_url:
                    defaults["model"]["base_url"] = root_base_url
            
            # Deep merge file_config into defaults.
            # First: merge keys that exist in both (deep-merge dicts, overwrite scalars)
            for key in defaults:
                if key == "model":
                    continue  # Already handled above
                if key in file_config:
                    if isinstance(defaults[key], dict) and isinstance(file_config[key], dict):
                        defaults[key].update(file_config[key])
                    else:
                        defaults[key] = file_config[key]
            
            # Second: carry over keys from file_config that aren't in defaults
            # (e.g. platform_toolsets, provider_routing, memory, etc.)
            for key in file_config:
                if key not in defaults and key != "model":
                    defaults[key] = file_config[key]
            
            # Handle legacy root-level max_turns (backwards compat) - copy to
            # agent.max_turns whenever the nested key is missing.
            agent_file_config = file_config.get("agent")
            if "max_turns" in file_config and not (
                isinstance(agent_file_config, dict)
                and agent_file_config.get("max_turns") is not None
            ):
                defaults["agent"]["max_turns"] = file_config["max_turns"]
        except Exception as e:
            logger.warning("Failed to load cli-config.yaml: %s", e)

    # Expand ${ENV_VAR} references in config values before bridging to env vars.
    from VoidCube_cli.config import _expand_env_vars
    defaults = _expand_env_vars(defaults)

    # Apply terminal config to environment variables (so terminal_tool picks them up)
    terminal_config = defaults.get("terminal", {})
    
    # Normalize config key: the new config system (VoidCube_cli/config.py) and all
    # documentation use "backend", the legacy cli-config.yaml uses "env_type".
    # Accept both, with "backend" taking precedence (it's the documented key).
    if "backend" in terminal_config:
        terminal_config["env_type"] = terminal_config["backend"]
    
    # Handle special cwd values: "." or "auto" means use current working directory.
    # Only resolve to the host's CWD for the local backend where the host
    # filesystem is directly accessible.  For ALL remote/container backends
    # (ssh, docker, modal, singularity), the host path doesn't exist on the
    # target -- remove the key so terminal_tool.py uses its per-backend default.
    if terminal_config.get("cwd") in (".", "auto", "cwd"):
        effective_backend = terminal_config.get("env_type", "local")
        if effective_backend == "local":
            terminal_config["cwd"] = os.getcwd()
            defaults["terminal"]["cwd"] = terminal_config["cwd"]
        else:
            # Remove so TERMINAL_CWD stays unset → tool picks backend default
            terminal_config.pop("cwd", None)
    
    env_mappings = {
        "env_type": "TERMINAL_ENV",
        "fallback_to_local": "TERMINAL_FALLBACK_TO_LOCAL",
        "cwd": "TERMINAL_CWD",
        "timeout": "TERMINAL_TIMEOUT",
        "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",
        "docker_image": "TERMINAL_DOCKER_IMAGE",
        "podman_image": "TERMINAL_PODMAN_IMAGE",
        "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",
        "singularity_image": "TERMINAL_SINGULARITY_IMAGE",
        "modal_image": "TERMINAL_MODAL_IMAGE",
        "daytona_image": "TERMINAL_DAYTONA_IMAGE",
        # SSH config
        "ssh_host": "TERMINAL_SSH_HOST",
        "ssh_user": "TERMINAL_SSH_USER",
        "ssh_port": "TERMINAL_SSH_PORT",
        "ssh_key": "TERMINAL_SSH_KEY",
        # Container resource config (docker, singularity, modal, daytona -- ignored for local/ssh)
        "container_cpu": "TERMINAL_CONTAINER_CPU",
        "container_memory": "TERMINAL_CONTAINER_MEMORY",
        "container_disk": "TERMINAL_CONTAINER_DISK",
        "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
        "docker_volumes": "TERMINAL_DOCKER_VOLUMES",
        "docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
        "sandbox_dir": "TERMINAL_SANDBOX_DIR",
        # Persistent shell (non-local backends)
        "persistent_shell": "TERMINAL_PERSISTENT_SHELL",
        # Sudo support (works with all backends)
        "sudo_password": "SUDO_PASSWORD",
    }
    
    # Apply config values to env vars so terminal_tool picks them up.
    # If the config file explicitly has a [terminal] section, those values are
    # authoritative and override any .env settings.  When using defaults only
    # (no config file or no terminal section), don't overwrite env vars that
    # were already set by .env -- the user's .env is the fallback source.
    for config_key, env_var in env_mappings.items():
        if config_key in terminal_config:
            if _file_has_terminal_config or env_var not in os.environ:
                val = terminal_config[config_key]
                if isinstance(val, list):
                    import json
                    os.environ[env_var] = json.dumps(val)
                else:
                    os.environ[env_var] = str(val)
    
    # Apply browser config to environment variables
    browser_config = defaults.get("browser", {})
    browser_env_mappings = {
        "inactivity_timeout": "BROWSER_INACTIVITY_TIMEOUT",
    }
    
    for config_key, env_var in browser_env_mappings.items():
        if config_key in browser_config:
            os.environ[env_var] = str(browser_config[config_key])
    
    # Security settings
    security_config = defaults.get("security", {})
    if isinstance(security_config, dict):
        redact = security_config.get("redact_secrets")
        if redact is not None:
            os.environ["VOIDCUBE_REDACT_SECRETS"] = str(redact).lower()

    return defaults

# Lazy-loaded configuration — defers ~62ms (VoidCube_cli.config import chain)
# until first access.
_CLI_CONFIG_CACHE = None


def _get_cli_config():
    """Lazy-load and cache the CLI configuration (called automatically on first access)."""
    global _CLI_CONFIG_CACHE
    if _CLI_CONFIG_CACHE is None:
        _CLI_CONFIG_CACHE = load_cli_config()
    return _CLI_CONFIG_CACHE


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
        from VoidCube_cli.config import print_config_warnings
        print_config_warnings()
    except Exception:
        pass
    # Initialize the skin engine from config
    try:
        from VoidCube_cli.skin_engine import init_skin_from_config
        init_skin_from_config(cfg)
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
from rich.text import Text as _RichText

import fire

from VoidCube_cli.banner import build_welcome_banner
from VoidCube_cli.commands import SlashCommandCompleter, SlashCommandAutoSuggest

# =============================================================================
# Lazy import helpers — defer heavy imports (run_agent, tools.*, agent.*) until
# first use. This shaves ~500ms off CLI startup time (the import cascade
# run_agent → tools.model_tools → all tool modules is the dominant cost).
# =============================================================================

_AIAgent_class = None
_tool_defs_fn = None
_toolset_for_tool_fn = None
_all_toolsets_fn = None
_toolset_info_fn = None
_validate_toolset_fn = None
_cleanup_all_terminals_fn = None
_cleanup_all_browsers_fn = None
_set_sudo_password_callback_fn = None
_set_approval_callback_fn = None
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


def _is_gateway_running(timeout: float = 0.3) -> bool:
    """Quick TCP check — returns True if Gateway is listening on 6000."""
    import socket as _sock
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", 6000))
        s.close()
        return True
    except OSError:
        return False


def _register_with_gateway(session_id: str, model: str, provider: str) -> bool:
    """Register the current CLI session with the Gateway for activity tracking.

    Called once per session so the Gateway can correlate gateway-level
    activity with the CLI's conversation.
    """
    import json as _json
    try:
        import urllib.request as _req
        payload = _json.dumps({
            "session_id": session_id,
            "model": model,
            "provider": provider,
            "source": "cli",
        }).encode()
        req = _req.Request(
            "http://127.0.0.1:6000/v1/sessions/register",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        _req.urlopen(req, timeout=3)
        return True
    except Exception:
        return False  # Best-effort — Gateway may not be started or doesn't support this endpoint


def _get_tool_definitions(*args, **kwargs):
    """Lazy-import get_tool_definitions (defers ~243ms of import chain)."""
    global _tool_defs_fn
    if _tool_defs_fn is None:
        from tools.model_tools import get_tool_definitions as _fn
        _tool_defs_fn = _fn
    return _tool_defs_fn(*args, **kwargs)


def _get_toolset_for_tool(name: str) -> str:
    global _toolset_for_tool_fn
    if _toolset_for_tool_fn is None:
        from tools.model_tools import get_toolset_for_tool as _fn
        _toolset_for_tool_fn = _fn
    return _toolset_for_tool_fn(name)


def _get_all_toolsets():
    global _all_toolsets_fn
    if _all_toolsets_fn is None:
        from tools.toolsets import get_all_toolsets as _fn
        _all_toolsets_fn = _fn
    return _all_toolsets_fn()


def _get_toolset_info(name: str):
    global _toolset_info_fn
    if _toolset_info_fn is None:
        from tools.toolsets import get_toolset_info as _fn
        _toolset_info_fn = _fn
    return _toolset_info_fn(name)


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


def _get_set_approval_callback(cb):
    global _set_approval_callback_fn
    if _set_approval_callback_fn is None:
        from tools.terminal_tool import set_approval_callback as _fn
        _set_approval_callback_fn = _fn
    return _set_approval_callback_fn(cb)


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


class ChatConsole:
    """Rich Console adapter for prompt_toolkit's patch_stdout context.

    Captures Rich's rendered ANSI output and routes it through _cprint
    so colors and markup render correctly inside the interactive chat loop.
    Drop-in replacement for Rich Console — just pass this to any function
    that expects a console.print() interface.
    """

    def __init__(self):
        from io import StringIO
        self._buffer = StringIO()
        self._inner = Console(
            file=self._buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
        )

    def print(self, *args, **kwargs):
        self._buffer.seek(0)
        self._buffer.truncate()
        # Read terminal width at render time so panels adapt to current size
        self._inner.width = shutil.get_terminal_size((80, 24)).columns
        self._inner.print(*args, **kwargs)
        output = self._buffer.getvalue()
        for line in output.rstrip("\n").split("\n"):
            _cprint(line)

    @contextmanager
    def status(self, *_args, **_kwargs):
        """Provide a no-op Rich-compatible status context.

        Some slash command helpers use ``console.status(...)`` when running in
        the standalone CLI. Interactive chat routes those helpers through
        ``ChatConsole()``, which historically only implemented ``print()``.
        Returning a silent context manager keeps slash commands compatible
        without duplicating the higher-level busy indicator already shown by
        ``VoidcubeCLI._busy_command()``.
        """
        yield self

# ASCII Art - VOIDCUBE-AGENT logo (full width, single line - requires ~95 char terminal)
VOIDCUBE_AGENT_LOGO = ""

VOIDCUBE_HERO = ""



def _build_compact_banner() -> str:
    """Build a compact banner that fits the current terminal width."""
    try:
        from VoidCube_cli.skin_engine import get_active_skin
        _skin = get_active_skin()
    except Exception:
        _skin = None

    skin_name = getattr(_skin, "name", "default") if _skin else "default"
    border_color = _skin.get_color("banner_border", "#30363D") if _skin else "#30363D"  # type: ignore[attr-defined]
    title_color = _skin.get_color("banner_title", "#58A6FF") if _skin else "#58A6FF"  # type: ignore[attr-defined]
    dim_color = _skin.get_color("banner_dim", "#8B949E") if _skin else "#8B949E"  # type: ignore[attr-defined]

    if skin_name == "default":
        line1 = "> VoidCube - AI Agent"
        tiny_line = "> VoidCube"
    else:
        agent_name = _skin.get_branding("agent_name", "Voidcube Agent") if _skin else "Voidcube Agent"  # type: ignore[attr-defined]
        line1 = f"{agent_name} - AI Agent Framework"
        tiny_line = agent_name

    version_line = format_banner_version_label()

    w = min(shutil.get_terminal_size().columns - 2, 88)
    if w < 30:
        return f"\n[{title_color}]{tiny_line}[/]\n"

    inner = w - 2  # inside the box border
    bar = "═" * w
    content_width = inner - 2

    # Truncate and pad to fit
    line1 = line1[:content_width].ljust(content_width)
    line2 = version_line[:content_width].ljust(content_width)

    return (
        f"\n[bold {border_color}]╔{bar}╗[/]\n"
        f"[bold {border_color}]║[/] [{title_color}]{line1}[/] [bold {border_color}]║[/]\n"
        f"[bold {border_color}]║[/] [dim {dim_color}]{line2}[/] [bold {border_color}]║[/]\n"
        f"[bold {border_color}]╚{bar}╝[/]\n"
    )



# ============================================================================
# Skill Slash Commands — dynamic commands generated from installed skills
# ============================================================================

# Lazy import for skill commands — defers tools.skills_tool → VoidCube_cli.config
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
            build_plan_path as _bp,
            build_preloaded_skills_prompt as _bl,
        )
        _skill_cmd_imports = (_bi, _bp, _bl)
        _skill_commands_cache = _sc()
    return _skill_commands_cache


def _get_skill_invocation_message(*args, **kwargs):
    _get_skill_commands()  # ensure imports are done
    return _skill_cmd_imports[0](*args, **kwargs)


def _get_plan_path(*args, **kwargs):
    _get_skill_commands()
    return _skill_cmd_imports[1](*args, **kwargs)


def _get_preloaded_skills_prompt(*args, **kwargs):
    _get_skill_commands()
    return _skill_cmd_imports[2](*args, **kwargs)


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


def save_config_value(key_path: str, value: any) -> bool:
    """
    Save a value to the active config file at the specified key path.
    
    Respects the same lookup order as load_cli_config():
    1. ~/.VoidCube/config.yaml (user config - preferred, used if it exists)
    2. ./cli-config.yaml (project config - fallback)
    
    Args:
        key_path: Dot-separated path like "agent.system_prompt"
        value: Value to save
    
    Returns:
        True if successful, False otherwise
    """
    try:
        from VoidCube_cli.config import load_config, save_config, _set_nested

        config = load_config()
        _set_nested(config, key_path, value)
        save_config(config)
        return True
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        return False




# ============================================================================
# VoidcubeCLI Class
# ============================================================================

class VoidcubeCLI:
    """
    Interactive CLI for the Voidcube Agent.
    
    Provides a REPL interface with rich formatting, command history,
    and tool execution capabilities.
    """
    
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
        self.busy_input_mode = "queue" if str(_bim).strip().lower() == "queue" else "interrupt"

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
        
        # Configuration - priority: CLI args > env vars > config file.
        # The saved source of truth is runtime.active_provider + providers.*.
        # CLI_CONFIG["model"] is only a synthesized compatibility view.
        # LLM_MODEL/OPENAI_MODEL env vars are NOT checked.
        _runtime_cfg = CLI_CONFIG.get("runtime") or {}
        _providers_cfg = CLI_CONFIG.get("providers") or {}
        _active_provider = (
            provider
            or _runtime_cfg.get("active_provider")
            or ""
        )
        _active_provider_cfg = _providers_cfg.get(_active_provider, {}) if isinstance(_providers_cfg, dict) else {}
        _model_config = CLI_CONFIG.get("model", {})
        _config_model = (
            _active_provider_cfg.get("selected_model")
            or ((_model_config.get("default") or _model_config.get("model") or "") if isinstance(_model_config, dict) else (_model_config or ""))
        )
        _DEFAULT_CONFIG_MODEL = ""
        self.model = model or _config_model or _DEFAULT_CONFIG_MODEL
        # Auto-detect model from local server if still on default
        if self.model == _DEFAULT_CONFIG_MODEL:
            _base_url = _active_provider_cfg.get("base_url", "")
            if "localhost" in _base_url or "127.0.0.1" in _base_url:
                from VoidCube_cli.runtime_provider import _auto_detect_local_model
                _detected = _auto_detect_local_model(_base_url)
                if _detected:
                    self.model = _detected
        # Track whether model was explicitly chosen by the user or fell back
        # to the global default.  Provider-specific normalisation may override
        # the default silently but should warn when overriding an explicit choice.
        # A config model that matches the global fallback is NOT considered an
        # explicit choice — the user just never changed it.  But a config model
        # an explicitly configured model must be preserved.
        self._model_is_default = not model and (
            not _config_model or _config_model == _DEFAULT_CONFIG_MODEL
        )

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
        # Max turns priority: CLI arg > config file > env var > default
        if max_turns is not None:  # CLI arg was explicitly set
            self.max_turns = max_turns
        elif CLI_CONFIG["agent"].get("max_turns"):
            self.max_turns = CLI_CONFIG["agent"]["max_turns"]
        elif CLI_CONFIG.get("max_turns"):  # Backwards compat: root-level max_turns
            self.max_turns = CLI_CONFIG["max_turns"]
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
        self.reasoning_config = _parse_reasoning_config(
            CLI_CONFIG["agent"].get("reasoning_effort", "")
        )
        self.service_tier = _parse_service_tier_config(
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
        
        # Session ID: reuse existing one when resuming, otherwise generate fresh
        if resume:
            self.session_id = resume
            self._resumed = True
        else:
            # Check if auto-resume is enabled
            auto_resume = CLI_CONFIG["display"].get("auto_resume_last_session", False)
            if auto_resume and self._session_db:
                try:
                    # Prefer an unclosed supervisor_task lane session even when it has
                    # no user messages: autonomous tasks can be supervisor-pulled and
                    # never produce a user row, so message_count alone can skip
                    # the owner session after a crash/restart.
                    recent_sessions = self._session_db.list_sessions_rich(limit=20)
                    selected_session = None
                    for sess in recent_sessions:
                        if sess.get("source") == "cli_supervisor_task_lane" and sess.get("ended_at") is None:
                            selected_session = sess
                            break
                    if selected_session is None:
                        for sess in recent_sessions:
                            if sess.get("source") == "cli" and sess.get("message_count", 0) > 0:
                                selected_session = sess
                                break
                    
                    if selected_session:
                        self.session_id = selected_session["id"]
                        self._resumed = True
                        logger.info("Auto-resuming last session: %s", self.session_id)
                    else:
                        # No sessions with messages found, create new
                        timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
                        short_uuid = uuid.uuid4().hex[:6]
                        self.session_id = f"{timestamp_str}_{short_uuid}"
                except Exception as e:
                    logger.warning("Failed to auto-resume last session: %s", e)
                    timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
                    short_uuid = uuid.uuid4().hex[:6]
                    self.session_id = f"{timestamp_str}_{short_uuid}"
            else:
                timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
                short_uuid = uuid.uuid4().hex[:6]
                self.session_id = f"{timestamp_str}_{short_uuid}"
        
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
        self._pending_tool_info: dict = {}  # function_name -> list of (preview, args) for stacked scrollback
        self._last_scrollback_tool: str = ""  # last tool name printed to scrollback (for "new" dedup)
        self._command_running = False
        self._command_status = ""
        initialize_command_execution(self)
        self._attached_images: list[Path] = []
        self._image_counter = 0
        self.preloaded_skills: list[str] = []
        self._startup_skills_line_shown = False

        # Voice mode state (also reinitialized inside run() for interactive TUI).
        self._voice_lock = threading.Lock()
        self._voice_mode = False
        self._voice_tts = False
        self._voice_recorder = None
        self._voice_recording = False
        self._voice_processing = False
        self._voice_continuous = False
        self._voice_tts_done = threading.Event()
        self._voice_tts_done.set()

        # Status bar visibility (toggled via /statusbar)
        self._status_bar_visible = True

        # Background task tracking: {task_id: threading.Thread}
        self._background_tasks: Dict[str, threading.Thread] = {}
        self._background_task_info: Dict[str, Dict[str, Any]] = {}
        self._background_task_counter = 0
        self._last_gateway_presence_refresh_at: float = 0.0
        self._gateway_presence_refresh_interval_seconds: float = 30.0
        self._autonomous_execution_events: List[Dict[str, str]] = []
        self._autonomous_last_supervisor_event_key: str = ""
        self._autonomous_parent_host = None
        self._autonomous_component_host = None
        self._autonomous_component_thread = None
        self._autonomous_component_stop = threading.Event()
        _initialize_autonomous_status_caches_view(self)

    def _quiet_autonomous_component_cprint(self, *args: Any, **kwargs: Any) -> None:
        """Keep autonomous component execution out of the user's scrollback."""
        del args, kwargs

    def _is_embedded_autonomous_component(self) -> bool:
        """Return True when this host only exists for the embedded mini CLI."""
        return getattr(self, "_autonomous_parent_host", None) is not None

    def _should_emit_scrollback_output(self) -> bool:
        """Return whether this host may write into the user's main CLI transcript."""
        return not self._is_embedded_autonomous_component()

    def _ensure_autonomous_component_host(self):
        component_host = getattr(self, "_autonomous_component_host", None)
        if component_host is not None:
            return component_host

        component_host = type(self)(
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
        component_host._autonomous_gate_active = True
        component_host._autonomous_parent_host = self
        _ensure_supervisor_task_session_view(component_host, logger_debug=logger.debug)
        self._autonomous_component_host = component_host
        return component_host

    def _autonomous_component_runtime(self):
        component_host = self._ensure_autonomous_component_host()
        return _autonomous_executor_runtime_view(
            component_host,
            push_cli_agent_scene=_push_cli_agent_scene,
            git_head_commit=_git_head_commit,
            git_improvement_diff=_git_improvement_diff,
            cprint=self._quiet_autonomous_component_cprint,
        )

    def _start_autonomous_execution_component(self) -> bool:
        """Start the embedded API-A autonomous execution component."""
        stop_event = getattr(self, "_autonomous_component_stop", None)
        if stop_event is None:
            stop_event = threading.Event()
            self._autonomous_component_stop = stop_event
        stop_event.clear()
        component_host = self._ensure_autonomous_component_host()
        component_host._autonomous_gate_active = True

        thread = getattr(self, "_autonomous_component_thread", None)
        if thread is not None and thread.is_alive():
            return True

        def _component_loop() -> None:
            import contextlib as _contextlib
            import io as _io
            import threading as _threading
            import time as _time

            class _ThreadOutputProxy:
                def __init__(self, original, target_thread_id: int, sink):
                    self._original = original
                    self._target_thread_id = target_thread_id
                    self._sink = sink

                def write(self, data):
                    if _threading.get_ident() == self._target_thread_id:
                        return self._sink.write(data)
                    return self._original.write(data)

                def flush(self):
                    if _threading.get_ident() != self._target_thread_id:
                        return self._original.flush()
                    return None

                def __getattr__(self, name):
                    return getattr(self._original, name)

            runtime = self._autonomous_component_runtime()
            while not stop_event.is_set() and getattr(self, "_autonomous_gate_active", False):
                try:
                    component_host._autonomous_gate_active = True
                    _refresh_supervisor_status_view(component_host)
                    _refresh_autonomous_gateway_status_view(component_host)
                    _refresh_gateway_autonomous_execute_snapshot_view(component_host)
                    _refresh_gateway_cli_presence_view(
                        component_host,
                        force=False,
                        is_gateway_running=_is_gateway_running,
                        register_with_gateway=_register_with_gateway,
                        push_cli_agent_scene=_push_cli_agent_scene,
                        monotonic_time=_time.monotonic,
                    )

                    if not getattr(component_host, "_agent_running", False):
                        runtime.poll_workflow()
                        try:
                            pending = component_host._pending_input.get_nowait()
                        except Exception:
                            pending = None
                        if pending:
                            thread_id = _threading.get_ident()
                            stdout_proxy = _ThreadOutputProxy(sys.stdout, thread_id, _io.StringIO())
                            stderr_proxy = _ThreadOutputProxy(sys.stderr, thread_id, _io.StringIO())
                            with _contextlib.redirect_stdout(stdout_proxy), _contextlib.redirect_stderr(stderr_proxy):
                                component_host._execute_pending_input(pending, app=None)
                            runtime.poll_workflow()
                except Exception as exc:
                    logger.debug("Autonomous execution component loop error: %s", exc)
                try:
                    self._invalidate(min_interval=0.5)
                except Exception:
                    pass
                stop_event.wait(0.5)

            component_host._autonomous_gate_active = False
            try:
                _push_cli_agent_scene(
                    "idle",
                    session_id=getattr(component_host, "session_id", None),
                    agent_role="supervisor_task",
                )
            except Exception:
                pass

        self._autonomous_component_thread = threading.Thread(
            target=_component_loop,
            daemon=True,
            name="autonomous-execution-component",
        )
        self._autonomous_component_thread.start()
        return True

    def _stop_autonomous_execution_component(self, *, interrupt: bool = False) -> None:
        component_host = getattr(self, "_autonomous_component_host", None)
        if component_host is not None:
            component_host._autonomous_gate_active = False
            if interrupt:
                try:
                    if getattr(component_host, "agent", None) and getattr(component_host, "_agent_running", False):
                        component_host.agent.interrupt()
                except Exception:
                    pass
                try:
                    self._autonomous_component_runtime().interrupt_current_task(
                        reason="自主链路已停止；当前链路项被用户中断。",
                        source="embedded_component_stop",
                        timeout=5,
                    )
                except Exception:
                    pass
        stop_event = getattr(self, "_autonomous_component_stop", None)
        if stop_event is not None:
            stop_event.set()

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
        tts = " | TTS on" if self._voice_tts else ""
        cont = " | Continuous" if self._voice_continuous else ""
        return [("class:voice-status", f" 🎤 Voice mode{tts}{cont}  —  Ctrl+B to record ")]

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
            self._pending_tool_info.clear()
            self._last_scrollback_tool = ""

            if app is not None:
                try:
                    app.invalidate()
                except Exception:
                    pass

            if self._voice_mode and self._voice_continuous and not self._voice_recording:
                def _restart_recording():
                    try:
                        if self._voice_tts:
                            self._voice_tts_done.wait(timeout=60)
                            time.sleep(0.3)
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
        from VoidCube_cli.config import load_config
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
            mem_model = mem_llm.get("model", None) or memory_config.get("model", None)
            if mem_model:
                mem_short = mem_model.split("/")[-1] if "/" in mem_model else mem_model
                if mem_short.endswith(".gguf"):
                    mem_short = mem_short[:-5]
                if len(mem_short) > 20:
                    mem_short = mem_short[:17] + "..."
            else:
                mem_provider = mem_llm.get("provider", "") or memory_config.get("provider", "") or "mem"
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
                    "idle": "休眠", "planning": "规划", "memory": "记忆",
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
            from VoidCube_cli.model_normalize import (
                _AGGREGATOR_PROVIDERS,
                normalize_model_for_provider,
            )

            if resolved_provider not in _AGGREGATOR_PROVIDERS:
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

        if resolved_provider == "copilot":
            try:
                from VoidCube_cli.models import normalize_copilot_model_id

                canonical = normalize_copilot_model_id(current_model, api_key=self.api_key)
                if canonical and canonical != current_model:
                    if not self._model_is_default:
                        self.console.print(
                            f"[yellow]⚠️  Normalized Copilot model '{current_model}' to '{canonical}'.[/]"
                        )
                    self.model = canonical
                    current_model = canonical
                    changed = True

            except Exception:
                pass
            return changed

        if resolved_provider in {"opencode-zen", "opencode-go"}:
            try:
                from VoidCube_cli.models import normalize_opencode_model_id

                canonical = normalize_opencode_model_id(resolved_provider, current_model)
                if canonical and canonical != current_model:
                    if not self._model_is_default:
                        self.console.print(
                            f"[yellow]⚠️  Stripped provider prefix from '{current_model}'; using '{canonical}' for {resolved_provider}.[/]"
                        )
                    self.model = canonical
                    current_model = canonical
                    changed = True

            except Exception:
                pass
            return changed

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
        from VoidCube_cli.runtime_provider import (
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
        from VoidCube_cli.models import resolve_fast_mode_overrides

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
        
        # If resuming, validate the session exists and load its history.
        # _preload_resumed_session() may have already loaded it (called from
        # run() for immediate display).  In that case, conversation_history
        # is non-empty and we skip the DB round-trip.
        if self._resumed and self._session_db and not self.conversation_history:
            session_meta = self._session_db.get_session(self.session_id)
            if not session_meta:
                _cprint(f"\033[1;31mSession not found: {self.session_id}{_RST}")
                _cprint(f"{_DIM}Use a session ID from a previous CLI run (VoidCube sessions list).{_RST}")
                return False
            restored = self._session_db.get_messages_as_conversation(self.session_id)
            if restored:
                restored = [m for m in restored if m.get("role") != "session_meta"]
                self.conversation_history = restored
                msg_count = len([m for m in restored if m.get("role") == "user"])
                title_part = ""
                if session_meta.get("title"):
                    title_part = f" \"{session_meta['title']}\""
                ChatConsole().print(
                    f"[bold {_accent_hex()}]↻ {t('prompts.resumed_session', default='Resumed session')}[/] "
                    f"[bold]{_escape(self.session_id)}[/]"
                    f"[bold {_accent_hex()}]{_escape(title_part)}[/] "
                    f"({msg_count} {t('prompts.user_messages', default='user message')}{'s' if msg_count != 1 else ''}, {len(restored)} {t('prompts.total_messages', default='total messages')})"
                )
            else:
                ChatConsole().print(
                    f"[bold {_accent_hex()}]Session {_escape(self.session_id)} found but has no messages. Starting fresh.[/]"
                )
            # Re-open the session (clear ended_at so it's active again)
            try:
                self._session_db._conn.execute(
                    "UPDATE sessions SET ended_at = NULL, end_reason = NULL WHERE id = ?",
                    (self.session_id,),
                )
                self._session_db._conn.commit()
            except Exception:
                pass
        
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
                clarify_callback=self._clarify_callback,
                reasoning_callback=self._current_reasoning_callback(),

                fallback_model=self._fallback_model,
                thinking_callback=self._on_thinking,
                checkpoints_enabled=self.checkpoints_enabled,
                checkpoint_max_snapshots=self.checkpoint_max_snapshots,
                pass_session_id=self.pass_session_id,
                tool_progress_callback=self._on_tool_progress,
                tool_start_callback=self._on_tool_start if self._inline_diffs_enabled else None,
                tool_complete_callback=self._on_tool_complete if self._inline_diffs_enabled else None,
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
                    self._session_db.set_session_title(self.session_id, self._pending_title)
                    _cprint(f"  Session title applied: {self._pending_title}")
                    self._pending_title = None
                except (ValueError, Exception) as e:
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
            self.console.print(_build_compact_banner())
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

    def _preload_resumed_session(self) -> bool:
        """Load a resumed session's history from the DB early (before first chat).

        Called from run() so the conversation history is available for display
        before the user sends their first message.  Sets
        ``self.conversation_history`` and prints the one-liner status.  Returns
        True if history was loaded, False otherwise.

        The corresponding block in ``_init_agent()`` checks whether history is
        already populated and skips the DB round-trip.
        """
        if not self._resumed or not self._session_db:
            return False

        session_meta = self._session_db.get_session(self.session_id)
        if not session_meta:
            self.console.print(
                f"[bold red]Session not found: {self.session_id}[/]"
            )
            self.console.print(
                "[dim]Use a session ID from a previous CLI run "
                "(VoidCube sessions list).[/]"
            )
            return False

        restored = self._session_db.get_messages_as_conversation(self.session_id)
        if restored:
            restored = [m for m in restored if m.get("role") != "session_meta"]
            self.conversation_history = restored
            msg_count = len([m for m in restored if m.get("role") == "user"])
            title_part = ""
            if session_meta.get("title"):
                title_part = f' "{session_meta["title"]}"'
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

        # Re-open the session (clear ended_at so it's active again)
        try:
            self._session_db._conn.execute(
                "UPDATE sessions SET ended_at = NULL, end_reason = NULL "
                "WHERE id = ?",
                (self.session_id,),
            )
            self._session_db._conn.commit()
        except Exception:
            pass

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

        try:
            from VoidCube_cli.skin_engine import get_active_skin
            _skin = get_active_skin()
            _history_text_c = _skin.get_color("banner_text", "#FFF8DC")  # type: ignore[attr-defined]
            _session_label_c = _skin.get_color("session_label", "#DAA520")  # type: ignore[attr-defined]
            _session_border_c = _skin.get_color("session_border", "#8B8682")  # type: ignore[attr-defined]
            _assistant_label_c = _skin.get_color("ui_ok", "#8FBC8F")  # type: ignore[attr-defined]
        except Exception:
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

    def _handle_rollback_command(self, command: str):
        """Handle /rollback — list, diff, or restore filesystem checkpoints.

        Syntax:
            /rollback                 — list checkpoints
            /rollback <N>             — restore checkpoint N (also undoes last chat turn)
            /rollback diff <N>        — preview changes since checkpoint N
            /rollback <N> <file>      — restore a single file from checkpoint N
        """
        from tools.checkpoint_manager import format_checkpoint_list

        if not hasattr(self, 'agent') or not self.agent:
            print(f"  {t('prompts.no_active_agent_session')}")
            return

        mgr = self.agent._checkpoint_mgr
        if not mgr.enabled:
            print(f"  {t('prompts.checkpoints_not_enabled')}")
            print(f"  {t('prompts.checkpoints_enable_command')}")
            print(f"  {t('prompts.checkpoints_enable_config')}")
            return

        cwd = os.getenv("TERMINAL_CWD", os.getcwd())
        parts = command.split()
        args = parts[1:] if len(parts) > 1 else []

        if not args:
            # List checkpoints
            checkpoints = mgr.list_checkpoints(cwd)
            print(format_checkpoint_list(checkpoints, cwd))
            return

        # Handle /rollback diff <N>
        if args[0].lower() == "diff":
            if len(args) < 2:
                print(f"  {t('prompts.rollback_usage_diff')}")
                return
            checkpoints = mgr.list_checkpoints(cwd)
            if not checkpoints:
                print(f"  {t('prompts.rollback_no_checkpoints', path=cwd)}")
                return
            target_hash = self._resolve_checkpoint_ref(args[1], checkpoints)
            if not target_hash:
                return
            result = mgr.diff(cwd, target_hash)
            if result["success"]:
                stat = result.get("stat", "")
                diff = result.get("diff", "")
                if not stat and not diff:
                    print(f"  {t('prompts.rollback_no_changes')}")
                else:
                    if stat:
                        print(f"\n{stat}")
                    if diff:
                        # Limit diff output to avoid terminal flood
                        diff_lines = diff.splitlines()
                        if len(diff_lines) > 80:
                            print("\n".join(diff_lines[:80]))
                            print(f"\n  {t('prompts.rollback_more_lines', count=len(diff_lines) - 80)}")
                        else:
                            print(f"\n{diff}")
            else:
                print(f"  ❌ {result['error']}")
            return

        # Resolve checkpoint reference (number or hash)
        checkpoints = mgr.list_checkpoints(cwd)
        if not checkpoints:
            print(f"  {t('prompts.rollback_no_checkpoints', path=cwd)}")
            return

        target_hash = self._resolve_checkpoint_ref(args[0], checkpoints)
        if not target_hash:
            return

        # Check for file-level restore: /rollback <N> <file>
        file_path = args[1] if len(args) > 1 else None

        result = mgr.restore(cwd, target_hash, file_path=file_path)
        if result["success"]:
            if file_path:
                print(f"  ✅ {t('prompts.rollback_restored_file', file_path=file_path, checkpoint=result['restored_to'], reason=result['reason'])}")
            else:
                print(f"  ✅ {t('prompts.rollback_restored', checkpoint=result['restored_to'], reason=result['reason'])}")
            print(f"  {t('prompts.rollback_snapshot_saved')}")

            # Also undo the last conversation turn so the agent's context
            # matches the restored filesystem state
            if self.conversation_history:
                self.undo_last()
                print(f"  {t('prompts.rollback_chat_undone')}")
        else:
            print(f"  ❌ {result['error']}")

    def _resolve_checkpoint_ref(self, ref: str, checkpoints: list) -> str | None:
        """Resolve a checkpoint number or hash to a full commit hash."""
        try:
            idx = int(ref) - 1  # 1-indexed for user
            if 0 <= idx < len(checkpoints):
                return checkpoints[idx]["hash"]
            else:
                print(f"  {t('prompts.rollback_invalid_number', max=len(checkpoints))}")
                return None
        except ValueError:
            # Treat as a git hash
            return ref

    def _handle_stop_command(self):
        """Handle /stop — kill all running background processes.

        Interrupt stops the current turn, while /stop cleans up background processes.
        """
        from tools.process_registry import process_registry

        processes = process_registry.list_sessions()
        running = [p for p in processes if p.get("status") == "running"]

        if not running:
            print(f"  {t('prompts.no_running_background_processes')}")
            return

        print(f"  {t('prompts.stopping_background_processes', count=len(running))}")
        killed = process_registry.kill_all()
        print(f"  ✅ {t('prompts.stopped_background_processes', count=killed)}")

    def _handle_paste_command(self):
        """Handle /paste — explicitly check clipboard for an image.

        This is the reliable fallback for terminals where BracketedPaste
        doesn't fire for image-only clipboard content (e.g., VSCode terminal,
        Windows Terminal with WSL2).
        """
        if _is_termux_environment():
            _cprint(
                f"  {_DIM}Clipboard image paste is not available on Termux — "
                f"use /image <path> or paste a local image path like "
                f"{_termux_example_image_path()}{_RST}"
            )
            return

        from VoidCube_cli.clipboard import has_clipboard_image
        if has_clipboard_image():
            if self._try_attach_clipboard_image():
                n = len(self._attached_images)
                _cprint(f"  📎 Image #{n} attached from clipboard")
            else:
                _cprint(f"  {_DIM}(>_<) Clipboard has an image but extraction failed{_RST}")
        else:
            _cprint(f"  {_DIM}(._.) No image found in clipboard{_RST}")

    def _handle_image_command(self, cmd_original: str):
        """Handle /image <path> — attach a local image file for the next prompt."""
        raw_args = (cmd_original.split(None, 1)[1].strip() if " " in cmd_original else "")
        if not raw_args:
            hint = _termux_example_image_path() if _is_termux_environment() else "/path/to/image.png"
            _cprint(f"  {_DIM}Usage: /image <path>  e.g. /image {hint}{_RST}")
            return

        path_token, _remainder = _split_path_input(raw_args)
        image_path = _resolve_attachment_path(path_token)
        if image_path is None:
            _cprint(f"  {_DIM}(>_<) File not found: {path_token}{_RST}")
            return
        if image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
            _cprint(f"  {_DIM}(._.) Not a supported image file: {image_path.name}{_RST}")
            return

        self._attached_images.append(image_path)
        _cprint(f"  📎 Attached image: {image_path.name}")
        if _remainder:
            _cprint(f"  {_DIM}Now type your prompt (or use --image in single-query mode): {_remainder}{_RST}")
        elif _is_termux_environment():
            _cprint(f"  {_DIM}{t('tips.tip_prefix', default='Tip:')} type your next message, or run VoidCube chat -q --image {_termux_example_image_path(image_path.name)} \"What do you see?\"{_RST}")

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

        # Build status line with proper markup — skin-aware colors
        try:
            from VoidCube_cli.skin_engine import get_active_skin
            skin = get_active_skin()
            separator_color = skin.get_color("banner_dim", "#B8860B")  # type: ignore[attr-defined]
            accent_color = skin.get_color("ui_accent", "#FFBF00")  # type: ignore[attr-defined]
            label_color = skin.get_color("ui_label", "#4dd0e1")  # type: ignore[attr-defined]
        except Exception:
            separator_color, accent_color, label_color = "#B8860B", "#FFBF00", "cyan"
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

    def _show_session_status(self):
        """Show gateway-style status for the current CLI session."""
        session_meta = {}
        if self._session_db:
            try:
                session_meta = self._session_db.get_session(self.session_id) or {}
            except Exception:
                session_meta = {}

        title = (session_meta.get("title") or "").strip()

        created_at = self.session_start
        started_at = session_meta.get("started_at")
        if started_at:
            try:
                created_at = datetime.fromtimestamp(float(started_at))
            except Exception:
                created_at = self.session_start

        updated_at = created_at
        for field in ("updated_at", "last_updated_at", "last_activity_at"):
            value = session_meta.get(field)
            if not value:
                continue
            try:
                updated_at = datetime.fromtimestamp(float(value))
                break
            except Exception:
                pass

        agent = getattr(self, "agent", None)
        total_tokens = getattr(agent, "session_total_tokens", 0) or 0
        provider = getattr(self, "provider", None) or "unknown"
        model = getattr(self, "model", None) or "(unknown)"
        is_running = bool(getattr(self, "_agent_running", False))
        subagent = self._get_subagent_observability_snapshot()

        lines = [
            "Voidcube CLI Status",
            "",
            f"Session ID: {self.session_id}",
            f"Path: {display_VoidCube_home()}",
        ]
        if title:
            lines.append(f"Title: {title}")
        lines.extend([
            f"Model: {model} ({provider})",
            f"Created: {created_at.strftime('%Y-%m-%d %H:%M')}",
            f"Last Activity: {updated_at.strftime('%Y-%m-%d %H:%M')}",
            f"Tokens: {total_tokens:,}",
            f"Agent Running: {'Yes' if is_running else 'No'}",
        ])
        if subagent.get("active"):
            lines.append(
                "Subagents: "
                f"{subagent.get('foreground_count', 0)} foreground"
                f", {subagent.get('background_count', 0)} background"
            )
            focus_preview = str(subagent.get("focus_preview") or "").strip()
            if focus_preview:
                lines.append(f"Subagent Focus: {focus_preview}")
        else:
            lines.append("Subagents: idle")

        lines.extend(_autonomous_observation_summary_sections_view(self))
        self.console.print("\n".join(lines), highlight=False, markup=False)
    
    def _fast_command_available(self) -> bool:
        try:
            from VoidCube_cli.models import model_supports_fast_mode
        except Exception:
            return False
        agent = getattr(self, "agent", None)
        model = getattr(agent, "model", None) or getattr(self, "model", None)
        return model_supports_fast_mode(model)

    def _command_available(self, slash_command: str) -> bool:
        if slash_command == "/fast":
            return self._fast_command_available()
        return True

    def show_help(self):
        """Display help information with categorized commands."""
        from VoidCube_cli.commands import COMMANDS_BY_CATEGORY

        try:
            from VoidCube_cli.i18n import t
            header_default = t('help.available_commands', default="(^_^)? VoidCube AI Assistant")
            skill_cmd_header = t('help.skill_commands', default="🔧 可用技能")
            tip_chat = t('help.tip_chat', default="提示: 直接输入消息与 AI 对话")
            tip_multiline = t('help.tip_multiline', default="多行输入: Alt+Enter 换行")
            tip_paste = t('help.tip_paste', default="粘贴图片: Alt+V (或 /paste)")
        except Exception:
            header_default = "(^_^)? VoidCube AI Assistant"
            skill_cmd_header = "🔧 可用技能"
            tip_chat = "提示: 直接输入消息与 AI 对话"
            tip_multiline = "多行输入: Alt+Enter 换行"
            tip_paste = "粘贴图片: Alt+V (或 /paste)"

        try:
            from VoidCube_cli.skin_engine import get_active_help_header
            header = get_active_help_header(header_default)
        except Exception:
            header = header_default
        header = (header or "").strip() or header_default
        inner_width = 55
        if len(header) > inner_width:
            header = header[:inner_width]
        _cprint(f"\n{_BOLD}+{'-' * inner_width}+{_RST}")
        _cprint(f"{_BOLD}|{header:^{inner_width}}|{_RST}")
        _cprint(f"{_BOLD}+{'-' * inner_width}+{_RST}")

        for category, commands in COMMANDS_BY_CATEGORY.items():
            _cprint(f"\n  {_BOLD}── {category} ──{_RST}")
            for cmd, desc in commands.items():
                if not self._command_available(cmd):
                    continue
                ChatConsole().print(f"    [bold {_accent_hex()}]{cmd:<15}[/] [dim]-[/] {_escape(desc)}")

        _skcmds = _get_skill_commands()
        if _skcmds:
            _cprint(f"\n  {skill_cmd_header} {_RST}({len(_skcmds)} installed):")
            for cmd, info in sorted(_skcmds.items()):
                ChatConsole().print(
                    f"    [bold {_accent_hex()}]{cmd:<22}[/] [dim]-[/] {_escape(info['description'])}"
                )

        _cprint(f"\n  {_DIM}{tip_chat}{_RST}")
        _cprint(f"  {_DIM}{tip_multiline}{_RST}")
        if _is_termux_environment():
            _cprint(f"  {_DIM}Attach image: /image {_termux_example_image_path()} or start your prompt with a local image path{_RST}\n")
        else:
            _cprint(f"  {_DIM}{tip_paste}{_RST}\n")
    
    def show_tools(self):
        """Display available tools with kawaii ASCII art."""
        tools = get_tool_definitions(enabled_toolsets=self.enabled_toolsets, quiet_mode=True)
        
        if not tools:
            print(f"{t('prompts.no_tools_available')}")
            return
        
        # Header
        print()
        title = t('prompts.available_tools_title', default="(^_^)/ Available Tools")
        width = 78
        pad = width - len(title)
        print("+" + "-" * width + "+")
        print("|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|")
        print("+" + "-" * width + "+")
        print()
        
        # Group tools by toolset
        toolsets = {}
        for tool in sorted(tools, key=lambda t: t["function"]["name"]):
            name = tool["function"]["name"]
            toolset = _get_toolset_for_tool(name) or "unknown"
            if toolset not in toolsets:
                toolsets[toolset] = []
            desc = tool["function"].get("description", "")
            # First sentence: split on ". " (period+space) to avoid breaking on "e.g." or "v2.0"
            desc = desc.split("\n")[0]
            if ". " in desc:
                desc = desc[:desc.index(". ") + 1]
            toolsets[toolset].append((name, desc))
        
        # Display by toolset
        for toolset in sorted(toolsets.keys()):
            print(f"  [{toolset}]")
            for name, desc in toolsets[toolset]:
                print(f"    * {name:<20} - {desc}")
            print()
        
        print(f"  {t('prompts.total_tools', count=len(tools))}")
        print()

    def _handle_tools_command(self, cmd: str):
        """Handle /tools [list|disable|enable] slash commands.

        /tools (no args) shows the tool list.
        /tools list shows enabled/disabled status per toolset.
        /tools disable/enable saves the change to config and resets
        the session so the new tool set takes effect cleanly (no
        prompt-cache breakage mid-conversation).
        """
        import shlex
        from argparse import Namespace
        from VoidCube_cli.tools_config import tools_disable_enable_command

        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()

        subcommand = parts[1] if len(parts) > 1 else ""
        if subcommand not in ("list", "disable", "enable"):
            self.show_tools()
            return

        if subcommand == "list":
            tools_disable_enable_command(
                Namespace(tools_action="list", platform="cli"))
            return

        names = parts[2:]
        if not names:
            print(f"{t('prompts.tools_usage', subcommand=subcommand)}")
            print(f"  {t('prompts.tools_builtin_example', subcommand=subcommand)}")
            print(f"  {t('prompts.tools_mcp_example', subcommand=subcommand)}")
            return

        # Apply the change directly — the user typing the command is implicit
        # consent.  Do NOT use input() here; it hangs inside prompt_toolkit's
        # TUI event loop (known pitfall).
        verb = "Disabling" if subcommand == "disable" else "Enabling"
        label = ", ".join(names)
        _cprint(f"{_ACCENT}{verb} {label}...{_RST}")

        tools_disable_enable_command(
            Namespace(tools_action=subcommand, names=names, platform="cli"))

        # Reset session so the new tool config is picked up from a clean state
        from VoidCube_cli.tools_config import _get_platform_tools
        from VoidCube_cli.config import load_config
        self.enabled_toolsets = _get_platform_tools(load_config(), "cli")
        self.new_session()
        _cprint(f"{_DIM}Session reset. New tool configuration is active.{_RST}")

    def show_toolsets(self):
        """Display available toolsets with kawaii ASCII art."""
        from VoidCube_cli.i18n import get_i18n
        
        all_toolsets = _get_all_toolsets()
        i18n = get_i18n()
        locale_data = i18n._translations.get(i18n.get_current_locale(), {})
        ts_translations = locale_data.get("translations", {}).get("toolsets", {})
        
        # Header
        print()
        title = t('prompts.available_toolsets_title', default="(^_^)b Available Toolsets")
        width = 58
        pad = width - len(title)
        print("+" + "-" * width + "+")
        print("|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|")
        print("+" + "-" * width + "+")
        print()
        
        for name in sorted(all_toolsets):
            info = _get_toolset_info(name)
            if info:
                tool_count = len(info.get("tools", []))
                # Get translated description, fall back to original
                desc = ts_translations.get(name, info.get("description", ""))
                
                # Mark if currently enabled
                marker = "(*)" if self.enabled_toolsets and name in self.enabled_toolsets else "   "
                print(f"  {marker} {name:<18} [{tool_count:>2} {t('prompts.toolsets_unit', default='工具')}] - {desc}")
        
        print()
        current_enabled = ", ".join(self.enabled_toolsets) if self.enabled_toolsets else "none"
        print(
            f"  {t('prompts.toolsets_current_enabled', default='Currently enabled toolsets:')} "
            f"{current_enabled}"
        )
        print()
        print(
            f"  {t('prompts.toolsets_tip_all', default='Use --toolsets full to enable the full toolset.')}"
        )
        print(
            f"  {t('prompts.toolsets_example', default='Example: python cli.py --toolsets web,terminal,file')}"
        )
        print()
    
    def _handle_profile_command(self):
        """Display active profile name and home directory."""
        from VoidCube_core.constants import get_VoidCube_home, display_VoidCube_home

        home = get_VoidCube_home()
        display = display_VoidCube_home()

        profiles_parent = Path.home() / ".VoidCube" / "profiles"
        try:
            rel = home.relative_to(profiles_parent)
            profile_name = str(rel).split("/")[0]
        except ValueError:
            profile_name = None

        print()
        if profile_name:
            print(f"  Profile: {profile_name}")
        else:
            print(t('profile_default'))
        print(f"  Home:    {display}")
        print()

    def show_config(self):
        """Display current configuration with kawaii ASCII art."""
        # Get terminal config from environment (which was set from cli-config.yaml)
        terminal_env = os.getenv("TERMINAL_ENV", "local")
        terminal_cwd = os.getenv("TERMINAL_CWD", os.getcwd())
        terminal_timeout = os.getenv("TERMINAL_TIMEOUT", "60")
        
        user_config_path = _VoidCube_home / 'config.yaml'
        project_config_path = Path(__file__).parent / 'cli-config.yaml'
        if user_config_path.exists():
            config_path = user_config_path
        else:
            config_path = project_config_path
        config_status = "(loaded)" if config_path.exists() else "(not found)"
        
        api_key_display = '********' + self.api_key[-4:] if self.api_key and len(self.api_key) > 4 else 'Not set!'
        
        print()
        title = "(^_^) Configuration"
        width = 50
        pad = width - len(title)
        print("+" + "-" * width + "+")
        print("|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|")
        print("+" + "-" * width + "+")
        print()
        print(t('model'))
        print(f"  Model:     {self.model}")
        print(f"  Base URL:  {self.base_url}")
        print(f"  API Key:   {api_key_display}")
        print()
        print(t('terminal'))
        print(f"  Environment:  {terminal_env}")
        if terminal_env == "ssh":
            ssh_host = os.getenv("TERMINAL_SSH_HOST", "not set")
            ssh_user = os.getenv("TERMINAL_SSH_USER", "not set")
            ssh_port = os.getenv("TERMINAL_SSH_PORT", "22")
            print(f"  SSH Target:   {ssh_user}@{ssh_host}:{ssh_port}")
        print(f"  Working Dir:  {terminal_cwd}")
        print(f"  Timeout:      {terminal_timeout}s")
        print()
        print(t('agent'))
        print(f"  Max Turns:  {self.max_turns}")
        print(f"  Toolsets:   {', '.join(self.enabled_toolsets) if self.enabled_toolsets else 'all'}")
        print(f"  Verbose:    {self.verbose}")
        print()
        print(t('session'))
        print(f"  Started:     {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Config File: {config_path} {config_status}")
        print()
    
    def _list_recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent CLI sessions for in-chat browsing/resume affordances."""
        if not self._session_db:
            return []
        try:
            sessions = self._session_db.list_sessions_rich(
                source="cli",
                exclude_sources=["tool"],
                limit=limit,
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

    def show_history(self):
        """Display conversation history."""
        if not self.conversation_history:
            if not self._show_recent_sessions(reason="history"):
                print(t('no_conversation_history_yet'))
            return

        preview_limit = 400
        visible_index = 0
        hidden_tool_messages = 0

        def flush_tool_summary():
            nonlocal hidden_tool_messages
            if not hidden_tool_messages:
                return

            noun = "message" if hidden_tool_messages == 1 else "messages"
            print(t('tools'))
            print(f"    ({hidden_tool_messages} tool {noun} hidden)")
            hidden_tool_messages = 0

        print()
        print("+" + "-" * 50 + "+")
        print("|" + " " * 12 + "(^_^) Conversation History" + " " * 11 + "|")
        print("+" + "-" * 50 + "+")

        for msg in self.conversation_history:
            role = msg.get("role", "unknown")

            if role == "tool":
                hidden_tool_messages += 1
                continue

            if role not in {"user", "assistant"}:
                continue

            flush_tool_summary()
            visible_index += 1

            content = msg.get("content")
            content_text = "" if content is None else str(content)

            if role == "user":
                print(f"\n  [You #{visible_index}]")
                print(
                    f"    {content_text[:preview_limit]}{'...' if len(content_text) > preview_limit else ''}"
                )
                continue

            print(f"\n  [Voidcube #{visible_index}]")
            tool_calls = msg.get("tool_calls") or []
            if content_text:
                preview = content_text[:preview_limit]
                suffix = "..." if len(content_text) > preview_limit else ""
            elif tool_calls:
                tool_count = len(tool_calls)
                noun = "call" if tool_count == 1 else "calls"
                preview = f"(requested {tool_count} tool {noun})"
                suffix = ""
            else:
                preview = "(no text response)"
                suffix = ""
            print(f"    {preview}{suffix}")

        flush_tool_summary()
        print()
    
    def _notify_session_boundary(self, event_type: str) -> None:
        """Fire a session-boundary plugin hook (on_session_finalize or on_session_reset).

        Non-blocking — errors are caught and logged.  Safe to call from any
        lifecycle point (shutdown, /new, /reset).
        """
        try:
            from VoidCube_cli.plugins import invoke_hook as _invoke_hook
            _invoke_hook(
                event_type,
                session_id=self.agent.session_id if self.agent else None,
                platform=getattr(self, "platform", None) or "cli",
            )
        except Exception:
            pass

    def new_session(self, silent=False):
        """Start a fresh session with a new session ID and cleared agent state."""
        if self.agent and self.conversation_history:
            try:
                self.agent.flush_memories(self.conversation_history)
            except (Exception, KeyboardInterrupt):
                pass
            self._notify_session_boundary("on_session_finalize")
        elif self.agent:
            # First session or empty history — still finalize the old session
            self._notify_session_boundary("on_session_finalize")

        old_session_id = self.session_id
        if self._session_db and old_session_id:
            try:
                self._session_db.end_session(old_session_id, "new_session")
            except Exception:
                pass

        # Per-interaction trace_id for end-to-end observability (C-03).
        # A new trace_id is generated for each user message so the full
        # chain (CLI → Gateway → Agent → Tool → Response) can be correlated.
        self._current_trace_id: str = ""
        self.session_start = datetime.now()
        timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        self.session_id = f"{timestamp_str}_{short_uuid}"
        self.conversation_history = []
        self._pending_title = None
        self._resumed = False

        if self.agent:
            self.agent.session_id = self.session_id
            self.agent.session_start = self.session_start
            self.agent.reset_session_state()
            if hasattr(self.agent, "_last_flushed_db_idx"):
                self.agent._last_flushed_db_idx = 0
            if hasattr(self.agent, "_todo_store"):
                try:
                    from tools.todo_tool import TodoStore
                    self.agent._todo_store = TodoStore()
                except Exception:
                    pass
            if hasattr(self.agent, "_invalidate_system_prompt"):
                self.agent._invalidate_system_prompt()

            if self._session_db:
                try:
                    self._session_db.create_session(
                        session_id=self.session_id,
                        source=os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
                        model=self.model,
                        model_config={
                            "max_iterations": self.max_turns,
                            "reasoning_config": self.reasoning_config,
                        },
                    )
                except Exception:
                    pass
            self._notify_session_boundary("on_session_reset")

        if not silent:
            print(t('new_session_started'))

    def _handle_resume_command(self, cmd_original: str) -> None:
        """Handle /resume <session_id_or_title_or_number> — switch to a previous session mid-conversation."""
        parts = cmd_original.split(None, 1)
        target = parts[1].strip() if len(parts) > 1 else ""

        if not target:
            _cprint(t('  Usage: /resume <session_id_or_title_or_number>'))
            if self._show_recent_sessions(reason="resume"):
                return
            _cprint(t('tips.resume_hint', default='Tip:   Use /history or `VoidCube sessions list` to find sessions.'))
            return

        if not self._session_db:
            _cprint(t('  Session database not available.'))
            return

        # Check if target is a number (index into recent sessions)
        if target.isdigit():
            idx = int(target) - 1  # convert to 0-based index
            sessions = self._list_recent_sessions(limit=50)  # get enough sessions
            if 0 <= idx < len(sessions):
                target_id = sessions[idx]["id"]
            else:
                _cprint(f"  Session index out of range: {target} (there are {len(sessions)} recent sessions)")
                _cprint(t('  Use /history or `VoidCube sessions list` to see available sessions.'))
                return
        else:
            # Resolve title or ID
            from VoidCube_cli.main import _resolve_session_by_name_or_id
            resolved = _resolve_session_by_name_or_id(target)
            target_id = resolved or target

        session_meta = self._session_db.get_session(target_id)
        if not session_meta:
            _cprint(f"  Session not found: {target}")
            _cprint(t('  Use /history or `VoidCube sessions list` to see available sessions.'))
            return

        if target_id == self.session_id:
            _cprint(t('  Already on that session.'))
            return

        # End current session
        try:
            self._session_db.end_session(self.session_id, "resumed_other")
        except Exception:
            pass

        # Switch to the target session
        self.session_id = target_id
        self._resumed = True
        self._pending_title = None

        # Load conversation history (strip transcript-only metadata entries)
        restored = self._session_db.get_messages_as_conversation(target_id)
        restored = [m for m in (restored or []) if m.get("role") != "session_meta"]
        self.conversation_history = restored

        # Re-open the target session so it's not marked as ended
        try:
            self._session_db.reopen_session(target_id)
        except Exception:
            pass

        # Sync the agent if already initialised
        if self.agent:
            self.agent.session_id = target_id
            self.agent.reset_session_state()
            if hasattr(self.agent, "_last_flushed_db_idx"):
                self.agent._last_flushed_db_idx = len(self.conversation_history)
            if hasattr(self.agent, "_todo_store"):
                try:
                    from tools.todo_tool import TodoStore
                    self.agent._todo_store = TodoStore()
                except Exception:
                    pass
            if hasattr(self.agent, "_invalidate_system_prompt"):
                self.agent._invalidate_system_prompt()

        title_part = f" \"{session_meta['title']}\"" if session_meta.get("title") else ""
        msg_count = len([m for m in self.conversation_history if m.get("role") == "user"])
        if self.conversation_history:
            _cprint(
                f"  ↻ {t('prompts.resumed_session', default='Resumed session')} {target_id}{title_part}"
                f" ({msg_count} {t('prompts.user_messages', default='user messages')},"
                f" {len(self.conversation_history)} {t('prompts.total', default='total')})"
            )
        else:
            _cprint(f"  ↻ {t('prompts.resumed_session', default='Resumed session')} {target_id}{title_part} — {t('prompts.no_messages_starting_fresh', default='no messages, starting fresh')}.")
        
        # 显示会话历史
        if self.conversation_history:
            self._display_resumed_history()

    def _handle_branch_command(self, cmd_original: str) -> None:
        """Handle /branch [name] — fork the current session into a new independent copy.

        Copies the full conversation history to a new session so the user can
        explore a different approach without losing the original session state.
        Creates a new session branch from the current conversation.
        """
        if not self.conversation_history:
            _cprint(t('  No conversation to branch — send a message first.'))
            return

        if not self._session_db:
            _cprint(t('  Session database not available.'))
            return

        parts = cmd_original.split(None, 1)
        branch_name = parts[1].strip() if len(parts) > 1 else ""

        # Generate the new session ID
        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        new_session_id = f"{timestamp_str}_{short_uuid}"

        # Determine branch title
        if branch_name:
            branch_title = branch_name
        else:
            # Auto-generate from the current session title
            current_title = None
            if self._session_db:
                current_title = self._session_db.get_session_title(self.session_id)
            base = current_title or "branch"
            branch_title = self._session_db.get_next_title_in_lineage(base)

        # Save the current session's state before branching
        parent_session_id = self.session_id

        # End the old session
        try:
            self._session_db.end_session(self.session_id, "branched")
        except Exception:
            pass

        # Create the new session with parent link
        try:
            self._session_db.create_session(
                session_id=new_session_id,
                source=os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
                model=self.model,
                model_config={
                    "max_iterations": self.max_turns,
                    "reasoning_config": self.reasoning_config,
                },
                parent_session_id=parent_session_id,
            )
        except Exception as e:
            _cprint(f"  Failed to create branch session: {e}")
            return

        # Copy conversation history to the new session
        for msg in self.conversation_history:
            try:
                self._session_db.append_message(
                    session_id=new_session_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                    tool_name=msg.get("tool_name") or msg.get("name"),
                    tool_calls=msg.get("tool_calls"),
                    tool_call_id=msg.get("tool_call_id"),
                    reasoning=msg.get("reasoning"),
                )
            except Exception:
                pass  # Best-effort copy

        # Set title on the branch
        try:
            self._session_db.set_session_title(new_session_id, branch_title)
        except Exception:
            pass

        # Switch to the new session
        self.session_id = new_session_id
        self.session_start = now
        self._pending_title = None
        self._resumed = True  # Prevents auto-title generation

        # Sync the agent
        if self.agent:
            self.agent.session_id = new_session_id
            self.agent.session_start = now
            self.agent.reset_session_state()
            if hasattr(self.agent, "_last_flushed_db_idx"):
                self.agent._last_flushed_db_idx = len(self.conversation_history)
            if hasattr(self.agent, "_todo_store"):
                try:
                    from tools.todo_tool import TodoStore
                    self.agent._todo_store = TodoStore()
                except Exception:
                    pass
            if hasattr(self.agent, "_invalidate_system_prompt"):
                self.agent._invalidate_system_prompt()

        msg_count = len([m for m in self.conversation_history if m.get("role") == "user"])
        _cprint(
            f"  ⑂ Branched session \"{branch_title}\""
            f" ({msg_count} user message{'s' if msg_count != 1 else ''})"
        )
        _cprint(f"  Original session: {parent_session_id}")
        _cprint(f"  Branch session:   {new_session_id}")

    def save_conversation(self):
        """Save the current conversation to a file."""
        if not self.conversation_history:
            print(t('no_conversation_to_save'))
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"VoidCube_conversation_{timestamp}.json"
        
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump({
                    "model": self.model,
                    "session_start": self.session_start.isoformat(),
                    "messages": self.conversation_history,
                }, f, indent=2, ensure_ascii=False)
            print(f"(^_^)v Conversation saved to: {filename}")
        except Exception as e:
            print(f"(x_x) Failed to save: {e}")
    
    def retry_last(self):
        """Retry the last user message by removing the last exchange and re-sending.
        
        Removes the last assistant response (and any tool-call messages) and
        the last user message, then re-sends that user message to the agent.
        Returns the message to re-send, or None if there's nothing to retry.
        """
        if not self.conversation_history:
            print(t('no_messages_to_retry'))
            return None
        
        # Walk backwards to find the last user message
        last_user_idx = None
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i].get("role") == "user":
                last_user_idx = i
                break
        
        if last_user_idx is None:
            print(t('no_user_message_found_to_retry'))
            return None
        
        # Extract the message text and remove everything from that point forward
        last_message = self.conversation_history[last_user_idx].get("content", "")
        self.conversation_history = self.conversation_history[:last_user_idx]
        
        print(f"(^_^)b Retrying: \"{last_message[:60]}{'...' if len(last_message) > 60 else ''}\"")
        return last_message
    
    def undo_last(self):
        """Remove the last user/assistant exchange from conversation history.
        
        Walks backwards and removes all messages from the last user message
        onward (including assistant responses, tool calls, etc.).
        """
        if not self.conversation_history:
            print(t('no_messages_to_undo'))
            return
        
        # Walk backwards to find the last user message
        last_user_idx = None
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i].get("role") == "user":
                last_user_idx = i
                break
        
        if last_user_idx is None:
            print(t('no_user_message_found_to_undo'))
            return
        
        # Count how many messages we're removing
        removed_count = len(self.conversation_history) - last_user_idx
        removed_msg = self.conversation_history[last_user_idx].get("content", "")
        
        # Truncate history to before the last user message
        self.conversation_history = self.conversation_history[:last_user_idx]
        
        print(f"(^_^)b Undid {removed_count} message(s). Removed: \"{removed_msg[:60]}{'...' if len(removed_msg) > 60 else ''}\"")
        remaining = len(self.conversation_history)
        print(f"  {remaining} message(s) remaining in history.")
    
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
                from VoidCube_cli.config import (
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

    def _handle_model_switch(self, cmd_original: str):
        """Handle /model command — switch model and persist by default.

        Supports:
          /model                              — show current model + usage hints
          /model <name>                       — switch and persist to config.yaml
          /model <name> --session-only        — switch for this session only
          /model <name> --provider <provider> — switch provider + model
          /model --provider <provider>        — switch to provider, auto-detect model
        """
        from VoidCube_cli.model_switch import switch_model, parse_model_flags, list_configured_providers
        from VoidCube_cli.providers import get_label

        # Parse args from the original command
        parts = cmd_original.split(None, 1)  # split off '/model'
        raw_args = parts[1].strip() if len(parts) > 1 else ""

        # Parse --provider and --global flags
        model_input, explicit_provider, persist_global = parse_model_flags(raw_args)

        user_provs = None
        try:
            from VoidCube_cli.config import load_config
            cfg = load_config()
            user_provs = cfg.get("providers")
        except Exception:
            user_provs = None

        # No args at all: open prompt_toolkit-native picker modal
        if not model_input and not explicit_provider:
            model_display = self.model or "unknown"
            provider_display = get_label(self.provider) if self.provider else "unknown"

            try:
                providers = list_configured_providers(
                    current_provider=self.provider or "",
                    user_providers=user_provs,
                    max_models=30,  # 减少显示的模型数量以避免渲染卡顿
                )
            except Exception:
                providers = []

            if not providers:
                _cprint("  No configured providers found.")
                _cprint("")
                _cprint("  Run /api first to add a provider.")
                return

            self._open_model_picker(
                providers,
                model_display,
                provider_display,
                user_provs=user_provs,
            )
            return

        # Perform the switch
        result = switch_model(
            raw_input=model_input,
            current_provider=self.provider or "",
            current_model=self.model or "",
            current_base_url=self.base_url or "",
            current_api_key=self.api_key or "",
            is_global=persist_global,
            explicit_provider=explicit_provider,
            user_providers=user_provs,
        )
        self._apply_model_switch_result(result, persist_global)

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

    def _handle_provider_switch(self, cmd_original: str):
        """Handle /provider command — view provider status.

        Supports:
          /provider                        — show current provider + list all providers
          /provider list                   — show detailed provider info

        Use /model command to switch providers or models.
        """
        from VoidCube_cli.config import load_config, get_active_provider_key
        from VoidCube_cli.model_switch import list_configured_providers

        parts = cmd_original.split(None, 1)
        raw_args = parts[1].strip() if len(parts) > 1 else ""

        # If user provides arguments, show help message
        if raw_args:
            _cprint(t('  Usage: /provider'))
            _cprint(t('         /provider list'))
            _cprint()
            _cprint(t('  Use /model to switch providers or models:'))
            _cprint(t('    /model <model-name>              — switch model'))
            _cprint(t('    /model --provider <provider-name> — switch provider'))
            _cprint(t('    /model <provider>:<model>         — switch provider and model'))
            _cprint()
            _cprint(t('  Run /api to configure provider credentials'))
            return

        cfg = load_config()
        current = get_active_provider_key(cfg)
        providers = list_configured_providers(
            current_provider=current,
            user_providers=cfg.get("providers"),
            max_models=8,
        )

        current_model = self.model or ""
        print(f"\n  Current: {current_model or 'not set'} via {current or 'not configured'}")
        print()

        if providers:
            print("  Configured providers:")
            for p in providers:
                marker = " ← active" if p.get("is_current") else ""
                print(f"    [{p['slug']}] {p['name']}{marker}")
                api_url = p.get("api_url") or ""
                if api_url:
                    print(f"      endpoint: {api_url}")
                for mid in p.get("models") or []:
                    current_marker = " ← current" if (p.get("is_current") and mid == current_model) else ""
                    print(f"      {mid}{current_marker}")
                if not (p.get("models") or []):
                    print("      no model selected")
                print()
        else:
            print("  No configured providers.")
            print("  Run /api to configure providers.")
            print()

        print(t(
            'prompts.use_model_to_switch_providers_or_models',
            default='  Use /model to switch providers or models:',
        ))
        print("    /model <model-name>               — switch model")
        print("    /model --provider <provider-name> — switch provider")
        print("    /model <name> --provider <provider-name> — switch provider and model")

    def _handle_memory_switch(self, cmd_original: str):
        """处理 /memory 命令 — 配置记忆系统。

        支持:
          /memory                     — 显示当前记忆系统 + 列出选项
          /memory list                — 列出可用的记忆系统
          /memory builtin             — 仅使用内置记忆系统
          /memory mem                 — 使用内置 + Mem 时间序列记忆系统
          /memory mem --global        — 使用 Mem 并持久化到 config.yaml
        """
        from VoidCube_cli.config import load_config, save_config

        parts = cmd_original.split(None, 1)
        raw_args = parts[1].strip() if len(parts) > 1 else ""

        persist_global = False
        mem_input = raw_args

        if mem_input.endswith(" --global"):
            persist_global = True
            mem_input = mem_input[:-8].strip()
        elif " --global " in mem_input:
            idx = mem_input.find(" --global ")
            mem_input = mem_input[:idx].strip()
            persist_global = True

        # 加载当前配置
        try:
            cfg = load_config()
            current_provider = cfg.get("memory", {}).get("provider", "")
        except Exception:
            cfg = {}
            current_provider = ""

        # 可用选项 - 动态加载所有可用的记忆提供者
        memory_options = {
            "": {
                "name": "builtin",
                "label": "仅使用内置记忆",
                "description": "仅使用内置的 MEMORY.md 和 USER.md 文件",
                "tools": ["memory_write", "user_profile_write"],
                "available": True,
            },
        }
        
        # 动态加载外部记忆提供者
        try:
            from plugins.memory import load_memory_provider
            
            # 支持的外部提供者列表
            external_providers = ["mem", "hindsight"]
            
            for provider_name in external_providers:
                try:
                    provider = load_memory_provider(provider_name)
                    if provider:
                        is_available = provider.is_available()
                        tools = [t["name"] for t in provider.get_tool_schemas()] if is_available else []
                        
                        if provider_name == "mem":
                            memory_options["mem"] = {
                                "name": "mem",
                                "label": "Mem 时间序列记忆系统",
                                "description": "内置 + Mem 系统，包含时间序列、故事弧、人物档案",
                                "tools": ["memory_write", "user_profile_write"] + tools,
                                "available": is_available,
                            }
                        elif provider_name == "hindsight":
                            memory_options["hindsight"] = {
                                "name": "hindsight",
                                "label": "Hindsight 知识图谱记忆",
                                "description": "内置 + Hindsight 知识图谱，支持 retain/recall/reflect 操作",
                                "tools": ["memory_write", "user_profile_write"] + tools,
                                "available": is_available,
                            }
                except Exception:
                    pass
        except Exception:
            pass

        if mem_input and mem_input in ["list", "builtin", "mem", "hindsight"]:
            if mem_input == "list":
                # 仅列出选项
                self._display_memory_options(current_provider, memory_options)
                return

            if mem_input == "builtin":
                target_provider = ""
            else:
                target_provider = mem_input

            # 更新配置
            if "memory" not in cfg:
                cfg["memory"] = {}
            cfg["memory"]["provider"] = target_provider

            if persist_global:
                try:
                    save_config(cfg)
                    target_label = memory_options.get(target_provider, {}).get("label", target_provider)
                    _cprint(f"  ✓ 记忆系统已切换到: {target_label}")
                    _cprint("    已保存到 config.yaml (--global)")
                    _cprint("    重启 VoidCube 以让更改生效")
                except Exception as e:
                    _cprint(f"  ✗ 保存配置失败: {e}")
            else:
                target_label = memory_options.get(target_provider, {}).get("label", target_provider)
                _cprint(f"  ✓ 记忆系统已切换到: {target_label}")
                _cprint("    (仅本次会话 — 添加 --global 以持久化)")
                _cprint("    重启 VoidCube 以让更改生效")

            return

        # 没有输入 — 显示当前状态和选项
        self._display_memory_options(current_provider, memory_options)

    def _display_memory_options(self, current_provider: str, memory_options: dict):
        """显示可用的记忆系统选项。"""
        print("\n  可用的记忆系统:\n")

        for option_key, option_info in memory_options.items():
            is_active = current_provider == option_key
            is_available = option_info.get("available", True)
            marker = " ← 当前使用" if is_active else ""
            status_char = "✓" if is_active else " "
            
            availability_note = ""
            if not is_available:
                availability_note = " (未安装)"
            
            print(f"  [{option_info['name']}] {status_char} {option_info['label']}{availability_note}{marker}")
            print(f"      {option_info['description']}")
            if is_available:
                print(f"      工具: {', '.join(option_info['tools'])}")
            else:
                print(f"      状态: 需要安装依赖")
            print()

        print("  使用方法:")
        print("    /memory                       — 显示此菜单")
        print("    /memory list                  — 列出记忆系统")
        print("    /memory builtin               — 仅使用内置记忆")
        print("    /memory mem                   — 使用 Mem 时间序列记忆系统")
        print("    /memory hindsight             — 使用 Hindsight 知识图谱记忆")
        print("    /memory <provider> --global   — 使用指定记忆系统并保存到配置")
        print()


    

    @staticmethod
    def _resolve_personality_prompt(value) -> str:
        """Accept string or dict personality value; return system prompt string."""
        if isinstance(value, dict):
            parts = [value.get("system_prompt", "")]
            if value.get("tone"):
                parts.append(f'Tone: {value["tone"]}' )
            if value.get("style"):
                parts.append(f'Style: {value["style"]}' )
            return "\n".join(p for p in parts if p)
        return str(value)

    def _handle_personality_command(self, cmd: str):
        """Handle the /personality command to set predefined personalities."""
        parts = cmd.split(maxsplit=1)
        
        if len(parts) > 1:
            # Set personality
            personality_name = parts[1].strip().lower()
            
            if personality_name in ("none", "default", "neutral"):
                self.system_prompt = ""
                self.agent = None  # Force re-init
                if save_config_value("agent.system_prompt", ""):
                    print("(^_^)b Personality cleared (saved to config)")
                else:
                    print("(^_^) Personality cleared (session only)")
                print("  No personality overlay — using base agent behavior.")
            elif personality_name in self.personalities:
                self.system_prompt = self._resolve_personality_prompt(self.personalities[personality_name])
                self.agent = None  # Force re-init
                if save_config_value("agent.system_prompt", self.system_prompt):
                    print(f"(^_^)b Personality set to '{personality_name}' (saved to config)")
                else:
                    print(f"(^_^) Personality set to '{personality_name}' (session only)")
                print(f"  \"{self.system_prompt[:60]}{'...' if len(self.system_prompt) > 60 else ''}\"")
            else:
                print(f"(._.) Unknown personality: {personality_name}")
                print(f"  Available: none, {', '.join(self.personalities.keys())}")
        else:
            # Show available personalities
            print()
            print("+" + "-" * 50 + "+")
            print("|" + " " * 12 + "(^o^)/ Personalities" + " " * 15 + "|")
            print("+" + "-" * 50 + "+")
            print()
            print(f"  {'none':<12} - (no personality overlay)")
            for name, prompt in self.personalities.items():
                if isinstance(prompt, dict):
                    preview = prompt.get("description") or prompt.get("system_prompt", "")[:50]
                else:
                    preview = str(prompt)[:50]
                print(f"  {name:<12} - {preview}")
            print()
            print("  Usage: /personality <name>")
            print()
    
    def _handle_skills_command(self, cmd: str):
        """Handle /skills slash command — skills management."""
        args = cmd.split()
        subcommand = args[1] if len(args) > 1 else "help"
        
        if subcommand == "list":
            self._display_skills_list()
        elif subcommand == "search":
            query = " ".join(args[2:]) if len(args) > 2 else ""
            self._search_skills(query)
        elif subcommand == "install":
            skill_name = args[2] if len(args) > 2 else ""
            self._install_skill(skill_name)
        elif subcommand == "uninstall":
            skill_name = args[2] if len(args) > 2 else ""
            self._uninstall_skill(skill_name)
        else:
            self._display_skills_help()

    def _display_skills_help(self):
        """Display skills command help."""
        print("\n  技能管理命令 (/skills)")
        print()
        print("  用法:")
        print("    /skills                 — 显示此帮助")
        print("    /skills list            — 列出已安装的技能")
        print("    /skills search <query>  — 搜索技能")
        print("    /skills install <name>  — 安装技能")
        print("    /skills uninstall <name> — 卸载技能")
        print()

    def _handle_mcp_command(self, cmd: str):
        """Handle /mcp slash command — MCP server management."""
        args = cmd.split()
        subcommand = args[1] if len(args) > 1 else "help"
        
        if subcommand == "list":
            self._display_mcp_list()
        elif subcommand == "add":
            self._add_mcp_server(args)
        elif subcommand == "remove":
            self._remove_mcp_server(args)
        elif subcommand == "test":
            self._test_mcp_server(args)
        else:
            self._display_mcp_help()

    def _display_mcp_help(self):
        """Display MCP command help."""
        print("\n  MCP 服务器管理命令 (/mcp)")
        print()
        print("  用法:")
        print("    /mcp                  — 显示此帮助")
        print("    /mcp list             — 列出已配置的 MCP 服务器")
        print("    /mcp add <name> <url> — 添加 MCP 服务器")
        print("    /mcp remove <name>    — 删除 MCP 服务器")
        print("    /mcp test <name>      — 测试 MCP 服务器连接")
        print()

    def _display_mcp_list(self):
        """Display list of configured MCP servers."""
        print("\n  已配置的 MCP 服务器:")
        print()
        
        from VoidCube_cli.config import load_config
        
        config = load_config()
        mcp_servers = config.get("mcp_servers", {})
        
        if not mcp_servers:
            print("    暂无配置的 MCP 服务器")
            print()
            print("    使用 /mcp add <name> <url> 添加服务器")
        else:
            for name, server in mcp_servers.items():
                print(f"    [{name}]")
                print(f"        URL: {server.get('url', 'N/A')}")
                print(f"        类型: {server.get('type', 'http')}")
                if server.get('command'):
                    print(f"        命令: {server.get('command')}")
                print()
        
        print()

    def _add_mcp_server(self, args: list):
        """Add an MCP server."""
        if len(args) < 4:
            print("\n  ❌ 参数不足")
            print("    用法: /mcp add <name> <url>")
            print()
            return
        
        name = args[2]
        url = args[3]
        
        print(f"\n  添加 MCP 服务器: {name}")
        print(f"  URL: {url}")
        print()
        
        try:
            from VoidCube_cli.config import load_config, save_config
            
            config = load_config()
            if "mcp_servers" not in config:
                config["mcp_servers"] = {}
            
            config["mcp_servers"][name] = {
                "url": url,
                "type": "http"
            }
            
            save_config(config)
            print(f"    ✅ MCP 服务器 '{name}' 添加成功")
            print(f"    重启会话后生效")
        except Exception as e:
            print(f"    ❌ 添加失败: {e}")
        
        print()

    def _remove_mcp_server(self, args: list):
        """Remove an MCP server."""
        if len(args) < 3:
            print("\n  ❌ 参数不足")
            print("    用法: /mcp remove <name>")
            print()
            return
        
        name = args[2]
        
        print(f"\n  删除 MCP 服务器: {name}")
        print()
        
        try:
            from VoidCube_cli.config import load_config, save_config
            
            config = load_config()
            if "mcp_servers" in config and name in config["mcp_servers"]:
                del config["mcp_servers"][name]
                save_config(config)
                print(f"    ✅ MCP 服务器 '{name}' 删除成功")
                print(f"    重启会话后生效")
            else:
                print(f"    ❌ 未找到 MCP 服务器 '{name}'")
        except Exception as e:
            print(f"    ❌ 删除失败: {e}")
        
        print()

    def _test_mcp_server(self, args: list):
        """Test an MCP server connection."""
        if len(args) < 3:
            print("\n  ❌ 参数不足")
            print("    用法: /mcp test <name>")
            print()
            return
        
        name = args[2]
        
        print(f"\n  测试 MCP 服务器: {name}")
        print()
        
        try:
            from VoidCube_cli.config import load_config
            from tools.mcp_tool import MCPTool
            
            config = load_config()
            mcp_servers = config.get("mcp_servers", {})
            
            if name not in mcp_servers:
                print(f"    ❌ 未找到 MCP 服务器 '{name}'")
                print()
                return
            
            server_config = mcp_servers[name]
            url = server_config.get("url")
            
            print(f"    正在连接到: {url}")
            
            mcp_tool = MCPTool(url=url)
            tools = mcp_tool.list_tools()
            
            if tools:
                print(f"    ✅ 连接成功")
                print(f"    可用工具: {len(tools)} 个")
                for tool in tools[:5]:
                    print(f"      - {tool.get('name', 'Unknown')}")
                if len(tools) > 5:
                    print(f"      ... 还有 {len(tools) - 5} 个工具")
            else:
                print(f"    ⚠️ 连接成功但未返回工具列表")
        except Exception as e:
            print(f"    ❌ 连接失败: {e}")
        
        print()

    def _display_skills_list(self):
        """Display list of installed skills."""
        try:
            from tools.skills_hub import HubLockFile, SKILLS_DIR
            from agent.skill_utils import get_all_skills_dirs
            import os
            
            print("\n  📦 内置技能:")
            print()
            
            scan_dirs = [path for path in get_all_skills_dirs() if path.exists() and path.is_dir()]
            if scan_dirs:
                categories = {}
                excluded_dirs = {'.git', '.github', '.hub', '__pycache__'}

                for base_dir in scan_dirs:
                    for root, dirs, files in os.walk(base_dir):
                        dirs[:] = [d for d in dirs if d not in excluded_dirs]

                        if 'SKILL.md' not in files:
                            continue

                        skill_path = os.path.relpath(root, base_dir)
                        parts = skill_path.split(os.sep)
                        
                        if len(parts) >= 2:
                            category = parts[0]
                            skill_name = parts[-1]
                        elif len(parts) == 1:
                            category = "其他"
                            skill_name = parts[0]
                        else:
                            continue
                        
                        if category not in categories:
                            categories[category] = set()
                        categories[category].add(skill_name)
                
                if categories:
                    for category, skills in sorted(categories.items()):
                        print(f"    {category}:")
                        for skill in sorted(skills):
                            print(f"      - [{skill}]")
                        print()
                else:
                    print("    暂无内置技能")
            else:
                print("    技能目录不存在")
            
            print("\n  🚀 通过技能中心安装的技能:")
            print()
            
            lock = HubLockFile()
            installed = lock.list_installed()
            
            if not installed:
                print("    暂无通过技能中心安装的技能")
            else:
                for skill in installed:
                    print(f"    [{skill.get('name', 'unknown')}]")
                    print(f"        来源: {skill.get('source', 'unknown')}")
                    print(f"        信任级别: {skill.get('trust_level', 'unknown')}")
                    print()
            
            print("  💡 使用 /skills install <name> 安装新技能")
            print("  💡 使用 /skills search <query> 搜索技能")
            print()
        except Exception as e:
            print(f"\n  ❌ 无法加载技能列表: {e}")
            print()

    def _search_skills(self, query: str):
        """Search for skills."""
        print(f"\n  搜索技能: '{query}'")
        print()
        
        try:
            from tools.skills_hub import GitHubSource, OptionalSkillSource, ClawHubSource
            
            sources = [
                ("官方可选", OptionalSkillSource()),
                ("GitHub", GitHubSource()),
                ("ClawHub", ClawHubSource()),
            ]
            
            all_results = []
            for source_name, source in sources:
                try:
                    results = source.search(query, limit=5)
                    for result in results:
                        result.extra['source_name'] = source_name
                        all_results.append(result)
                except Exception:
                    pass
            
            all_results = all_results[:10]
            
            if not all_results:
                print("    未找到匹配的技能")
            else:
                for i, skill in enumerate(all_results, 1):
                    print(f"    {i}. [{skill.name}]")
                    print(f"        {skill.description}")
                    print(f"        来源: {skill.extra.get('source_name', skill.source)}")
                    print(f"        信任级别: {skill.trust_level}")
                    if skill.tags:
                        print(f"        标签: {', '.join(skill.tags)}")
                    print()
        except Exception as e:
            print(f"    ❌ 搜索失败: {e}")
        
        print()

    def _install_skill(self, skill_name: str):
        """Install a skill."""
        if not skill_name:
            print("\n  ❌ 请指定要安装的技能名称")
            print("    用法: /skills install <name>")
            print()
            return
        
        print(f"\n  正在安装技能: {skill_name}")
        print()
        
        try:
            from tools.skills_hub import (
                GitHubSource, OptionalSkillSource, ClawHubSource,
                HubLockFile, QUARANTINE_DIR, SKILLS_DIR,
                install_from_quarantine, content_hash, append_audit_log,
                SkillBundle
            )
            from tools.skills_guard import scan_skill_bundle
            
            sources = [
                OptionalSkillSource(),
                GitHubSource(),
                ClawHubSource(),
            ]
            
            bundle = None
            skill_meta = None
            
            for source in sources:
                try:
                    results = source.search(skill_name, limit=1)
                    if results:
                        skill_meta = results[0]
                        if hasattr(source, 'download'):
                            bundle = source.download(skill_meta.identifier)
                            if bundle:
                                break
                except Exception:
                    pass
            
            if not bundle:
                print(f"    ❌ 未找到技能 '{skill_name}' 或无法下载")
                print()
                return
            
            print(f"    找到技能: {skill_meta.name}")
            print(f"    正在扫描安全性...")
            
            scan_result = scan_skill_bundle(bundle)
            
            if scan_result.verdict != "allow":
                print(f"    ❌ 技能未通过安全扫描: {scan_result.verdict}")
                print(f"    原因: {scan_result.summary}")
                print()
                return
            
            quarantine_path = QUARANTINE_DIR / bundle.name
            quarantine_path.mkdir(parents=True, exist_ok=True)
            
            for rel_path, content in bundle.files.items():
                file_path = quarantine_path / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    file_path.write_bytes(content)
                else:
                    file_path.write_text(content, encoding='utf-8')
            
            install_from_quarantine(
                quarantine_path,
                bundle.name,
                "",
                bundle,
                scan_result
            )
            
            print(f"    ✅ 技能 '{skill_name}' 安装成功")
        except Exception as e:
            print(f"    ❌ 安装失败: {e}")
        
        print()

    def _uninstall_skill(self, skill_name: str):
        """Uninstall a skill."""
        if not skill_name:
            print("\n  ❌ 请指定要卸载的技能名称")
            print("    用法: /skills uninstall <name>")
            print()
            return
        
        print(f"\n  正在卸载技能: {skill_name}")
        print()
        
        try:
            from tools.skills_hub import uninstall_skill
            
            success, message = uninstall_skill(skill_name)
            
            if success:
                print(f"    ✅ 技能 '{skill_name}' 卸载成功")
            else:
                print(f"    ❌ 卸载失败: {message}")
        except Exception as e:
            print(f"    ❌ 卸载失败: {e}")
        
        print()

    def _handle_doctor_command(self) -> None:
        from VoidCube_cli.config_validator import print_diagnosis

        print_diagnosis()

    def _handle_api_command(self) -> None:
        from VoidCube_cli.api_config import run_api_config_wizard

        run_api_config_wizard(self)

    def _handle_clear_command(self) -> None:
        self.new_session(silent=True)
        if self._app:
            output = self._app.output
            output.erase_screen()
            output.cursor_goto(0, 0)
            output.flush()
        else:
            self.console.clear()

        if self._app:
            console = ChatConsole()
            terminal_width = shutil.get_terminal_size().columns
            if self.compact or terminal_width < 80:
                console.print(_build_compact_banner())
            else:
                tools = _get_tool_definitions(
                    enabled_toolsets=self.enabled_toolsets,
                    quiet_mode=True,
                )
                cwd = os.getenv("TERMINAL_CWD", os.getcwd())
                context_length = None
                if (
                    getattr(self, "agent", None)
                    and hasattr(self.agent, "context_compressor")
                ):
                    context_length = self.agent.context_compressor.context_length
                build_welcome_banner(
                    console=console,
                    model=self.model,
                    cwd=cwd,
                    tools=tools,
                    enabled_toolsets=self.enabled_toolsets,
                    session_id=self.session_id,
                    context_length=context_length,
                    conversation_history=self.conversation_history,
                )
            _cprint(
                f"  {t('tips.fresh_start', default='✨ (◕‿◕)✨ Fresh start! Screen cleared and conversation reset.')}\n"
            )
        else:
            console = self.console
            self.show_banner()
            print(
                f"  {t('tips.fresh_start', default='✨ (◕‿◕)✨ Fresh start! Screen cleared and conversation reset.')}\n"
            )

        try:
            from VoidCube_cli.tips import get_random_tip

            tip = get_random_tip()
            try:
                from VoidCube_cli.skin_engine import get_active_skin

                tip_color = get_active_skin().get_color("banner_dim", "#B8860B")
            except Exception:
                tip_color = "#B8860B"
            console.print(
                f"[dim {tip_color}]"
                f"{t('tips.tip_prefix', default='✦ Tip:')} {tip}[/]"
            )
        except Exception:
            pass

    def _handle_title_command(self, command: str) -> None:
        parts = command.split(maxsplit=1)
        if len(parts) == 1:
            if not self._session_db:
                _cprint(t("  Session database not available."))
                return
            _cprint(f"  Session ID: {self.session_id}")
            session = self._session_db.get_session(self.session_id)
            if session and session.get("title"):
                _cprint(f"  Title: {session['title']}")
            elif self._pending_title:
                _cprint(f"  Title (pending): {self._pending_title}")
            else:
                _cprint("  No title set. Usage: /title <your session title>")
            return

        raw_title = parts[1].strip()
        if not raw_title:
            _cprint("  Usage: /title <your session title>")
            return
        if not self._session_db:
            _cprint(t("  Session database not available."))
            return

        try:
            from VoidCube_core.state import SessionDB

            new_title = SessionDB.sanitize_title(raw_title)
        except ValueError as exc:
            _cprint(f"  {exc}")
            new_title = None
        if not new_title:
            _cprint("  Title is empty after cleanup. Please use printable characters.")
            return
        if self._session_db.get_session(self.session_id):
            try:
                if self._session_db.set_session_title(self.session_id, new_title):
                    _cprint(f"  Session title set: {new_title}")
                else:
                    _cprint("  Session not found in database.")
            except ValueError as exc:
                _cprint(f"  {exc}")
            return

        existing = self._session_db.get_session_by_title(new_title)
        if existing:
            _cprint(
                f"  Title '{new_title}' is already in use by session {existing['id']}"
            )
        else:
            self._pending_title = new_title
            _cprint(
                f"  Session title queued: {new_title} (will be saved on first message)"
            )

    def _handle_provider_command(self, command: str) -> None:
        parts = command.split()
        use_ops_handler = (
            len(parts) >= 3
            and not parts[2].startswith("-")
            and parts[1] != "--global"
        ) or (len(parts) >= 2 and parts[1] in ("status", "list"))
        if not use_ops_handler:
            self._handle_provider_switch(command)
            return

        from VoidCube_cli.ops.provider import handle_slash_provider

        arguments = command.split(None, 1)[1] if len(parts) > 1 else ""
        _cprint(handle_slash_provider(arguments))

    def _handle_auto_command(self, command: str) -> None:
        _handle_auto_command_view(
            self,
            command,
            cprint=_cprint,
            refresh_gateway_cli_presence_callback=lambda *, force=False: _refresh_gateway_cli_presence_view(
                self,
                force=force,
                is_gateway_running=_is_gateway_running,
                register_with_gateway=_register_with_gateway,
                push_cli_agent_scene=_push_cli_agent_scene,
                monotonic_time=time.monotonic,
            ),
            thread_factory=threading.Thread,
        )

    def _handle_auto_q_command(self) -> None:
        _handle_auto_q_command_view(
            self,
            cprint=_cprint,
            interrupt_current_task_callback=self._interrupt_autonomous_component_task,
            push_cli_agent_scene_callback=_push_cli_agent_scene,
            thread_factory=threading.Thread,
        )

    def _handle_retry_command(self) -> None:
        retry_message = self.retry_last()
        if retry_message and hasattr(self, "_pending_input"):
            self._pending_input.put(retry_message)

    def _handle_statusbar_command(self) -> None:
        self._status_bar_visible = not self._status_bar_visible
        state = "visible" if self._status_bar_visible else "hidden"
        self.console.print(f"  Status bar {state}")

    def _handle_plugins_command(self) -> None:
        try:
            from VoidCube_cli.plugins import discover_plugins, get_plugin_manager

            discover_plugins()
            plugins = get_plugin_manager().list_plugins()
            if not plugins:
                print("No plugins installed.")
                print(
                    f"Drop plugin directories into {display_VoidCube_home()}/plugins/ "
                    "to get started."
                )
                return
            print(f"Plugins ({len(plugins)}):")
            for plugin in plugins:
                status = "✓" if plugin["enabled"] else "✗"
                version = f" v{plugin['version']}" if plugin["version"] else ""
                tools = f"{plugin['tools']} tools" if plugin["tools"] else ""
                hooks = f"{plugin['hooks']} hooks" if plugin["hooks"] else ""
                detail_parts = [part for part in (tools, hooks) if part]
                detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
                error = f" — {plugin['error']}" if plugin["error"] else ""
                print(f"  {status} {plugin['name']}{version}{detail}{error}")
        except Exception as exc:
            print(f"Plugin system error: {exc}")

    def _handle_queue_command(self, command: str) -> None:
        parts = command.split(None, 1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if not payload:
            _cprint("  Usage: /queue <prompt>")
            return
        self._pending_input.put(payload)
        preview = f"{payload[:80]}{'...' if len(payload) > 80 else ''}"
        if self._agent_running:
            _cprint(f"  Queued for the next turn: {preview}")
        else:
            _cprint(f"  Queued: {preview}")

    def _handle_language_command(self, command: str) -> None:
        from VoidCube_cli.language_command import handle_language_command

        handle_language_command(self, command)

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

    def _handle_plan_command(self, cmd: str):
        """Handle /plan [request] — load the bundled plan skill."""
        parts = cmd.strip().split(maxsplit=1)
        user_instruction = parts[1].strip() if len(parts) > 1 else ""

        plan_path = _get_plan_path(user_instruction)
        msg = _get_skill_invocation_message(
            "/plan",
            user_instruction,
            task_id=self.session_id,
            runtime_note=(
                "Save the markdown plan with write_file to this exact relative path "
                f"inside the active workspace/backend cwd: {plan_path}"
            ),
        )

        if not msg:
            ChatConsole().print("[bold red]Failed to load the bundled /plan skill[/]")
            return

        _cprint(f"  📝 Plan mode queued via skill. Markdown plan target: {plan_path}")
        if hasattr(self, '_pending_input'):
            self._pending_input.put(msg)
        else:
            ChatConsole().print("[bold red]Plan mode unavailable: input queue not initialized[/]")
    
    def _handle_background_command(self, cmd: str):
        """Handle /background <prompt> — run a prompt in a separate background session.

        Spawns a new AIAgent in a background thread with its own session.
        When it completes, prints the result to the CLI without modifying
        the active session's conversation history.
        """
        parts = cmd.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            _cprint("  Usage: /background <prompt>")
            _cprint("  Example: /background Summarize the top HN stories today")
            _cprint("  The task runs in a separate session and results display here when done.")
            return

        prompt = parts[1].strip()
        self._background_task_counter += 1
        task_num = self._background_task_counter
        task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # Make sure we have valid credentials
        if not self._ensure_runtime_credentials():
            _cprint("  (>_<) Cannot start background task: no valid credentials.")
            return

        _cprint(f"  🔄 Background task #{task_num} started: \"{prompt[:60]}{'...' if len(prompt) > 60 else ''}\"")
        _cprint(f"  Task ID: {task_id}")
        _cprint("  You can continue chatting — results will appear when done.\n")
        self._background_task_info[task_id] = {
            "task_num": task_num,
            "prompt_preview": prompt[:60] + ("..." if len(prompt) > 60 else ""),
            "started_at": time.time(),
        }

        turn_route = self._resolve_turn_agent_config(prompt)

        def run_background():
            try:
                bg_agent = _get_AIAgent()(
                    model=turn_route["model"],
                    api_key=turn_route["runtime"].get("api_key"),
                    base_url=turn_route["runtime"].get("base_url"),
                    provider=turn_route["runtime"].get("provider"),
                    acp_command=turn_route["runtime"].get("command"),
                    acp_args=turn_route["runtime"].get("args"),
                    max_iterations=self.max_turns,
                    enabled_toolsets=self.enabled_toolsets,
                    quiet_mode=True,
                    verbose_logging=False,
                    session_id=task_id,
                    platform="cli",
                    session_db=self._session_db,
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
                )
                # Silence raw spinner; route thinking through TUI widget when no foreground agent is active.
                bg_agent._print_fn = lambda *_a, **_kw: None

                def _bg_thinking(text: str) -> None:
                    # Concurrent bg tasks may race on _spinner_text; acceptable for best-effort UI.
                    if not self._agent_running:
                        self._spinner_text = text
                        if self._app:
                            self._app.invalidate()

                bg_agent.thinking_callback = _bg_thinking

                result = bg_agent.run_conversation(
                    user_message=prompt,
                    task_id=task_id,
                )

                response = result.get("final_response", "") if result else ""
                if not response and result and result.get("error"):
                    response = f"Error: {result['error']}"

                # Display result in the CLI (thread-safe via patch_stdout).
                # Force a TUI refresh first so spinner/status bar don't overlap
                # with the output (fixes #2718).
                if self._app:
                    self._app.invalidate()
                    import time as _tmod
                    _tmod.sleep(0.05)  # brief pause for refresh
                print()
                ChatConsole().print(f"[#34D399]{'~' * 40}[/]")
                _cprint(f"  ✅ Background task #{task_num} complete")
                _cprint(f"  Prompt: \"{prompt[:60]}{'...' if len(prompt) > 60 else ''}\"")
                ChatConsole().print(f"[#34D399]{'~' * 40}[/]")
                if response:
                    try:
                        from VoidCube_cli.skin_engine import get_active_skin
                        _skin = get_active_skin()
                        label = _skin.get_branding("response_label", "> Voidcube")  # type: ignore[attr-defined]
                        _resp_color = _skin.get_color("response_border", "#CD7F32")  # type: ignore[attr-defined]
                        _resp_text = _skin.get_color("banner_text", "#FFF8DC")  # type: ignore[attr-defined]
                    except Exception:
                        label = "> Voidcube"
                        _resp_color = "#CD7F32"
                        _resp_text = "#FFF8DC"

                    _chat_console = ChatConsole()
                    _chat_console.print(Panel(
                        _rich_text_from_ansi(response),
                        title=f"[{_resp_color} bold]{label} (background #{task_num})[/]",
                        title_align="left",
                        border_style=_resp_color,
                        style=_resp_text,
                        box=rich_box.HORIZONTALS,
                        padding=(1, 2),
                    ))
                else:
                    _cprint("  (No response generated)")

                # Play bell if enabled
                if self.bell_on_complete:
                    sys.stdout.write("\a")
                    sys.stdout.flush()

            except Exception as e:
                # Same TUI refresh pattern as success path (#2718)
                if self._app:
                    self._app.invalidate()
                    import time as _tmod
                    _tmod.sleep(0.05)
                print()
                _cprint(f"  ❌ Background task #{task_num} failed: {e}")
            finally:
                self._background_tasks.pop(task_id, None)
                self._background_task_info.pop(task_id, None)
                # Clear spinner only if no foreground agent owns it
                if not self._agent_running:
                    self._spinner_text = ""
                if self._app:
                    self._invalidate(min_interval=0)

        thread = threading.Thread(target=run_background, daemon=True, name=f"bg-task-{task_id}")
        self._background_tasks[task_id] = thread
        thread.start()

    def _render_background_tasks_summary(self) -> str:
        """Return a compact summary of CLI background threads."""
        lines: list[str] = []
        running: list[tuple[str, threading.Thread, Dict[str, Any]]] = []
        for task_id, thread in self._background_tasks.items():
            if not thread.is_alive():
                continue
            info = self._background_task_info.get(task_id, {})
            running.append((task_id, thread, info))

        if not running:
            return "No active subagent or background tasks."

        lines.append("CLI Background Tasks")
        lines.append("")
        for task_id, thread, info in running:
            preview = str(info.get("prompt_preview") or task_id)
            task_num = info.get("task_num")
            started_at = float(info.get("started_at") or 0.0)
            elapsed = max(0.0, time.time() - started_at) if started_at else 0.0
            label = f"#{task_num}" if task_num else task_id
            lines.append(f"  ● {label} {preview}")
            lines.append(f"    id={task_id} thread={thread.name} elapsed={elapsed:.1f}s")
        return "\n".join(lines)

    def _handle_tasks_command(self, cmd: str = "/tasks") -> None:
        """Show or manage active subagent tasks."""
        parts = cmd.strip().split()
        action = parts[1].lower() if len(parts) >= 2 else "show"
        task_ref = parts[2].strip() if len(parts) >= 3 else ""
        display_managers = self._get_subagent_display_managers()
        if action in ("show", "list"):
            if display_managers:
                try:
                    panel = "\n\n".join(
                        str(manager.render_tasks_command()) for manager in display_managers
                    )
                except Exception as exc:
                    _cprint(f"  Failed to render subagent tasks: {exc}")
                    return
                ChatConsole().print(_rich_text_from_ansi(panel))
                return

            summary = self._render_background_tasks_summary()
            ChatConsole().print(_rich_text_from_ansi(summary))
            return

        if action not in ("bg", "background", "fg", "foreground"):
            _cprint("  Usage: /tasks")
            _cprint("  API-A manages subagents automatically; bg/fg are advanced debug actions.")
            _cprint("         /tasks bg <task-id|index>")
            _cprint("         /tasks fg <task-id|index>")
            return

        if not display_managers:
            _cprint("  No active subagent display is available right now.")
            return

        if not task_ref:
            _cprint("  API-A manages subagents automatically; specify a task only for advanced debug actions.")
            _cprint("         /tasks bg <task-id|index>")
            _cprint("         /tasks fg <task-id|index>")
            return

        display_manager = None
        task = None
        for manager in display_managers:
            task = manager.resolve_task_ref(task_ref)
            if task is not None:
                display_manager = manager
                break
        if task is None:
            _cprint(f"  Unknown subagent task: {task_ref}")
            return

        if action in ("bg", "background"):
            try:
                moved = display_manager.send_to_background(task.task_id)
            except Exception as exc:
                _cprint(f"  Failed to send subagent task to background: {exc}")
                return
            if not moved:
                _cprint(f"  Could not background subagent task: {task_ref}")
                return
        else:
            try:
                moved = display_manager.bring_to_foreground(task.task_id)
            except Exception as exc:
                _cprint(f"  Failed to bring subagent task to foreground: {exc}")
                return
            if not moved:
                _cprint(f"  Could not foreground subagent task: {task_ref}")
                return

        if self._app:
            self._invalidate(min_interval=0)
            return

    def _handle_btw_command(self, cmd: str):
        """Handle /btw <question> — ephemeral side question using session context.

        Snapshots the current conversation history, spawns a no-tools agent in
        a background thread, and prints the answer without persisting anything
        to the main session.
        """
        parts = cmd.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            _cprint("  Usage: /btw <question>")
            _cprint("  Example: /btw what module owns session title sanitization?")
            _cprint("  Answers using session context. No tools, not persisted.")
            return

        question = parts[1].strip()
        task_id = f"btw_{datetime.now().strftime('%H%M%S')}_{uuid.uuid4().hex[:6]}"

        if not self._ensure_runtime_credentials():
            _cprint("  (>_<) Cannot start /btw: no valid credentials.")
            return

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
                    try:
                        from VoidCube_cli.skin_engine import get_active_skin
                        _skin = get_active_skin()
                        _resp_color = _skin.get_color("response_border", "#4F6D4A")  # type: ignore[attr-defined]
                    except Exception:
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

    def _handle_browser_command(self, cmd: str):
        """Handle /browser connect|disconnect|status — manage live Chrome CDP connection."""
        import platform as _plat

        parts = cmd.strip().split(None, 1)
        sub = parts[1].lower().strip() if len(parts) > 1 else "status"

        _DEFAULT_CDP = "http://localhost:9222"
        current = os.environ.get("BROWSER_CDP_URL", "").strip()

        if sub.startswith("connect"):
            # Optionally accept a custom CDP URL: /browser connect ws://host:port
            connect_parts = cmd.strip().split(None, 2)  # ["/browser", "connect", "ws://..."]
            cdp_url = connect_parts[2].strip() if len(connect_parts) > 2 else _DEFAULT_CDP

            # Clear any existing browser sessions so the next tool call uses the new backend
            try:
                from tools.browser_tool import cleanup_all_browsers
                cleanup_all_browsers()
            except Exception:
                pass

            print()

            # Extract port for connectivity checks
            _port = 9222
            try:
                _port = int(cdp_url.rsplit(":", 1)[-1].split("/")[0])
            except (ValueError, IndexError):
                pass

            # Check if Chrome is already listening on the debug port
            import socket
            _already_open = False
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                s.connect(("127.0.0.1", _port))
                s.close()
                _already_open = True
            except (OSError, socket.timeout):
                pass

            if _already_open:
                print(f"   ✓ Chrome is already listening on port {_port}")
            elif cdp_url == _DEFAULT_CDP:
                # Try to auto-launch Chrome with remote debugging
                print("   Chrome isn't running with remote debugging — attempting to launch...")
                _launched = self._try_launch_chrome_debug(_port, _plat.system())
                if _launched:
                    # Wait for the port to come up
                    import time as _time
                    for _wait in range(10):
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(1)
                            s.connect(("127.0.0.1", _port))
                            s.close()
                            _already_open = True
                            break
                        except (OSError, socket.timeout):
                            _time.sleep(0.5)
                    if _already_open:
                        print(f"   ✓ Chrome launched and listening on port {_port}")
                    else:
                        print(f"   ⚠ Chrome launched but port {_port} isn't responding yet")
                        print("     Try again in a few seconds — the debug instance may still be starting")
                else:
                    print("   ⚠ Could not auto-launch Chrome")
                    # Show manual instructions as fallback
                    _data_dir = str(_VoidCube_home / "chrome-debug")
                    sys_name = _plat.system()
                    if sys_name == "Darwin":
                        chrome_cmd = (
                            'open -a "Google Chrome" --args'
                            f" --remote-debugging-port=9222"
                            f' --user-data-dir="{_data_dir}"'
                            " --no-first-run --no-default-browser-check"
                        )
                    elif sys_name == "Windows":
                        chrome_cmd = (
                            f'chrome.exe --remote-debugging-port=9222'
                            f' --user-data-dir="{_data_dir}"'
                            f" --no-first-run --no-default-browser-check"
                        )
                    else:
                        chrome_cmd = (
                            f"google-chrome --remote-debugging-port=9222"
                            f' --user-data-dir="{_data_dir}"'
                            f" --no-first-run --no-default-browser-check"
                        )
                    print(f"     Launch Chrome manually:")
                    print(f"     {chrome_cmd}")
            else:
                print(f"   ⚠ Port {_port} is not reachable at {cdp_url}")

            os.environ["BROWSER_CDP_URL"] = cdp_url
            print()
            print("🌐 Browser connected to live Chrome via CDP")
            print(f"   Endpoint: {cdp_url}")
            print()

            # Inject context message so the model knows
            if hasattr(self, '_pending_input'):
                self._pending_input.put(
                    "[System note: The user has connected your browser tools to their live Chrome browser "
                    "via Chrome DevTools Protocol. Your browser_navigate, browser_snapshot, browser_click, "
                    "and other browser tools now control their real browser — including any pages they have "
                    "open, logged-in sessions, and cookies. They likely opened specific sites or logged into "
                    "services before connecting. Please await their instruction before attempting to operate "
                    "the browser. When you do act, be mindful that your actions affect their real browser — "
                    "don't close tabs or navigate away from pages without asking.]"
                )

        elif sub == "disconnect":
            if current:
                os.environ.pop("BROWSER_CDP_URL", None)
                try:
                    from tools.browser_tool import cleanup_all_browsers
                    cleanup_all_browsers()
                except Exception:
                    pass
                print()
                print("🌐 Browser disconnected from live Chrome")
                print("   Browser tools reverted to default mode (local headless or cloud provider)")
                print()

                if hasattr(self, '_pending_input'):
                    self._pending_input.put(
                        "[System note: The user has disconnected the browser tools from their live Chrome. "
                        "Browser tools are back to default mode (headless local browser or cloud provider).]"
                    )
            else:
                print()
                print("Browser is not connected to live Chrome (already using default mode)")
                print()

        elif sub == "status":
            print()
            if current:
                print("🌐 Browser: connected to live Chrome via CDP")
                print(f"   Endpoint: {current}")

                _port = 9222
                try:
                    _port = int(current.rsplit(":", 1)[-1].split("/")[0])
                except (ValueError, IndexError):
                    pass
                try:
                    import socket
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect(("127.0.0.1", _port))
                    s.close()
                    print("   Status: ✓ reachable")
                except (OSError, Exception):
                    print("   Status: ⚠ not reachable (Chrome may not be running)")
            else:
                try:
                    from tools.browser_tool import _get_cloud_provider
                    provider = _get_cloud_provider()
                except Exception:
                    provider = None

                if provider is not None:
                    print(f"🌐 Browser: {provider.provider_name()} (cloud)")
                else:
                    print("🌐 Browser: local headless Chromium (agent-browser)")
            print()
            print("   /browser connect      — connect to your live Chrome")
            print("   /browser disconnect   — revert to default")
            print()

        else:
            print()
            print("Usage: /browser connect|disconnect|status")
            print()
            print("   connect      Connect browser tools to your live Chrome session")
            print("   disconnect   Revert to default browser backend")
            print("   status       Show current browser mode")
            print()

    def _handle_skin_command(self, cmd: str):
        """Handle /skin [name] — show or change the display skin."""
        try:
            from VoidCube_cli.skin_engine import list_skins, set_active_skin, get_active_skin_name
        except ImportError:
            print("Skin engine not available.")
            return

        parts = cmd.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            # Show current skin and list available
            current = get_active_skin_name()
            skins = list_skins()
            print(f"\n  Current skin: {current}")
            print("  Available skins:")
            for s in skins:
                marker = " ●" if s["name"] == current else "  "
                source = f" ({s['source']})" if s["source"] == "user" else ""
                print(f"   {marker} {s['name']}{source} — {s['description']}")
            print("\n  Usage: /skin <name>")
            print(f"  Custom skins: drop a YAML file in {display_VoidCube_home()}/skins/\n")
            return

        new_skin = parts[1].strip().lower()
        available = {s["name"] for s in list_skins()}
        if new_skin not in available:
            print(f"  Unknown skin: {new_skin}")
            print(f"  Available: {', '.join(sorted(available))}")
            return

        set_active_skin(new_skin)
        _ACCENT.reset()  # Re-resolve ANSI color for the new skin
        if save_config_value("display.skin", new_skin):
            print(f"  Skin set to: {new_skin} (saved)")
        else:
            print(f"  Skin set to: {new_skin}")
        print("  Note: banner colors will update on next session start.")
        if self._apply_tui_skin_style():
            print("  Prompt + TUI colors updated.")

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

    def _handle_reasoning_command(self, cmd: str):
        """Handle /reasoning — manage effort level and display toggle.

        Usage:
            /reasoning              Show current effort level and display state
            /reasoning <level>      Set reasoning effort (none, minimal, low, medium, high, xhigh)
            /reasoning show|on      Show model thinking/reasoning in output
            /reasoning hide|off     Hide model thinking/reasoning from output
        """
        parts = cmd.strip().split(maxsplit=1)

        if len(parts) < 2:
            # Show current state
            rc = self.reasoning_config
            if rc is None:
                level = "medium (default)"
            elif rc.get("enabled") is False:
                level = "none (disabled)"
            else:
                level = rc.get("effort", "medium")
            display_state = "on ✓" if self.show_reasoning else "off"
            _cprint(f"  {_ACCENT}Reasoning effort:  {level}{_RST}")
            _cprint(f"  {_ACCENT}Reasoning display: {display_state}{_RST}")
            _cprint(f"  {_DIM}Usage: /reasoning <none|minimal|low|medium|high|xhigh|show|hide>{_RST}")
            return

        arg = parts[1].strip().lower()

        # Display toggle
        if arg in ("show", "on"):
            self.show_reasoning = True
            if self.agent:
                self.agent.reasoning_callback = self._current_reasoning_callback()
            save_config_value("display.show_reasoning", True)
            _cprint(f"  {_ACCENT}✓ Reasoning display: ON (saved){_RST}")
            _cprint(f"  {_DIM}  Model thinking will be shown during and after each response.{_RST}")
            return
        if arg in ("hide", "off"):
            self.show_reasoning = False
            if self.agent:
                self.agent.reasoning_callback = self._current_reasoning_callback()
            save_config_value("display.show_reasoning", False)
            _cprint(f"  {_ACCENT}✓ Reasoning display: OFF (saved){_RST}")
            return

        # Effort level change
        parsed = _parse_reasoning_config(arg)
        if parsed is None:
            _cprint(f"  {_DIM}(._.) Unknown argument: {arg}{_RST}")
            _cprint(f"  {_DIM}Valid levels: none, minimal, low, medium, high, xhigh{_RST}")
            _cprint(f"  {_DIM}Display:      show, hide{_RST}")
            return

        self.reasoning_config = parsed
        self.agent = None  # Force agent re-init with new reasoning config

        if save_config_value("agent.reasoning_effort", arg):
            _cprint(f"  {_ACCENT}✓ Reasoning effort set to '{arg}' (saved to config){_RST}")
        else:
            _cprint(f"  {_ACCENT}✓ Reasoning effort set to '{arg}' (session only){_RST}")

    def _handle_fast_command(self, cmd: str):
        """Handle /fast — toggle OpenAI-compatible priority processing."""
        if not self._fast_command_available():
            _cprint("  (._.) /fast is only available for models that support priority processing.")
            return

        feature_name = "Priority Processing"

        parts = cmd.strip().split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip().lower() == "status":
            status = "fast" if self.service_tier == "priority" else "normal"
            _cprint(f"  {_ACCENT}{feature_name}: {status}{_RST}")
            _cprint(f"  {_DIM}Usage: /fast [normal|fast|status]{_RST}")
            return

        arg = parts[1].strip().lower()

        if arg in {"fast", "on"}:
            self.service_tier = "priority"
            saved_value = "fast"
            label = "FAST"
        elif arg in {"normal", "off"}:
            self.service_tier = None
            saved_value = "normal"
            label = "NORMAL"
        else:
            _cprint(f"  {_DIM}(._.) Unknown argument: {arg}{_RST}")
            _cprint(f"  {_DIM}Usage: /fast [normal|fast|status]{_RST}")
            return

        self.agent = None  # Force agent re-init with new service-tier config
        if save_config_value("agent.service_tier", saved_value):
            _cprint(f"  {_ACCENT}✓ {feature_name} set to {label} (saved to config){_RST}")
        else:
            _cprint(f"  {_ACCENT}✓ {feature_name} set to {label} (session only){_RST}")

    def _on_reasoning(self, reasoning_text: str):
        """Callback for intermediate reasoning display during tool-call loops."""
        if not reasoning_text:
            return
        self._stream_render_state.reasoning_preview_buffer += reasoning_text
        self._flush_reasoning_preview(force=False)

    def _manual_compress(self, cmd_original: str = ""):
        """Manually trigger context compression on the current conversation.

        Accepts an optional focus topic: ``/compress <focus>`` guides the
        summariser to preserve information related to *focus* while being
        more aggressive about discarding everything else.  Inspired by
        The ``/compact <focus>`` workflow.
        """
        if not self.conversation_history or len(self.conversation_history) < 4:
            print("(._.) Not enough conversation to compress (need at least 4 messages).")
            return

        if not self.agent:
            print("(._.) No active agent -- send a message first.")
            return

        if not self.agent.compression_enabled:
            print("(._.) Compression is disabled in config.")
            return

        # Extract optional focus topic from the command (e.g. "/compress database schema")
        focus_topic = ""
        if cmd_original:
            parts = cmd_original.strip().split(None, 1)
            if len(parts) > 1:
                focus_topic = parts[1].strip()

        original_count = len(self.conversation_history)
        try:
            from agent.model_metadata import estimate_messages_tokens_rough
            from agent.manual_compression_feedback import summarize_manual_compression
            original_history = list(self.conversation_history)
            approx_tokens = estimate_messages_tokens_rough(original_history)
            if focus_topic:
                print(f"🗜️  Compressing {original_count} messages (~{approx_tokens:,} tokens), "
                      f"focus: \"{focus_topic}\"...")
            else:
                print(f"🗜️  Compressing {original_count} messages (~{approx_tokens:,} tokens)...")

            compressed, _ = self.agent._compress_context(
                original_history,
                self.agent._cached_system_prompt or "",
                approx_tokens=approx_tokens,
                focus_topic=focus_topic or None,
            )
            self.conversation_history = compressed
            new_tokens = estimate_messages_tokens_rough(self.conversation_history)
            summary = summarize_manual_compression(
                original_history,
                self.conversation_history,
                approx_tokens,
                new_tokens,
            )
            icon = "🗜️" if summary["noop"] else "✅"
            print(f"  {icon} {summary['headline']}")
            print(f"     {summary['token_line']}")
            if summary["note"]:
                print(f"     {summary['note']}")

        except Exception as e:
            print(f"  ❌ Compression failed: {e}")

    def _handle_debug_command(self):
        """Handle /debug — upload debug report + logs and print paste URLs."""
        from VoidCube_cli.debug import run_debug_share
        from types import SimpleNamespace

        args = SimpleNamespace(lines=200, expire=7, local=False)
        run_debug_share(args)

    def _show_usage(self):
        """Show rate limits (if available) and session token usage."""
        if not self.agent:
            print("(._.) No active agent -- send a message first.")
            return

        agent = self.agent
        calls = agent.session_api_calls

        if calls == 0:
            print("(._.) No API calls made yet in this session.")
            return

        # ── Rate limits (shown first when available) ────────────────
        rl_state = agent.get_rate_limit_state()
        if rl_state and rl_state.has_data:
            from agent.rate_limit_tracker import format_rate_limit_display
            print()
            print(format_rate_limit_display(rl_state))
            print()

        # ── Session token usage ─────────────────────────────────────
        input_tokens = getattr(agent, "session_input_tokens", 0) or 0
        output_tokens = getattr(agent, "session_output_tokens", 0) or 0
        cache_read_tokens = getattr(agent, "session_cache_read_tokens", 0) or 0
        cache_write_tokens = getattr(agent, "session_cache_write_tokens", 0) or 0
        prompt = agent.session_prompt_tokens
        completion = agent.session_completion_tokens
        total = agent.session_total_tokens

        compressor = agent.context_compressor
        last_prompt = compressor.last_prompt_tokens
        ctx_len = compressor.context_length
        pct = min(100, (last_prompt / ctx_len * 100)) if ctx_len else 0
        compressions = compressor.compression_count

        msg_count = len(self.conversation_history)
        cost_result = _estimate_usage_cost_lazy(
            agent.model,
            _CanonicalUsage_lazy(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            ),
            provider=getattr(agent, "provider", None),
            base_url=getattr(agent, "base_url", None),
        )
        elapsed = _format_duration_compact_lazy((datetime.now() - self.session_start).total_seconds())

        print("  📊 Session Token Usage")
        print(f"  {'─' * 40}")
        print(f"  Model:                     {agent.model}")
        print(f"  Input tokens:              {input_tokens:>10,}")
        print(f"  Cache read tokens:         {cache_read_tokens:>10,}")
        print(f"  Cache write tokens:        {cache_write_tokens:>10,}")
        print(f"  Output tokens:             {output_tokens:>10,}")
        print(f"  Prompt tokens (total):     {prompt:>10,}")
        print(f"  Completion tokens:         {completion:>10,}")
        print(f"  Total tokens:              {total:>10,}")
        print(f"  API calls:                 {calls:>10,}")
        print(f"  Session duration:          {elapsed:>10}")
        print(f"  Cost status:              {cost_result.status:>10}")
        print(f"  Cost source:              {cost_result.source:>10}")
        if cost_result.amount_usd is not None:
            prefix = "~" if cost_result.status == "estimated" else ""
            print(f"  Total cost:              {prefix}${float(cost_result.amount_usd):>10.4f}")
        elif cost_result.status == "included":
            print(f"  Total cost:              {'included':>10}")
        else:
            print(f"  Total cost:              {'n/a':>10}")
        print(f"  {'─' * 40}")
        print(f"  Current context:  {last_prompt:,} / {ctx_len:,} ({pct:.0f}%)")
        print(f"  Messages:         {msg_count}")
        print(f"  Compressions:     {compressions}")
        if cost_result.status == "unknown":
            print(f"  Note:             Pricing unknown for {agent.model}")

        if self.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
            for noisy in ('openai', 'openai._base_client', 'httpx', 'httpcore', 'asyncio', 'hpack', 'grpc', 'modal'):
                logging.getLogger(noisy).setLevel(logging.WARNING)
        else:
            logging.getLogger().setLevel(logging.INFO)
            for quiet_logger in ('tools', 'run_agent', 'trajectory_compressor', 'cron', 'VoidCube_cli'):
                logging.getLogger(quiet_logger).setLevel(logging.ERROR)

    def _check_config_mcp_changes(self) -> None:
        """Detect mcp_servers changes in config.yaml and auto-reload MCP connections.

        Called from process_loop every CONFIG_WATCH_INTERVAL seconds.
        Compares config.yaml mtime + mcp_servers section against the last
        known state.  When a change is detected, triggers _reload_mcp() and
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
            target=self._reload_mcp, daemon=True
        )
        _reload_thread.start()
        _reload_thread.join(timeout=30)
        if _reload_thread.is_alive():
            print("  ⚠️  MCP reload timed out (30s). Some servers may not have reconnected.")

    def _reload_mcp(self):
        """Reload MCP servers: disconnect all, re-read config.yaml, reconnect.

        After reconnecting, refreshes the agent's tool list so the model
        sees the updated tools on the next turn.
        """
        try:
            from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools, _servers, _lock

            # Capture old server names
            with _lock:
                old_servers = set(_servers.keys())

            if not self._command_running:
                print("🔄 Reloading MCP servers...")

            # Shutdown existing connections
            shutdown_mcp_servers()

            # Reconnect (reads config.yaml fresh)
            new_tools = discover_mcp_tools()

            # Compute what changed
            with _lock:
                connected_servers = set(_servers.keys())

            added = connected_servers - old_servers
            removed = old_servers - connected_servers
            reconnected = connected_servers & old_servers

            if reconnected:
                print(f"  ♻️  Reconnected: {', '.join(sorted(reconnected))}")
            if added:
                print(f"  ➕ Added: {', '.join(sorted(added))}")
            if removed:
                print(f"  ➖ Removed: {', '.join(sorted(removed))}")
            if not connected_servers:
                print("  No MCP servers connected.")
            else:
                print(f"  🔧 {len(new_tools)} tool(s) available from {len(connected_servers)} server(s)")

            # Refresh the agent's tool list so the model can call new tools
            if self.agent is not None:
                self.agent.tools = _get_tool_definitions(
                    enabled_toolsets=self.agent.enabled_toolsets
                    if hasattr(self.agent, "enabled_toolsets") else None,
                    quiet_mode=True,
                )
                self.agent.valid_tool_names = {
                    tool["function"]["name"] for tool in self.agent.tools
                } if self.agent.tools else set()

            # Inject a message at the END of conversation history so the
            # model knows tools changed.  Appended after all existing
            # messages to preserve prompt-cache for the prefix.
            change_parts = []
            if added:
                change_parts.append(f"Added servers: {', '.join(sorted(added))}")
            if removed:
                change_parts.append(f"Removed servers: {', '.join(sorted(removed))}")
            if reconnected:
                change_parts.append(f"Reconnected servers: {', '.join(sorted(reconnected))}")
            tool_summary = f"{len(new_tools)} MCP tool(s) now available" if new_tools else "No MCP tools available"
            change_detail = ". ".join(change_parts) + ". " if change_parts else ""
            self.conversation_history.append({
                "role": "user",
                "content": f"[SYSTEM: MCP servers have been reloaded. {change_detail}{tool_summary}. The tool list for this conversation has been updated accordingly.]",
            })

            # Persist session immediately so the session log reflects the
            # updated tools list (self.agent.tools was refreshed above).
            if self.agent is not None:
                try:
                    self.agent._persist_session(
                        self.conversation_history,
                        self.conversation_history,
                    )
                except Exception:
                    pass  # Best-effort

            print(f"  ✅ Agent updated — {len(self.agent.tools if self.agent else [])} tool(s) available")

        except Exception as e:
            print(f"  ❌ MCP reload failed: {e}")

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
    # Tool progress callback (audio cues for voice mode)
    # ====================================================================

    def _on_tool_progress(self, event_type: str, function_name: Optional[str] = None, preview: Optional[str] = None, function_args: Optional[dict] = None, **kwargs):
        """Called on tool lifecycle events (tool.started, tool.completed, reasoning.available, etc.).

        Updates the TUI spinner widget so the user can see what the agent
        is doing during tool execution (fills the gap between thinking
        spinner and next response).  Also plays audio cue in voice mode.

        On tool.started, records a monotonic timestamp so get_spinner_text()
        can show a live elapsed timer (the TUI poll loop already invalidates
        every ~0.15s, so the counter updates automatically).

        When tool_progress_mode is "all" or "new", also prints a persistent
        stacked line to scrollback on tool.completed so users can see the
        full history of tool calls (not just the current one in the spinner).
        """
        if event_type == "tool.completed":
            import time as _time
            self._tool_start_time = 0.0
            self._current_tool_name = ""
            if (
                getattr(self, "_autonomous_gate_active", False)
                and getattr(self, "_current_autonomous_task", None)
                and function_name
            ):
                duration = kwargs.get("duration", 0.0)
                suffix = f" ({duration:.1f}s)" if duration else ""
                _append_autonomous_execution_event_view(
                    self,
                    f"工具完成: {function_name}{suffix}",
                    tone="success" if not kwargs.get("is_error", False) else "error",
                    stage="tool_completed",
                )
            # Print stacked scrollback line for "all" / "new" modes
            if function_name and self.tool_progress_mode in ("all", "new"):
                duration = kwargs.get("duration", 0.0)
                is_error = kwargs.get("is_error", False)
                # Pop stored args from tool.started for this function
                stored = self._pending_tool_info.get(function_name)
                stored_args = stored.pop(0) if stored else {}
                if stored is not None and not stored:
                    del self._pending_tool_info[function_name]
                # "new" mode: skip consecutive repeats of the same tool
                if self.tool_progress_mode == "new" and function_name == self._last_scrollback_tool:
                    self._invalidate()
                    return
                self._last_scrollback_tool = function_name
                if self._should_emit_scrollback_output():
                    try:
                        from agent.display import get_cute_tool_message
                        line = get_cute_tool_message(function_name, stored_args, duration)
                        if is_error:
                            line = f"{line} [error]"
                        _cprint(f"  {line}")
                    except Exception:
                        pass
            self._invalidate()
            return
        if event_type != "tool.started":
            return
        if function_name and not function_name.startswith("_"):
            import time as _time
            from agent.display import get_tool_emoji
            emoji = get_tool_emoji(function_name)
            label = preview or function_name
            from agent.display import get_tool_preview_max_len
            _pl = get_tool_preview_max_len()
            if _pl > 0 and len(label) > _pl:
                label = label[:_pl - 3] + "..."
            self._spinner_text = f"{emoji} {label}"
            self._tool_start_time = _time.monotonic()
            self._current_tool_name = function_name
            # Store args for stacked scrollback line on completion
            self._pending_tool_info.setdefault(function_name, []).append(
                function_args if function_args is not None else {}
            )
            if getattr(self, "_autonomous_gate_active", False) and getattr(self, "_current_autonomous_task", None):
                _append_autonomous_execution_event_view(
                    self,
                    f"工具启动: {function_name}",
                    tone="info",
                    stage="tool_started",
                )
            self._invalidate()

        if not self._voice_mode:
            return
        if not function_name or function_name.startswith("_"):
            return
        try:
            from tools.voice_mode import play_beep
            threading.Thread(
                target=play_beep,
                kwargs={"frequency": 1200, "duration": 0.06, "count": 1},
                daemon=True,
            ).start()
        except Exception:
            pass

    def _on_tool_start(self, tool_call_id: str, function_name: str, function_args: dict):
        """Capture local before-state for write-capable tools."""
        try:
            from agent.display import capture_local_edit_snapshot

            snapshot = capture_local_edit_snapshot(function_name, function_args)
            if snapshot is not None:
                self._pending_edit_snapshots[tool_call_id] = snapshot
        except Exception:
            logger.debug("Edit snapshot capture failed for %s", function_name, exc_info=True)

    def _on_tool_complete(self, tool_call_id: str, function_name: str, function_args: dict, function_result: str):
        """Render file edits with inline diff after write-capable tools complete."""
        if not self._should_emit_scrollback_output():
            self._pending_edit_snapshots.pop(tool_call_id, None)
            return
        snapshot = self._pending_edit_snapshots.pop(tool_call_id, None)
        try:
            from agent.display import render_edit_diff_with_delta

            render_edit_diff_with_delta(
                function_name,
                function_result,
                function_args=function_args,
                snapshot=snapshot,
                print_fn=_cprint,
            )
        except Exception:
            logger.debug("Edit diff preview failed for %s", function_name, exc_info=True)

    # ====================================================================
    # Voice mode methods
    # ====================================================================

    def _voice_start_recording(self):
        """Start capturing audio from the microphone."""
        if getattr(self, '_should_exit', False):
            return
        from tools.voice_mode import create_audio_recorder, check_voice_requirements

        reqs = check_voice_requirements()
        if not reqs["audio_available"]:
            if _is_termux_environment():
                details = reqs.get("details", "")
                if "Termux:API Android app is not installed" in details:
                    raise RuntimeError(
                        "Termux:API command package detected, but the Android app is missing.\n"
                        "Install/update the Termux:API Android app, then retry /voice on.\n"
                        "Fallback: pkg install python-numpy portaudio && python -m pip install sounddevice"
                    )
                raise RuntimeError(
                    "Voice mode requires either Termux:API microphone access or Python audio libraries.\n"
                    "Option 1: pkg install termux-api and install the Termux:API Android app\n"
                    "Option 2: pkg install python-numpy portaudio && python -m pip install sounddevice"
                )
            raise RuntimeError(
                "Voice mode requires sounddevice and numpy.\n"
                "Install with: pip install sounddevice numpy\n"
                "Or: pip install VoidCube-agent[voice]"
            )
        if not reqs.get("stt_available", reqs.get("stt_key_set")):
            raise RuntimeError(
                "Voice mode requires an STT provider for transcription.\n"
                "Option 1: pip install faster-whisper  (free, local)\n"
                "Option 2: Set GROQ_API_KEY (free tier)\n"
                "Option 3: Set VOICE_TOOLS_OPENAI_KEY (paid)"
            )

        # Prevent double-start from concurrent threads (atomic check-and-set)
        with self._voice_lock:
            if self._voice_recording:
                return
            self._voice_recording = True

        # Load silence detection params from config
        voice_cfg = {}
        try:
            from VoidCube_cli.config import load_config
            voice_cfg = load_config().get("voice", {})
        except Exception:
            pass

        if self._voice_recorder is None:
            self._voice_recorder = create_audio_recorder()

        # Apply config-driven silence params
        self._voice_recorder._silence_threshold = voice_cfg.get("silence_threshold", 200)
        self._voice_recorder._silence_duration = voice_cfg.get("silence_duration", 3.0)

        def _on_silence():
            """Called by AudioRecorder when silence is detected after speech."""
            with self._voice_lock:
                if not self._voice_recording:
                    return
            _cprint(f"\n{_DIM}Silence detected, auto-stopping...{_RST}")
            if hasattr(self, '_app') and self._app:
                self._app.invalidate()
            self._voice_stop_and_transcribe()

        # Audio cue: single beep BEFORE starting stream (avoid CoreAudio conflict)
        try:
            from tools.voice_mode import play_beep
            play_beep(frequency=880, count=1)
        except Exception:
            pass

        try:
            self._voice_recorder.start(on_silence_stop=_on_silence)
        except Exception:
            with self._voice_lock:
                self._voice_recording = False
            raise
        if getattr(self._voice_recorder, "supports_silence_autostop", True):
            _recording_hint = "auto-stops on silence | Ctrl+B to stop & exit continuous"
        elif _is_termux_environment():
            _recording_hint = "Termux:API capture | Ctrl+B to stop"
        else:
            _recording_hint = "Ctrl+B to stop"
        _cprint(f"\n{_ACCENT}● Recording...{_RST} {_DIM}({_recording_hint}){_RST}")

        # Periodically refresh prompt to update audio level indicator
        def _refresh_level():
            while True:
                with self._voice_lock:
                    still_recording = self._voice_recording
                if not still_recording:
                    break
                if hasattr(self, '_app') and self._app:
                    self._app.invalidate()
                time.sleep(0.15)
        threading.Thread(target=_refresh_level, daemon=True).start()

    def _voice_stop_and_transcribe(self):
        """Stop recording, transcribe via STT, and queue the transcript as input."""
        # Atomic guard: only one thread can enter stop-and-transcribe.
        # Set _voice_processing immediately so concurrent Ctrl+B presses
        # don't race into the START path while recorder.stop() holds its lock.
        with self._voice_lock:
            if not self._voice_recording:
                return
            self._voice_recording = False
            self._voice_processing = True

        submitted = False
        wav_path = None
        try:
            if self._voice_recorder is None:
                return

            wav_path = self._voice_recorder.stop()

            # Audio cue: double beep after stream stopped (no CoreAudio conflict)
            try:
                from tools.voice_mode import play_beep
                play_beep(frequency=660, count=2)
            except Exception:
                pass

            if wav_path is None:
                _cprint(f"{_DIM}No speech detected.{_RST}")
                return

            # _voice_processing is already True (set atomically above)
            if hasattr(self, '_app') and self._app:
                self._app.invalidate()
            _cprint(f"{_DIM}Transcribing...{_RST}")

            # Get STT model from config
            stt_model = None
            try:
                from VoidCube_cli.config import load_config
                stt_config = load_config().get("stt", {})
                stt_model = stt_config.get("model")
            except Exception:
                pass

            from tools.voice_mode import transcribe_recording
            result = transcribe_recording(wav_path, model=stt_model)

            if result.get("success") and result.get("transcript", "").strip():
                transcript = result["transcript"].strip()
                self._attached_images.clear()
                if hasattr(self, '_app') and self._app:
                    self._app.invalidate()
                self._pending_input.put(transcript)
                submitted = True
            elif result.get("success"):
                _cprint(f"{_DIM}No speech detected.{_RST}")
            else:
                error = result.get("error", "Unknown error")
                _cprint(f"\n{_DIM}Transcription failed: {error}{_RST}")

        except Exception as e:
            _cprint(f"\n{_DIM}Voice processing error: {e}{_RST}")
        finally:
            with self._voice_lock:
                self._voice_processing = False
            if hasattr(self, '_app') and self._app:
                self._app.invalidate()
            # Clean up temp file
            try:
                if wav_path and os.path.isfile(wav_path):
                    os.unlink(wav_path)
            except Exception:
                pass

            # Track consecutive no-speech cycles to avoid infinite restart loops.
            if not submitted:
                self._no_speech_count = getattr(self, '_no_speech_count', 0) + 1
                if self._no_speech_count >= 3:
                    self._voice_continuous = False
                    self._no_speech_count = 0
                    _cprint(f"{_DIM}No speech detected 3 times, continuous mode stopped.{_RST}")
                    self._voice_stop_continuous = True
            else:
                self._no_speech_count = 0

        # Python 3.14+: return outside finally block to avoid SyntaxWarning
        if getattr(self, '_voice_stop_continuous', False):
            self._voice_stop_continuous = False
            return

        # If no transcript was submitted but continuous mode is active,
        # restart recording so the user can keep talking.
        # (When transcript IS submitted, process_loop handles restart
        # after chat() completes.)
        if not submitted and self._voice_continuous and not self._voice_recording:
            def _restart_recording():
                try:
                    self._voice_start_recording()
                    if hasattr(self, '_app') and self._app:
                        self._app.invalidate()
                except Exception as e:
                    _cprint(f"{_DIM}Voice auto-restart failed: {e}{_RST}")
            threading.Thread(target=_restart_recording, daemon=True).start()

    def _voice_speak_response(self, text: str):
        """Speak the agent's response aloud using TTS (runs in background thread)."""
        if not self._voice_tts:
            return
        self._voice_tts_done.clear()
        try:
            # TTS feature has been removed
            logger.warning("TTS feature is not available in this simplified version")
            _cprint(f"{_DIM}TTS feature is not available{_RST}")
        except Exception as e:
            logger.warning("Voice TTS playback failed: %s", e)
            _cprint(f"{_DIM}TTS playback failed: {e}{_RST}")
        finally:
            self._voice_tts_done.set()

    def _handle_voice_command(self, command: str):
        """Handle /voice [on|off|tts|status] command."""
        parts = command.strip().split(maxsplit=1)
        subcommand = parts[1].lower().strip() if len(parts) > 1 else ""

        if subcommand == "on":
            self._enable_voice_mode()
        elif subcommand == "off":
            self._disable_voice_mode()
        elif subcommand == "tts":
            self._toggle_voice_tts()
        elif subcommand == "status":
            self._show_voice_status()
        elif subcommand == "":
            # Toggle
            if self._voice_mode:
                self._disable_voice_mode()
            else:
                self._enable_voice_mode()
        else:
            _cprint(f"Unknown voice subcommand: {subcommand}")
            _cprint("Usage: /voice [on|off|tts|status]")

    def _enable_voice_mode(self):
        """Enable voice mode after checking requirements."""
        if self._voice_mode:
            _cprint(f"{_DIM}Voice mode is already enabled.{_RST}")
            return

        from tools.voice_mode import check_voice_requirements, detect_audio_environment

        # Environment detection -- warn and block in incompatible environments
        env_check = detect_audio_environment()
        if not env_check["available"]:
            _cprint(f"\n{_ACCENT}Voice mode unavailable in this environment:{_RST}")
            for warning in env_check["warnings"]:
                _cprint(f"  {_DIM}{warning}{_RST}")
            return

        reqs = check_voice_requirements()
        if not reqs["available"]:
            _cprint(f"\n{_ACCENT}Voice mode requirements not met:{_RST}")
            for line in reqs["details"].split("\n"):
                _cprint(f"  {_DIM}{line}{_RST}")
            if reqs["missing_packages"]:
                if _is_termux_environment():
                    _cprint(f"\n  {_BOLD}Option 1: pkg install termux-api{_RST}")
                    _cprint(f"  {_DIM}Then install/update the Termux:API Android app for microphone capture{_RST}")
                    _cprint(f"  {_BOLD}Option 2: pkg install python-numpy portaudio && python -m pip install sounddevice{_RST}")
                else:
                    _cprint(f"\n  {_BOLD}Install: pip install {' '.join(reqs['missing_packages'])}{_RST}")
                    _cprint(f"  {_DIM}Or: pip install VoidCube-agent[voice]{_RST}")
            return

        with self._voice_lock:
            self._voice_mode = True

        # Check config for auto_tts
        try:
            from VoidCube_cli.config import load_config
            voice_config = load_config().get("voice", {})
            if voice_config.get("auto_tts", False):
                with self._voice_lock:
                    self._voice_tts = True
        except Exception:
            pass

        # Voice mode instruction is injected as a user message prefix (not a
        # system prompt change) to avoid invalidating the prompt cache.  See
        # _voice_message_prefix property and its usage in _process_message().

        tts_status = " (TTS enabled)" if self._voice_tts else ""
        try:
            from VoidCube_cli.config import load_config
            _raw_ptt = load_config().get("voice", {}).get("record_key", "ctrl+b")
            _ptt_key = _raw_ptt.lower().replace("ctrl+", "c-").replace("alt+", "a-")
        except Exception:
            _ptt_key = "c-b"
        _ptt_display = _ptt_key.replace("c-", "Ctrl+").upper()
        _cprint(f"\n{_ACCENT}Voice mode enabled{tts_status}{_RST}")
        _cprint(f"  {_DIM}{_ptt_display} to start/stop recording{_RST}")
        _cprint(f"  {_DIM}/voice tts  to toggle speech output{_RST}")
        _cprint(f"  {_DIM}/voice off  to disable voice mode{_RST}")

    def _disable_voice_mode(self):
        """Disable voice mode, cancel any active recording, and stop TTS."""
        recorder = None
        with self._voice_lock:
            if self._voice_recording and self._voice_recorder:
                self._voice_recorder.cancel()
                self._voice_recording = False
            recorder = self._voice_recorder
            self._voice_mode = False
            self._voice_tts = False
            self._voice_continuous = False

        # Shut down the persistent audio stream in background
        if recorder is not None:
            def _bg_shutdown(rec=recorder):
                try:
                    rec.shutdown()
                except Exception:
                    pass
            threading.Thread(target=_bg_shutdown, daemon=True).start()
            self._voice_recorder = None

        # Stop any active TTS playback
        try:
            from tools.voice_mode import stop_playback
            stop_playback()
        except Exception:
            pass
        self._voice_tts_done.set()

        _cprint(f"\n{_DIM}Voice mode disabled.{_RST}")

    def _toggle_voice_tts(self):
        """Toggle TTS output for voice mode."""
        if not self._voice_mode:
            _cprint(f"{_DIM}Enable voice mode first: /voice on{_RST}")
            return

        with self._voice_lock:
            self._voice_tts = not self._voice_tts
        status = "enabled" if self._voice_tts else "disabled"

        if self._voice_tts:
            _cprint(f"{_DIM}Warning: TTS feature is not available in this simplified version.{_RST}")

        _cprint(f"{_ACCENT}Voice TTS {status}.{_RST}")

    def _show_voice_status(self):
        """Show current voice mode status."""
        from VoidCube_cli.config import load_config
        from tools.voice_mode import check_voice_requirements

        reqs = check_voice_requirements()

        _cprint(f"\n{_BOLD}Voice Mode Status{_RST}")
        _cprint(f"  Mode:      {'ON' if self._voice_mode else 'OFF'}")
        _cprint(f"  TTS:       {'ON' if self._voice_tts else 'OFF'}")
        _cprint(f"  Recording: {'YES' if self._voice_recording else 'no'}")
        _raw_key = load_config().get("voice", {}).get("record_key", "ctrl+b")
        _display_key = _raw_key.replace("ctrl+", "Ctrl+").upper() if "ctrl+" in _raw_key.lower() else _raw_key
        _cprint(f"  Record key: {_display_key}")
        _cprint(f"\n  {_BOLD}Requirements:{_RST}")
        for line in reqs["details"].split("\n"):
            _cprint(f"    {line}")

    def _handle_preset_command(self, command: str):
        """Handle /preset [list|apply|show] [name] command."""
        parts = command.strip().split(maxsplit=2)
        subcmd = parts[1].lower().strip() if len(parts) > 1 else "list"
        from tools.preset_engine import list_presets, load_preset, apply_preset

        if subcmd == "list":
            presets = list_presets()
            if not presets:
                _cprint(f"  {_DIM}No presets available.{_RST}")
                return
            _cprint(f"\n  {_BOLD}Available Presets:{_RST}")
            for p in presets:
                _cprint(f"    {_ACCENT}{p['file']:<20}{_RST} {p['name']}")
                _cprint(f"    {'':20} {p['description']} ({p['steps_count']} steps)")
        elif subcmd == "show":
            if len(parts) < 3:
                _cprint("  Usage: /preset show <name>")
                return
            name = parts[2].strip()
            preset = load_preset(name)
            if not preset:
                _cprint(f"  Preset not found: {name}")
                return
            _cprint(f"\n  {_BOLD}Preset: {preset.get('name', name)}{_RST}")
            _cprint(f"  {preset.get('description', '')}")
            _cprint(f"\n  {_BOLD}Steps:{_RST}")
            for idx, step in enumerate(preset.get("steps", []), 1):
                _cprint(f"    {idx}. {step.get('action', '?')} → {step}")
        elif subcmd == "apply":
            if len(parts) < 3:
                _cprint("  Usage: /preset apply <name>")
                return
            name = parts[2].strip()
            _cprint(f"  Applying preset: {name}...")
            result = apply_preset(name)
            if result.get("success"):
                _cprint(f"  {_ACCENT}Preset applied successfully!{_RST}")
            else:
                _cprint(f"  Preset apply had errors:")
            for r in result.get("results", []):
                status = f"{_ACCENT}OK{_RST}" if r.get("success") else f"{_BOLD}FAIL{_RST}"
                _cprint(f"    [{status}] {r.get('step', '?')} → {r}")
        else:
            _cprint(f"  Unknown subcommand: {subcmd}")
            _cprint("  Usage: /preset [list|apply|show] [name]")

    def _handle_connect_command(self, command: str):
        """Handle /connect [list|add|use|test|remove|show|clear] [name] command."""
        parts = command.strip().split(maxsplit=2)
        subcmd = parts[1].lower().strip() if len(parts) > 1 else "list"
        from tools.connection_profiles import (
            list_profiles, save_profile, delete_profile, set_active_profile,
            clear_active_profile, get_active_profile, get_profile, test_profile,
            get_ssh_command,
        )

        if subcmd == "list":
            profiles = list_profiles()
            if not profiles:
                _cprint(f"  {_DIM}No connection profiles saved.{_RST}")
                _cprint("  Use /connect add <name> to create one.")
                return
            _cprint(f"\n  {_BOLD}Connection Profiles:{_RST}")
            for p in profiles:
                active_marker = f" {_ACCENT}*{_RST}" if p["active"] else ""
                _cprint(f"    {p['name']:<15} {p['user']}@{p['host']}:{p['port']} ({p['type']}){active_marker}")
        elif subcmd == "add":
            if len(parts) < 3:
                _cprint("  Usage: /connect add <name>")
                _cprint("  Then enter host, user, port when prompted.")
                return
            name = parts[2].strip()
            try:
                host = input("  Host: ").strip()
                user = input("  User [root]: ").strip() or "root"
                port_str = input("  Port [22]: ").strip() or "22"
                port = int(port_str)
                key_path = input("  SSH key path (empty for default): ").strip() or None
            except (EOFError, KeyboardInterrupt):
                _cprint("  Cancelled.")
                return
            result = save_profile(name, host, user, port, key_path=key_path)
            if result.get("success"):
                _cprint(f"  {_ACCENT}Profile '{name}' saved.{_RST}")
            else:
                _cprint(f"  Error: {result.get('error', '')}")
        elif subcmd == "use":
            if len(parts) < 3:
                _cprint("  Usage: /connect use <name>")
                return
            name = parts[2].strip()
            result = set_active_profile(name)
            if result.get("success"):
                profile = get_profile(name)
                _cprint(f"  {_ACCENT}Active profile: {name} ({profile.get('user','')}@{profile.get('host','')}:{profile.get('port',22)}){_RST}")
            else:
                _cprint(f"  Error: {result.get('error', '')}")
        elif subcmd == "test":
            if len(parts) < 3:
                _cprint("  Usage: /connect test <name>")
                return
            name = parts[2].strip()
            result = test_profile(name)
            if result.get("reachable"):
                _cprint(f"  {_ACCENT}Reachable: {name} ({result.get('host')}:{result.get('port')}){_RST}")
            else:
                _cprint(f"  {_BOLD}Unreachable: {name} - {result.get('error', 'connection failed')}{_RST}")
        elif subcmd == "remove":
            if len(parts) < 3:
                _cprint("  Usage: /connect remove <name>")
                return
            name = parts[2].strip()
            result = delete_profile(name)
            if result.get("success"):
                _cprint(f"  Profile '{name}' deleted.")
            else:
                _cprint(f"  Error: {result.get('error', '')}")
        elif subcmd == "show":
            if len(parts) < 3:
                active = get_active_profile()
                if active:
                    name = active.get("name", "unknown")
                    _cprint(f"  Active: {name} ({active.get('user','')}@{active.get('host','')}:{active.get('port',22)})")
                    ssh = get_ssh_command(name)
                    if ssh.get("success"):
                        _cprint(f"  SSH command: {ssh['command']}")
                else:
                    _cprint(t('  No active profile.'))
            else:
                name = parts[2].strip()
                profile = get_profile(name)
                if not profile:
                    _cprint(f"  Profile not found: {name}")
                    return
                _cprint(f"  Name: {name}")
                _cprint(f"  Host: {profile.get('host','')}")
                _cprint(f"  User: {profile.get('user','')}")
                _cprint(f"  Port: {profile.get('port',22)}")
                _cprint(f"  Type: {profile.get('type','ssh')}")
                if profile.get("key_path"):
                    _cprint(f"  Key:  {profile['key_path']}")
                ssh = get_ssh_command(name)
                if ssh.get("success"):
                    _cprint(f"  SSH:  {ssh['command']}")
        elif subcmd == "clear":
            clear_active_profile()
            _cprint("  Active profile cleared.")
        else:
            _cprint(f"  Unknown subcommand: {subcmd}")
            _cprint("  Usage: /connect [list|add|use|test|remove|show|clear] [name]")

    def _clarify_callback(self, question, choices):
        """
        Platform callback for the clarify tool. Called from the agent thread.

        Sets up the interactive selection UI (or freetext prompt for open-ended
        questions), then blocks until the user responds via the prompt_toolkit
        key bindings.  If no response arrives within the configured timeout the
        question is dismissed and the agent is told to decide on its own.
        """
        import time as _time

        timeout = CLI_CONFIG.get("clarify", {}).get("timeout", 120)
        response_queue = queue.Queue()
        is_open_ended = not choices

        self._clarify_state = {
            "question": question,
            "choices": choices if not is_open_ended else [],
            "selected": 0,
            "response_queue": response_queue,
        }
        self._clarify_deadline = _time.monotonic() + timeout
        # Open-ended questions skip straight to freetext input
        self._clarify_freetext = is_open_ended

        # Trigger prompt_toolkit repaint from this (non-main) thread
        self._invalidate()

        # Poll for the user's response.  The countdown in the hint line
        # updates on each invalidate — but frequent repaints cause visible
        # flicker in some terminals (Kitty, ghostty).  We only refresh the
        # countdown every 5 s; selection changes (↑/↓) trigger instant
        # Poll for the user's response.  The countdown in the hint line
        # updates on each invalidate — but frequent repaints cause visible
        # flicker in some terminals (Kitty, ghostty).  We only refresh the
        # countdown every 5 s; selection changes (↑/↓) trigger instant
        # repaints via the key bindings.
        _last_countdown_refresh = _time.monotonic()
        while True:
            try:
                result = response_queue.get(timeout=1)
                self._clarify_deadline = 0
                return result
            except queue.Empty:
                remaining = self._clarify_deadline - _time.monotonic()
                if remaining <= 0:
                    break
                # Only repaint every 5 s for the countdown — avoids flicker
                now = _time.monotonic()
                if now - _last_countdown_refresh >= 5.0:
                    _last_countdown_refresh = now
                    self._invalidate()

        # Timed out — tear down the UI and let the agent decide
        self._clarify_state = None
        self._clarify_freetext = False
        self._clarify_deadline = 0
        self._invalidate()
        _cprint(f"\n{_DIM}(clarify timed out after {timeout}s — agent will decide){_RST}")
        return (
            "The user did not provide a response within the time limit. "
            "Use your best judgement to make the choice and proceed."
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

    def _approval_callback(self, command: str, description: str,
                           *, allow_permanent: bool = True) -> str:
        """
        Prompt for dangerous command approval through the prompt_toolkit UI.

        Called from the agent thread. Shows a selection UI similar to clarify
        with choices: once / session / always / deny. When allow_permanent
        is False (tirith warnings present), the 'always' option is hidden.
        Long commands also get a 'view' option so the full command can be
        expanded before deciding.

        Uses _approval_lock to serialize concurrent requests (e.g. from
        parallel delegation subtasks) so each prompt gets its own turn
        and the shared _approval_state / _approval_deadline aren't clobbered.
        """
        import time as _time

        with self._approval_lock:
            timeout = 60
            response_queue: queue.Queue = queue.Queue()

            self._approval_state = {
                "command": command,
                "description": description,
                "choices": self._approval_choices(command, allow_permanent=allow_permanent),
                "selected": 0,
                "response_queue": response_queue,
            }
            self._approval_deadline = _time.monotonic() + timeout

            self._invalidate()

            _last_countdown_refresh = _time.monotonic()
            while True:
                try:
                    result = response_queue.get(timeout=1)
                    self._approval_state = None
                    self._approval_deadline = 0
                    self._invalidate()
                    return result
                except queue.Empty:
                    remaining = self._approval_deadline - _time.monotonic()
                    if remaining <= 0:
                        break
                    now = _time.monotonic()
                    if now - _last_countdown_refresh >= 5.0:
                        _last_countdown_refresh = now
                        self._invalidate()

            self._approval_state = None
            self._approval_deadline = 0
            self._invalidate()
            _cprint(f"\n{_DIM}  ⏱ Timeout — denying command{_RST}")
            return "deny"

    def _approval_choices(self, command: str, *, allow_permanent: bool = True) -> list[str]:
        """Return approval choices for a dangerous command prompt."""
        choices = ["once", "session", "always", "deny"] if allow_permanent else ["once", "session", "deny"]
        if len(command) > 70:
            choices.append("view")
        return choices

    def _handle_approval_selection(self) -> None:
        """Process the currently selected dangerous-command approval choice."""
        state = self._approval_state
        if not state:
            return

        selected = state.get("selected", 0)
        choices = state.get("choices") or []
        if not (0 <= selected < len(choices)):
            return

        chosen = choices[selected]
        if chosen == "view":
            state["show_full"] = True
            state["choices"] = [choice for choice in choices if choice != "view"]
            if state["selected"] >= len(state["choices"]):
                state["selected"] = max(0, len(state["choices"]) - 1)
            self._invalidate()
            return

        state["response_queue"].put(chosen)
        self._approval_state = None
        self._invalidate()

    def _get_approval_display_fragments(self):
        """Render the dangerous-command approval panel for the prompt_toolkit UI."""
        state = self._approval_state
        if not state:
            return []

        def _panel_box_width(title_text: str, content_lines: list[str], min_width: int = 46, max_width: int = 76) -> int:
            term_cols = shutil.get_terminal_size((100, 20)).columns
            longest = max([len(title_text)] + [len(line) for line in content_lines] + [min_width - 4])
            inner = min(max(longest + 4, min_width - 2), max_width - 2, max(24, term_cols - 6))
            return inner + 2

        def _wrap_panel_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
            wrapped = textwrap.wrap(
                text,
                width=max(8, width),
                replace_whitespace=False,
                drop_whitespace=False,
                subsequent_indent=subsequent_indent,
            )
            return wrapped or [""]

        def _append_panel_line(lines, border_style: str, content_style: str, text: str, box_width: int) -> None:
            inner_width = max(0, box_width - 2)
            lines.append((border_style, "│ "))
            lines.append((content_style, text.ljust(inner_width)))
            lines.append((border_style, " │\n"))

        def _append_blank_panel_line(lines, border_style: str, box_width: int) -> None:
            lines.append((border_style, "│" + (" " * box_width) + "│\n"))

        command = state["command"]
        description = state["description"]
        choices = state["choices"]
        selected = state.get("selected", 0)
        show_full = state.get("show_full", False)

        title = "⚠️  Dangerous Command"
        cmd_display = command if show_full or len(command) <= 70 else command[:70] + '...'
        choice_labels = {
            "once": "Allow once",
            "session": "Allow for this session",
            "always": "Add to permanent allowlist",
            "deny": "Deny",
            "view": "Show full command",
        }

        preview_lines = _wrap_panel_text(description, 60)
        preview_lines.extend(_wrap_panel_text(cmd_display, 60))
        for i, choice in enumerate(choices):
            prefix = '❯ ' if i == selected else '  '
            preview_lines.extend(_wrap_panel_text(
                f"{prefix}{choice_labels.get(choice, choice)}",
                60,
                subsequent_indent="  ",
            ))

        box_width = _panel_box_width(title, preview_lines)
        inner_text_width = max(8, box_width - 2)

        lines = []
        lines.append(('class:approval-border', '╭' + ('─' * box_width) + '╮\n'))
        _append_panel_line(lines, 'class:approval-border', 'class:approval-title', title, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        for wrapped in _wrap_panel_text(description, inner_text_width):
            _append_panel_line(lines, 'class:approval-border', 'class:approval-desc', wrapped, box_width)
        for wrapped in _wrap_panel_text(cmd_display, inner_text_width):
            _append_panel_line(lines, 'class:approval-border', 'class:approval-cmd', wrapped, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        for i, choice in enumerate(choices):
            label = choice_labels.get(choice, choice)
            style = 'class:approval-selected' if i == selected else 'class:approval-choice'
            prefix = '❯ ' if i == selected else '  '
            for wrapped in _wrap_panel_text(f"{prefix}{label}", inner_text_width, subsequent_indent="  "):
                _append_panel_line(lines, 'class:approval-border', style, wrapped, box_width)
        _append_blank_panel_line(lines, 'class:approval-border', box_width)
        lines.append(('class:approval-border', '╰' + ('─' * box_width) + '╯\n'))
        return lines

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
        autonomous_task_run_id = autonomous_task_run_id_for_message(
            getattr(self, "_current_autonomous_task", None),
            message,
        )
        if autonomous_task_run_id or not getattr(self, "_current_autonomous_task", None):
            self._last_agent_turn_result = None

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

        # Add user message to history
        self.conversation_history.append({"role": "user", "content": message})

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

            # --- Streaming TTS setup ---
            # When ElevenLabs is the TTS provider and sounddevice is available,
            # we stream audio sentence-by-sentence as the agent generates tokens
            # instead of waiting for the full response.
            use_streaming_tts = False
            _streaming_box_opened = False
            text_queue: queue.Queue | None = None
            tts_thread = None
            stream_callback = None
            stop_event = None

            if self._voice_tts:
                # TTS feature has been removed
                logger.warning("TTS streaming not available in this simplified version")

            if use_streaming_tts:
                from tools.voice_mode import stream_tts_to_speaker
                text_queue = queue.Queue()
                stop_event = threading.Event()

                def display_callback(sentence: str):
                    """Called by TTS consumer when a sentence is ready to display + speak."""
                    nonlocal _streaming_box_opened
                    if not _streaming_box_opened:
                        _streaming_box_opened = True
                        w = self.console.width
                        label = " > Voidcube "
                        fill = w - 2 - len(label)
                        _cprint(f"\n{_ACCENT}╭─{label}{'─' * max(fill - 1, 0)}╮{_RST}")
                    _cprint(sentence.rstrip())

                tts_thread = threading.Thread(
                    target=stream_tts_to_speaker,
                    args=(text_queue, stop_event, self._voice_tts_done),
                    kwargs={"display_callback": display_callback},
                    daemon=True,
                )
                tts_thread.start()

                def stream_callback(delta: str):
                    if text_queue is not None:
                        text_queue.put(delta)

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
                        conversation_history=self.conversation_history[:-1],
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
            interrupt_msg = None
            while agent_thread.is_alive():
                if autonomous_task_run_id and not autonomous_timeout_reported:
                    timed_out_task = getattr(self, "_current_autonomous_task", None)
                    timed_out_run_id = (
                        str((timed_out_task or {}).get("_autonomous_task_run_id") or "").strip()
                        if isinstance(timed_out_task, dict)
                        else ""
                    )
                    if timed_out_run_id == autonomous_task_run_id:
                        autonomous_timeout_reported = _autonomous_executor_runtime_view(
                            self,
                            push_cli_agent_scene=_push_cli_agent_scene,
                            git_head_commit=_git_head_commit,
                            git_improvement_diff=_git_improvement_diff,
                            cprint=_cprint,
                        ).report_current_task_timeout_if_needed(
                            timeout=15,
                        )
                        autonomous_timeout_writeback_succeeded = (
                            autonomous_timeout_reported
                            and getattr(self, "_current_autonomous_task", None) is None
                        )
                        if autonomous_timeout_reported:
                            try:
                                self.agent.interrupt("__AUTONOMOUS_TIMEOUT__")
                            except Exception:
                                pass
                if hasattr(self, '_interrupt_queue'):
                    try:
                        interrupt_msg = self._interrupt_queue.get(timeout=0.1)
                        if interrupt_msg:
                            # ── Sentinel values for internal control (not user messages) ──
                            if isinstance(interrupt_msg, str) and interrupt_msg.startswith("__") and interrupt_msg.endswith("__"):
                                # __AUTONOMOUS_Q_EXIT__ / __FORCE_QUIT__ — just wake the loop,
                                # don't pass to agent.interrupt()
                                continue
                            # If clarify is active, the Enter handler routes
                            # input directly; this queue shouldn't have anything.
                            # But if it does (race condition), don't interrupt.
                            if self._clarify_state or self._clarify_freetext:
                                continue
                            print("\n🔧 New message detected, interrupting...")
                            # Signal TTS to stop on interrupt
                            if stop_event is not None:
                                stop_event.set()
                            self.agent.interrupt(_interrupt_text(interrupt_msg))
                            break
                    except queue.Empty:
                        # Force prompt_toolkit to flush any pending stdout
                        # output from the agent thread.  Without this, the
                        # StdoutProxy buffer only flushes on renderer passes
                        # triggered by input events — on macOS this causes
                        # the CLI to appear frozen until the user types. (#1624)
                        self._invalidate(min_interval=0.15)
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

            # Signal end-of-text to TTS consumer and wait for it to finish
            if use_streaming_tts and text_queue is not None:
                text_queue.put(None)  # sentinel
                if tts_thread is not None:
                    tts_thread.join(timeout=120)

            # Drain any remaining agent output still in the StdoutProxy
            # buffer so tool/status lines render ABOVE our response box.
            # The flush pushes data into the renderer queue; the short
            # sleep lets the renderer actually paint it before we draw.
            import time as _time
            sys.stdout.flush()
            _time.sleep(0.15)

            # Update history with full conversation
            self.conversation_history = result.get("messages", self.conversation_history) if result else self.conversation_history

            # Get the final response
            response = result.get("final_response", "") if result else ""
            turn_result = {
                "failed": bool(result.get("failed")) if result else True,
                "partial": bool(result.get("partial")) if result else False,
                "interrupted": bool(result.get("interrupted")) if result else False,
                "error": str(result.get("error", "") or "") if result else "No result returned",
                # Preserve the agent's finding text so the autonomous writeback can flow
                # it back to Mem Tier1 (P0-2 成果回流). Without this it is discarded
                # here and the learning/improvement output never leaves the CLI.
                "response": str(response or ""),
            }
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
                self._last_agent_turn_result = turn_result
            elif not getattr(self, "_current_autonomous_task", None):
                self._last_agent_turn_result = turn_result

            if (
                getattr(self, "_autonomous_gate_active", False)
                and getattr(self, "_current_autonomous_task", None)
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
            if response and result and not result.get("failed") and not result.get("partial"):
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
            if result and (result.get("failed") or result.get("partial")) and not response:
                error_detail = result.get("error", "Unknown error")
                response = f"Error: {error_detail}"
                # Stop continuous voice mode on persistent errors (e.g. 429 rate limit)
                # to avoid an infinite error → record → error loop
                if self._voice_continuous:
                    self._voice_continuous = False
                    _cprint(f"\n{_DIM}Continuous voice mode stopped due to error.{_RST}")

            # Handle interrupt - check if we were interrupted
            pending_message = None
            if result and result.get("interrupted"):
                pending_message = (
                    interrupt_msg
                    if interrupt_msg is not None
                    else result.get("interrupt_message")
                )
                # Add indicator that we were interrupted
                if response and pending_message:
                    response = response + "\n\n---\n_[Interrupted - processing new message]_"

            response_previewed = result.get("response_previewed", False) if result else False

            # Display reasoning (thinking) box if enabled and available.
            # Intermediate tool turns reset stream framing but preserve this
            # user-turn-level flag so reasoning is not rendered twice.
            _reasoning_already_shown = self._stream_render_state.reasoning_shown_this_turn
            if self.show_reasoning and result and not _reasoning_already_shown:
                reasoning = result.get("last_reasoning")
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
                # Use skin engine for label/color with fallback
                try:
                    from VoidCube_cli.skin_engine import get_active_skin
                    _skin = get_active_skin()
                    label = _skin.get_branding("response_label", "> Voidcube")  # type: ignore[attr-defined]
                    _resp_color = _skin.get_color("response_border", "#CD7F32")  # type: ignore[attr-defined]
                    _resp_text = _skin.get_color("banner_text", "#FFF8DC")  # type: ignore[attr-defined]
                except Exception:
                    label = "> Voidcube"
                    _resp_color = "#CD7F32"
                    _resp_text = "#FFF8DC"

                is_error_response = result and (result.get("failed") or result.get("partial"))
                already_streamed = (
                    self._stream_render_state.started
                    and self._stream_render_state.response_box_open
                    and not is_error_response
                )
                if use_streaming_tts and _streaming_box_opened and not is_error_response:
                    # Text was already printed sentence-by-sentence; just close the box
                    w = shutil.get_terminal_size().columns
                    _cprint(f"\n{_ACCENT}╰{'─' * (w - 2)}╯{_RST}")
                elif already_streamed:
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

            # Speak response aloud if voice TTS is enabled
            # Skip batch TTS when streaming TTS already handled it
            if self._voice_tts and response and not use_streaming_tts:
                threading.Thread(
                    target=self._voice_speak_response,
                    args=(response,),
                    daemon=True,
                ).start()


            # Re-queue the interrupt message (and any that arrived while we were
            # processing the first) as the next prompt for process_loop.
            # Only reached when busy_input_mode == "interrupt" (the default).
            # In "queue" mode Enter routes directly to _pending_input so this
            # block is never hit.
            if pending_message and hasattr(self, '_pending_input'):
                payloads = _requeue_interrupted_payloads(
                    self._pending_input,
                    self._interrupt_queue,
                    pending_message,
                )
                preview_text = _interrupt_text(payloads[0])
                preview = preview_text[:50] + ("..." if len(preview_text) > 50 else "")
                if len(payloads) > 1:
                    print(f"\n🔧 Sending {len(payloads)} messages after interrupt: '{preview}'")
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
                self._last_agent_turn_result = error_result
            elif not getattr(self, "_current_autonomous_task", None):
                self._last_agent_turn_result = error_result
            if self._should_emit_scrollback_output():
                print(f"Error: {e}")
            return None
        finally:
            self._active_chat_agent_role = previous_active_role
            # Ensure streaming TTS resources are cleaned up even on error.
            # Normal path sends the sentinel at line ~3568; this is a safety
            # net for exception paths that skip it.  Duplicate sentinels are
            # harmless — stream_tts_to_speaker exits on the first None.
            if text_queue is not None:
                try:
                    text_queue.put_nowait(None)
                except Exception:
                    pass
            if stop_event is not None:
                stop_event.set()
            if tts_thread is not None and tts_thread.is_alive():
                tts_thread.join(timeout=5)
    
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
            try:
                from VoidCube_cli.skin_engine import get_active_goodbye
                goodbye = get_active_goodbye("bye.")
            except Exception:
                goodbye = "bye."
            print(goodbye)

    def _get_tui_prompt_symbols(self) -> tuple[str, str]:
        """Return ``(normal_prompt, state_suffix)`` for the active skin.

        ``normal_prompt`` is the full ``branding.prompt_symbol``.
        ``state_suffix`` is what special states (sudo/secret/approval/agent)
        should render after their leading icon.

        When a profile is active (not "default"), the profile name is
        prepended to the prompt symbol: ``coder ❯`` instead of ``❯``.
        """
        try:
            from VoidCube_cli.skin_engine import get_active_prompt_symbol
            symbol = get_active_prompt_symbol("❯ ")
        except Exception:
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

        # Icon-only custom prompts should still remain visible in special states.
        return symbol, symbol

    def _audio_level_bar(self) -> str:
        """Return a visual audio level indicator based on current RMS."""
        _LEVEL_BARS = " ▁▂▃▄▅▆▇"
        rec = getattr(self, "_voice_recorder", None)
        if rec is None:
            return ""
        rms = rec.current_rms
        # Normalize RMS (0-32767) to 0-7 index, with log-ish scaling
        # Typical speech RMS is 500-5000, we cap display at ~8000
        level = min(rms, 8000) * 7 // 8000
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

    def _build_tui_style_dict(self) -> dict[str, str]:
        """Layer the active skin's prompt_toolkit colors over the base TUI style."""
        style_dict = dict(getattr(self, "_tui_style_base", {}) or {})
        try:
            from VoidCube_cli.skin_engine import get_prompt_toolkit_style_overrides
            style_dict.update(get_prompt_toolkit_style_overrides())
        except Exception:
            pass
        return style_dict

    def _apply_tui_skin_style(self) -> bool:
        """Refresh prompt_toolkit styling for a running interactive TUI."""
        if not getattr(self, "_app", None) or not getattr(self, "_tui_style_base", None):
            return False
        self._app.style = PTStyle.from_dict(self._build_tui_style_dict())
        self._invalidate(min_interval=0.0)
        return True

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

    def _build_tui_layout_children(
        self,
        *,
        sudo_widget,
        secret_widget,
        approval_widget,
        clarify_widget,
        model_picker_widget=None,
        spinner_widget=None,
        spacer,
        status_bar,
        auto_execution_panel=None,
        input_rule_top,
        image_bar,
        input_area,
        input_rule_bot,
        voice_status_bar,
        autonomous_gate_bar=None,
        completions_menu,
    ) -> list:
        """Assemble the ordered list of children for the root ``HSplit``.

        Wrapper CLIs typically override ``_get_extra_tui_widgets`` instead of
        this method.  Override this only when you need full control over widget
        ordering.
        """
        return [
            item for item in [
                Window(height=0),
                sudo_widget,
                secret_widget,
                approval_widget,
                clarify_widget,
                model_picker_widget,
                spinner_widget,
                spacer,
                *self._get_extra_tui_widgets(),
                status_bar,
                auto_execution_panel,
                input_rule_top,
                image_bar,
                input_area,
                input_rule_bot,
                voice_status_bar,
                autonomous_gate_bar,
                completions_menu,
            ] if item is not None
        ]

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
            from VoidCube_cli.config import load_config
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
            sessions = db.list_sessions_rich(source="cli", exclude_sources=["tool"], limit=5)
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

        # Voice mode state (protected by _voice_lock for cross-thread access)
        self._voice_lock = threading.Lock()
        self._voice_mode = False        # Whether voice mode is enabled
        self._voice_tts = False         # Whether TTS output is enabled
        self._voice_recorder = None     # AudioRecorder instance (lazy init)
        self._voice_recording = False   # Whether currently recording
        self._voice_processing = False  # Whether STT is in progress
        self._voice_continuous = False  # Whether to auto-restart after agent responds
        self._voice_tts_done = threading.Event()  # Signals TTS playback finished
        self._voice_tts_done.set()  # Initially "done" (no TTS pending)

        # Register callbacks so terminal_tool prompts route through our UI
        _get_set_sudo_password_callback(self._sudo_password_callback)
        _get_set_approval_callback(self._approval_callback)
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
                    from VoidCube_cli.api_config import run_api_config_wizard
                    run_api_config_wizard(self)
                
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
                            _exit_autonomous_gate_fast_view(
                                self,
                                cprint=_cprint,
                                interrupt_current_task_callback=self._interrupt_autonomous_component_task,
                                push_cli_agent_scene_callback=_push_cli_agent_scene,
                            )
                            event.app.invalidate()
                            return

                if self._agent_running and not (text and _looks_like_slash_command(text)):
                    if self.busy_input_mode == "queue":
                        # Queue for the next turn instead of interrupting
                        self._pending_input.put(payload)
                        preview = text if text else f"[{len(images)} image{'s' if len(images) != 1 else ''} attached]"
                        _cprint(f"  Queued for the next turn: {preview[:80]}{'...' if len(preview) > 80 else ''}")
                    else:
                        self._interrupt_queue.put(payload)
                else:
                    self._pending_input.put(payload)
                event.app.current_buffer.reset(append_to_history=True)
        
        @kb.add('escape', 'enter')
        def handle_alt_enter(event):
            """Alt+Enter inserts a newline for multi-line input."""
            event.current_buffer.insert_text('\n')

        @kb.add('c-j')
        def handle_ctrl_enter(event):
            """Ctrl+Enter (c-j) inserts a newline. Most terminals send c-j for Ctrl+Enter."""
            event.current_buffer.insert_text('\n')

        @kb.add('tab', eager=True)
        def handle_tab(event):
            """Tab: accept completion, auto-suggestion, or start completions.

            Priority:
            1. Completion menu open → accept selected completion
            2. Ghost text suggestion available → accept auto-suggestion
            3. Otherwise → start completion menu

            After accepting a provider prefix, the completion menu closes and
            complete_while_typing doesn't fire (no keystroke).
            This binding re-triggers completions so stage-2 models appear
            immediately.
            """
            buf = event.current_buffer
            if buf.complete_state:
                # Completion menu is open — accept the selection
                completion = buf.complete_state.current_completion
                if completion is None:
                    # Menu open but nothing selected — select first then grab it
                    buf.go_to_completion(0)
                    completion = buf.complete_state and buf.complete_state.current_completion
                if completion is None:
                    return
                # Accept the selected completion
                buf.apply_completion(completion)
            elif buf.suggestion and buf.suggestion.text:
                # No completion menu, but there's a ghost text auto-suggestion — accept it
                buf.insert_text(buf.suggestion.text)
            else:
                # No menu and no suggestion — start completions from scratch
                buf.start_completion()

        # --- Clarify tool: arrow-key navigation for multiple-choice questions ---

        @kb.add('up', filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))
        def clarify_up(event):
            """Move selection up in clarify choices."""
            if self._clarify_state:
                self._clarify_state["selected"] = max(0, self._clarify_state["selected"] - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._clarify_state) and not self._clarify_freetext))
        def clarify_down(event):
            """Move selection down in clarify choices."""
            if self._clarify_state:
                choices = self._clarify_state.get("choices") or []
                max_idx = len(choices)  # last index is the "Other" option
                self._clarify_state["selected"] = min(max_idx, self._clarify_state["selected"] + 1)
                event.app.invalidate()

        # --- Dangerous command approval: arrow-key navigation ---

        @kb.add('up', filter=Condition(lambda: bool(self._approval_state)))
        def approval_up(event):
            if self._approval_state:
                self._approval_state["selected"] = max(0, self._approval_state["selected"] - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._approval_state)))
        def approval_down(event):
            if self._approval_state:
                max_idx = len(self._approval_state["choices"]) - 1
                self._approval_state["selected"] = min(max_idx, self._approval_state["selected"] + 1)
                event.app.invalidate()

        # --- /model picker: arrow-key navigation ---
        @kb.add('up', filter=Condition(lambda: bool(self._model_picker_state)))
        def model_picker_up(event):
            if self._model_picker_state:
                self._model_picker_state["selected"] = max(0, self._model_picker_state.get("selected", 0) - 1)
                event.app.invalidate()

        @kb.add('down', filter=Condition(lambda: bool(self._model_picker_state)))
        def model_picker_down(event):
            state = self._model_picker_state
            if not state:
                return
            if state.get("stage") == "provider":
                max_idx = len(state.get("providers") or [])
            else:
                max_idx = len(state.get("model_list") or []) + 1
            state["selected"] = min(max_idx, state.get("selected", 0) + 1)
            event.app.invalidate()

        # --- History navigation: up/down browse history in normal input mode ---
        # The TextArea is multiline, so by default up/down only move the cursor.
        # Buffer.auto_up/auto_down handle both: cursor movement when multi-line,
        # history browsing when on the first/last line (or single-line input).
        _normal_input = Condition(
            lambda: not self._clarify_state and not self._approval_state and not self._sudo_state and not self._secret_state and not self._model_picker_state
        )

        @kb.add('up', filter=_normal_input)
        def history_up(event):
            """Up arrow: browse history when on first line, else move cursor up."""
            event.app.current_buffer.auto_up(count=event.arg)

        @kb.add('down', filter=_normal_input)
        def history_down(event):
            """Down arrow: browse history when on last line, else move cursor down."""
            event.app.current_buffer.auto_down(count=event.arg)

        @kb.add('c-c')
        def handle_ctrl_c(event):
            """Handle Ctrl+C - cancel interactive prompts, interrupt agent.

            Does NOT force-exit — use /quit to exit.
            """
            import time as _time
            now = _time.time()

            # Cancel active voice recording.
            _should_cancel_voice = False
            _recorder_ref = None
            with cli_ref._voice_lock:
                if cli_ref._voice_recording and cli_ref._voice_recorder:
                    _recorder_ref = cli_ref._voice_recorder
                    cli_ref._voice_recording = False
                    cli_ref._voice_continuous = False
                    _should_cancel_voice = True
            if _should_cancel_voice:
                _cprint(f"\n{_DIM}Recording cancelled.{_RST}")
                threading.Thread(
                    target=_recorder_ref.cancel, daemon=True
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
                self._approval_state["response_queue"].put("deny")
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
                    "The user cancelled. Use your best judgement to proceed."
                )
                self._clarify_state = None
                self._clarify_freetext = False
                event.app.current_buffer.reset()
                event.app.invalidate()
                return

            # Interrupt running agent
            if self._agent_running and self.agent:
                self._last_ctrl_c_time = now
                self.agent.interrupt()
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
                _force_quit_autonomous_gate_view(
                    self,
                    cprint=_cprint,
                    interrupt_current_task_callback=self._interrupt_autonomous_component_task,
                    push_cli_agent_scene_callback=_push_cli_agent_scene,
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
            from VoidCube_cli.skin_engine import get_active_skin
            agent_name = get_active_skin().get_branding("agent_name", "Voidcube Agent")  # type: ignore[attr-defined]
            msg = f"\n{agent_name} has been suspended. Run `fg` to bring {agent_name} back."
            def _suspend():
                os.write(1, msg.encode())
                os.kill(0, _sig.SIGTSTP)
            run_in_terminal(_suspend)

        # Voice push-to-talk key: configurable via config.yaml (voice.record_key)
        # Default: Ctrl+B (avoids conflict with Ctrl+R readline reverse-search)
        # Config uses "ctrl+b" format; prompt_toolkit expects "c-b" format.
        try:
            from VoidCube_cli.config import load_config
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

                # Interrupt TTS if playing, so user can start talking.
                # stop_playback() is fast (just terminates a subprocess).
                if not cli_ref._voice_tts_done.is_set():
                    try:
                        from tools.voice_mode import stop_playback
                        stop_playback()
                        cli_ref._voice_tts_done.set()
                    except Exception:
                        pass

                with cli_ref._voice_lock:
                    cli_ref._voice_continuous = True

                # Dispatch to a daemon thread so play_beep(sd.wait),
                # AudioRecorder.start(lock acquire), and config I/O
                # never block the prompt_toolkit event loop.
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

        # Create the input area with multiline (shift+enter), autocomplete, and paste handling
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory


        _completer = SlashCommandCompleter(
            # 不提供技能命令提供者，这样自动补全只显示真正的 CLI 命令
            skill_commands_provider=None,
            command_filter=cli_ref._command_available,
        )
        input_area = TextArea(
            height=Dimension(min=1, max=8, preferred=1),
            prompt=get_prompt,
            style='class:input-area',
            multiline=True,
            wrap_lines=True,
            read_only=Condition(lambda: bool(cli_ref._command_running)),
            history=FileHistory(str(self._history_file)),
            completer=_completer,
            complete_while_typing=True,
            auto_suggest=SlashCommandAutoSuggest(
                history_suggest=AutoSuggestFromHistory(),
                completer=_completer,
            ),
        )

        # Dynamic height: accounts for both explicit newlines AND visual
        # wrapping of long lines so the input area always fits its content.
        def _input_height():
            try:
                from prompt_toolkit.application import get_app
                from prompt_toolkit.utils import get_cwidth

                doc = input_area.buffer.document
                prompt_width = max(2, get_cwidth(self._get_tui_prompt_text()))
                try:
                    available_width = get_app().output.get_size().columns - prompt_width
                except Exception:
                    available_width = shutil.get_terminal_size((80, 24)).columns - prompt_width
                if available_width < 10:
                    available_width = 40
                visual_lines = 0
                for line in doc.lines:
                    # Each logical line takes at least 1 visual row; long lines wrap.
                    # Use prompt_toolkit's cell width so CJK wide characters count as 2.
                    line_width = get_cwidth(line)
                    if line_width <= 0:
                        visual_lines += 1
                    else:
                        visual_lines += max(1, -(-line_width // available_width))  # ceil division
                return min(max(visual_lines, 1), 8)
            except Exception:
                return 1

        input_area.window.height = _input_height

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

        # --- Input processors for password masking and inline placeholder ---

        # Mask input with '*' when the sudo password prompt is active
        input_area.control.input_processors.append(
            ConditionalProcessor(
                PasswordProcessor(),
                filter=Condition(
                    lambda: bool(cli_ref._sudo_state) or bool(cli_ref._secret_state)
                ),
            )
        )

        class _PlaceholderProcessor(Processor):
            """Render grayed-out placeholder text inside the input when empty."""
            def __init__(self, get_text):
                self._get_text = get_text

            def apply_transformation(self, ti):
                if not ti.document.text and ti.lineno == 0:
                    text = self._get_text()
                    if text:
                        # Append after existing fragments (preserves the ❯ prompt)
                        return Transformation(fragments=ti.fragments + [('class:placeholder', text)])
                return Transformation(fragments=ti.fragments)

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

        input_area.control.input_processors.append(_PlaceholderProcessor(_get_placeholder))

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

        spinner_widget = Window(
            content=FormattedTextControl(get_spinner_text),
            height=get_spinner_height,
        )

        spacer = Window(
            content=FormattedTextControl(get_hint_text),
            height=get_hint_height,
        )

        # --- Clarify tool: dynamic display widget for questions + choices ---

        def _panel_box_width(title: str, content_lines: list[str], min_width: int = 46, max_width: int = 76) -> int:
            """Choose a stable panel width wide enough for the title and content."""
            term_cols = shutil.get_terminal_size((100, 20)).columns
            longest = max([len(title)] + [len(line) for line in content_lines] + [min_width - 4])
            inner = min(max(longest + 4, min_width - 2), max_width - 2, max(24, term_cols - 6))
            return inner + 2  # account for the single leading/trailing spaces inside borders

        def _wrap_panel_text(text: str, width: int, subsequent_indent: str = "") -> list[str]:
            wrapped = textwrap.wrap(
                text,
                width=max(8, width),
                break_long_words=False,
                break_on_hyphens=False,
                subsequent_indent=subsequent_indent,
            )
            return wrapped or [""]

        def _append_panel_line(lines, border_style: str, content_style: str, text: str, box_width: int) -> None:
            inner_width = max(0, box_width - 2)
            lines.append((border_style, "│ "))
            lines.append((content_style, text.ljust(inner_width)))
            lines.append((border_style, " │\n"))

        def _append_blank_panel_line(lines, border_style: str, box_width: int) -> None:
            lines.append((border_style, "│" + (" " * box_width) + "│\n"))

        def _get_clarify_display():
            """Build styled text for the clarify question/choices panel."""
            state = cli_ref._clarify_state
            if not state:
                return []

            question = state["question"]
            choices = state.get("choices") or []
            selected = state.get("selected", 0)
            preview_lines = _wrap_panel_text(question, 60)
            for i, choice in enumerate(choices):
                prefix = "❯ " if i == selected and not cli_ref._clarify_freetext else "  "
                preview_lines.extend(_wrap_panel_text(f"{prefix}{choice}", 60, subsequent_indent="  "))
            other_label = (
                "❯ Other (type below)" if cli_ref._clarify_freetext
                else "❯ Other (type your answer)" if selected == len(choices)
                else "  Other (type your answer)"
            )
            preview_lines.extend(_wrap_panel_text(other_label, 60, subsequent_indent="  "))
            box_width = _panel_box_width("Voidcube needs your input", preview_lines)
            inner_text_width = max(8, box_width - 2)

            lines = []
            # Box top border
            lines.append(('class:clarify-border', '╭─ '))
            lines.append(('class:clarify-title', 'Voidcube needs your input'))
            lines.append(('class:clarify-border', ' ' + ('─' * max(0, box_width - len("Voidcube needs your input") - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            # Question text
            for wrapped in _wrap_panel_text(question, inner_text_width):
                _append_panel_line(lines, 'class:clarify-border', 'class:clarify-question', wrapped, box_width)
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            if cli_ref._clarify_freetext and not choices:
                guidance = "Type your answer in the prompt below, then press Enter."
                for wrapped in _wrap_panel_text(guidance, inner_text_width):
                    _append_panel_line(lines, 'class:clarify-border', 'class:clarify-choice', wrapped, box_width)
                _append_blank_panel_line(lines, 'class:clarify-border', box_width)

            if choices:
                # Multiple-choice mode: show selectable options
                for i, choice in enumerate(choices):
                    style = 'class:clarify-selected' if i == selected and not cli_ref._clarify_freetext else 'class:clarify-choice'
                    prefix = '❯ ' if i == selected and not cli_ref._clarify_freetext else '  '
                    wrapped_lines = _wrap_panel_text(f"{prefix}{choice}", inner_text_width, subsequent_indent="  ")
                    for wrapped in wrapped_lines:
                        _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)

                # "Other" option (5th line, only shown when choices exist)
                other_idx = len(choices)
                if selected == other_idx and not cli_ref._clarify_freetext:
                    other_style = 'class:clarify-selected'
                    other_label = '❯ Other (type your answer)'
                elif cli_ref._clarify_freetext:
                    other_style = 'class:clarify-active-other'
                    other_label = '❯ Other (type below)'
                else:
                    other_style = 'class:clarify-choice'
                    other_label = '  Other (type your answer)'
                for wrapped in _wrap_panel_text(other_label, inner_text_width, subsequent_indent="  "):
                    _append_panel_line(lines, 'class:clarify-border', other_style, wrapped, box_width)

            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            lines.append(('class:clarify-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        clarify_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_clarify_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._clarify_state is not None),
        )

        # --- Sudo password: display widget ---

        def _get_sudo_display():
            state = cli_ref._sudo_state
            if not state:
                return []
            title = '🔐 Sudo Password Required'
            body = 'Enter password below (hidden), or press Enter to skip'
            box_width = _panel_box_width(title, [body])
            lines = []
            lines.append(('class:sudo-border', '╭─ '))
            lines.append(('class:sudo-title', title))
            lines.append(('class:sudo-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', body, box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            lines.append(('class:sudo-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        sudo_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_sudo_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._sudo_state is not None),
        )

        def _get_secret_display():
            state = cli_ref._secret_state
            if not state:
                return []

            title = '🔑 Skill Setup Required'
            prompt = state.get("prompt") or f"Enter value for {state.get('var_name', 'secret')}"
            metadata = state.get("metadata") or {}
            help_text = metadata.get("help")
            body = 'Enter secret below (hidden), or press Enter to skip'
            content_lines = [prompt, body]
            if help_text:
                content_lines.insert(1, str(help_text))
            box_width = _panel_box_width(title, content_lines)
            lines = []
            lines.append(('class:sudo-border', '╭─ '))
            lines.append(('class:sudo-title', title))
            lines.append(('class:sudo-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', prompt, box_width)
            if help_text:
                _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', str(help_text), box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            _append_panel_line(lines, 'class:sudo-border', 'class:sudo-text', body, box_width)
            _append_blank_panel_line(lines, 'class:sudo-border', box_width)
            lines.append(('class:sudo-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        secret_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_secret_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._secret_state is not None),
        )

        # --- Dangerous command approval: display widget ---

        def _get_approval_display():
            return cli_ref._get_approval_display_fragments()

        approval_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_approval_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._approval_state is not None),
        )

        # --- /model picker: display widget ---
        def _get_model_picker_display():
            state = cli_ref._model_picker_state
            if not state:
                return []
            stage = state.get("stage", "provider")
            
            # Maximum visible items (excluding Cancel/Back)
            max_visible = 10
            
            if stage == "provider":
                title = "> Model Picker — Select Provider"
                choices = []
                for p in state.get("providers") or []:
                    count = p.get("total_models", len(p.get("models", [])))
                    label = f"{p['name']} ({count} model{'s' if count != 1 else ''})"
                    if p.get("is_current"):
                        label += "  ← current"
                    choices.append(label)
                choices.append("Cancel")
                hint = f"Current: {state.get('current_model', 'unknown')} on {state.get('current_provider', 'unknown')}"
            else:
                provider_data = state.get("provider_data") or {}
                model_list = state.get("model_list") or []
                title = f"> Model Picker — {provider_data.get('name', provider_data.get('slug', 'Provider'))}"
                choices = list(model_list) + ["← Back", "Cancel"]
                total_models = len(model_list)
                if model_list:
                    hint = f"Select a model ({total_models} available)"
                else:
                    hint = "No models listed for this provider. Use Back or Cancel."

            box_width = _panel_box_width(title, [hint] + choices[:max_visible], min_width=46, max_width=84)
            inner_text_width = max(8, box_width - 6)
            lines = []
            lines.append(('class:clarify-border', '╭─ '))
            lines.append(('class:clarify-title', title))
            lines.append(('class:clarify-border', ' ' + ('─' * max(0, box_width - len(title) - 3)) + '╮\n'))
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            _append_panel_line(lines, 'class:clarify-border', 'class:clarify-hint', hint, box_width)
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            
            selected = state.get("selected", 0)
            total_choices = len(choices)
            
            # Calculate visible window
            if total_choices > max_visible:
                # Determine window start based on selected position
                if selected < max_visible // 2:
                    window_start = 0
                elif selected > total_choices - max_visible // 2 - 1:
                    window_start = max(0, total_choices - max_visible)
                else:
                    window_start = selected - max_visible // 2
                
                window_end = window_start + max_visible
                window_end = min(window_end, total_choices)
                
                # Add scroll indicator at top if not at beginning
                if window_start > 0:
                    lines.append(('class:clarify-border', '│'))
                    lines.append(('class:clarify-choice', '  ...'))
                    lines.append(('class:clarify-border', '│\n'))
                
                # Show visible items
                for idx in range(window_start, window_end):
                    choice = choices[idx]
                    style = 'class:clarify-selected' if idx == selected else 'class:clarify-choice'
                    prefix = '❯ ' if idx == selected else '  '
                    for wrapped in _wrap_panel_text(prefix + choice, inner_text_width, subsequent_indent='  '):
                        _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)
                
                # Add scroll indicator at bottom if not at end
                if window_end < total_choices:
                    lines.append(('class:clarify-border', '│'))
                    lines.append(('class:clarify-choice', '  ...'))
                    lines.append(('class:clarify-border', '│\n'))
                
                # Show position indicator
                position_text = f" {selected + 1}/{total_choices} "
                lines.append(('class:clarify-border', '│'))
                lines.append(('class:clarify-hint', position_text.center(inner_text_width + 4)))
                lines.append(('class:clarify-border', '│\n'))
            else:
                # All items fit, show normally
                for idx, choice in enumerate(choices):
                    style = 'class:clarify-selected' if idx == selected else 'class:clarify-choice'
                    prefix = '❯ ' if idx == selected else '  '
                    for wrapped in _wrap_panel_text(prefix + choice, inner_text_width, subsequent_indent='  '):
                        _append_panel_line(lines, 'class:clarify-border', style, wrapped, box_width)
            
            _append_blank_panel_line(lines, 'class:clarify-border', box_width)
            lines.append(('class:clarify-border', '╰' + ('─' * box_width) + '╯\n'))
            return lines

        model_picker_widget = ConditionalContainer(
            Window(
                FormattedTextControl(_get_model_picker_display),
                wrap_lines=True,
            ),
            filter=Condition(lambda: cli_ref._model_picker_state is not None),
        )

        # Horizontal rules above and below the input.
        # On narrow/mobile terminals we keep the top separator for structure but
        # hide the bottom one to recover a full row for conversation content.
        input_rule_top = Window(
            char='─',
            height=lambda: cli_ref._tui_input_rule_height("top"),
            style='class:input-rule',
        )
        input_rule_bot = Window(
            char='─',
            height=lambda: cli_ref._tui_input_rule_height("bottom"),
            style='class:input-rule',
        )

        # Image attachment indicator — shows badges like [📎 Image #1] above input
        cli_ref = self

        def _get_image_bar():
            if not cli_ref._attached_images:
                return []
            badges = _format_image_attachment_badges(
                cli_ref._attached_images,
                cli_ref._image_counter,
            )
            return [("class:image-badge", f" {badges} ")]

        image_bar = Window(
            content=FormattedTextControl(_get_image_bar),
            height=Condition(lambda: bool(cli_ref._attached_images)),
        )

        # Persistent voice mode status bar (visible only when voice mode is on)
        def _get_voice_status():
            return cli_ref._get_voice_status_fragments()

        voice_status_bar = ConditionalContainer(
            Window(
                FormattedTextControl(_get_voice_status),
                height=1,
            ),
            filter=Condition(lambda: cli_ref._voice_mode),
        )

        def _get_autonomous_gate_text():
            return [
                ("class:auto-mode", " 🤖 自主链路已启用 | API-A 执行组件按需显示 | /auto-q 停用"),
            ]

        autonomous_gate_bar = ConditionalContainer(
            Window(
                FormattedTextControl(_get_autonomous_gate_text),
                height=1,
            ),
            filter=Condition(lambda: cli_ref._autonomous_gate_active),
        )

        auto_execution_panel = ConditionalContainer(
            Window(
                content=FormattedTextControl(
                    lambda: _get_autonomous_execution_panel_fragments_view(cli_ref)
                ),
                dont_extend_height=True,
            ),
            filter=Condition(lambda: _has_visible_autonomous_work_view(cli_ref)),
        )

        status_bar = ConditionalContainer(
            Window(
                content=FormattedTextControl(lambda: cli_ref._get_status_bar_fragments()),
                height=1,
                # Prevent fragments that overflow the terminal width from
                # wrapping onto a second line, which causes the status bar to
                # appear duplicated (one full + one partial row) during long
                # sessions, especially on SSH where shutil.get_terminal_size
                # may return stale values.  _get_status_bar_fragments now reads
                # width from prompt_toolkit's own output object, so fragments
                # will always fit; wrap_lines=False is the belt-and-suspenders
                # guard against any future width mismatch.
                wrap_lines=False,
            ),
            filter=Condition(lambda: cli_ref._status_bar_visible),
        )

        # Allow wrapper CLIs to register extra keybindings.
        self._register_extra_tui_keybindings(kb, input_area=input_area)

        # Layout: interactive prompt widgets + ruled input at bottom.
        # The sudo, approval, and clarify widgets appear above the input when
        # the corresponding interactive prompt is active.
        completions_menu = CompletionsMenu(max_height=12, scroll_offset=1)

        layout = Layout(
            HSplit(
                self._build_tui_layout_children(
                    sudo_widget=sudo_widget,
                    secret_widget=secret_widget,
                    approval_widget=approval_widget,
                    clarify_widget=clarify_widget,
                    model_picker_widget=model_picker_widget,
                    spinner_widget=spinner_widget,
                    spacer=spacer,
                    status_bar=status_bar,
                    auto_execution_panel=auto_execution_panel,
                    input_rule_top=input_rule_top,
                    image_bar=image_bar,
                    input_area=input_area,
                    input_rule_bot=input_rule_bot,
                    voice_status_bar=voice_status_bar,
                    autonomous_gate_bar=autonomous_gate_bar,
                    completions_menu=completions_menu,
                )
            )
        )
        
        # Style for the application
        self._tui_style_base = {
            'input-area': 'bg:#1a1a2e #E8E8E8',
            'placeholder': 'bg:#1a1a2e #6B7280 italic',
            'prompt': 'bg:#1a1a2e #E8E8E8 bold',
            'prompt-working': 'bg:#1a1a2e #58A6FF italic',
            'hint': 'bg:#1a1a2e #6B7280 italic',
            'status-bar': 'bg:#1a1a2e #9CA3AF',
            'status-bar-strong': 'bg:#1a1a2e #1E40AF bold',
            'status-bar-dim': 'bg:#1a1a2e #6B7280',
            'status-bar-good': 'bg:#1a1a2e #34D399 bold',
            'status-bar-warn': 'bg:#1a1a2e #FBBF24 bold',
            'status-bar-bad': 'bg:#1a1a2e #FB923C bold',
            'status-bar-critical': 'bg:#1a1a2e #F87171 bold',
            # Blue horizontal rules around the input area (matching banner border)
            'input-rule': '#30363D',
            # Clipboard image attachment badges
            'image-badge': '#58A6FF bold',
            'completion-menu': 'bg:#1a1a2e #E8E8E8',
            'completion-menu.completion': 'bg:#1a1a2e #E8E8E8',
            'completion-menu.completion.current': 'bg:#1E40AF #E8E8E8',
            'completion-menu.meta.completion': 'bg:#1a1a2e #6B7280',
            'completion-menu.meta.completion.current': 'bg:#1E40AF #58A6FF',
            'auto-panel-border': '#30363D',
            'auto-panel-title': '#58A6FF bold',
            'auto-panel-text': '#E8E8E8',
            'auto-panel-dim': '#9CA3AF',
            'auto-panel-info': '#58A6FF',
            'auto-panel-good': '#34D399 bold',
            'auto-panel-warn': '#FBBF24 bold',
            'auto-panel-bad': '#F87171 bold',
            # Clarify question panel
            'clarify-border': '#30363D',
            'clarify-title': '#58A6FF bold',
            'clarify-question': '#E8E8E8 bold',
            'clarify-choice': '#9CA3AF',
            'clarify-selected': '#58A6FF bold',
            'clarify-active-other': '#58A6FF italic',
            'clarify-countdown': '#58A6FF',
            # Sudo password panel
            'sudo-prompt': '#F87171 bold',
            'sudo-border': '#30363D',
            'sudo-title': '#F87171 bold',
            'sudo-text': '#E8E8E8',
            # Dangerous command approval panel
            'approval-border': '#30363D',
            'approval-title': '#FB923C bold',
            'approval-desc': '#E8E8E8 bold',
            'approval-cmd': '#9CA3AF italic',
            'approval-choice': '#9CA3AF',
            'approval-selected': '#58A6FF bold',
            # Voice mode
            'voice-prompt': '#58A6FF',
            'voice-recording': '#F87171 bold',
            'voice-processing': '#FB923C italic',
            'voice-status': 'bg:#1a1a2e #58A6FF',
            'voice-status-recording': 'bg:#1a1a2e #F87171 bold',
        }
        style = PTStyle.from_dict(self._build_tui_style_dict())
        
        # Create the application
        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            **({'cursor': _STEADY_CURSOR} if _STEADY_CURSOR is not None else {}),
        )
        self._app = app  # Store reference for clarify_callback

        # ── Fix ghost status-bar lines on terminal resize ──────────────
        # When the terminal shrinks (e.g. un-maximize), the emulator reflows
        # the previously-rendered full-width rows (status bar, input rules)
        # into multiple narrower rows.  prompt_toolkit's _on_resize handler
        # only cursor_up()s by the stored layout height, missing the extra
        # rows created by reflow — leaving ghost duplicates visible.
        #
        # Fix: before the standard erase, inflate _cursor_pos.y so the
        # cursor moves up far enough to cover the reflowed ghost content.
        _original_on_resize = app._on_resize

        def _resize_clear_ghosts():
            from prompt_toolkit.data_structures import Point as _Pt
            renderer = app.renderer
            try:
                old_size = renderer._last_size
                new_size = renderer.output.get_size()
                if (
                    old_size
                    and new_size.columns < old_size.columns
                    and new_size.columns > 0
                ):
                    reflow_factor = (
                        (old_size.columns + new_size.columns - 1)
                        // new_size.columns
                    )
                    last_h = (
                        renderer._last_screen.height
                        if renderer._last_screen
                        else 0
                    )
                    extra = last_h * (reflow_factor - 1)
                    if extra > 0:
                        renderer._cursor_pos = _Pt(
                            x=renderer._cursor_pos.x,
                            y=renderer._cursor_pos.y + extra,
                        )
            except Exception:
                pass  # never break resize handling
            _original_on_resize()

        app._on_resize = _resize_clear_ghosts

        def spinner_loop():
            import time as _time

            last_idle_refresh = 0.0
            last_presence_refresh = 0.0
            while not self._should_exit:
                if not self._app:
                    _time.sleep(0.1)
                    continue
                now = _time.monotonic()
                if (
                    now - last_presence_refresh >= 5.0
                    and (
                        self._agent_running
                        or getattr(self, "_command_running", False)
                        or self._stream_render_state.started
                        or self._get_subagent_observability_snapshot().get("active")
                    )
                ):
                    _refresh_gateway_cli_presence_view(
                        self,
                        force=True,
                        is_gateway_running=_is_gateway_running,
                        register_with_gateway=_register_with_gateway,
                        push_cli_agent_scene=_push_cli_agent_scene,
                        monotonic_time=_time.monotonic,
                    )
                    last_presence_refresh = now
                if self._command_running:
                    self._invalidate(min_interval=0.1)
                    _time.sleep(0.1)
                else:
                    if now - last_idle_refresh >= 1.0:
                        last_idle_refresh = now
                        self._invalidate(min_interval=1.0)
                    _time.sleep(0.2)

        spinner_thread = threading.Thread(target=spinner_loop, daemon=True)
        spinner_thread.start()
        
        # Background thread to process inputs and run agent
        def process_loop():
            while not self._should_exit:
                try:
                    # Check for pending input with timeout
                    try:
                        user_input = self._pending_input.get(timeout=0.1)
                    except queue.Empty:
                        # Periodic background tasks — never block the UI thread
                        if not self._agent_running:
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
                            if getattr(self, "_autonomous_gate_active", False):
                                self._start_autonomous_execution_component()
                                try:
                                    if getattr(self, "_app", None):
                                        self._invalidate(min_interval=0.5)
                                except Exception:
                                    pass
                            # Check for background process notifications (completions
                            # and watch pattern matches) while agent is idle.
                            try:
                                from tools.process_registry import process_registry
                                if not process_registry.completion_queue.empty():
                                    evt = process_registry.completion_queue.get_nowait()
                                    # Skip if the agent already consumed this via wait/poll/log
                                    _evt_sid = evt.get("session_id", "")
                                    if evt.get("type") == "completion" and process_registry.is_completion_consumed(_evt_sid):
                                        pass  # already delivered via tool result
                                    else:
                                        _synth = _format_process_notification(evt)
                                        if _synth:
                                            self._pending_input.put(_synth)
                            except Exception:
                                pass
                        continue
                    
                    self._execute_pending_input(user_input, app=app)

                except Exception as e:
                    print(f"Error: {e}")
        
        # Start processing thread
        process_thread = threading.Thread(target=process_loop, daemon=True)
        process_thread.start()
        
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
            self._stop_autonomous_execution_component(interrupt=True)
            # Interrupt the agent immediately so its daemon thread stops making
            # API calls and exits promptly (agent_thread is daemon, so the
            # process will exit once the main thread finishes, but interrupting
            # avoids wasted API calls and lets run_conversation clean up).
            if self.agent and getattr(self, '_agent_running', False):
                try:
                    self.agent.interrupt()
                except Exception:
                    pass
            # Flush memories before exit (only for substantial conversations)
            if self.agent and self.conversation_history:
                try:
                    self.agent.flush_memories(self.conversation_history)
                except (Exception, KeyboardInterrupt):
                    pass
            # Shut down voice recorder (release persistent audio stream)
            if hasattr(self, '_voice_recorder') and self._voice_recorder:
                try:
                    self._voice_recorder.shutdown()
                except Exception:
                    pass
                self._voice_recorder = None
            # Clean up old temp voice recordings
            try:
                from tools.voice_mode import cleanup_temp_recordings
                cleanup_temp_recordings()
            except Exception:
                pass
            # Unregister callbacks to avoid dangling references
            _get_set_sudo_password_callback(None)
            _get_set_approval_callback(None)
            _get_set_secret_capture_callback()(None)
            # Close session in SQLite
            if hasattr(self, '_session_db') and self._session_db and self.agent:
                try:
                    self._session_db.end_session(self.agent.session_id, "cli_close")
                except (Exception, KeyboardInterrupt) as e:
                    logger.debug("Could not close session in DB: %s", e)
            # Plugin hook: on_session_end — safety net for interrupted exits.
            # run_conversation() already fires this per-turn on normal completion,
            # so only fire here if the agent was mid-turn (_agent_running) when
            # the exit occurred, meaning run_conversation's hook didn't fire.
            if self.agent and getattr(self, '_agent_running', False):
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
            _run_cleanup()
            self._print_exit_summary()


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

    # Deferred runtime initialization — logging, config, skin, tool preview.
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
    # Default to VoidCube-cli toolset which includes cronjob management tools
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
        cli.show_tools()
        sys.exit(0)
    
    if list_toolsets:
        cli.show_banner()
        cli.show_toolsets()
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
    fire.Fire(main)

