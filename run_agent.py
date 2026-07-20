#!/usr/bin/env python3
"""
AI Agent Runner with Tool Calling

This module provides a clean, standalone agent that can execute AI models
with tool calling capabilities. It handles the conversation loop, tool execution,
and response management.

Features:
- Automatic tool calling loop until completion
- Configurable model parameters
- Error handling and recovery
- Message history management
- Support for multiple model providers

Usage:
    from run_agent import AIAgent
    
    agent = AIAgent(base_url="http://localhost:30000/v1", model="qwen3.5-plus")
    response = agent.run_conversation("Tell me about the latest Python updates")
"""

import asyncio
import concurrent.futures
import copy
import json
import logging
logger = logging.getLogger(__name__)
import os
import random
import re
import sys
import time
import threading
from dataclasses import replace
from types import SimpleNamespace
import uuid
from typing import List, Dict, Any, Optional
from openai import OpenAI
import fire
from datetime import datetime
from pathlib import Path

from VoidCube_core.constants import get_VoidCube_home

# Load .env from ~/.VoidCube/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from VoidCube_cli.env_loader import load_VoidCube_dotenv

_VoidCube_home = get_VoidCube_home()
_project_env = Path(__file__).parent / '.env'
_loaded_env_paths = load_VoidCube_dotenv(VoidCube_home=_VoidCube_home, project_env=_project_env)
if _loaded_env_paths:
    if len(_loaded_env_paths) == 2:
        logger.info("Loaded environment variables:")
        logger.info("  - %s (user config, highest priority)", _loaded_env_paths[0])
        logger.info("  - %s (dev fallback, fills missing values only)", _loaded_env_paths[1])
    else:
        for _env_path in _loaded_env_paths:
            logger.info("Loaded environment variables from %s", _env_path)
else:
    logger.info("No .env file found. Using system environment variables.")

# Load max workers from SOUL config or use default
try:
    from VoidCube_cli.soul_config import parse_soul_config
    from agent.tool_scheduler import MAX_TOOL_WORKERS as SCHEDULER_MAX_TOOL_WORKERS
    _soul_config = parse_soul_config()
    MAX_TOOL_WORKERS = int(_soul_config.get("agent", {}).get("max_tool_workers", SCHEDULER_MAX_TOOL_WORKERS))
except Exception:
    from agent.tool_scheduler import MAX_TOOL_WORKERS


# Import our tool system
from tools.model_tools import (
    get_tool_definitions,
    get_toolset_for_tool,
    handle_function_call,
    check_toolset_requirements,
)
from tools.terminal_tool import cleanup_vm, get_active_env, is_persistent_env
from tools.tool_result_storage import maybe_persist_tool_result, enforce_turn_budget
from tools.interrupt import set_interrupt as _set_interrupt
from tools.browser_tool import cleanup_browser


from VoidCube_core.constants import OPENROUTER_BASE_URL

# Agent internals extracted to agent/ package for modularity
from agent.memory_manager import build_memory_context_block
from agent.retry_utils import (
    RetryKind,
    decide_retry_directive,
    jittered_backoff,
)
from agent.error_classifier import (
    FailoverReason,
    classify_api_error,
    clean_error_message,
    is_stream_drop_error,
    retry_after_seconds,
    summarize_api_error,
)
from agent.prompt_builder import (
    DEFAULT_AGENT_IDENTITY, PLATFORM_HINTS,
    MEMORY_GUIDANCE, SESSION_SEARCH_GUIDANCE, SKILLS_GUIDANCE,
    build_nous_subscription_prompt,
)
from agent.model_metadata import (
    fetch_model_metadata,
    estimate_tokens_rough, estimate_messages_tokens_rough, estimate_request_tokens_rough,
    save_context_length, is_local_endpoint,
    query_ollama_num_ctx,
)
from agent.context_compressor import (
    CompressionRecoveryResult,
    ContextCompressor,
    apply_context_recovery_plan,
    build_context_recovery_plan,
    next_compression_attempt,
)
from agent.iteration_control import IterationBudget
from agent.subdirectory_hints import SubdirectoryHintTracker
from agent.prompt_builder import build_skills_system_prompt, build_context_files_prompt, build_environment_hints, load_soul_md, TOOL_USE_ENFORCEMENT_GUIDANCE, TOOL_USE_ENFORCEMENT_MODELS, GOOGLE_MODEL_OPERATIONAL_GUIDANCE, OPENAI_MODEL_EXECUTION_GUIDANCE
from agent.api_request import (
    ChatRequestConfig,
    build_chat_completion_kwargs,
)
from agent.integration_policy import require_active_integration
from agent.api_response import (
    extract_reasoning,
    has_thinking_tags,
    has_visible_content,
    normalize_assistant_message,
    strip_thinking_blocks,
    strip_thinking_tags,
)
from agent.usage_pricing import estimate_usage_cost, normalize_usage
from agent.tool_scheduler import (
    NEVER_PARALLEL_TOOLS, PARALLEL_SAFE_TOOLS, PATH_SCOPED_TOOLS,
    is_destructive_command, should_parallelize_tool_batch,
)
from agent.display import (
    KawaiiSpinner, build_tool_preview as _build_tool_preview,
    get_cute_tool_message as _get_cute_tool_message_impl,
    _detect_tool_failure,
    get_tool_emoji as _get_tool_emoji,
)
from agent.message_sanitizer import (
    sanitize_messages_non_ascii,
    sanitize_messages_surrogates,
    sanitize_surrogates,
)
from agent.tool_schema import normalize_tool_definitions
from VoidCube_core.utils import atomic_json_write, env_var_enabled



from agent.stream_handler import _SafeWriter


def _install_safe_stdio() -> None:
    """Wrap stdout/stderr so best-effort console output cannot crash the agent."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _SafeWriter):
            setattr(sys, stream_name, _SafeWriter(stream))


# IterationBudget imported from agent.iteration_control (was duplicated here)

# Tool scheduling logic moved to agent/tool_scheduler.py



# =========================================================================
# Large tool result handler — save oversized output to temp file
# =========================================================================


# =========================================================================
# Qwen Portal headers — mimics QwenCode CLI for portal.qwen.ai compatibility.
# Extracted as a module-level helper so both __init__ and
# _apply_client_headers_for_base_url can share it.
# =========================================================================
_QWEN_CODE_VERSION = "0.14.1"


def _qwen_portal_headers() -> dict:
    """Return default HTTP headers required by Qwen Portal API."""
    import platform as _plat

    _ua = f"QwenCode/{_QWEN_CODE_VERSION} ({_plat.system().lower()}; {_plat.machine()})"
    return {
        "User-Agent": _ua,
        "X-DashScope-CacheControl": "enable",
        "X-DashScope-UserAgent": _ua,
        "X-DashScope-AuthType": "qwen-oauth",
    }


class AIAgent:
    """
    AI Agent with tool calling capabilities.

    This class manages the conversation flow, tool execution, and response handling
    for AI models that support function calling.
    """

    # ── Class-level context pressure dedup (survives across instances) ──
    # The gateway creates a new AIAgent per message, so instance-level flags
    # reset every time.  This dict tracks {session_id: (warn_level, timestamp)}
    # to suppress duplicate warnings within a cooldown window.
    _context_pressure_last_warned: dict = {}
    _CONTEXT_PRESSURE_COOLDOWN = 300  # seconds between re-warning same session

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self._base_url = value
        self._base_url_lower = value.lower() if value else ""

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        provider: str = None,
        acp_command: str = None,
        acp_args: list[str] | None = None,
        command: str = None,
        args: list[str] | None = None,
        model: str = "",
        max_iterations: int = 90,  # Default tool-calling iterations (shared with subagents)
        tool_delay: float = 1.0,
        enabled_toolsets: List[str] = None,
        disabled_toolsets: List[str] = None,
        verbose_logging: bool = False,
        quiet_mode: bool = False,
        ephemeral_system_prompt: str = None,
        log_prefix_chars: int = 100,
        log_prefix: str = "",
        providers_allowed: List[str] = None,
        providers_ignored: List[str] = None,
        providers_order: List[str] = None,
        provider_sort: str = None,
        provider_require_parameters: bool = False,
        provider_data_collection: str = None,
        session_id: str = None,
        tool_progress_callback: callable = None,
        tool_start_callback: callable = None,
        tool_complete_callback: callable = None,
        thinking_callback: callable = None,
        reasoning_callback: callable = None,
        clarify_callback: callable = None,
        step_callback: callable = None,
        stream_delta_callback: callable = None,
        interim_assistant_callback: callable = None,
        tool_gen_callback: callable = None,
        status_callback: callable = None,
        max_tokens: int = None,
        reasoning_config: Dict[str, Any] = None,
        service_tier: str = None,
        request_overrides: Dict[str, Any] = None,
        prefill_messages: List[Dict[str, Any]] = None,
        platform: str = None,
        user_id: str = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        session_db=None,
        parent_session_id: str = None,
        iteration_budget: "IterationBudget" = None,
        fallback_model: Dict[str, Any] = None,
        credential_pool=None,
        checkpoints_enabled: bool = False,
        checkpoint_max_snapshots: int = 50,
        pass_session_id: bool = False,
        persist_session: bool = True,
    ):
        """
        Initialize the AI Agent.

        Args:
            base_url (str): Base URL for the model API (optional)
            api_key (str): API key for authentication (optional, uses env var if not provided)
            provider (str): Provider identifier (optional; used for telemetry/routing hints)
            model (str): Model name to use (default: "deepseek/deepseek-chat")
            max_iterations (int): Maximum number of tool calling iterations (default: 90)
            tool_delay (float): Delay between tool calls in seconds (default: 1.0)
            enabled_toolsets (List[str]): Only enable tools from these toolsets (optional)
            disabled_toolsets (List[str]): Disable tools from these toolsets (optional)
            verbose_logging (bool): Enable verbose logging for debugging (default: False)
            quiet_mode (bool): Suppress progress output for clean CLI experience (default: False)
            ephemeral_system_prompt (str): System prompt used during agent execution but NOT saved to trajectories (optional)
            log_prefix_chars (int): Number of characters to show in log previews for tool calls/responses (default: 100)
            log_prefix (str): Prefix to add to all log messages for identification in parallel processing (default: "")
            providers_allowed (List[str]): OpenRouter providers to allow (optional)
            providers_ignored (List[str]): OpenRouter providers to ignore (optional)
            providers_order (List[str]): OpenRouter providers to try in order (optional)
            provider_sort (str): Sort providers by price/throughput/latency (optional)
            session_id (str): Pre-generated session ID for logging (optional, auto-generated if not provided)
            tool_progress_callback (callable): Callback function(tool_name, args_preview) for progress notifications
            clarify_callback (callable): Callback function(question, choices) -> str for interactive user questions.
                Provided by the platform layer (CLI or gateway). If None, the clarify tool returns an error.
            max_tokens (int): Maximum tokens for model responses (optional, uses model default if not set)
            reasoning_config (Dict): OpenRouter reasoning configuration override (e.g. {"effort": "none"} to disable thinking).
                If None, defaults to {"enabled": True, "effort": "medium"} for OpenRouter. Set to disable/customize reasoning.
            prefill_messages (List[Dict]): Messages to prepend to conversation history as prefilled context.
                Useful for injecting a few-shot example or priming the model's response style.
                Example: [{"role": "user", "content": "Hi!"}, {"role": "assistant", "content": "Hello!"}]
            platform (str): The interface platform the user is on (e.g. "cli", "telegram", "discord", "whatsapp").
                Used to inject platform-specific formatting hints into the system prompt.
            skip_context_files (bool): If True, skip auto-injection of SOUL.md, AGENTS.md, and .cursorrules
                into the system prompt. Use this for batch processing and data generation to avoid
                polluting trajectories with user-specific persona or project instructions.
        """
        _install_safe_stdio()

        self.model = model
        self.max_iterations = max_iterations
        # Shared iteration budget — parent creates, children inherit.
        # Consumed by every LLM turn across parent + all subagents.
        self.iteration_budget = iteration_budget or IterationBudget(max_iterations)
        self.tool_delay = tool_delay
        self.verbose_logging = verbose_logging
        self.quiet_mode = quiet_mode
        self.ephemeral_system_prompt = ephemeral_system_prompt
        self.platform = platform  # "cli", "telegram", "discord", "whatsapp", etc.
        self._user_id = user_id  # Platform user identifier (gateway sessions)
        # Pluggable print function — CLI replaces this with _cprint so that
        # raw ANSI status lines are routed through prompt_toolkit's renderer
        # instead of going directly to stdout where patch_stdout's StdoutProxy
        # would mangle the escape sequences.  None = use builtins.print.
        self._print_fn = None
        self.background_review_callback = None  # Optional sync callback for gateway delivery
        self.skip_context_files = skip_context_files
        self.pass_session_id = pass_session_id
        self.persist_session = persist_session
        self._credential_pool = credential_pool
        self.log_prefix_chars = log_prefix_chars
        self.log_prefix = f"{log_prefix} " if log_prefix else ""
        # Store effective base URL for feature detection (prompt caching, reasoning, etc.)
        self.base_url = base_url or ""
        provider_name = provider.strip().lower() if isinstance(provider, str) and provider.strip() else None
        self.provider = provider_name or ""
        self.acp_command = acp_command or command
        self.acp_args = list(acp_args or args or [])

        try:
            from VoidCube_cli.model_normalize import (
                _AGGREGATOR_PROVIDERS,
                normalize_model_for_provider,
            )

            if self.provider not in _AGGREGATOR_PROVIDERS:
                self.model = normalize_model_for_provider(self.model, self.provider)
        except Exception:
            pass

        # Pre-warm OpenRouter model metadata cache in a background thread.
        # fetch_model_metadata() is cached for 1 hour; this avoids a blocking
        # HTTP request on the first API response when pricing is estimated.
        if self.provider == "openrouter" or self._is_openrouter_url():
            threading.Thread(
                target=lambda: fetch_model_metadata(),
                daemon=True,
            ).start()

        self.tool_progress_callback = tool_progress_callback
        self.tool_start_callback = tool_start_callback
        self.tool_complete_callback = tool_complete_callback
        self.suppress_status_output = False
        self.thinking_callback = thinking_callback
        self.reasoning_callback = reasoning_callback
        self.clarify_callback = clarify_callback
        self.step_callback = step_callback
        self.stream_delta_callback = stream_delta_callback
        self.interim_assistant_callback = interim_assistant_callback
        self.status_callback = status_callback
        self.tool_gen_callback = tool_gen_callback

        
        # Tool execution state — allows _vprint during tool execution
        # even when stream consumers are registered (no tokens streaming then)
        self._executing_tools = False

        # Interrupt mechanism for breaking out of tool loops
        self._interrupt_requested = False
        self._interrupt_message = None  # Optional message that triggered interrupt
        self._execution_thread_id: int | None = None  # Set at run_conversation() start
        self._client_lock = threading.RLock()
        
        # Subagent delegation state
        self._delegate_depth = 0        # 0 = top-level agent, incremented for children
        self._active_children: list = []      # Running child AIAgents (for interrupt propagation)
        self._active_children_lock = threading.Lock()
        self._subagent_display_manager = None
        
        # Store OpenRouter provider preferences
        self.providers_allowed = providers_allowed
        self.providers_ignored = providers_ignored
        self.providers_order = providers_order
        self.provider_sort = provider_sort
        self.provider_require_parameters = provider_require_parameters
        self.provider_data_collection = provider_data_collection

        # Store toolset filtering options
        self.enabled_toolsets = enabled_toolsets
        self.disabled_toolsets = disabled_toolsets
        
        # Model response configuration
        self.max_tokens = max_tokens  # None = use model default
        self.reasoning_config = reasoning_config  # None = use default (medium for OpenRouter)
        self.service_tier = service_tier
        self.request_overrides = dict(request_overrides or {})
        self.prefill_messages = prefill_messages or []  # Prefilled conversation turns
        
        # Iteration budget: the LLM is only notified when it actually exhausts
        # the iteration budget (api_call_count >= max_iterations).  At that
        # point we inject ONE message, allow one final API call, and if the
        # model doesn't produce a text response, force a user-message asking
        # it to summarise.  No intermediate pressure warnings — they caused
        # models to "give up" prematurely on complex tasks (#7915).
        self._budget_exhausted_injected = False
        self._budget_grace_call = False

        # Context pressure warnings: notify the USER (not the LLM) as context
        # fills up.  Purely informational — displayed in CLI output and sent via
        # status_callback for gateway platforms.  Does NOT inject into messages.
        # Tiered: fires at 85% and again at 95% of compaction threshold.
        self._context_pressure_warned_at = 0.0  # highest tier already shown

        # Activity tracking — updated on each API call, tool execution, and
        # stream chunk.  Used by the gateway timeout handler to report what the
        # agent was doing when it was killed, and by the "still working"
        # notifications to show progress.
        self._last_activity_ts: float = time.time()
        self._last_activity_desc: str = "initializing"
        self._current_tool: str | None = None
        self._api_call_count: int = 0

        # Rate limit tracking — updated from x-ratelimit-* response headers
        # after each API call.  Accessed by /usage slash command.
        self._rate_limit_state: Optional["RateLimitState"] = None

        # Centralized logging — agent.log (INFO+) and errors.log (WARNING+)
        # both live under ~/.VoidCube/logs/.  Idempotent, so gateway mode
        # (which creates a new AIAgent per message) won't duplicate handlers.
        from VoidCube_core.logging import setup_logging, setup_verbose_logging
        setup_logging(VoidCube_home=_VoidCube_home)

        if self.verbose_logging:
            setup_verbose_logging()
            logger.info("Verbose logging enabled (third-party library logs suppressed)")
        else:
            if self.quiet_mode:
                # In quiet mode (CLI default), suppress all tool/infra log
                # noise on the *console*. The TUI has its own rich display
                # for status; logger INFO/WARNING messages just clutter it.
                # File handlers (agent.log, errors.log) still capture everything.
                for quiet_logger in [
                    'tools',               # all tools.* (terminal, browser, web, file, etc.)
                    'run_agent',            # agent runner internals
                    'trajectory_compressor',
                    'cron',                 # scheduler (only relevant in daemon mode)
                    'VoidCube_cli',           # CLI helpers
                ]:
                    logging.getLogger(quiet_logger).setLevel(logging.ERROR)
        
        # Internal stream callback (set during streaming TTS).
        # Initialized here so _vprint can reference it before run_conversation.
        self._stream_callback = None
        # Deferred paragraph break flag — set after tool iterations so a
        # single "\n\n" is prepended to the next real text delta.
        self._stream_needs_break = False
        # Visible assistant text already delivered through live token callbacks
        # during the current model response. Used to avoid re-sending the same
        # commentary when the provider later returns it as a completed interim
        # assistant message.
        self._current_streamed_assistant_text = ""

        # Optional current-turn user-message override used when the API-facing
        # user message intentionally differs from the persisted transcript
        # (e.g. CLI voice mode adds a temporary prefix for the live call only).
        self._persist_user_message_idx = None
        self._persist_user_message_override = None

        # Initialize the sole OpenAI-compatible chat-completions client.
        if api_key and base_url:
            # Explicit credentials from CLI/gateway — construct directly.
            # The runtime provider resolver already handled auth for us.
            client_kwargs = {"api_key": api_key, "base_url": base_url}
            if self.provider == "copilot-acp":
                client_kwargs["command"] = self.acp_command
                client_kwargs["args"] = self.acp_args
            effective_base = base_url
            if "openrouter" in effective_base.lower():
                client_kwargs["default_headers"] = {
                    "HTTP-Referer": "https://VoidCube-agent.nousresearch.com",
                    "X-OpenRouter-Title": "Voidcube Agent",
                    "X-OpenRouter-Categories": "productivity,cli-agent",
                }
            elif "api.githubcopilot.com" in effective_base.lower():
                from VoidCube_cli.models import copilot_default_headers

                client_kwargs["default_headers"] = copilot_default_headers()
            elif "api.kimi.com" in effective_base.lower():
                client_kwargs["default_headers"] = {
                    "User-Agent": "KimiCLI/1.30.0",
                }
            elif "portal.qwen.ai" in effective_base.lower():
                client_kwargs["default_headers"] = _qwen_portal_headers()
        else:
            # No explicit creds — use the centralized provider router.
            from agent.auxiliary_client import resolve_provider_client
            _routed_client, _ = resolve_provider_client(
                self.provider or "auto", model=self.model)
            if _routed_client is not None:
                client_kwargs = {
                    "api_key": _routed_client.api_key,
                    "base_url": str(_routed_client.base_url),
                }
                if hasattr(_routed_client, '_default_headers') and _routed_client._default_headers:
                    client_kwargs["default_headers"] = dict(_routed_client._default_headers)
            else:
                _explicit = (self.provider or "").strip().lower()
                if _explicit and _explicit not in ("auto", "openrouter", "custom"):
                    raise RuntimeError(
                        f"Provider '{_explicit}' is set in config.yaml but no API key "
                        f"was found. Set the {_explicit.upper()}_API_KEY environment "
                        f"variable, or switch to a different provider with `VoidCube model`."
                    )
                client_kwargs = {
                    "api_key": os.getenv("OPENROUTER_API_KEY", ""),
                    "base_url": OPENROUTER_BASE_URL,
                    "default_headers": {
                        "HTTP-Referer": "https://VoidCube-agent.nousresearch.com",
                        "X-OpenRouter-Title": "Voidcube Agent",
                        "X-OpenRouter-Categories": "productivity,cli-agent",
                    },
                }

        self._client_kwargs = client_kwargs
        self.api_key = client_kwargs.get("api_key", "")
        self.base_url = client_kwargs.get("base_url", self.base_url)
        try:
            self.client = self._create_openai_client(client_kwargs, reason="agent_init", shared=True)
            if not self.quiet_mode:
                print(f"🤖 AI Agent initialized with model: {self.model}")
                if base_url:
                    print(f"🔗 Using custom base URL: {base_url}")
                key_used = client_kwargs.get("api_key", "none")
                if key_used and key_used != "dummy-key" and len(key_used) > 12:
                    print(f"🔑 Using API key: {key_used[:8]}...{key_used[-4:]}")
                else:
                    print(f"⚠️  Warning: API key appears invalid or missing (got: '{key_used[:20] if key_used else 'none'}...')")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize OpenAI client: {e}")
        
        # Provider fallback chain — ordered list of backup providers tried
        # when the primary is exhausted (rate-limit, overload, connection
        # failure).  Supports both legacy single-dict ``fallback_model`` and
        # new list ``fallback_providers`` format.
        if isinstance(fallback_model, list):
            self._fallback_chain = [
                f for f in fallback_model
                if isinstance(f, dict) and f.get("provider") and f.get("model")
            ]
        elif isinstance(fallback_model, dict) and fallback_model.get("provider") and fallback_model.get("model"):
            self._fallback_chain = [fallback_model]
        else:
            self._fallback_chain = []
        self._fallback_index = 0
        self._fallback_activated = False
        # Legacy attribute kept for backward compat (tests, external callers)
        self._fallback_model = self._fallback_chain[0] if self._fallback_chain else None
        if self._fallback_chain and not self.quiet_mode:
            if len(self._fallback_chain) == 1:
                fb = self._fallback_chain[0]
                print(f"🔄 Fallback model: {fb['model']} ({fb['provider']})")
            else:
                print(f"🔄 Fallback chain ({len(self._fallback_chain)} providers): " +
                      " → ".join(f"{f['model']} ({f['provider']})" for f in self._fallback_chain))

        # Get available tools with filtering
        self.tools = get_tool_definitions(
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=disabled_toolsets,
            quiet_mode=self.quiet_mode,
        )
        self.tools = normalize_tool_definitions(self.tools)
        
        # Show tool configuration and store valid tool names for validation
        self.valid_tool_names = set()
        if self.tools:
            self.valid_tool_names = {tool["function"]["name"] for tool in self.tools}
            tool_names = sorted(self.valid_tool_names)
            if not self.quiet_mode:
                print(f"🛠️  Loaded {len(self.tools)} tools: {', '.join(tool_names)}")
                
                # Show filtering info if applied
                if enabled_toolsets:
                    print(f"   ✅ Enabled toolsets: {', '.join(enabled_toolsets)}")
                if disabled_toolsets:
                    print(f"   ❌ Disabled toolsets: {', '.join(disabled_toolsets)}")
        elif not self.quiet_mode:
            print("🛠️  No tools loaded (all tools filtered out or unavailable)")
        
        # Check tool requirements
        if self.tools and not self.quiet_mode:
            requirements = check_toolset_requirements()
            missing_reqs = [name for name, available in requirements.items() if not available]
            if missing_reqs:
                print(f"⚠️  Some tools may not work due to missing requirements: {missing_reqs}")
        
        # Show ephemeral system prompt status
        if self.ephemeral_system_prompt and not self.quiet_mode:
            prompt_preview = self.ephemeral_system_prompt[:60] + "..." if len(self.ephemeral_system_prompt) > 60 else self.ephemeral_system_prompt
            print(f"🔒 Ephemeral system prompt: '{prompt_preview}' (not saved to trajectories)")
        
        # Prompt caching is disabled
        
        # Session logging setup - auto-save conversation trajectories for debugging
        self.session_start = datetime.now()
        if session_id:
            # Use provided session ID (e.g., from CLI)
            self.session_id = session_id
        else:
            # Generate a new session ID
            timestamp_str = self.session_start.strftime("%Y%m%d_%H%M%S")
            short_uuid = uuid.uuid4().hex[:6]
            self.session_id = f"{timestamp_str}_{short_uuid}"
        
        # Session logs go into ~/.VoidCube/sessions/ alongside gateway sessions
        VoidCube_home = get_VoidCube_home()
        self.logs_dir = VoidCube_home / "sessions"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.session_log_file = self.logs_dir / f"session_{self.session_id}.json"
        
        # Track conversation messages for session logging
        self._session_messages: List[Dict[str, Any]] = []
        
        # Session state management
        self._session_state = None
        try:
            from VoidCube_cli.session_state import SessionState
            self._session_state = SessionState(self.session_id, VoidCube_home)
        except Exception:
            pass
        
        # Cached system prompt -- built once per session, only rebuilt on compression
        self._cached_system_prompt: Optional[str] = None
        
        # Filesystem checkpoint manager (transparent — not a tool)
        from tools.checkpoint_manager import CheckpointManager
        self._checkpoint_mgr = CheckpointManager(
            enabled=checkpoints_enabled,
            max_snapshots=checkpoint_max_snapshots,
        )
        
        # SQLite session store (optional -- provided by CLI or gateway)
        self._session_db = session_db
        self._parent_session_id = parent_session_id
        self._last_flushed_db_idx = 0  # tracks DB-write cursor to prevent duplicate writes
        if self._session_db:
            try:
                self._session_db.create_session(
                    session_id=self.session_id,
                    source=self.platform or os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
                    model=self.model,
                    model_config={
                        "max_iterations": self.max_iterations,
                        "reasoning_config": reasoning_config,
                        "max_tokens": max_tokens,
                    },
                    user_id=None,
                    parent_session_id=self._parent_session_id,
                )
            except Exception as e:
                # Transient SQLite lock contention (e.g. CLI and gateway writing
                # concurrently) must NOT permanently disable session_search for
                # this agent.  Keep _session_db alive — subsequent message
                # flushes and session_search calls will still work once the
                # lock clears.  The session row may be missing from the index
                # for this run, but that is recoverable (flushes upsert rows).
                logger.warning(
                    "Session DB create_session failed (session_search still available): %s", e
                )
        
        # In-memory todo list for task planning (one per agent/session)
        from tools.todo_tool import TodoStore
        self._todo_store = TodoStore()
        
        # Load config once for memory, skills, and compression sections
        try:
            from VoidCube_cli.config import load_config as _load_agent_config
            _agent_cfg = _load_agent_config()
        except Exception:
            _agent_cfg = {}

        # Persistent memory (MEMORY.md + USER.md) -- loaded from disk
        self._memory_store = None
        self._memory_enabled = False
        self._user_profile_enabled = False
        self._memory_nudge_interval = 10
        self._memory_flush_min_turns = 6
        self._turns_since_memory = 0
        self._iters_since_skill = 0
        if not skip_memory:
            try:
                mem_config = _agent_cfg.get("memory", {})
                self._memory_enabled = mem_config.get("memory_enabled", False)
                self._user_profile_enabled = mem_config.get("user_profile_enabled", False)
                self._memory_nudge_interval = int(mem_config.get("nudge_interval", 10))
                self._memory_flush_min_turns = int(mem_config.get("flush_min_turns", 6))
                if self._memory_enabled or self._user_profile_enabled:
                    from tools.memory_tool import MemoryStore
                    self._memory_store = MemoryStore(
                        memory_char_limit=mem_config.get("memory_char_limit", 2200),
                        user_char_limit=mem_config.get("user_char_limit", 1375),
                    )
                    self._memory_store.load_from_disk()
            except Exception:
                pass  # Memory is optional -- don't break agent init
        


        # Memory provider plugin (external — one at a time, alongside built-in)
        # Reads memory.provider from config to select which plugin to activate.
        # Default is "mem" for Mem time-series memory system.
        self._memory_manager = None
        if not skip_memory:
            try:
                _mem_provider_name = mem_config.get("provider", "mem") if mem_config else "mem"

                if _mem_provider_name:
                    from agent.memory_manager import MemoryManager as _MemoryManager
                    from plugins.memory import load_memory_provider as _load_mem
                    self._memory_manager = _MemoryManager()
                    _mp = _load_mem(_mem_provider_name)
                    if _mp and _mp.is_available():
                        self._memory_manager.add_provider(_mp)
                    if self._memory_manager.providers:
                        from VoidCube_core.constants import get_VoidCube_home as _ghh
                        _init_kwargs = {
                            "session_id": self.session_id,
                            "platform": platform or "cli",
                            "VoidCube_home": str(_ghh()),
                            "agent_context": "primary",
                        }
                        # Thread gateway user identity for per-user memory scoping
                        if self._user_id:
                            _init_kwargs["user_id"] = self._user_id
                        # Profile identity for per-profile provider scoping
                        try:
                            from VoidCube_cli.profiles import get_active_profile_name
                            _profile = get_active_profile_name()
                            _init_kwargs["agent_identity"] = _profile
                            _init_kwargs["agent_workspace"] = "VoidCube"
                        except Exception:
                            pass
                        self._memory_manager.initialize_all(**_init_kwargs)
                        logger.info("Memory provider '%s' activated", _mem_provider_name)
                    else:
                        logger.debug("Memory provider '%s' not found or not available", _mem_provider_name)
                        self._memory_manager = None
            except Exception as _mpe:
                logger.warning("Memory provider plugin init failed: %s", _mpe)
                self._memory_manager = None

        # Inject memory provider tool schemas into the tool surface
        if self._memory_manager and self.tools is not None:
            for _schema in self._memory_manager.get_all_tool_schemas():
                _wrapped = {"type": "function", "function": _schema}
                self.tools.append(normalize_tool_definitions([_wrapped])[0])
                _tname = _schema.get("name", "")
                if _tname:
                    self.valid_tool_names.add(_tname)

        # Skills config: nudge interval for skill creation reminders
        self._skill_nudge_interval = 10
        try:
            skills_config = _agent_cfg.get("skills", {})
            self._skill_nudge_interval = int(skills_config.get("creation_nudge_interval", 10))
        except Exception:
            pass

        # Tool-use enforcement config: "auto" (default — matches hardcoded
        # model list), true (always), false (never), or list of substrings.
        _agent_section = _agent_cfg.get("agent", {})
        if not isinstance(_agent_section, dict):
            _agent_section = {}
        self._tool_use_enforcement = _agent_section.get("tool_use_enforcement", "auto")

        # Initialize context compressor for automatic context management
        # Compresses conversation when approaching model's context limit
        # Configuration via config.yaml (compression section)
        _compression_cfg = _agent_cfg.get("compression", {})
        if not isinstance(_compression_cfg, dict):
            _compression_cfg = {}
        compression_threshold = float(_compression_cfg.get("threshold", 0.50))
        compression_enabled = str(_compression_cfg.get("enabled", True)).lower() in ("true", "1", "yes")
        compression_target_ratio = float(_compression_cfg.get("target_ratio", 0.20))
        compression_protect_last = int(_compression_cfg.get("protect_last_n", 20))

        # Read explicit context_length override from model config
        _model_cfg = _agent_cfg.get("model", {})
        if isinstance(_model_cfg, dict):
            _config_context_length = _model_cfg.get("context_length")
        else:
            _config_context_length = None
        if _config_context_length is not None:
            try:
                _config_context_length = int(_config_context_length)
            except (TypeError, ValueError):
                _config_context_length = None

        # Store for reuse in switch_model (so config override persists across model switches)
        self._config_context_length = _config_context_length

        # Check custom_providers per-model context_length
        if _config_context_length is None:
            _custom_providers = _agent_cfg.get("custom_providers")
            if isinstance(_custom_providers, list):
                for _cp_entry in _custom_providers:
                    if not isinstance(_cp_entry, dict):
                        continue
                    _cp_url = (_cp_entry.get("base_url") or "").rstrip("/")
                    if _cp_url and _cp_url == self.base_url.rstrip("/"):
                        _cp_models = _cp_entry.get("models", {})
                        if isinstance(_cp_models, dict):
                            _cp_model_cfg = _cp_models.get(self.model, {})
                            if isinstance(_cp_model_cfg, dict):
                                _cp_ctx = _cp_model_cfg.get("context_length")
                                if _cp_ctx is not None:
                                    try:
                                        _config_context_length = int(_cp_ctx)
                                    except (TypeError, ValueError):
                                        pass
                        break
        
        # Select context engine: config-driven (like memory providers).
        # 1. Check config.yaml context.engine setting
        # 2. Check plugins/context_engine/<name>/ directory (repo-shipped)
        # 3. Check general plugin system (user-installed plugins)
        # 4. Fall back to built-in ContextCompressor
        _selected_engine = None
        _engine_name = "compressor"  # default
        try:
            _ctx_cfg = _agent_cfg.get("context", {}) if isinstance(_agent_cfg, dict) else {}
            _engine_name = _ctx_cfg.get("engine", "compressor") or "compressor"
        except Exception:
            pass

        if _engine_name != "compressor":
            # Try loading from plugins/context_engine/<name>/
            try:
                from plugins.context_engine import load_context_engine
                _selected_engine = load_context_engine(_engine_name)
            except Exception as _ce_load_err:
                logger.debug("Context engine load from plugins/context_engine/: %s", _ce_load_err)

            # Try general plugin system as fallback
            if _selected_engine is None:
                try:
                    from VoidCube_cli.plugins import get_plugin_context_engine
                    _candidate = get_plugin_context_engine()
                    if _candidate and _candidate.name == _engine_name:
                        _selected_engine = _candidate
                except Exception:
                    pass

            if _selected_engine is None:
                logger.warning(
                    "Context engine '%s' not found — falling back to built-in compressor",
                    _engine_name,
                )
        # else: config says "compressor" — use built-in, don't auto-activate plugins

        if _selected_engine is not None:
            self.context_compressor = _selected_engine
            if not self.quiet_mode:
                logger.info("Using context engine: %s", _selected_engine.name)
        else:
            self.context_compressor = ContextCompressor(
                model=self.model,
                threshold_percent=compression_threshold,
                protect_first_n=3,
                protect_last_n=compression_protect_last,
                summary_target_ratio=compression_target_ratio,
                quiet_mode=self.quiet_mode,
                base_url=self.base_url,
                api_key=getattr(self, "api_key", ""),
                config_context_length=_config_context_length,
                provider=self.provider,
            )
        self.compression_enabled = compression_enabled

        # Reject models whose context window is below the minimum required
        # for reliable tool-calling workflows (64K tokens).
        from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
        _ctx = getattr(self.context_compressor, "context_length", 0)
        if _ctx and _ctx < MINIMUM_CONTEXT_LENGTH:
            raise ValueError(
                f"Model {self.model} has a context window of {_ctx:,} tokens, "
                f"which is below the minimum {MINIMUM_CONTEXT_LENGTH:,} required "
                f"by Voidcube Agent.  Choose a model with at least "
                f"{MINIMUM_CONTEXT_LENGTH // 1000}K context, or set "
                f"model.context_length in config.yaml to override."
            )

        # Inject context engine tool schemas (e.g. lcm_grep, lcm_describe, lcm_expand)
        self._context_engine_tool_names: set = set()
        if hasattr(self, "context_compressor") and self.context_compressor and self.tools is not None:
            for _schema in self.context_compressor.get_tool_schemas():
                _wrapped = {"type": "function", "function": _schema}
                self.tools.append(normalize_tool_definitions([_wrapped])[0])
                _tname = _schema.get("name", "")
                if _tname:
                    self.valid_tool_names.add(_tname)
                    self._context_engine_tool_names.add(_tname)

        # Notify context engine of session start
        if hasattr(self, "context_compressor") and self.context_compressor:
            try:
                self.context_compressor.on_session_start(
                    self.session_id,
                    VoidCube_home=str(get_VoidCube_home()),
                    platform=self.platform or "cli",
                    model=self.model,
                    context_length=getattr(self.context_compressor, "context_length", 0),
                )
            except Exception as _ce_err:
                logger.debug("Context engine on_session_start: %s", _ce_err)

        self._subdirectory_hints = SubdirectoryHintTracker(
            working_dir=os.getenv("TERMINAL_CWD") or None,
        )
        self._user_turn_count = 0

        # Cumulative token usage for the session
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_api_calls = 0
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_estimated_cost_usd = 0.0
        self.session_cost_status = "unknown"
        self.session_cost_source = "none"
        
        # ── Ollama num_ctx injection ──
        # Ollama defaults to 2048 context regardless of the model's capabilities.
        # When running against an Ollama server, detect the model's max context
        # and pass num_ctx on every chat request so the full window is used.
        # User override: set model.ollama_num_ctx in config.yaml to cap VRAM use.
        self._ollama_num_ctx: int | None = None
        _ollama_num_ctx_override = None
        if isinstance(_model_cfg, dict):
            _ollama_num_ctx_override = _model_cfg.get("ollama_num_ctx")
        if _ollama_num_ctx_override is not None:
            try:
                self._ollama_num_ctx = int(_ollama_num_ctx_override)
            except (TypeError, ValueError):
                logger.debug("Invalid ollama_num_ctx config value: %r", _ollama_num_ctx_override)
        if self._ollama_num_ctx is None and self.base_url and is_local_endpoint(self.base_url):
            try:
                _detected = query_ollama_num_ctx(self.model, self.base_url)
                if _detected and _detected > 0:
                    self._ollama_num_ctx = _detected
            except Exception as exc:
                logger.debug("Ollama num_ctx detection failed: %s", exc)
        if self._ollama_num_ctx and not self.quiet_mode:
            logger.info(
                "Ollama num_ctx: will request %d tokens (model max from /api/show)",
                self._ollama_num_ctx,
            )

        if not self.quiet_mode:
            if compression_enabled:
                print(f"📊 Context limit: {self.context_compressor.context_length:,} tokens (compress at {int(compression_threshold*100)}% = {self.context_compressor.threshold_tokens:,})")
            else:
                print(f"📊 Context limit: {self.context_compressor.context_length:,} tokens (auto-compression disabled)")

        # Check immediately so CLI users see the warning at startup.
        # Gateway status_callback is not yet wired, so any warning is stored
        # in _compression_warning and replayed in the first run_conversation().
        self._compression_warning = None
        self._check_compression_model_feasibility()

        # Snapshot primary runtime for per-turn restoration.  When fallback
        # activates during a turn, the next turn restores these values so the
        # preferred model gets a fresh attempt each time.  Uses a single dict
        # so new state fields are easy to add without N individual attributes.
        _cc = self.context_compressor
        self._primary_runtime = {
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": getattr(self, "api_key", ""),
            "client_kwargs": dict(self._client_kwargs),
            # Context engine state that _try_activate_fallback() overwrites.
            # Use getattr for model/base_url/api_key/provider since plugin
            # engines may not have these (they're ContextCompressor-specific).
            "compressor_model": getattr(_cc, "model", self.model),
            "compressor_base_url": getattr(_cc, "base_url", self.base_url),
            "compressor_api_key": getattr(_cc, "api_key", ""),
            "compressor_provider": getattr(_cc, "provider", self.provider),
            "compressor_context_length": _cc.context_length,
            "compressor_threshold_tokens": _cc.threshold_tokens,
        }
    def reset_session_state(self):
        """Reset all session-scoped token counters to 0 for a fresh session.
        
        This method encapsulates the reset logic for all session-level metrics
        including:
        - Token usage counters (input, output, total, prompt, completion)
        - Cache read/write tokens
        - API call count
        - Reasoning tokens
        - Estimated cost tracking
        - Context compressor internal counters
        
        The method safely handles optional attributes (e.g., context compressor)
        using ``hasattr`` checks.
        
        This keeps the counter reset logic DRY and maintainable in one place
        rather than scattering it across multiple methods.
        """
        # Token usage counters
        self.session_total_tokens = 0
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_api_calls = 0
        self.session_estimated_cost_usd = 0.0
        self.session_cost_status = "unknown"
        self.session_cost_source = "none"
        
        # Turn counter (added after reset_session_state was first written — #2635)
        self._user_turn_count = 0

        # Context engine reset (works for both built-in compressor and plugins)
        if hasattr(self, "context_compressor") and self.context_compressor:
            self.context_compressor.on_session_reset()
    
    def switch_model(self, new_model, new_provider, api_key='', base_url=''):
        """Switch the model/provider in-place for a live agent.

        Called by the /model command handlers (CLI and gateway) after
        ``model_switch.switch_model()`` has resolved credentials and
        validated the model.  This method performs the actual runtime
        swap: rebuilding clients and refreshing
        the context compressor.

        The implementation mirrors ``_try_activate_fallback()`` for the
        client-swap logic but also updates ``_primary_runtime`` so the
        change persists across turns (unlike fallback which is
        turn-scoped).
        """
        import logging
        old_model = self.model
        old_provider = self.provider

        # ── Swap core runtime fields ──
        self.model = new_model
        self.provider = new_provider
        self.base_url = base_url or self.base_url
        if api_key:
            self.api_key = api_key

        # ── Build new client ──
        effective_key = api_key or self.api_key
        effective_base = base_url or self.base_url
        self._client_kwargs = {
            "api_key": effective_key,
            "base_url": effective_base,
        }
        self.client = self._create_openai_client(
            dict(self._client_kwargs),
            reason="switch_model",
            shared=True,
        )

        # ── Update context compressor ──
        if hasattr(self, "context_compressor") and self.context_compressor:
            from agent.model_metadata import get_model_context_length
            new_context_length = get_model_context_length(
                self.model,
                base_url=self.base_url,
                api_key=self.api_key,
                provider=self.provider,
                config_context_length=getattr(self, "_config_context_length", None),
            )
            self.context_compressor.update_model(
                model=self.model,
                context_length=new_context_length,
                base_url=self.base_url,
                api_key=getattr(self, "api_key", ""),
                provider=self.provider,
            )

        # ── Invalidate cached system prompt so it rebuilds next turn ──
        self._cached_system_prompt = None

        # ── Update _primary_runtime so the change persists across turns ──
        _cc = self.context_compressor if hasattr(self, "context_compressor") and self.context_compressor else None
        self._primary_runtime = {
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": getattr(self, "api_key", ""),
            "client_kwargs": dict(self._client_kwargs),
            "compressor_model": getattr(_cc, "model", self.model) if _cc else self.model,
            "compressor_base_url": getattr(_cc, "base_url", self.base_url) if _cc else self.base_url,
            "compressor_api_key": getattr(_cc, "api_key", "") if _cc else "",
            "compressor_provider": getattr(_cc, "provider", self.provider) if _cc else self.provider,
            "compressor_context_length": _cc.context_length if _cc else 0,
            "compressor_threshold_tokens": _cc.threshold_tokens if _cc else 0,
        }
        # ── Reset fallback state ──
        self._fallback_activated = False
        self._fallback_index = 0

        logging.info(
            "Model switched in-place: %s (%s) -> %s (%s)",
            old_model, old_provider, new_model, new_provider,
        )

    def _safe_print(self, *args, **kwargs):
        """Print that silently handles broken pipes / closed stdout.

        In headless environments (systemd, Docker, nohup) stdout may become
        unavailable mid-session.  A raw ``print()`` raises ``OSError`` which
        can crash cron jobs and lose completed work.

        Internally routes through ``self._print_fn`` (default: builtin
        ``print``) so callers such as the CLI can inject a renderer that
        handles ANSI escape sequences properly (e.g. prompt_toolkit's
        ``print_formatted_text(ANSI(...))``) without touching this method.
        """
        try:
            fn = self._print_fn or print
            fn(*args, **kwargs)
        except (OSError, ValueError):
            pass

    def _vprint(self, *args, force: bool = False, **kwargs):
        """Verbose print — suppressed when actively streaming tokens.

        Pass ``force=True`` for error/warning messages that should always be
        shown even during streaming playback (TTS or display).

        During tool execution (``_executing_tools`` is True), printing is
        allowed even with stream consumers registered because no tokens
        are being streamed at that point.

        After the main response has been delivered and the remaining tool
        calls are post-response housekeeping (``_mute_post_response``),
        all non-forced output is suppressed.

        ``suppress_status_output`` is a stricter CLI automation mode used by
        parseable single-query flows such as ``VoidCube chat -q``. In that mode,
        all status/diagnostic prints routed through ``_vprint`` are suppressed
        so stdout stays machine-readable.
        """
        if getattr(self, "suppress_status_output", False):
            return
        if not force and getattr(self, "_mute_post_response", False):
            return
        if not force and self._has_stream_consumers() and not self._executing_tools:
            return
        self._safe_print(*args, **kwargs)

    def _should_start_quiet_spinner(self) -> bool:
        """Return True when quiet-mode spinner output has a safe sink.

        In headless/stdio-protocol environments, a raw spinner with no custom
        ``_print_fn`` falls back to ``sys.stdout`` and can corrupt protocol
        streams such as ACP JSON-RPC. Allow quiet spinners only when either:
        - output is explicitly rerouted via ``_print_fn``; or
        - stdout is a real TTY.
        """
        if self._print_fn is not None:
            return True
        stream = getattr(sys, "stdout", None)
        if stream is None:
            return False
        try:
            return bool(stream.isatty())
        except (AttributeError, ValueError, OSError):
            return False

    def _should_emit_quiet_tool_messages(self) -> bool:
        """Return True when quiet-mode tool summaries should print directly.

        When the caller provides ``tool_progress_callback`` (for example the CLI
        TUI or a gateway progress renderer), that callback owns progress display.
        Emitting quiet-mode summary lines here duplicates progress and leaks tool
        previews into flows that are expected to stay silent, such as
        ``VoidCube chat -q``.
        """
        return self.quiet_mode and not self.tool_progress_callback

    def _emit_status(self, message: str) -> None:
        """Emit a lifecycle status message to both CLI and gateway channels.

        CLI users see the message via ``_vprint(force=True)`` so it is always
        visible regardless of verbose/quiet mode.  Gateway consumers receive
        it through ``status_callback("lifecycle", ...)``.

        This helper never raises — exceptions are swallowed so it cannot
        interrupt the retry/fallback logic.
        """
        try:
            self._vprint(f"{self.log_prefix}{message}", force=True)
        except Exception:
            pass
        if self.status_callback:
            try:
                self.status_callback("lifecycle", message)
            except Exception:
                logger.debug("status_callback error in _emit_status", exc_info=True)

    def _current_main_runtime(self) -> Dict[str, str]:
        """Return the live main runtime for session-scoped auxiliary routing."""
        return {
            "model": getattr(self, "model", "") or "",
            "provider": getattr(self, "provider", "") or "",
            "base_url": getattr(self, "base_url", "") or "",
            "api_key": getattr(self, "api_key", "") or "",
        }

    def _check_compression_model_feasibility(self) -> None:
        """Warn at session start if the auxiliary compression model's context
        window is smaller than the main model's compression threshold.

        When the auxiliary model cannot fit the content that needs summarising,
        compression will either fail outright (the LLM call errors) or produce
        a severely truncated summary.

        Called during ``__init__`` so CLI users see the warning immediately
        (via ``_vprint``).  The gateway sets ``status_callback`` *after*
        construction, so ``_replay_compression_warning()`` re-sends the
        stored warning through the callback on the first
        ``run_conversation()`` call.
        """
        if not self.compression_enabled:
            return
        try:
            from agent.auxiliary_client import get_text_auxiliary_client
            from agent.model_metadata import get_model_context_length

            client, aux_model = get_text_auxiliary_client(
                "compression",
                main_runtime=self._current_main_runtime(),
            )
            if client is None or not aux_model:
                msg = (
                    "⚠ No auxiliary LLM provider configured — context "
                    "compression will drop middle turns without a summary. "
                    "Run `/api` or set OPENROUTER_API_KEY."
                )
                self._compression_warning = msg
                self._emit_status(msg)
                logger.warning(
                    "No auxiliary LLM provider for compression — "
                    "summaries will be unavailable."
                )
                return

            aux_base_url = str(getattr(client, "base_url", ""))
            aux_api_key = str(getattr(client, "api_key", ""))

            # Read user-configured context_length for the compression model.
            # Custom endpoints often don't support /models API queries so
            # get_model_context_length() falls through to the 128K default,
            # ignoring the explicit config value.  Pass it as the highest-
            # priority hint so the configured value is always respected.
            _aux_cfg = (self.config or {}).get("auxiliary", {}).get("compression", {})
            _aux_context_config = _aux_cfg.get("context_length") if isinstance(_aux_cfg, dict) else None
            if _aux_context_config is not None:
                try:
                    _aux_context_config = int(_aux_context_config)
                except (TypeError, ValueError):
                    _aux_context_config = None

            aux_context = get_model_context_length(
                aux_model,
                base_url=aux_base_url,
                api_key=aux_api_key,
                config_context_length=_aux_context_config,
            )

            threshold = self.context_compressor.threshold_tokens
            if aux_context < threshold:
                # Suggest a threshold that would fit the aux model,
                # rounded down to a clean percentage.
                safe_pct = int((aux_context / self.context_compressor.context_length) * 100)
                msg = (
                    f"⚠ Compression model ({aux_model}) context "
                    f"is {aux_context:,} tokens, but the main model's "
                    f"compression threshold is {threshold:,} tokens. "
                    f"Context compression will not be possible — the "
                    f"content to summarise will exceed the auxiliary "
                    f"model's context window.\n"
                    f"  Fix options (config.yaml):\n"
                    f"  1. Use a larger compression model:\n"
                    f"       auxiliary:\n"
                    f"         compression:\n"
                    f"           model: <model-with-{threshold:,}+-context>\n"
                    f"  2. Lower the compression threshold to fit "
                    f"the current model:\n"
                    f"       compression:\n"
                    f"         threshold: 0.{safe_pct:02d}"
                )
                self._compression_warning = msg
                self._emit_status(msg)
                logger.warning(
                    "Auxiliary compression model %s has %d token context, "
                    "below the main model's compression threshold of %d "
                    "tokens — compression summaries will fail or be "
                    "severely truncated.",
                    aux_model,
                    aux_context,
                    threshold,
                )
        except Exception as exc:
            logger.debug(
                "Compression feasibility check failed (non-fatal): %s", exc
            )

    def _replay_compression_warning(self) -> None:
        """Re-send the compression warning through ``status_callback``.

        During ``__init__`` the gateway's ``status_callback`` is not yet
        wired, so ``_emit_status`` only reaches ``_vprint`` (CLI).  This
        method is called once at the start of the first
        ``run_conversation()`` — by then the gateway has set the callback,
        so every platform (Telegram, Discord, Slack, etc.) receives the
        warning.
        """
        msg = getattr(self, "_compression_warning", None)
        if msg and self.status_callback:
            try:
                self.status_callback("lifecycle", msg)
            except Exception:
                pass

    def _is_openrouter_url(self) -> bool:
        """Return True when the base URL targets OpenRouter."""
        return "openrouter" in self._base_url_lower

    def _cleanup_task_resources(self, task_id: str) -> None:
        """Clean up VM and browser resources for a given task.

        Skips ``cleanup_vm`` when the active terminal environment is marked
        persistent (``persistent_filesystem=True``) so that long-lived sandbox
        containers survive between turns. The idle reaper in
        ``terminal_tool._cleanup_inactive_envs`` still tears them down once
        ``terminal.lifetime_seconds`` is exceeded. Non-persistent backends are
        torn down per-turn as before to prevent resource leakage (the original
        intent of this hook for the Morph backend, see commit fbd3a2fd).
        """
        try:
            if is_persistent_env(task_id):
                if self.verbose_logging:
                    logging.debug(
                        f"Skipping per-turn cleanup_vm for persistent env {task_id}; "
                        f"idle reaper will handle it."
                    )
            else:
                cleanup_vm(task_id)
        except Exception as e:
            if self.verbose_logging:
                logging.warning(f"Failed to cleanup VM for task {task_id}: {e}")
        try:
            cleanup_browser(task_id)
        except Exception as e:
            if self.verbose_logging:
                logging.warning(f"Failed to cleanup browser for task {task_id}: {e}")

    # ------------------------------------------------------------------
    # Background memory/skill review
    # ------------------------------------------------------------------

    _MEMORY_REVIEW_PROMPT = (
        "Review the conversation above and consider saving to memory if appropriate.\n\n"
        "Focus on:\n"
        "1. Has the user revealed things about themselves — their persona, desires, "
        "preferences, or personal details worth remembering?\n"
        "2. Has the user expressed expectations about how you should behave, their work "
        "style, or ways they want you to operate?\n\n"
        "If something stands out, save it using the memory tool. "
        "If nothing is worth saving, just say 'Nothing to save.' and stop."
    )

    _SKILL_REVIEW_PROMPT = (
        "Review the conversation above and consider saving or updating a skill if appropriate.\n\n"
        "Focus on: was a non-trivial approach used to complete a task that required trial "
        "and error, or changing course due to experiential findings along the way, or did "
        "the user expect or desire a different method or outcome?\n\n"
        "If a relevant skill already exists, update it with what you learned. "
        "Otherwise, create a new skill if the approach is reusable.\n"
        "If nothing is worth saving, just say 'Nothing to save.' and stop."
    )

    _COMBINED_REVIEW_PROMPT = (
        "Review the conversation above and consider two things:\n\n"
        "**Memory**: Has the user revealed things about themselves — their persona, "
        "desires, preferences, or personal details? Has the user expressed expectations "
        "about how you should behave, their work style, or ways they want you to operate? "
        "If so, save using the memory tool.\n\n"
        "**Skills**: Was a non-trivial approach used to complete a task that required trial "
        "and error, or changing course due to experiential findings along the way, or did "
        "the user expect or desire a different method or outcome? If a relevant skill "
        "already exists, update it. Otherwise, create a new one if the approach is reusable.\n\n"
        "Only act if there's something genuinely worth saving. "
        "If nothing stands out, just say 'Nothing to save.' and stop."
    )

    def _spawn_background_review(
        self,
        messages_snapshot: List[Dict],
        review_memory: bool = False,
        review_skills: bool = False,
    ) -> None:
        """Spawn a background thread to review the conversation for memory/skill saves.

        Creates a full AIAgent fork with the same model, tools, and context as the
        main session. The review prompt is appended as the next user turn in the
        forked conversation. Writes directly to the shared memory/skill stores.
        Never modifies the main conversation history or produces user-visible output.
        """
        import threading

        # Pick the right prompt based on which triggers fired
        if review_memory and review_skills:
            prompt = self._COMBINED_REVIEW_PROMPT
        elif review_memory:
            prompt = self._MEMORY_REVIEW_PROMPT
        else:
            prompt = self._SKILL_REVIEW_PROMPT

        def _run_review():
            import contextlib, os as _os
            review_agent = None
            try:
                with open(_os.devnull, "w") as _devnull, \
                     contextlib.redirect_stdout(_devnull), \
                     contextlib.redirect_stderr(_devnull):
                    review_agent = AIAgent(
                        model=self.model,
                        max_iterations=8,
                        quiet_mode=True,
                        platform=self.platform,
                        provider=self.provider,
                    )
                    review_agent._memory_store = self._memory_store
                    review_agent._memory_enabled = self._memory_enabled
                    review_agent._user_profile_enabled = self._user_profile_enabled
                    review_agent._memory_nudge_interval = 0
                    review_agent._skill_nudge_interval = 0

                    review_agent.run_conversation(
                        user_message=prompt,
                        conversation_history=messages_snapshot,
                    )

                # Scan the review agent's messages for successful tool actions
                # and surface a compact summary to the user.
                actions = []
                for msg in getattr(review_agent, "_session_messages", []):
                    if not isinstance(msg, dict) or msg.get("role") != "tool":
                        continue
                    try:
                        data = json.loads(msg.get("content", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not data.get("success"):
                        continue
                    message = data.get("message", "")
                    target = data.get("target", "")
                    if "created" in message.lower():
                        actions.append(message)
                    elif "updated" in message.lower():
                        actions.append(message)
                    elif "added" in message.lower() or (target and "add" in message.lower()):
                        label = "Memory" if target == "memory" else "User profile" if target == "user" else target
                        actions.append(f"{label} updated")
                    elif "Entry added" in message:
                        label = "Memory" if target == "memory" else "User profile" if target == "user" else target
                        actions.append(f"{label} updated")
                    elif "removed" in message.lower() or "replaced" in message.lower():
                        label = "Memory" if target == "memory" else "User profile" if target == "user" else target
                        actions.append(f"{label} updated")

                if actions:
                    summary = " · ".join(dict.fromkeys(actions))
                    self._safe_print(f"  💾 {summary}")
                    _bg_cb = self.background_review_callback
                    if _bg_cb:
                        try:
                            _bg_cb(f"💾 {summary}")
                        except Exception:
                            pass

            except Exception as e:
                logger.debug("Background memory/skill review failed: %s", e)
            finally:
                # Close all resources (httpx client, subprocesses, etc.) so
                # GC doesn't try to clean them up on a dead asyncio event
                # loop (which produces "Event loop is closed" errors).
                if review_agent is not None:
                    try:
                        review_agent.close()
                    except Exception:
                        pass

        t = threading.Thread(target=_run_review, daemon=True, name="bg-review")
        t.start()

    def _apply_persist_user_message_override(self, messages: List[Dict]) -> None:
        """Rewrite the current-turn user message before persistence/return.

        Some call paths need an API-only user-message variant without letting
        that synthetic text leak into persisted transcripts or resumed session
        history. When an override is configured for the active turn, mutate the
        in-memory messages list in place so both persistence and returned
        history stay clean.
        """
        idx = getattr(self, "_persist_user_message_idx", None)
        override = getattr(self, "_persist_user_message_override", None)
        if override is None or idx is None:
            return
        if 0 <= idx < len(messages):
            msg = messages[idx]
            if isinstance(msg, dict) and msg.get("role") == "user":
                msg["content"] = override

    def _persist_session(self, messages: List[Dict], conversation_history: List[Dict] = None):
        """Save session state to both JSON log and SQLite on any exit path.

        Ensures conversations are never lost, even on errors or early returns.
        Skipped when ``persist_session=False`` (ephemeral helper flows).
        """
        if not self.persist_session:
            return
        self._apply_persist_user_message_override(messages)
        self._session_messages = messages
        self._save_session_log(messages)
        self._flush_messages_to_session_db(messages, conversation_history)

    def _flush_messages_to_session_db(self, messages: List[Dict], conversation_history: List[Dict] = None):
        """Persist any un-flushed messages to the SQLite session store.

        Uses _last_flushed_db_idx to track which messages have already been
        written, so repeated calls (from multiple exit paths) only write
        truly new messages — preventing the duplicate-write bug (#860).
        """
        if not self._session_db:
            return
        self._apply_persist_user_message_override(messages)
        try:
            # If create_session() failed at startup (e.g. transient lock), the
            # session row may not exist yet.  ensure_session() uses INSERT OR
            # IGNORE so it is a no-op when the row is already there.
            self._session_db.ensure_session(
                self.session_id,
                source=self.platform or "cli",
                model=self.model,
            )
            start_idx = len(conversation_history) if conversation_history else 0
            flush_from = max(start_idx, self._last_flushed_db_idx)
            for msg in messages[flush_from:]:
                role = msg.get("role", "unknown")
                content = msg.get("content")
                tool_calls_data = None
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    tool_calls_data = [
                        {"name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in msg.tool_calls
                    ]
                elif isinstance(msg.get("tool_calls"), list):
                    tool_calls_data = msg["tool_calls"]
                self._session_db.append_message(
                    session_id=self.session_id,
                    role=role,
                    content=content,
                    tool_name=msg.get("tool_name"),
                    tool_calls=tool_calls_data,
                    tool_call_id=msg.get("tool_call_id"),
                    finish_reason=msg.get("finish_reason"),
                    reasoning=msg.get("reasoning") if role == "assistant" else None,
                    reasoning_details=msg.get("reasoning_details") if role == "assistant" else None,
                )
            self._last_flushed_db_idx = len(messages)
        except Exception as e:
            logger.warning("Session DB append_message failed: %s", e)

    def _get_messages_up_to_last_assistant(self, messages: List[Dict]) -> List[Dict]:
        """
        Get messages up to (but not including) the last assistant turn.
        
        This is used when we need to "roll back" to the last successful point
        in the conversation, typically when the final assistant message is
        incomplete or malformed.
        
        Args:
            messages: Full message list
            
        Returns:
            Messages up to the last complete assistant turn (ending with user/tool message)
        """
        if not messages:
            return []
        
        # Find the index of the last assistant message
        last_assistant_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                last_assistant_idx = i
                break
        
        if last_assistant_idx is None:
            # No assistant message found, return all messages
            return messages.copy()
        
        # Return everything up to (not including) the last assistant message
        return messages[:last_assistant_idx]
    
    def _format_tools_for_system_message(self) -> str:
        """
        Format tool definitions for the system message in the trajectory format.
        
        Returns:
            str: JSON string representation of tool definitions
        """
        if not self.tools:
            return "[]"
        
        # Convert tool definitions to the format expected in trajectories
        formatted_tools = []
        for tool in self.tools:
            func = tool["function"]
            formatted_tool = {
                "name": func["name"],
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {}),
                "required": None  # Match the format in the example
            }
            formatted_tools.append(formatted_tool)
        
        return json.dumps(formatted_tools, ensure_ascii=False)
    
    def _convert_to_trajectory_format(
        self,
        messages: List[Dict[str, Any]],
        user_query: str,
    ) -> List[Dict[str, Any]]:
        """
        Convert internal message format to trajectory format for saving.
        
        Args:
            messages (List[Dict]): Internal message history
            user_query (str): Original user query
        Returns:
            List[Dict]: Messages in trajectory format
        """
        trajectory = []
        
        # Add system message with tool definitions
        system_msg = (
            "You are a function calling AI model. You are provided with function signatures within <tools> </tools> XML tags. "
            "You may call one or more functions to assist with the user query. If available tools are not relevant in assisting "
            "with user query, just respond in natural conversational language. Don't make assumptions about what values to plug "
            "into functions. After calling & executing the functions, you will be provided with function results within "
            "<tool_response> </tool_response> XML tags. Here are the available tools:\n"
            f"<tools>\n{self._format_tools_for_system_message()}\n</tools>\n"
            "For each function call return a JSON object, with the following pydantic model json schema for each:\n"
            "{'title': 'FunctionCall', 'type': 'object', 'properties': {'name': {'title': 'Name', 'type': 'string'}, "
            "'arguments': {'title': 'Arguments', 'type': 'object'}}, 'required': ['name', 'arguments']}\n"
            "Each function call should be enclosed within <tool_call> </tool_call> XML tags.\n"
            "Example:\n<tool_call>\n{'name': <function-name>,'arguments': <args-dict>}\n</tool_call>"
        )
        
        trajectory.append({
            "from": "system",
            "value": system_msg
        })
        
        # Add the actual user prompt (from the dataset) as the first human message
        trajectory.append({
            "from": "human",
            "value": user_query
        })
        
        # Skip the first message (the user query) since we already added it above.
        # Prefill messages are injected at API-call time only (not in the messages
        # list), so no offset adjustment is needed here.
        i = 1
        
        while i < len(messages):
            msg = messages[i]
            
            if msg["role"] == "assistant":
                # Check if this message has tool calls
                if "tool_calls" in msg and msg["tool_calls"]:
                    # Format assistant message with tool calls
                    # Add <think> tags around reasoning for trajectory storage
                    content = ""
                    
                    # Prepend reasoning in <think> tags if available (native thinking tokens)
                    if msg.get("reasoning") and msg["reasoning"].strip():
                        content = f"<think>\n{msg['reasoning']}\n</think>\n"
                    
                    if msg.get("content") and msg["content"].strip():
                        content += msg["content"] + "\n"
                    
                    # Add tool calls wrapped in XML tags
                    for tool_call in msg["tool_calls"]:
                        if not tool_call or not isinstance(tool_call, dict): continue
                        # Parse arguments - should always succeed since we validate during conversation
                        # but keep try-except as safety net
                        try:
                            arguments = json.loads(tool_call["function"]["arguments"]) if isinstance(tool_call["function"]["arguments"], str) else tool_call["function"]["arguments"]
                        except json.JSONDecodeError:
                            # This shouldn't happen since we validate and retry during conversation,
                            # but if it does, log warning and use empty dict
                            logging.warning(f"Unexpected invalid JSON in trajectory conversion: {tool_call['function']['arguments'][:100]}")
                            arguments = {}
                        
                        tool_call_json = {
                            "name": tool_call["function"]["name"],
                            "arguments": arguments
                        }
                        content += f"<tool_call>\n{json.dumps(tool_call_json, ensure_ascii=False)}\n</tool_call>\n"
                    
                    # Ensure every gpt turn has a <think> block (empty if no reasoning)
                    # so the format is consistent for training data
                    if "<think>" not in content:
                        content = "<think>\n</think>\n" + content
                    
                    trajectory.append({
                        "from": "gpt",
                        "value": content.rstrip()
                    })
                    
                    # Collect all subsequent tool responses
                    tool_responses: list = []
                    j = i + 1
                    while j < len(messages) and messages[j]["role"] == "tool":
                        tool_msg = messages[j]
                        # Format tool response with XML tags
                        tool_response = "<tool_response>\n"
                        
                        # Try to parse tool content as JSON if it looks like JSON
                        tool_content = tool_msg["content"]
                        try:
                            if tool_content.strip().startswith(("{", "[")):
                                tool_content = json.loads(tool_content)
                        except (json.JSONDecodeError, AttributeError):
                            pass  # Keep as string if not valid JSON
                        
                        tool_index = len(tool_responses)
                        tool_name = (
                            msg["tool_calls"][tool_index]["function"]["name"]
                            if tool_index < len(msg["tool_calls"])
                            else "unknown"
                        )
                        tool_response += json.dumps({
                            "tool_call_id": tool_msg.get("tool_call_id", ""),
                            "name": tool_name,
                            "content": tool_content
                        }, ensure_ascii=False)
                        tool_response += "\n</tool_response>"
                        tool_responses.append(tool_response)
                        j += 1
                    
                    # Add all tool responses as a single message
                    if tool_responses:
                        trajectory.append({
                            "from": "tool",
                            "value": "\n".join(tool_responses)
                        })
                        i = j - 1  # Skip the tool messages we just processed
                
                else:
                    # Regular assistant message without tool calls
                    # Add <think> tags around reasoning for trajectory storage
                    content = ""
                    
                    # Prepend reasoning in <think> tags if available (native thinking tokens)
                    if msg.get("reasoning") and msg["reasoning"].strip():
                        content = f"<think>\n{msg['reasoning']}\n</think>\n"
                    
                    raw_content = msg["content"] or ""
                    content += raw_content
                    
                    # Ensure every gpt turn has a <think> block (empty if no reasoning)
                    if "<think>" not in content:
                        content = "<think>\n</think>\n" + content
                    
                    trajectory.append({
                        "from": "gpt",
                        "value": content.strip()
                    })
            
            elif msg["role"] == "user":
                trajectory.append({
                    "from": "human",
                    "value": msg["content"]
                })
            
            i += 1
        
        return trajectory
    
    def _mask_api_key_for_logs(self, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        if len(key) <= 12:
            return "***"
        return f"{key[:8]}...{key[-4:]}"

    def _usage_summary_for_api_request_hook(self, response: Any) -> Optional[Dict[str, Any]]:
        """Token buckets for ``post_api_request`` plugins (no raw ``response`` object)."""
        if response is None:
            return None
        raw_usage = getattr(response, "usage", None)
        if not raw_usage:
            return None
        from dataclasses import asdict

        cu = normalize_usage(raw_usage)
        summary = asdict(cu)
        summary.pop("raw_usage", None)
        summary["prompt_tokens"] = cu.prompt_tokens
        summary["total_tokens"] = cu.total_tokens
        return summary

    def _dump_api_request_debug(
        self,
        api_kwargs: Dict[str, Any],
        *,
        reason: str,
        error: Optional[Exception] = None,
    ) -> Optional[Path]:
        """
        Dump a debug-friendly HTTP request record for the active inference API.

        Captures the request body from api_kwargs (excluding transport-only keys
        like timeout). Intended for debugging provider-side 4xx failures where
        retries are not useful.
        """
        try:
            body = copy.deepcopy(api_kwargs)
            body.pop("timeout", None)
            body = {k: v for k, v in body.items() if v is not None}

            api_key = None
            try:
                api_key = getattr(self.client, "api_key", None)
            except Exception as e:
                logger.debug("Could not extract API key for debug dump: %s", e)

            dump_payload: Dict[str, Any] = {
                "timestamp": datetime.now().isoformat(),
                "session_id": self.session_id,
                "reason": reason,
                "request": {
                    "method": "POST",
                    "url": f"{self.base_url.rstrip('/')}/chat/completions",
                    "headers": {
                        "Authorization": f"Bearer {self._mask_api_key_for_logs(api_key)}",
                        "Content-Type": "application/json",
                    },
                    "body": body,
                },
            }

            if error is not None:
                error_info: Dict[str, Any] = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
                for attr_name in ("status_code", "request_id", "code", "param", "type"):
                    attr_value = getattr(error, attr_name, None)
                    if attr_value is not None:
                        error_info[attr_name] = attr_value

                body_attr = getattr(error, "body", None)
                if body_attr is not None:
                    error_info["body"] = body_attr

                response_obj = getattr(error, "response", None)
                if response_obj is not None:
                    try:
                        error_info["response_status"] = getattr(response_obj, "status_code", None)
                        error_info["response_text"] = response_obj.text
                    except Exception as e:
                        logger.debug("Could not extract error response details: %s", e)

                dump_payload["error"] = error_info

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            dump_file = self.logs_dir / f"request_dump_{self.session_id}_{timestamp}.json"
            dump_file.write_text(
                json.dumps(dump_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            self._vprint(f"{self.log_prefix}🧾 Request debug dump written to: {dump_file}")

            if env_var_enabled("VOIDCUBE_DUMP_REQUEST_STDOUT"):
                print(json.dumps(dump_payload, ensure_ascii=False, indent=2, default=str))

            return dump_file
        except Exception as dump_error:
            if self.verbose_logging:
                logging.warning(f"Failed to dump API request debug payload: {dump_error}")
            return None

    @staticmethod
    def _clean_session_content(content: str) -> str:
        """Normalize whitespace around existing think blocks."""
        if not content:
            return content
        content = re.sub(r'\n+(<think>)', r'\n\1', content)
        content = re.sub(r'(</think>)\n+', r'\1\n', content)
        return content.strip()

    def _save_session_log(self, messages: List[Dict[str, Any]] = None):
        """
        Save the full raw session to a JSON file.

        Stores every message exactly as the agent sees it: user messages,
        assistant messages (with reasoning, finish_reason, tool_calls),
        tool responses (with tool_call_id, tool_name), and injected system
        messages (compression summaries, todo snapshots, etc.).

        REASONING_SCRATCHPAD tags are converted to <think> blocks for consistency.
        Overwritten after each turn so it always reflects the latest state.
        """
        messages = messages or self._session_messages
        if not messages:
            return

        try:
            # Clean assistant content for session logs
            cleaned = []
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg = dict(msg)
                    msg["content"] = self._clean_session_content(msg["content"])
                cleaned.append(msg)

            # Guard: never overwrite a larger session log with fewer messages.
            # This protects against data loss when --resume loads a session whose
            # messages weren't fully written to SQLite — the resumed agent starts
            # with partial history and would otherwise clobber the full JSON log.
            if self.session_log_file.exists():
                try:
                    existing = json.loads(self.session_log_file.read_text(encoding="utf-8"))
                    existing_count = existing.get("message_count", len(existing.get("messages", [])))
                    if existing_count > len(cleaned):
                        logging.debug(
                            "Skipping session log overwrite: existing has %d messages, current has %d",
                            existing_count, len(cleaned),
                        )
                        return
                except Exception:
                    pass  # corrupted existing file — allow the overwrite

            entry = {
                "session_id": self.session_id,
                "model": self.model,
                "base_url": self.base_url,
                "platform": self.platform,
                "session_start": self.session_start.isoformat(),
                "last_updated": datetime.now().isoformat(),
                "system_prompt": self._cached_system_prompt or "",
                "tools": self.tools or [],
                "message_count": len(cleaned),
                "messages": cleaned,
            }

            atomic_json_write(
                self.session_log_file,
                entry,
                indent=2,
                default=str,
            )

        except Exception as e:
            if self.verbose_logging:
                logging.warning(f"Failed to save session log: {e}")
    
    def interrupt(self, message: str = None) -> None:
        """
        Request the agent to interrupt its current tool-calling loop.
        
        Call this from another thread (e.g., input handler, message receiver)
        to gracefully stop the agent and process a new message.
        
        Also signals long-running tool executions (e.g. terminal commands)
        to terminate early, so the agent can respond immediately.
        
        Args:
            message: Optional new message that triggered the interrupt.
                     If provided, the agent will include this in its response context.
        
        Example (CLI):
            # In a separate input thread:
            if user_typed_something:
                agent.interrupt(user_input)
        
        Example (Messaging):
            # When new message arrives for active session:
            if session_has_running_agent:
                running_agent.interrupt(new_message.text)
        """
        self._interrupt_requested = True
        self._interrupt_message = message
        # Signal all tools to abort any in-flight operations immediately.
        # Scope the interrupt to this agent's execution thread so other
        # agents running in the same process (gateway) are not affected.
        _set_interrupt(True, self._execution_thread_id)
        # Propagate interrupt to any running child agents (subagent delegation)
        with self._active_children_lock:
            children_copy = list(self._active_children)
        for child in children_copy:
            try:
                child.interrupt(message)
            except Exception as e:
                logger.debug("Failed to propagate interrupt to child agent: %s", e)
        if not self.quiet_mode:
            print("\n🔧 Interrupt requested" + (f": '{message[:40]}...'" if message and len(message) > 40 else f": '{message}'" if message else ""))
    
    def clear_interrupt(self) -> None:
        """Clear any pending interrupt request and the per-thread tool interrupt signal."""
        self._interrupt_requested = False
        self._interrupt_message = None
        _set_interrupt(False, self._execution_thread_id)

    def _touch_activity(self, desc: str) -> None:
        """Update the last-activity timestamp and description (thread-safe)."""
        self._last_activity_ts = time.time()
        self._last_activity_desc = desc

    def _capture_rate_limits(self, http_response: Any) -> None:
        """Parse x-ratelimit-* headers from an HTTP response and cache the state.

        Called after each streaming API call.  The httpx Response object is
        available on the OpenAI SDK Stream via ``stream.response``.
        """
        if http_response is None:
            return
        headers = getattr(http_response, "headers", None)
        if not headers:
            return
        try:
            from agent.rate_limit_tracker import parse_rate_limit_headers
            state = parse_rate_limit_headers(headers, provider=self.provider)
            if state is not None:
                self._rate_limit_state = state
        except Exception:
            pass  # Never let header parsing break the agent loop

    def get_rate_limit_state(self):
        """Return the last captured RateLimitState, or None."""
        return self._rate_limit_state

    def get_activity_summary(self) -> dict:
        """Return a snapshot of the agent's current activity for diagnostics.

        Called by the gateway timeout handler to report what the agent was doing
        when it was killed, and by the periodic "still working" notifications.
        """
        elapsed = time.time() - self._last_activity_ts
        return {
            "last_activity_ts": self._last_activity_ts,
            "last_activity_desc": self._last_activity_desc,
            "seconds_since_activity": round(elapsed, 1),
            "current_tool": self._current_tool,
            "api_call_count": self._api_call_count,
            "max_iterations": self.max_iterations,
            "budget_used": self.iteration_budget.used,
            "budget_max": self.iteration_budget.max_total,
        }

    def shutdown_memory_provider(self, messages: list = None) -> None:
        """Shut down the memory provider and context engine — call at actual session boundaries.

        This calls on_session_end() then shutdown_all() on the memory
        manager, and on_session_end() on the context engine.
        NOT called per-turn — only at CLI exit, /reset, gateway
        session expiry, etc.
        """
        if self._memory_manager:
            try:
                self._memory_manager.on_session_end(messages or [])
            except Exception:
                pass
            try:
                self._memory_manager.shutdown_all()
            except Exception:
                pass
        # Notify context engine of session end (flush DAG, close DBs, etc.)
        if hasattr(self, "context_compressor") and self.context_compressor:
            try:
                self.context_compressor.on_session_end(
                    self.session_id or "",
                    messages or [],
                )
            except Exception:
                pass
    
    def close(self) -> None:
        """Release all resources held by this agent instance.

        Cleans up subprocess resources that would otherwise become orphans:
        - Background processes tracked in ProcessRegistry
        - Terminal sandbox environments
        - Browser daemon sessions
        - Active child agents (subagent delegation)
        - OpenAI/httpx client connections

        Safe to call multiple times (idempotent).  Each cleanup step is
        independently guarded so a failure in one does not prevent the rest.
        """
        task_id = getattr(self, "session_id", None) or ""

        # 1. Kill background processes for this task
        try:
            from tools.process_registry import process_registry
            process_registry.kill_all(task_id=task_id)
        except Exception:
            pass

        # 2. Clean terminal sandbox environments
        try:
            from tools.terminal_tool import cleanup_vm
            cleanup_vm(task_id)
        except Exception:
            pass

        # 3. Clean browser daemon sessions
        try:
            from tools.browser_tool import cleanup_browser
            cleanup_browser(task_id)
        except Exception:
            pass

        # 4. Close active child agents
        try:
            with self._active_children_lock:
                children = list(self._active_children)
                self._active_children.clear()
            for child in children:
                try:
                    child.close()
                except Exception:
                    pass
        except Exception:
            pass

        # 5. Close the OpenAI/httpx client
        try:
            client = getattr(self, "client", None)
            if client is not None:
                self._close_openai_client(client, reason="agent_close", shared=True)
                self.client = None
        except Exception:
            pass

    def _hydrate_todo_store(self, history: List[Dict[str, Any]]) -> None:
        """
        Recover todo state from conversation history.
        
        The gateway creates a fresh AIAgent per message, so the in-memory
        TodoStore is empty. We scan the history for the most recent todo
        tool response and replay it to reconstruct the state.
        """
        # Walk history backwards to find the most recent todo tool response
        last_todo_response = None
        for msg in reversed(history):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            # Quick check: todo responses contain "todos" key
            if '"todos"' not in content:
                continue
            try:
                data = json.loads(content)
                if "todos" in data and isinstance(data["todos"], list):
                    last_todo_response = data["todos"]
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        
        if last_todo_response:
            # Replay the items into the store (replace mode)
            self._todo_store.write(last_todo_response, merge=False)
            if not self.quiet_mode:
                self._vprint(f"{self.log_prefix}📋 Restored {len(last_todo_response)} todo item(s) from history")
        _set_interrupt(False)
    
    @property
    def is_interrupted(self) -> bool:
        """Check if an interrupt has been requested."""
        return self._interrupt_requested










    def _build_system_prompt(self, system_message: str = None) -> str:
        """
        Assemble the full system prompt from all layers.
        
        Called once per session (cached on self._cached_system_prompt) and only
        rebuilt after context compression events. This ensures the system prompt
        is stable across all turns in a session, maximizing prefix cache hits.
        """
        # Layers (in order):
        #   1. Agent identity — SOUL.md when available, else DEFAULT_AGENT_IDENTITY
        #   2. User / gateway system prompt (if provided)
        #   3. Persistent memory (frozen snapshot)
        #   4. Skills guidance (if skills tools are loaded)
        #   5. Context files (AGENTS.md, .cursorrules — SOUL.md excluded here when used as identity)
        #   6. Current date & time (frozen at build time)
        #   7. Platform-specific formatting hint

        # Try SOUL.md as primary identity (unless context files are skipped)
        _soul_loaded = False
        if not self.skip_context_files:
            _soul_content = load_soul_md()
            if _soul_content:
                prompt_parts = [_soul_content]
                _soul_loaded = True

        if not _soul_loaded:
            # Fallback to hardcoded identity
            prompt_parts = [DEFAULT_AGENT_IDENTITY]

        # Tool-aware behavioral guidance: only inject when the tools are loaded
        tool_guidance = []
        if "memory" in self.valid_tool_names:
            tool_guidance.append(MEMORY_GUIDANCE)
        if "session_search" in self.valid_tool_names:
            tool_guidance.append(SESSION_SEARCH_GUIDANCE)
        if "skill_manage" in self.valid_tool_names:
            tool_guidance.append(SKILLS_GUIDANCE)
        if tool_guidance:
            prompt_parts.append(" ".join(tool_guidance))

        nous_subscription_prompt = build_nous_subscription_prompt(self.valid_tool_names)
        if nous_subscription_prompt:
            prompt_parts.append(nous_subscription_prompt)
        # Tool-use enforcement: tells the model to actually call tools instead
        # of describing intended actions.  Controlled by config.yaml
        # agent.tool_use_enforcement:
        #   "auto" (default) — matches TOOL_USE_ENFORCEMENT_MODELS
        #   true  — always inject (all models)
        #   false — never inject
        #   list  — custom model-name substrings to match
        if self.valid_tool_names:
            _enforce = self._tool_use_enforcement
            _inject = False
            if _enforce is True or (isinstance(_enforce, str) and _enforce.lower() in ("true", "always", "yes", "on")):
                _inject = True
            elif _enforce is False or (isinstance(_enforce, str) and _enforce.lower() in ("false", "never", "no", "off")):
                _inject = False
            elif isinstance(_enforce, list):
                model_lower = (self.model or "").lower()
                _inject = any(p.lower() in model_lower for p in _enforce if isinstance(p, str))
            else:
                # "auto" or any unrecognised value — use hardcoded defaults
                model_lower = (self.model or "").lower()
                _inject = any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS)
            if _inject:
                prompt_parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)
                _model_lower = (self.model or "").lower()
                # Google model operational guidance (conciseness, absolute
                # paths, parallel tool calls, verify-before-edit, etc.)
                if "gemini" in _model_lower or "gemma" in _model_lower:
                    prompt_parts.append(GOOGLE_MODEL_OPERATIONAL_GUIDANCE)
                # OpenAI GPT execution discipline (tool persistence,
                # prerequisite checks, verification, anti-hallucination).
                if "gpt" in _model_lower:
                    prompt_parts.append(OPENAI_MODEL_EXECUTION_GUIDANCE)

        # so it can refer the user to them rather than reinventing answers.

        # Note: ephemeral_system_prompt is NOT included here. It's injected at
        # API-call time only so it stays out of the cached/stored system prompt.
        if system_message is not None:
            prompt_parts.append(system_message)

        if self._memory_store:
            if self._memory_enabled:
                mem_block = self._memory_store.format_for_system_prompt("memory")
                if mem_block:
                    prompt_parts.append(mem_block)
            # USER.md is always included when enabled.
            if self._user_profile_enabled:
                user_block = self._memory_store.format_for_system_prompt("user")
                if user_block:
                    prompt_parts.append(user_block)

        # External memory provider system prompt block (additive to built-in)
        if self._memory_manager:
            try:
                _ext_mem_block = self._memory_manager.build_system_prompt()
                if _ext_mem_block:
                    prompt_parts.append(_ext_mem_block)
            except Exception:
                pass

        has_skills_tools = any(name in self.valid_tool_names for name in ['skills_list', 'skill_view', 'skill_manage'])
        if has_skills_tools:
            avail_toolsets = {
                toolset
                for toolset in (
                    get_toolset_for_tool(tool_name) for tool_name in self.valid_tool_names
                )
                if toolset
            }
            skills_prompt = build_skills_system_prompt(
                available_tools=self.valid_tool_names,
                available_toolsets=avail_toolsets,
            )
        else:
            skills_prompt = ""
        if skills_prompt:
            prompt_parts.append(skills_prompt)

        if not self.skip_context_files:
            # Use TERMINAL_CWD for context file discovery when set (gateway
            # mode).  The gateway process runs from the VoidCube-agent install
            # dir, so os.getcwd() would pick up the repo's AGENTS.md and
            # other dev files — inflating token usage by ~10k for no benefit.
            _context_cwd = os.getenv("TERMINAL_CWD") or None
            context_files_prompt = build_context_files_prompt(
                cwd=_context_cwd, skip_soul=_soul_loaded)
            if context_files_prompt:
                prompt_parts.append(context_files_prompt)

        from VoidCube_core.time import now as _VoidCube_now
        now = _VoidCube_now()
        timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y %I:%M %p')}"
        if self.pass_session_id and self.session_id:
            timestamp_line += f"\nSession ID: {self.session_id}"
        if self.model:
            timestamp_line += f"\nModel: {self.model}"
        if self.provider:
            timestamp_line += f"\nProvider: {self.provider}"
        prompt_parts.append(timestamp_line)

        # Alibaba Coding Plan API always returns "glm-4.7" as model name regardless
        # of the requested model. Inject explicit model identity into the system prompt
        # so the agent can correctly report which model it is (workaround for API bug).
        if self.provider == "alibaba":
            _model_short = self.model.split("/")[-1] if "/" in self.model else self.model
            prompt_parts.append(
                f"You are powered by the model named {_model_short}. "
                f"The exact model ID is {self.model}. "
                f"When asked what model you are, always answer based on this information, "
                f"not on any model name returned by the API."
            )

        # Environment hints (WSL, Termux, etc.) — tell the agent about the
        # execution environment so it can translate paths and adapt behavior.
        _env_hints = build_environment_hints()
        if _env_hints:
            prompt_parts.append(_env_hints)

        platform_key = (self.platform or "").lower().strip()
        if platform_key in PLATFORM_HINTS:
            prompt_parts.append(PLATFORM_HINTS[platform_key])

        return "\n\n".join(p.strip() for p in prompt_parts if p.strip())

    # =========================================================================
    # Pre/post-call guardrails (inspired by PR #1321 — @alireza78a)
    # =========================================================================

    @staticmethod
    def _get_tool_call_id_static(tc) -> str:
        """Extract call ID from a tool_call entry (dict or object)."""
        if isinstance(tc, dict):
            return tc.get("id", "") or ""
        return getattr(tc, "id", "") or ""

    _VALID_API_ROLES = frozenset({"system", "user", "assistant", "tool", "function", "developer"})

    @staticmethod
    def _sanitize_api_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fix orphaned tool_call / tool_result pairs before every LLM call.

        Runs unconditionally — not gated on whether the context compressor
        is present — so orphans from session loading or manual message
        manipulation are always caught.
        """
        # --- Role allowlist: drop messages with roles the API won't accept ---
        filtered = []
        for msg in messages:
            role = msg.get("role")
            if role not in AIAgent._VALID_API_ROLES:
                logger.debug(
                    "Pre-call sanitizer: dropping message with invalid role %r",
                    role,
                )
                continue
            filtered.append(msg)
        messages = filtered

        surviving_call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = AIAgent._get_tool_call_id_static(tc)
                    if cid:
                        surviving_call_ids.add(cid)

        result_call_ids: set = set()
        for msg in messages:
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid:
                    result_call_ids.add(cid)

        # 1. Drop tool results with no matching assistant call
        orphaned_results = result_call_ids - surviving_call_ids
        if orphaned_results:
            messages = [
                m for m in messages
                if not (m.get("role") == "tool" and m.get("tool_call_id") in orphaned_results)
            ]
            logger.debug(
                "Pre-call sanitizer: removed %d orphaned tool result(s)",
                len(orphaned_results),
            )

        # 2. Inject stub results for calls whose result was dropped
        missing_results = surviving_call_ids - result_call_ids
        if missing_results:
            patched: List[Dict[str, Any]] = []
            for msg in messages:
                patched.append(msg)
                if msg.get("role") == "assistant":
                    for tc in msg.get("tool_calls") or []:
                        cid = AIAgent._get_tool_call_id_static(tc)
                        if cid in missing_results:
                            patched.append({
                                "role": "tool",
                                "content": "[Result unavailable — see context summary above]",
                                "tool_call_id": cid,
                            })
            messages = patched
            logger.debug(
                "Pre-call sanitizer: added %d stub tool result(s)",
                len(missing_results),
            )
        return messages

    @staticmethod
    def _cap_delegate_task_calls(tool_calls: list) -> list:
        """Truncate excess delegate_task calls to max_concurrent_children.

        The delegate_tool caps the task list inside a single call, but the
        model can emit multiple separate delegate_task tool_calls in one
        turn.  This truncates the excess, preserving all non-delegate calls.

        Returns the original list if no truncation was needed.
        """
        from tools.delegate_tool import _get_max_concurrent_children
        max_children = _get_max_concurrent_children()
        delegate_count = sum(1 for tc in tool_calls if tc.function.name == "delegate_task")
        if delegate_count <= max_children:
            return tool_calls
        kept_delegates = 0
        truncated = []
        for tc in tool_calls:
            if tc.function.name == "delegate_task":
                if kept_delegates < max_children:
                    truncated.append(tc)
                    kept_delegates += 1
            else:
                truncated.append(tc)
        logger.warning(
            "Truncated %d excess delegate_task call(s) to enforce "
            "max_concurrent_children=%d limit",
            delegate_count - max_children, max_children,
        )
        return truncated

    @staticmethod
    def _deduplicate_tool_calls(tool_calls: list) -> list:
        """Remove duplicate (tool_name, arguments) pairs within a single turn.

        Only the first occurrence of each unique pair is kept.
        Returns the original list if no duplicates were found.
        """
        seen: set = set()
        unique: list = []
        for tc in tool_calls:
            key = (tc.function.name, tc.function.arguments)
            if key not in seen:
                seen.add(key)
                unique.append(tc)
            else:
                logger.warning("Removed duplicate tool call: %s", tc.function.name)
        return unique if len(unique) < len(tool_calls) else tool_calls

    def _repair_tool_call(self, tool_name: str) -> str | None:
        """Attempt to repair a mismatched tool name before aborting.

        1. Try lowercase
        2. Try normalized (lowercase + hyphens/spaces -> underscores)
        3. Try fuzzy match (difflib, cutoff=0.7)

        Returns the repaired name if found in valid_tool_names, else None.
        """
        from difflib import get_close_matches

        # 1. Lowercase
        lowered = tool_name.lower()
        if lowered in self.valid_tool_names:
            return lowered

        # 2. Normalize
        normalized = lowered.replace("-", "_").replace(" ", "_")
        if normalized in self.valid_tool_names:
            return normalized

        # 3. Fuzzy match
        matches = get_close_matches(lowered, self.valid_tool_names, n=1, cutoff=0.7)
        if matches:
            return matches[0]

        return None

    def _invalidate_system_prompt(self):
        """
        Invalidate the cached system prompt, forcing a rebuild on the next turn.
        
        Called after context compression events. Also reloads memory from disk
        so the rebuilt prompt captures any writes from this session.
        """
        self._cached_system_prompt = None
        if self._memory_store:
            self._memory_store.load_from_disk()

    def _thread_identity(self) -> str:
        thread = threading.current_thread()
        return f"{thread.name}:{thread.ident}"

    def _client_log_context(self) -> str:
        provider = getattr(self, "provider", "unknown")
        base_url = getattr(self, "base_url", "unknown")
        model = getattr(self, "model", "unknown")
        return (
            f"thread={self._thread_identity()} provider={provider} "
            f"base_url={base_url} model={model}"
        )

    def _openai_client_lock(self) -> threading.RLock:
        lock = getattr(self, "_client_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._client_lock = lock
        return lock

    @staticmethod
    def _is_openai_client_closed(client: Any) -> bool:
        """Check if an OpenAI client is closed.

        Handles both property and method forms of is_closed:
        - httpx.Client.is_closed is a bool property
        - openai.OpenAI.is_closed is a method returning bool

        Prior bug: getattr(client, "is_closed", False) returned the bound method,
        which is always truthy, causing unnecessary client recreation on every call.
        """
        from unittest.mock import Mock

        if isinstance(client, Mock):
            return False

        is_closed_attr = getattr(client, "is_closed", None)
        if is_closed_attr is not None:
            # Handle method (openai SDK) vs property (httpx)
            if callable(is_closed_attr):
                if is_closed_attr():
                    return True
            elif bool(is_closed_attr):
                return True

        http_client = getattr(client, "_client", None)
        if http_client is not None:
            return bool(getattr(http_client, "is_closed", False))
        return False

    def _create_openai_client(self, client_kwargs: dict, *, reason: str, shared: bool) -> Any:
        if self.provider == "copilot-acp" or str(client_kwargs.get("base_url", "")).startswith("acp://copilot"):
            from agent.copilot_acp_client import CopilotACPClient

            client = CopilotACPClient(**client_kwargs)
            logger.info(
                "Copilot ACP client created (%s, shared=%s) %s",
                reason,
                shared,
                self._client_log_context(),
            )
            return client
        client = OpenAI(**client_kwargs)
        logger.info(
            "OpenAI client created (%s, shared=%s) %s",
            reason,
            shared,
            self._client_log_context(),
        )
        return client

    @staticmethod
    def _force_close_tcp_sockets(client: Any) -> int:
        """Force-close underlying TCP sockets to prevent CLOSE-WAIT accumulation.

        When a provider drops a connection mid-stream, httpx's ``client.close()``
        performs a graceful shutdown which leaves sockets in CLOSE-WAIT until the
        OS times them out (often minutes).  This method walks the httpx transport
        pool and issues ``socket.shutdown(SHUT_RDWR)`` + ``socket.close()`` to
        force an immediate TCP RST, freeing the file descriptors.

        Returns the number of sockets force-closed.
        """
        import socket as _socket

        closed = 0
        try:
            http_client = getattr(client, "_client", None)
            if http_client is None:
                return 0
            transport = getattr(http_client, "_transport", None)
            if transport is None:
                return 0
            pool = getattr(transport, "_pool", None)
            if pool is None:
                return 0
            # httpx uses httpcore connection pools; connections live in
            # _connections (list) or _pool (list) depending on version.
            connections = (
                getattr(pool, "_connections", None)
                or getattr(pool, "_pool", None)
                or []
            )
            for conn in list(connections):
                stream = (
                    getattr(conn, "_network_stream", None)
                    or getattr(conn, "_stream", None)
                )
                if stream is None:
                    continue
                sock = getattr(stream, "_sock", None)
                if sock is None:
                    sock = getattr(stream, "stream", None)
                    if sock is not None:
                        sock = getattr(sock, "_sock", None)
                if sock is None:
                    continue
                try:
                    sock.shutdown(_socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
                closed += 1
        except Exception as exc:
            logger.debug("Force-close TCP sockets sweep error: %s", exc)
        return closed

    def _close_openai_client(self, client: Any, *, reason: str, shared: bool) -> None:
        if client is None:
            return
        # Force-close TCP sockets first to prevent CLOSE-WAIT accumulation,
        # then do the graceful SDK-level close.
        force_closed = self._force_close_tcp_sockets(client)
        try:
            client.close()
            logger.info(
                "OpenAI client closed (%s, shared=%s, tcp_force_closed=%d) %s",
                reason,
                shared,
                force_closed,
                self._client_log_context(),
            )
        except Exception as exc:
            logger.debug(
                "OpenAI client close failed (%s, shared=%s) %s error=%s",
                reason,
                shared,
                self._client_log_context(),
                exc,
            )

    def _replace_primary_openai_client(self, *, reason: str) -> bool:
        with self._openai_client_lock():
            old_client = getattr(self, "client", None)
            try:
                new_client = self._create_openai_client(self._client_kwargs, reason=reason, shared=True)
            except Exception as exc:
                logger.warning(
                    "Failed to rebuild shared OpenAI client (%s) %s error=%s",
                    reason,
                    self._client_log_context(),
                    exc,
                )
                return False
            self.client = new_client
        self._close_openai_client(old_client, reason=f"replace:{reason}", shared=True)
        return True

    def _ensure_primary_openai_client(self, *, reason: str) -> Any:
        require_active_integration(self.provider, self.model, self.base_url)
        with self._openai_client_lock():
            client = getattr(self, "client", None)
            if client is not None and not self._is_openai_client_closed(client):
                return client

        logger.warning(
            "Detected closed shared OpenAI client; recreating before use (%s) %s",
            reason,
            self._client_log_context(),
        )
        if not self._replace_primary_openai_client(reason=f"recreate_closed:{reason}"):
            raise RuntimeError("Failed to recreate closed OpenAI client")
        with self._openai_client_lock():
            return self.client

    def _cleanup_dead_connections(self) -> bool:
        """Detect and clean up dead TCP connections on the primary client.

        Inspects the httpx connection pool for sockets in unhealthy states
        (CLOSE-WAIT, errors).  If any are found, force-closes all sockets
        and rebuilds the primary client from scratch.

        Returns True if dead connections were found and cleaned up.
        """
        client = getattr(self, "client", None)
        if client is None:
            return False
        try:
            http_client = getattr(client, "_client", None)
            if http_client is None:
                return False
            transport = getattr(http_client, "_transport", None)
            if transport is None:
                return False
            pool = getattr(transport, "_pool", None)
            if pool is None:
                return False
            connections = (
                getattr(pool, "_connections", None)
                or getattr(pool, "_pool", None)
                or []
            )
            dead_count = 0
            for conn in list(connections):
                # Check for connections that are idle but have closed sockets
                stream = (
                    getattr(conn, "_network_stream", None)
                    or getattr(conn, "_stream", None)
                )
                if stream is None:
                    continue
                sock = getattr(stream, "_sock", None)
                if sock is None:
                    sock = getattr(stream, "stream", None)
                    if sock is not None:
                        sock = getattr(sock, "_sock", None)
                if sock is None:
                    continue
                # Probe socket health with a non-blocking recv peek
                import socket as _socket
                try:
                    sock.setblocking(False)
                    data = sock.recv(1, _socket.MSG_PEEK | _socket.MSG_DONTWAIT)
                    if data == b"":
                        dead_count += 1
                except BlockingIOError:
                    pass  # No data available — socket is healthy
                except OSError:
                    dead_count += 1
                finally:
                    try:
                        sock.setblocking(True)
                    except OSError:
                        pass
            if dead_count > 0:
                logger.warning(
                    "Found %d dead connection(s) in client pool — rebuilding client",
                    dead_count,
                )
                self._replace_primary_openai_client(reason="dead_connection_cleanup")
                return True
        except Exception as exc:
            logger.debug("Dead connection check error: %s", exc)
        return False

    def _create_request_openai_client(self, *, reason: str) -> Any:
        from unittest.mock import Mock

        primary_client = self._ensure_primary_openai_client(reason=reason)
        if isinstance(primary_client, Mock):
            return primary_client
        with self._openai_client_lock():
            request_kwargs = dict(self._client_kwargs)
        return self._create_openai_client(request_kwargs, reason=reason, shared=False)

    def _close_request_openai_client(self, client: Any, *, reason: str) -> None:
        self._close_openai_client(client, reason=reason, shared=False)
    def _try_refresh_nous_client_credentials(self, *, force: bool = True) -> bool:
        if self.provider != "nous":
            return False

        try:
            from VoidCube_cli.auth import resolve_nous_runtime_credentials

            creds = resolve_nous_runtime_credentials(
                min_key_ttl_seconds=max(60, int(os.getenv("VOIDCUBE_NOUS_MIN_KEY_TTL_SECONDS", "1800"))),
                timeout_seconds=float(os.getenv("VOIDCUBE_NOUS_TIMEOUT_SECONDS", "15")),
                force_mint=force,
            )
        except Exception as exc:
            logger.debug("Nous credential refresh failed: %s", exc)
            return False

        api_key = creds.get("api_key")
        base_url = creds.get("base_url")
        if not isinstance(api_key, str) or not api_key.strip():
            return False
        if not isinstance(base_url, str) or not base_url.strip():
            return False

        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self._client_kwargs["api_key"] = self.api_key
        self._client_kwargs["base_url"] = self.base_url
        # Nous requests should not inherit OpenRouter-only attribution headers.
        self._client_kwargs.pop("default_headers", None)

        if not self._replace_primary_openai_client(reason="nous_credential_refresh"):
            return False

        return True

    def _apply_client_headers_for_base_url(self, base_url: str) -> None:
        from agent.auxiliary_client import _OR_HEADERS

        normalized = (base_url or "").lower()
        if "openrouter" in normalized:
            self._client_kwargs["default_headers"] = dict(_OR_HEADERS)
        elif "api.githubcopilot.com" in normalized:
            from VoidCube_cli.models import copilot_default_headers

            self._client_kwargs["default_headers"] = copilot_default_headers()
        elif "api.kimi.com" in normalized:
            self._client_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
        elif "portal.qwen.ai" in normalized:
            self._client_kwargs["default_headers"] = _qwen_portal_headers()
        else:
            self._client_kwargs.pop("default_headers", None)

    def _swap_credential(self, entry) -> None:
        runtime_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
        runtime_base = getattr(entry, "runtime_base_url", None) or getattr(entry, "base_url", None) or self.base_url

        self.api_key = runtime_key
        self.base_url = runtime_base.rstrip("/") if isinstance(runtime_base, str) else runtime_base
        self._client_kwargs["api_key"] = self.api_key
        self._client_kwargs["base_url"] = self.base_url
        self._apply_client_headers_for_base_url(self.base_url)
        self._replace_primary_openai_client(reason="credential_rotation")

    def _recover_with_credential_pool(
        self,
        *,
        status_code: Optional[int],
        has_retried_429: bool,
        classified_reason: Optional[FailoverReason] = None,
        error_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, bool]:
        """Attempt credential recovery via pool rotation.

        Returns (recovered, has_retried_429).
        On rate limits: first occurrence retries same credential (sets flag True).
                        second consecutive failure rotates to next credential.
        On billing exhaustion: immediately rotates.
        On auth failures: attempts token refresh before rotating.

        `classified_reason` lets the recovery path honor the structured error
        classifier instead of relying only on raw HTTP codes. This matters for
        providers that surface billing/rate-limit/auth conditions under a
        different status code.
        """
        pool = self._credential_pool
        if pool is None:
            return False, has_retried_429

        effective_reason = classified_reason
        if effective_reason is None:
            if status_code == 402:
                effective_reason = FailoverReason.billing
            elif status_code == 429:
                effective_reason = FailoverReason.rate_limit
            elif status_code == 401:
                effective_reason = FailoverReason.auth

        if effective_reason == FailoverReason.billing:
            rotate_status = status_code if status_code is not None else 402
            next_entry = pool.mark_exhausted_and_rotate(status_code=rotate_status, error_context=error_context)
            if next_entry is not None:
                logger.info(
                    "Credential %s (billing) — rotated to pool entry %s",
                    rotate_status,
                    getattr(next_entry, "id", "?"),
                )
                self._swap_credential(next_entry)
                return True, False
            return False, has_retried_429

        if effective_reason == FailoverReason.rate_limit:
            if not has_retried_429:
                return False, True
            rotate_status = status_code if status_code is not None else 429
            next_entry = pool.mark_exhausted_and_rotate(status_code=rotate_status, error_context=error_context)
            if next_entry is not None:
                logger.info(
                    "Credential %s (rate limit) — rotated to pool entry %s",
                    rotate_status,
                    getattr(next_entry, "id", "?"),
                )
                self._swap_credential(next_entry)
                return True, False
            return False, True

        if effective_reason == FailoverReason.auth:
            refreshed = pool.try_refresh_current()
            if refreshed is not None:
                logger.info(f"Credential auth failure — refreshed pool entry {getattr(refreshed, 'id', '?')}")
                self._swap_credential(refreshed)
                return True, has_retried_429
            # Refresh failed — rotate to next credential instead of giving up.
            # The failed entry is already marked exhausted by try_refresh_current().
            rotate_status = status_code if status_code is not None else 401
            next_entry = pool.mark_exhausted_and_rotate(status_code=rotate_status, error_context=error_context)
            if next_entry is not None:
                logger.info(
                    "Credential %s (auth refresh failed) — rotated to pool entry %s",
                    rotate_status,
                    getattr(next_entry, "id", "?"),
                )
                self._swap_credential(next_entry)
                return True, False

        return False, has_retried_429

    def _interruptible_api_call(self, api_kwargs: dict):
        """
        Run the API call in a background thread so the main conversation loop
        can detect interrupts without waiting for the full HTTP round-trip.

        Each worker thread gets its own OpenAI client instance. Interrupts only
        close that worker-local client, so retries and other requests never
        inherit a closed transport.
        """
        result = {"response": None, "error": None}
        request_client_holder = {"client": None}

        def _call():
            try:
                request_client_holder["client"] = self._create_request_openai_client(reason="chat_completion_request")
                result["response"] = request_client_holder["client"].chat.completions.create(**api_kwargs)
            except Exception as e:
                result["error"] = e
            finally:
                request_client = request_client_holder.get("client")
                if request_client is not None:
                    self._close_request_openai_client(request_client, reason="request_complete")

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        while t.is_alive():
            t.join(timeout=0.3)
            if self._interrupt_requested:
                # Force-close the in-flight worker-local HTTP connection to stop
                # token generation without poisoning the shared client used to
                # seed future retries.
                try:
                    request_client = request_client_holder.get("client")
                    if request_client is not None:
                        self._close_request_openai_client(request_client, reason="interrupt_abort")
                except Exception:
                    pass
                raise InterruptedError("Agent interrupted during API call")
        if result["error"] is not None:
            raise result["error"]
        return result["response"]

    # ── Unified streaming API call ─────────────────────────────────────────

    def _reset_stream_delivery_tracking(self) -> None:
        """Reset tracking for text delivered during the current model response."""
        self._current_streamed_assistant_text = ""

    def _record_streamed_assistant_text(self, text: str) -> None:
        """Accumulate visible assistant text emitted through stream callbacks."""
        if isinstance(text, str) and text:
            self._current_streamed_assistant_text = (
                getattr(self, "_current_streamed_assistant_text", "") + text
            )

    @staticmethod
    def _normalize_interim_visible_text(text: str) -> str:
        if not isinstance(text, str):
            return ""
        return re.sub(r"\s+", " ", text).strip()

    def _interim_content_was_streamed(self, content: str) -> bool:
        visible_content = self._normalize_interim_visible_text(
            strip_thinking_blocks(content or "")
        )
        if not visible_content:
            return False
        streamed = self._normalize_interim_visible_text(
            strip_thinking_blocks(getattr(self, "_current_streamed_assistant_text", "") or "")
        )
        return bool(streamed) and streamed == visible_content

    def _emit_interim_assistant_message(self, assistant_msg: Dict[str, Any]) -> None:
        """Surface a real mid-turn assistant commentary message to the UI layer."""
        cb = getattr(self, "interim_assistant_callback", None)
        if cb is None or not isinstance(assistant_msg, dict):
            return
        content = assistant_msg.get("content")
        visible = strip_thinking_blocks(content or "").strip()
        if not visible or visible == "(empty)":
            return
        already_streamed = self._interim_content_was_streamed(visible)
        try:
            cb(visible, already_streamed=already_streamed)
        except Exception:
            logger.debug("interim_assistant_callback error", exc_info=True)

    def _fire_stream_delta(self, text: str) -> None:
        """Fire all registered stream delta callbacks (display + TTS)."""
        # If a tool iteration set the break flag, prepend a single paragraph
        # break before the first real text delta.  This prevents the original
        # problem (text concatenation across tool boundaries) without stacking
        # blank lines when multiple tool iterations run back-to-back.
        if getattr(self, "_stream_needs_break", False) and text and text.strip():
            self._stream_needs_break = False
            text = "\n\n" + text
        callbacks = [cb for cb in (self.stream_delta_callback, self._stream_callback) if cb is not None]
        delivered = False
        for cb in callbacks:
            try:
                cb(text)
                delivered = True
            except Exception:
                pass
        if delivered:
            self._record_streamed_assistant_text(text)

    def _fire_reasoning_delta(self, text: str) -> None:
        """Fire reasoning callback if registered."""
        cb = self.reasoning_callback
        if cb is not None:
            try:
                cb(text)
            except Exception:
                pass

    def _fire_tool_gen_started(self, tool_name: str) -> None:
        """Notify display layer that the model is generating tool call arguments.

        Fires once per tool name when the streaming response begins producing
        tool_call / tool_use tokens.  Gives the TUI a chance to show a spinner
        or status line so the user isn't staring at a frozen screen while a
        large tool payload (e.g. a 45 KB write_file) is being generated.
        """
        cb = self.tool_gen_callback
        if cb is not None:
            try:
                cb(tool_name)
            except Exception:
                pass

    def _has_stream_consumers(self) -> bool:
        """Return True if any streaming consumer is registered."""
        return (
            self.stream_delta_callback is not None
            or getattr(self, "_stream_callback", None) is not None
        )

    def _interruptible_streaming_api_call(
        self, api_kwargs: dict, *, on_first_delta: callable = None
    ):
        """Streaming variant of _interruptible_api_call for real-time token delivery.

        Uses stream=True on OpenAI-compatible chat-completions endpoints.

        Fires stream_delta_callback and _stream_callback for each text token.
        Tool-call turns suppress the callback — only text-only final responses
        stream to the consumer.  Returns a SimpleNamespace that mimics the
        non-streaming response shape so the rest of the agent loop is unchanged.

        Falls back to _interruptible_api_call on provider errors indicating
        streaming is not supported.
        """
        result = {"response": None, "error": None}
        request_client_holder = {"client": None}
        first_delta_fired = {"done": False}
        deltas_were_sent = {"yes": False}  # Track if any deltas were fired (for fallback)
        # Wall-clock timestamp of the last real streaming chunk.  The outer
        # poll loop uses this to detect stale connections that keep receiving
        # SSE keep-alive pings but no actual data.
        last_chunk_time = {"t": time.time()}

        def _fire_first_delta():
            if not first_delta_fired["done"] and on_first_delta:
                first_delta_fired["done"] = True
                try:
                    on_first_delta()
                except Exception:
                    pass

        def _call_chat_completions():
            """Stream a chat completions response."""
            import httpx as _httpx
            _base_timeout = float(os.getenv("VOIDCUBE_API_TIMEOUT", 1800.0))
            _stream_read_timeout = float(os.getenv("VOIDCUBE_STREAM_READ_TIMEOUT", 120.0))
            # Local providers (Ollama, llama.cpp, vLLM) can take minutes for
            # prefill on large contexts before producing the first token.
            # Auto-increase the httpx read timeout unless the user explicitly
            # overrode VOIDCUBE_STREAM_READ_TIMEOUT.
            if _stream_read_timeout == 120.0 and self.base_url and is_local_endpoint(self.base_url):
                _stream_read_timeout = _base_timeout
                logger.debug(
                    "Local provider detected (%s) — stream read timeout raised to %.0fs",
                    self.base_url, _stream_read_timeout,
                )
            stream_kwargs = {
                **api_kwargs,
                "stream": True,
                "stream_options": {"include_usage": True},
                "timeout": _httpx.Timeout(
                    connect=30.0,
                    read=_stream_read_timeout,
                    write=_base_timeout,
                    pool=30.0,
                ),
            }
            request_client_holder["client"] = self._create_request_openai_client(
                reason="chat_completion_stream_request"
            )
            # Reset stale-stream timer so the detector measures from this
            # attempt's start, not a previous attempt's last chunk.
            last_chunk_time["t"] = time.time()
            self._touch_activity("waiting for provider response (streaming)")
            stream = request_client_holder["client"].chat.completions.create(**stream_kwargs)

            # Capture rate limit headers from the initial HTTP response.
            # The OpenAI SDK Stream object exposes the underlying httpx
            # response via .response before any chunks are consumed.
            self._capture_rate_limits(getattr(stream, "response", None))

            content_parts: list = []
            tool_calls_acc: dict = {}
            tool_gen_notified: set = set()
            # Ollama-compatible endpoints reuse index 0 for every tool call
            # in a parallel batch, distinguishing them only by id.  Track
            # the last seen id per raw index so we can detect a new tool
            # call starting at the same index and redirect it to a fresh slot.
            _last_id_at_idx: dict = {}      # raw_index -> last seen non-empty id
            _active_slot_by_idx: dict = {}  # raw_index -> current slot in tool_calls_acc
            finish_reason = None
            model_name = None
            role = "assistant"
            reasoning_parts: list = []
            usage_obj = None
            _first_chunk_seen = False
            for chunk in stream:
                last_chunk_time["t"] = time.time()
                if not _first_chunk_seen:
                    _first_chunk_seen = True
                    self._touch_activity("receiving stream response")

                if self._interrupt_requested:
                    break

                if not chunk.choices:
                    if hasattr(chunk, "model") and chunk.model:
                        model_name = chunk.model
                    # Usage comes in the final chunk with empty choices
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage_obj = chunk.usage
                    continue

                delta = chunk.choices[0].delta
                if hasattr(chunk, "model") and chunk.model:
                    model_name = chunk.model

                # Accumulate reasoning content
                reasoning_text = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning_text:
                    reasoning_parts.append(reasoning_text)
                    _fire_first_delta()
                    self._fire_reasoning_delta(reasoning_text)

                # Accumulate text content — fire callback only when no tool calls
                if delta and delta.content:
                    content_parts.append(delta.content)
                    if not tool_calls_acc:
                        _fire_first_delta()
                        self._fire_stream_delta(delta.content)
                        deltas_were_sent["yes"] = True
                    else:
                        # Tool calls suppress regular content streaming (avoids
                        # displaying chatty "I'll use the tool..." text alongside
                        # tool calls).  But reasoning tags embedded in suppressed
                        # content should still reach the display — otherwise the
                        # reasoning box only appears as a post-response fallback,
                        # rendering it confusingly after the already-streamed
                        # response.  Route suppressed content through the stream
                        # delta callback so its tag extraction can fire the
                        # reasoning display.  Non-reasoning text is harmlessly
                        # suppressed by the CLI's _stream_delta when the stream
                        # box is already closed (tool boundary flush).
                        if self.stream_delta_callback:
                            try:
                                self.stream_delta_callback(delta.content)
                                self._record_streamed_assistant_text(delta.content)
                            except Exception:
                                pass

                # Accumulate tool call deltas — notify display on first name
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        raw_idx = tc_delta.index if tc_delta.index is not None else 0
                        delta_id = tc_delta.id or ""

                        # Ollama fix: detect a new tool call reusing the same
                        # raw index (different id) and redirect to a fresh slot.
                        if raw_idx not in _active_slot_by_idx:
                            _active_slot_by_idx[raw_idx] = raw_idx
                        if (
                            delta_id
                            and raw_idx in _last_id_at_idx
                            and delta_id != _last_id_at_idx[raw_idx]
                        ):
                            new_slot = max(tool_calls_acc, default=-1) + 1
                            _active_slot_by_idx[raw_idx] = new_slot
                        if delta_id:
                            _last_id_at_idx[raw_idx] = delta_id
                        idx = _active_slot_by_idx[raw_idx]

                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                                "extra_content": None,
                            }
                        entry = tool_calls_acc[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                entry["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                entry["function"]["arguments"] += tc_delta.function.arguments
                        extra = getattr(tc_delta, "extra_content", None)
                        if extra is None and hasattr(tc_delta, "model_extra"):
                            extra = (tc_delta.model_extra or {}).get("extra_content")
                        if extra is not None:
                            if hasattr(extra, "model_dump"):
                                extra = extra.model_dump()
                            entry["extra_content"] = extra
                        # Fire once per tool when the full name is available
                        name = entry["function"]["name"]
                        if name and idx not in tool_gen_notified:
                            tool_gen_notified.add(idx)
                            _fire_first_delta()
                            self._fire_tool_gen_started(name)

                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

                # Usage in the final chunk
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_obj = chunk.usage

            # Build mock response matching non-streaming shape
            full_content = "".join(content_parts) or None
            mock_tool_calls = None
            has_truncated_tool_args = False
            if tool_calls_acc:
                mock_tool_calls = []
                for idx in sorted(tool_calls_acc):
                    tc = tool_calls_acc[idx]
                    arguments = tc["function"]["arguments"]
                    if arguments and arguments.strip():
                        try:
                            json.loads(arguments)
                        except json.JSONDecodeError:
                            has_truncated_tool_args = True
                    mock_tool_calls.append(SimpleNamespace(
                        id=tc["id"],
                        type=tc["type"],
                        extra_content=tc.get("extra_content"),
                        function=SimpleNamespace(
                            name=tc["function"]["name"],
                            arguments=arguments,
                        ),
                    ))

            effective_finish_reason = finish_reason or "stop"
            if has_truncated_tool_args:
                effective_finish_reason = "length"

            full_reasoning = "".join(reasoning_parts) or None
            mock_message = SimpleNamespace(
                role=role,
                content=full_content,
                tool_calls=mock_tool_calls,
                reasoning_content=full_reasoning,
            )
            mock_choice = SimpleNamespace(
                index=0,
                message=mock_message,
                finish_reason=effective_finish_reason,
            )
            return SimpleNamespace(
                id="stream-" + str(uuid.uuid4()),
                model=model_name,
                choices=[mock_choice],
                usage=usage_obj,
            )

        def _call():
            import httpx as _httpx

            _max_stream_retries = int(os.getenv("VOIDCUBE_STREAM_RETRIES", 2))

            try:
                for _stream_attempt in range(_max_stream_retries + 1):
                    try:
                        result["response"] = _call_chat_completions()
                        return  # success
                    except Exception as e:
                        if deltas_were_sent["yes"]:
                            # Streaming failed AFTER some tokens were already
                            # delivered.  Don't retry or fall back — partial
                            # content already reached the user.
                            logger.warning(
                                "Streaming failed after partial delivery, not retrying: %s", e
                            )
                            result["error"] = e
                            return

                        _is_timeout = isinstance(
                            e, (_httpx.ReadTimeout, _httpx.ConnectTimeout, _httpx.PoolTimeout)
                        )
                        _is_conn_err = isinstance(
                            e, (_httpx.ConnectError, _httpx.RemoteProtocolError, ConnectionError)
                        )

                        # SSE error events from proxies (e.g. OpenRouter sends
                        # {"error":{"message":"Network connection lost."}}) are
                        # raised as APIError by the OpenAI SDK.  These are
                        # semantically identical to httpx connection drops —
                        # the upstream stream died — and should be retried with
                        # a fresh connection.  Distinguish from HTTP errors:
                        # APIError from SSE has no status_code, while
                        # APIStatusError (4xx/5xx) always has one.
                        _is_sse_conn_err = False
                        if not _is_timeout and not _is_conn_err:
                            from openai import APIError as _APIError
                            if isinstance(e, _APIError) and not getattr(e, "status_code", None):
                                _err_lower_sse = str(e).lower()
                                _SSE_CONN_PHRASES = (
                                    "connection lost",
                                    "connection reset",
                                    "connection closed",
                                    "connection terminated",
                                    "network error",
                                    "network connection",
                                    "terminated",
                                    "peer closed",
                                    "broken pipe",
                                    "upstream connect error",
                                )
                                _is_sse_conn_err = any(
                                    phrase in _err_lower_sse
                                    for phrase in _SSE_CONN_PHRASES
                                )

                        if _is_timeout or _is_conn_err or _is_sse_conn_err:
                            # Transient network / timeout error. Retry the
                            # streaming request with a fresh connection first.
                            if _stream_attempt < _max_stream_retries:
                                logger.info(
                                    "Streaming attempt %s/%s failed (%s: %s), "
                                    "retrying with fresh connection...",
                                    _stream_attempt + 1,
                                    _max_stream_retries + 1,
                                    type(e).__name__,
                                    e,
                                )
                                self._emit_status(
                                    f"⚠️ Connection to provider dropped "
                                    f"({type(e).__name__}). Reconnecting… "
                                    f"(attempt {_stream_attempt + 2}/{_max_stream_retries + 1})"
                                )
                                # Close the stale request client before retry
                                stale = request_client_holder.get("client")
                                if stale is not None:
                                    self._close_request_openai_client(
                                        stale, reason="stream_retry_cleanup"
                                    )
                                    request_client_holder["client"] = None
                                # Also rebuild the primary client to purge
                                # any dead connections from the pool.
                                try:
                                    self._replace_primary_openai_client(
                                        reason="stream_retry_pool_cleanup"
                                    )
                                except Exception:
                                    pass
                                continue
                            self._emit_status(
                                "❌ Connection to provider failed after "
                                f"{_max_stream_retries + 1} attempts. "
                                "The provider may be experiencing issues — "
                                "try again in a moment."
                            )
                            logger.warning(
                                "Streaming exhausted %s retries on transient error, "
                                "falling back to non-streaming: %s",
                                _max_stream_retries + 1,
                                e,
                            )
                        else:
                            _err_lower = str(e).lower()
                            _is_stream_unsupported = (
                                "stream" in _err_lower
                                and "not supported" in _err_lower
                            )
                            if _is_stream_unsupported:
                                self._safe_print(
                                    "\n⚠  Streaming is not supported for this "
                                    "model/provider. Falling back to non-streaming.\n"
                                    "   To avoid this delay, set display.streaming: false "
                                    "in config.yaml\n"
                                )
                            logger.info(
                                "Streaming failed before delivery, falling back to non-streaming: %s",
                                e,
                            )

                        try:
                            # Reset stale timer — the non-streaming fallback
                            # uses its own client; prevent the stale detector
                            # from firing on stale timestamps from failed streams.
                            last_chunk_time["t"] = time.time()
                            result["response"] = self._interruptible_api_call(api_kwargs)
                        except Exception as fallback_err:
                            result["error"] = fallback_err
                        return
            finally:
                request_client = request_client_holder.get("client")
                if request_client is not None:
                    self._close_request_openai_client(request_client, reason="stream_request_complete")

        _stream_stale_timeout_base = float(os.getenv("VOIDCUBE_STREAM_STALE_TIMEOUT", 180.0))
        # Local providers (Ollama, oMLX, llama-cpp) can take 300+ seconds
        # for prefill on large contexts.  Disable the stale detector unless
        # the user explicitly set VOIDCUBE_STREAM_STALE_TIMEOUT.
        if _stream_stale_timeout_base == 180.0 and self.base_url and is_local_endpoint(self.base_url):
            _stream_stale_timeout = float("inf")
            logger.debug("Local provider detected (%s) — stale stream timeout disabled", self.base_url)
        else:
            # Scale the stale timeout for large contexts: slow models (like Opus)
            # can legitimately think for minutes before producing the first token
            # when the context is large.  Without this, the stale detector kills
            # healthy connections during the model's thinking phase, producing
            # spurious RemoteProtocolError ("peer closed connection").
            _est_tokens = sum(len(str(v)) for v in api_kwargs.get("messages", [])) // 4
            if _est_tokens > 100_000:
                _stream_stale_timeout = max(_stream_stale_timeout_base, 300.0)
            elif _est_tokens > 50_000:
                _stream_stale_timeout = max(_stream_stale_timeout_base, 240.0)
            else:
                _stream_stale_timeout = _stream_stale_timeout_base

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        while t.is_alive():
            t.join(timeout=0.3)

            # Detect stale streams: connections kept alive by SSE pings
            # but delivering no real chunks.  Kill the client so the
            # inner retry loop can start a fresh connection.
            _stale_elapsed = time.time() - last_chunk_time["t"]
            if _stale_elapsed > _stream_stale_timeout:
                _est_ctx = sum(len(str(v)) for v in api_kwargs.get("messages", [])) // 4
                logger.warning(
                    "Stream stale for %.0fs (threshold %.0fs) — no chunks received. "
                    "model=%s context=~%s tokens. Killing connection.",
                    _stale_elapsed, _stream_stale_timeout,
                    api_kwargs.get("model", "unknown"), f"{_est_ctx:,}",
                )
                self._emit_status(
                    f"⚠️ No response from provider for {int(_stale_elapsed)}s "
                    f"(model: {api_kwargs.get('model', 'unknown')}, "
                    f"context: ~{_est_ctx:,} tokens). "
                    f"Reconnecting..."
                )
                try:
                    rc = request_client_holder.get("client")
                    if rc is not None:
                        self._close_request_openai_client(rc, reason="stale_stream_kill")
                except Exception:
                    pass
                # Rebuild the primary client too — its connection pool
                # may hold dead sockets from the same provider outage.
                try:
                    self._replace_primary_openai_client(reason="stale_stream_pool_cleanup")
                except Exception:
                    pass
                # Reset the timer so we don't kill repeatedly while
                # the inner thread processes the closure.
                last_chunk_time["t"] = time.time()

            if self._interrupt_requested:
                try:
                    request_client = request_client_holder.get("client")
                    if request_client is not None:
                        self._close_request_openai_client(request_client, reason="stream_interrupt_abort")
                except Exception:
                    pass
                raise InterruptedError("Agent interrupted during streaming API call")
        if result["error"] is not None:
            if deltas_were_sent["yes"]:
                # Streaming failed AFTER some tokens were already delivered to
                # the platform.  Re-raising would let the outer retry loop make
                # a new API call, creating a duplicate message.  Return a
                # partial "stop" response instead so the outer loop treats this
                # turn as complete (no retry, no fallback).
                logger.warning(
                    "Partial stream delivered before error; returning stub "
                    "response to prevent duplicate messages: %s",
                    result["error"],
                )
                _stub_msg = SimpleNamespace(
                    role="assistant", content=None, tool_calls=None,
                    reasoning_content=None,
                )
                return SimpleNamespace(
                    id="partial-stream-stub",
                    model=getattr(self, "model", "unknown"),
                    choices=[SimpleNamespace(
                        index=0, message=_stub_msg, finish_reason="stop",
                    )],
                    usage=None,
                )
            raise result["error"]
        return result["response"]

    # ── Provider fallback ──────────────────────────────────────────────────

    def _try_activate_fallback(self) -> bool:
        """Switch to the next fallback model/provider in the chain.

        Called when the current model is failing after retries.  Swaps the
        OpenAI client, model slug, and provider in-place so the retry loop
        can continue with the new backend.  Advances through the chain on
        each call; returns False when exhausted.

        Uses the centralized provider router (resolve_provider_client) for
        auth resolution and client construction — no duplicated provider→key
        mappings.
        """
        if self._fallback_index >= len(self._fallback_chain):
            return False

        fb = self._fallback_chain[self._fallback_index]
        self._fallback_index += 1
        fb_provider = (fb.get("provider") or "").strip().lower()
        fb_model = (fb.get("model") or "").strip()
        if not fb_provider or not fb_model:
            return self._try_activate_fallback()  # skip invalid, try next

        # Use centralized router for client construction.
        try:
            from agent.auxiliary_client import resolve_provider_client
            # Pass base_url and api_key from fallback config so custom
            # endpoints (e.g. Ollama Cloud) resolve correctly instead of
            # falling through to OpenRouter defaults.
            fb_base_url_hint = (fb.get("base_url") or "").strip() or None
            fb_api_key_hint = (fb.get("api_key") or "").strip() or None
            # For Ollama Cloud endpoints, pull OLLAMA_API_KEY from env
            # when no explicit key is in the fallback config.
            if fb_base_url_hint and "ollama.com" in fb_base_url_hint.lower() and not fb_api_key_hint:
                fb_api_key_hint = os.getenv("OLLAMA_API_KEY") or None
            fb_client, _resolved_fb_model = resolve_provider_client(
                fb_provider, model=fb_model,
                explicit_base_url=fb_base_url_hint,
                explicit_api_key=fb_api_key_hint)
            if fb_client is None:
                logging.warning(
                    "Fallback to %s failed: provider not configured",
                    fb_provider)
                return self._try_activate_fallback()  # try next in chain
            try:
                from VoidCube_cli.model_normalize import normalize_model_for_provider

                fb_model = normalize_model_for_provider(fb_model, fb_provider)
            except Exception:
                pass

            fb_base_url = str(fb_client.base_url)
            old_model = self.model
            self.model = fb_model
            self.provider = fb_provider
            self.base_url = fb_base_url
            self._fallback_activated = True

            # Swap OpenAI client and config in-place.
            self.api_key = fb_client.api_key
            self.client = fb_client
            fb_headers = getattr(fb_client, "_custom_headers", None)
            if not fb_headers:
                fb_headers = getattr(fb_client, "default_headers", None)
            self._client_kwargs = {
                "api_key": fb_client.api_key,
                "base_url": fb_base_url,
                **({"default_headers": dict(fb_headers)} if fb_headers else {}),
            }

            # Prompt caching is disabled

            # Update context compressor limits for the fallback model.
            # Without this, compression decisions use the primary model's
            # context window (e.g. 200K) instead of the fallback's (e.g. 32K),
            # causing oversized sessions to overflow the fallback.
            if hasattr(self, 'context_compressor') and self.context_compressor:
                from agent.model_metadata import get_model_context_length
                fb_context_length = get_model_context_length(
                    self.model, base_url=self.base_url,
                    api_key=self.api_key, provider=self.provider,
                )
                self.context_compressor.update_model(
                    model=self.model,
                    context_length=fb_context_length,
                    base_url=self.base_url,
                    api_key=getattr(self, "api_key", ""),
                    provider=self.provider,
                )

            self._emit_status(
                f"🔄 Primary model failed — switching to fallback: "
                f"{fb_model} via {fb_provider}"
            )
            logging.info(
                "Fallback activated: %s → %s (%s)",
                old_model, fb_model, fb_provider,
            )
            return True
        except Exception as e:
            logging.error("Failed to activate fallback %s: %s", fb_model, e)
            return self._try_activate_fallback()  # try next in chain

    # ── Per-turn primary restoration ─────────────────────────────────────

    def _restore_primary_runtime(self) -> bool:
        """Restore the primary runtime at the start of a new turn.

        In long-lived CLI sessions a single AIAgent instance spans multiple
        turns.  Without restoration, one transient failure pins the session
        to the fallback provider for every subsequent turn.  Calling this at
        the top of ``run_conversation()`` makes fallback turn-scoped.

        The gateway caches agents across messages (``_agent_cache`` in
        ``gateway/run.py``), so this restoration IS needed there too.
        """
        if not self._fallback_activated:
            return False

        rt = self._primary_runtime
        try:
            # ── Core runtime state ──
            self.model = rt["model"]
            self.provider = rt["provider"]
            self.base_url = rt["base_url"]           # setter updates _base_url_lower
            self.api_key = rt["api_key"]
            self._client_kwargs = dict(rt["client_kwargs"])

            # ── Rebuild client for the primary provider ──
            self.client = self._create_openai_client(
                dict(rt["client_kwargs"]),
                reason="restore_primary",
                shared=True,
            )

            # ── Restore context engine state ──
            cc = self.context_compressor
            cc.update_model(
                model=rt["compressor_model"],
                context_length=rt["compressor_context_length"],
                base_url=rt["compressor_base_url"],
                api_key=rt["compressor_api_key"],
                provider=rt["compressor_provider"],
            )

            # ── Reset fallback chain for the new turn ──
            self._fallback_activated = False
            self._fallback_index = 0

            logging.info(
                "Primary runtime restored for new turn: %s (%s)",
                self.model, self.provider,
            )
            return True
        except Exception as e:
            logging.warning("Failed to restore primary runtime: %s", e)
            return False

    # Which error types indicate a transient transport failure worth
    # one more attempt with a rebuilt client / connection pool.
    _TRANSIENT_TRANSPORT_ERRORS = frozenset({
        "ReadTimeout", "ConnectTimeout", "PoolTimeout",
        "ConnectError", "RemoteProtocolError",
        "APIConnectionError", "APITimeoutError",
    })

    def _try_recover_primary_transport(
        self, api_error: Exception, *, retry_count: int, max_retries: int,
    ) -> bool:
        """Attempt one extra primary-provider recovery cycle for transient transport failures.

        After ``max_retries`` exhaust, rebuild the primary client (clearing
        stale connection pools) and give it one more attempt before falling
        back. This is most useful for direct endpoints where a TCP-level hiccup does not
        mean the provider is down.

        Skipped for proxy/aggregator providers (OpenRouter, Nous) which
        already manage connection pools and retries server-side — if our
        retries through them are exhausted, one more rebuilt client won't help.
        """
        if self._fallback_activated:
            return False

        # Only for transient transport errors
        error_type = type(api_error).__name__
        if error_type not in self._TRANSIENT_TRANSPORT_ERRORS:
            return False

        # Skip for aggregator providers — they manage their own retry infra
        if self._is_openrouter_url():
            return False
        provider_lower = (self.provider or "").strip().lower()
        if provider_lower in ("nous", "nous-research"):
            return False

        try:
            # Close existing client to release stale connections
            if getattr(self, "client", None) is not None:
                try:
                    self._close_openai_client(
                        self.client, reason="primary_recovery", shared=True,
                    )
                except Exception:
                    pass

            # Rebuild from primary snapshot
            rt = self._primary_runtime
            self._client_kwargs = dict(rt["client_kwargs"])
            self.model = rt["model"]
            self.provider = rt["provider"]
            self.base_url = rt["base_url"]
            self.api_key = rt["api_key"]

            self.client = self._create_openai_client(
                dict(rt["client_kwargs"]),
                reason="primary_recovery",
                shared=True,
            )

            wait_time = min(3 + retry_count, 8)
            self._vprint(
                f"{self.log_prefix}🔁 Transient {error_type} on {self.provider} — "
                f"rebuilt client, waiting {wait_time}s before one last primary attempt.",
                force=True,
            )
            time.sleep(wait_time)
            return True
        except Exception as e:
            logging.warning("Primary transport recovery failed: %s", e)
            return False

    # ── End provider fallback ──────────────────────────────────────────────

    def _chat_request_config(self) -> ChatRequestConfig:
        """Snapshot mutable Agent state for pure request preparation."""
        return ChatRequestConfig(
            model=self.model,
            base_url=self.base_url,
            session_id=self.session_id or "",
            tools=tuple(self.tools or ()),
            max_tokens=self.max_tokens,
            providers_allowed=tuple(self.providers_allowed or ()),
            providers_ignored=tuple(self.providers_ignored or ()),
            providers_order=tuple(self.providers_order or ()),
            provider_sort=self.provider_sort or "",
            provider_require_parameters=bool(self.provider_require_parameters),
            provider_data_collection=self.provider_data_collection or "",
            reasoning_config=(
                dict(self.reasoning_config)
                if isinstance(self.reasoning_config, dict)
                else None
            ),
            ollama_num_ctx=self._ollama_num_ctx,
            request_overrides=dict(self.request_overrides or {}),
            timeout=float(os.getenv("VOIDCUBE_API_TIMEOUT", 1800.0)),
        )

    def _build_assistant_message(self, assistant_message, finish_reason: str) -> dict:
        """Normalize a response message and emit non-streaming reasoning."""
        msg = normalize_assistant_message(assistant_message, finish_reason)
        reasoning_text = msg["reasoning"]

        if reasoning_text and self.verbose_logging:
            logging.debug(f"Captured reasoning ({len(reasoning_text)} chars): {reasoning_text}")

        if reasoning_text and self.reasoning_callback:
            # Skip callback when streaming is active — reasoning was already
            # displayed during the stream via one of two paths:
            #   (a) _fire_reasoning_delta (structured reasoning_content deltas)
            #   (b) _stream_delta tag extraction (<think>/<REASONING_SCRATCHPAD>)
            # When streaming is NOT active, always fire so non-streaming modes
            # (gateway, batch, quiet) still get reasoning.
            # Any reasoning that wasn't shown during streaming is caught by the
            # CLI post-response display fallback (cli.py _reasoning_shown_this_turn).
            if not self.stream_delta_callback:
                try:
                    self.reasoning_callback(reasoning_text)
                except Exception:
                    pass
        return msg

    def flush_memories(self, messages: list = None, min_turns: int = None):
        """Give the model one turn to persist memories before context is lost.

        Called before compression, session reset, or CLI exit. Injects a flush
        message, makes one API call, executes any memory tool calls, then
        strips all flush artifacts from the message list.

        Args:
            messages: The current conversation messages. If None, uses
                      self._session_messages (last run_conversation state).
            min_turns: Minimum user turns required to trigger the flush.
                       None = use config value (flush_min_turns).
                       0 = always flush (used for compression).
        """
        if self._memory_flush_min_turns == 0 and min_turns is None:
            return
        if "memory" not in self.valid_tool_names or not self._memory_store:
            return
        effective_min = min_turns if min_turns is not None else self._memory_flush_min_turns
        if self._user_turn_count < effective_min:
            return

        if messages is None:
            messages = getattr(self, '_session_messages', None)
        if not messages or len(messages) < 3:
            return

        flush_content = (
            "[System: The session is being compressed. "
            "Save anything worth remembering — prioritize user preferences, "
            "corrections, and recurring patterns over task-specific details.]"
        )
        _sentinel = f"__flush_{id(self)}_{time.monotonic()}"
        flush_msg = {"role": "user", "content": flush_content, "_flush_sentinel": _sentinel}
        messages.append(flush_msg)

        try:
            # Build API messages for the flush call
            api_messages = []
            for msg in messages:
                api_msg = msg.copy()
                if msg.get("role") == "assistant":
                    reasoning = msg.get("reasoning")
                    if reasoning:
                        api_msg["reasoning_content"] = reasoning
                api_msg.pop("reasoning", None)
                api_msg.pop("finish_reason", None)
                api_msg.pop("_flush_sentinel", None)
                api_msg.pop("_thinking_prefill", None)
                api_messages.append(api_msg)

            if self._cached_system_prompt:
                api_messages = [{"role": "system", "content": self._cached_system_prompt}] + api_messages

            # Make one API call with only the memory tool available
            memory_tool_def = None
            for t in (self.tools or []):
                if t.get("function", {}).get("name") == "memory":
                    memory_tool_def = t
                    break

            if not memory_tool_def:
                messages.pop()  # remove flush msg
                return

            # Use the auxiliary client for the flush call when available.
            from agent.auxiliary_client import call_llm as _call_llm
            _aux_available = True
            try:
                response = _call_llm(
                    task="flush_memories",
                    messages=api_messages,
                    tools=[memory_tool_def],
                    temperature=0.3,
                    max_tokens=5120,
                    # timeout resolved from auxiliary.flush_memories.timeout config
                )
            except RuntimeError:
                _aux_available = False
                response = None

            if not _aux_available:
                from agent.auxiliary_client import _get_task_timeout
                flush_config = replace(
                    self._chat_request_config(),
                    tools=(memory_tool_def,),
                    max_tokens=5120,
                    request_overrides={"temperature": 0.3},
                    timeout=_get_task_timeout("flush_memories"),
                )
                api_kwargs = build_chat_completion_kwargs(
                    flush_config,
                    api_messages,
                )
                response = self._ensure_primary_openai_client(reason="flush_memories").chat.completions.create(
                    **api_kwargs
                )

            # Extract tool calls from the chat-completions response.
            tool_calls = []
            if hasattr(response, "choices") and response.choices:
                assistant_message = response.choices[0].message
                if assistant_message.tool_calls:
                    tool_calls = assistant_message.tool_calls

            for tc in tool_calls:
                if tc.function.name == "memory":
                    try:
                        args = json.loads(tc.function.arguments)
                        flush_target = args.get("target", "memory")
                        from tools.memory_tool import memory_tool as _memory_tool
                        _memory_tool(
                            action=args.get("action"),
                            target=flush_target,
                            content=args.get("content"),
                            old_text=args.get("old_text"),
                            store=self._memory_store,
                        )
                        if not self.quiet_mode:
                            print(f"  🧠 Memory flush: saved to {args.get('target', 'memory')}")
                    except Exception as e:
                        logger.debug("Memory flush tool call failed: %s", e)
        except Exception as e:
            logger.debug("Memory flush API call failed: %s", e)
        finally:
            # Strip flush artifacts: remove everything from the flush message onward.
            # Use sentinel marker instead of identity check for robustness.
            while messages and messages[-1].get("_flush_sentinel") != _sentinel:
                messages.pop()
                if not messages:
                    break
            if messages and messages[-1].get("_flush_sentinel") == _sentinel:
                messages.pop()

    def _compress_context(self, messages: list, system_message: str, *, approx_tokens: int = None, task_id: str = "default", focus_topic: str = None) -> tuple:
        """Compress conversation context and split the session in SQLite.

        Args:
            focus_topic: Optional focus string for guided compression — the
                summariser will prioritise preserving information related to
                this topic.

        Returns:
            (compressed_messages, new_system_prompt) tuple
        """
        _pre_msg_count = len(messages)
        logger.info(
            "context compression started: session=%s messages=%d tokens=~%s model=%s focus=%r",
            self.session_id or "none", _pre_msg_count,
            f"{approx_tokens:,}" if approx_tokens else "unknown", self.model,
            focus_topic,
        )
        # Pre-compression memory flush: let the model save memories before they're lost
        self.flush_memories(messages, min_turns=0)

        # Notify external memory provider before compression discards context
        if self._memory_manager:
            try:
                self._memory_manager.on_pre_compress(messages)
            except Exception:
                pass

        compressed = self.context_compressor.compress(messages, current_tokens=approx_tokens, focus_topic=focus_topic)

        todo_snapshot = self._todo_store.format_for_injection()
        if todo_snapshot:
            compressed.append({"role": "user", "content": todo_snapshot})

        self._invalidate_system_prompt()
        new_system_prompt = self._build_system_prompt(system_message)
        self._cached_system_prompt = new_system_prompt

        if self._session_db:
            try:
                # Propagate title to the new session with auto-numbering
                old_title = self._session_db.get_session_title(self.session_id)
                self._session_db.end_session(self.session_id, "compression")
                old_session_id = self.session_id
                self.session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
                # Update session_log_file to point to the new session's JSON file
                self.session_log_file = self.logs_dir / f"session_{self.session_id}.json"
                self._session_db.create_session(
                    session_id=self.session_id,
                    source=self.platform or os.environ.get("VOIDCUBE_SESSION_SOURCE", "cli"),
                    model=self.model,
                    parent_session_id=old_session_id,
                )
                # Auto-number the title for the continuation session
                if old_title:
                    try:
                        new_title = self._session_db.get_next_title_in_lineage(old_title)
                        self._session_db.set_session_title(self.session_id, new_title)
                    except (ValueError, Exception) as e:
                        logger.debug("Could not propagate title on compression: %s", e)
                self._session_db.update_system_prompt(self.session_id, new_system_prompt)
                # Reset flush cursor — new session starts with no messages written
                self._last_flushed_db_idx = 0
            except Exception as e:
                logger.warning("Session DB compression split failed — new session will NOT be indexed: %s", e)

        # Warn on repeated compressions (quality degrades with each pass)
        _cc = self.context_compressor.compression_count
        if _cc >= 2:
            self._vprint(
                f"{self.log_prefix}⚠️  Session compressed {_cc} times — "
                f"accuracy may degrade. Consider /new to start fresh.",
                force=True,
            )

        # Update token estimate after compaction so pressure calculations
        # use the post-compression count, not the stale pre-compression one.
        _compressed_est = (
            estimate_tokens_rough(new_system_prompt)
            + estimate_messages_tokens_rough(compressed)
        )
        self.context_compressor.last_prompt_tokens = _compressed_est
        self.context_compressor.last_completion_tokens = 0

        # Only reset the pressure warning if compression actually brought
        # us below the warning level (85% of threshold).  When compression
        # can't reduce enough (e.g. threshold is very low, or system prompt
        # alone exceeds the warning level), keep the tier set to prevent
        # spamming the user with repeated warnings every loop iteration.
        if self.context_compressor.threshold_tokens > 0:
            _post_progress = _compressed_est / self.context_compressor.threshold_tokens
            if _post_progress < 0.85:
                self._context_pressure_warned_at = 0.0
                # Clear class-level dedup for this session so a fresh
                # warning cycle can start if context grows again.
                _sid = self.session_id or "default"
                AIAgent._context_pressure_last_warned.pop(_sid, None)

        # Clear the file-read dedup cache.  After compression the original
        # read content is summarised away — if the model re-reads the same
        # file it needs the full content, not a "file unchanged" stub.
        try:
            from tools.file_tools import reset_file_dedup
            reset_file_dedup(task_id)
        except Exception:
            pass

        logger.info(
            "context compression done: session=%s messages=%d->%d tokens=~%s",
            self.session_id or "none", _pre_msg_count, len(compressed),
            f"{_compressed_est:,}",
        )
        return compressed, new_system_prompt

    def _compress_for_api_recovery(
        self,
        messages: list,
        system_message: str,
        *,
        approx_tokens: int,
        task_id: str,
        context_length_changed: bool = False,
    ) -> CompressionRecoveryResult:
        """Execute one recovery compression and assess whether it made progress."""
        original_count = len(messages)
        compressed, system_prompt = self._compress_context(
            messages,
            system_message,
            approx_tokens=approx_tokens,
            task_id=task_id,
        )
        return CompressionRecoveryResult(
            messages=compressed,
            system_prompt=system_prompt,
            original_message_count=original_count,
            context_length_changed=context_length_changed,
        )

    def _context_recovery_failure_result(
        self,
        *,
        messages: list,
        conversation_history: list | None,
        api_call_count: int,
        error: str,
    ) -> dict:
        """Persist once and build the canonical partial context failure result."""
        self._persist_session(messages, conversation_history)
        return {
            "messages": messages,
            "completed": False,
            "api_calls": api_call_count,
            "error": error,
            "partial": True,
        }

    def _execute_tool_calls(self, assistant_message, messages: list, effective_task_id: str, api_call_count: int = 0) -> None:
        """Execute tool calls from the assistant message and append results to messages.

        Dispatches to concurrent execution only for batches that look
        independent: read-only tools may always share the parallel path, while
        file reads/writes may do so only when their target paths do not overlap.
        """
        tool_calls = assistant_message.tool_calls

        # Allow _vprint during tool execution even with stream consumers
        self._executing_tools = True
        try:
            if not should_parallelize_tool_batch(tool_calls):
                return self._execute_tool_calls_sequential(
                    assistant_message, messages, effective_task_id, api_call_count
                )

            return self._execute_tool_calls_concurrent(
                assistant_message, messages, effective_task_id, api_call_count
            )
        finally:
            self._executing_tools = False

    def _invoke_tool(self, function_name: str, function_args: dict, effective_task_id: str,
                     tool_call_id: Optional[str] = None) -> str:
        """Invoke a single tool and return the result string. No display logic.

        Handles both agent-level tools (todo, memory, etc.) and registry-dispatched
        tools. Used by the concurrent execution path; the sequential path retains
        its own inline invocation for backward-compatible display handling.
        """
        if function_name == "todo":
            from tools.todo_tool import todo_tool as _todo_tool
            return _todo_tool(
                todos=function_args.get("todos"),
                merge=function_args.get("merge", False),
                store=self._todo_store,
            )
        elif function_name == "session_search":
            if not self._session_db:
                return json.dumps({"success": False, "error": "Session database not available."})
            from tools.session_search_tool import session_search as _session_search
            return _session_search(
                query=function_args.get("query", ""),
                role_filter=function_args.get("role_filter"),
                limit=function_args.get("limit", 3),
                db=self._session_db,
                current_session_id=self.session_id,
            )
        elif function_name == "memory":
            target = function_args.get("target", "memory")
            from tools.memory_tool import memory_tool as _memory_tool
            result = _memory_tool(
                action=function_args.get("action"),
                target=target,
                content=function_args.get("content"),
                old_text=function_args.get("old_text"),
                store=self._memory_store,
            )
            # Bridge: notify external memory provider of built-in memory writes
            if self._memory_manager and function_args.get("action") in ("add", "replace"):
                try:
                    self._memory_manager.on_memory_write(
                        function_args.get("action", ""),
                        target,
                        function_args.get("content", ""),
                    )
                except Exception:
                    pass
            return result
        elif self._memory_manager and self._memory_manager.has_tool(function_name):
            return self._memory_manager.handle_tool_call(function_name, function_args)
        elif function_name == "clarify":
            from tools.clarify_tool import clarify_tool as _clarify_tool
            return _clarify_tool(
                question=function_args.get("question", ""),
                choices=function_args.get("choices"),
                callback=self.clarify_callback,
            )
        elif function_name == "delegate_task":
            from tools.delegate_tool import delegate_task as _delegate_task
            return _delegate_task(
                goal=function_args.get("goal"),
                context=function_args.get("context"),
                toolsets=function_args.get("toolsets"),
                tasks=function_args.get("tasks"),
                max_iterations=function_args.get("max_iterations"),
                parent_agent=self,
            )
        else:
            return handle_function_call(
                function_name, function_args, effective_task_id,
                tool_call_id=tool_call_id,
                session_id=self.session_id or "",
                enabled_tools=list(self.valid_tool_names) if self.valid_tool_names else None,
                main_runtime=self._current_main_runtime(),
            )

    def _execute_tool_calls_concurrent(self, assistant_message, messages: list, effective_task_id: str, api_call_count: int = 0) -> None:
        """Execute multiple tool calls concurrently using a thread pool.

        Results are collected in the original tool-call order and appended to
        messages so the API sees them in the expected sequence.
        """
        tool_calls = assistant_message.tool_calls
        num_tools = len(tool_calls)

        # ── Pre-flight: interrupt check ──────────────────────────────────
        if self._interrupt_requested:
            print(f"{self.log_prefix}🔧 Interrupt: skipping {num_tools} tool call(s)")
            for tc in tool_calls:
                messages.append({
                    "role": "tool",
                    "content": f"[Tool execution cancelled — {tc.function.name} was skipped due to user interrupt]",
                    "tool_call_id": tc.id,
                })
            return

        # ── Parse args + pre-execution bookkeeping ───────────────────────
        parsed_calls = []  # list of (tool_call, function_name, function_args)
        for tool_call in tool_calls:
            function_name = tool_call.function.name

            # Reset nudge counters
            if function_name == "memory":
                self._turns_since_memory = 0
            elif function_name == "skill_manage":
                self._iters_since_skill = 0

            try:
                function_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                function_args = {}
            if not isinstance(function_args, dict):
                function_args = {}

            # Checkpoint for file-mutating tools
            if function_name in ("write_file", "patch") and self._checkpoint_mgr.enabled:
                try:
                    file_path = function_args.get("path", "")
                    if file_path:
                        work_dir = self._checkpoint_mgr.get_working_dir_for_path(file_path)
                        self._checkpoint_mgr.ensure_checkpoint(work_dir, f"before {function_name}")
                except Exception:
                    pass

            # Checkpoint before destructive terminal commands
            if function_name == "terminal" and self._checkpoint_mgr.enabled:
                try:
                    cmd = function_args.get("command", "")
                    if is_destructive_command(cmd):
                        cwd = function_args.get("workdir") or os.getenv("TERMINAL_CWD", os.getcwd())
                        self._checkpoint_mgr.ensure_checkpoint(
                            cwd, f"before terminal: {cmd[:60]}"
                        )
                except Exception:
                    pass

            parsed_calls.append((tool_call, function_name, function_args))

        # ── Logging / callbacks ──────────────────────────────────────────
        tool_names_str = ", ".join(name for _, name, _ in parsed_calls)
        if not self.quiet_mode:
            print(f"  🔧 Concurrent: {num_tools} tool calls — {tool_names_str}")
            for i, (tc, name, args) in enumerate(parsed_calls, 1):
                args_str = json.dumps(args, ensure_ascii=False)
                if self.verbose_logging:
                    print(f"  📞 Tool {i}: {name}({list(args.keys())})")
                    print(f"     Args: {args_str}")
                else:
                    args_preview = args_str[:self.log_prefix_chars] + "..." if len(args_str) > self.log_prefix_chars else args_str
                    print(f"  📞 Tool {i}: {name}({list(args.keys())}) - {args_preview}")

        for tc, name, args in parsed_calls:
            if self.tool_progress_callback:
                try:
                    preview = _build_tool_preview(name, args)
                    self.tool_progress_callback("tool.started", name, preview, args)
                except Exception as cb_err:
                    logging.debug(f"Tool progress callback error: {cb_err}")

        for tc, name, args in parsed_calls:
            if self.tool_start_callback:
                try:
                    self.tool_start_callback(tc.id, name, args)
                except Exception as cb_err:
                    logging.debug(f"Tool start callback error: {cb_err}")

        # ── Concurrent execution ─────────────────────────────────────────
        # Each slot holds (function_name, function_args, function_result, duration, error_flag)
        results = [None] * num_tools

        def _run_tool(index, tool_call, function_name, function_args):
            """Worker function executed in a thread."""
            start = time.time()
            try:
                result = self._invoke_tool(function_name, function_args, effective_task_id, tool_call.id)
            except Exception as tool_error:
                result = f"Error executing tool '{function_name}': {tool_error}"
                logger.error("_invoke_tool raised for %s: %s", function_name, tool_error, exc_info=True)
            duration = time.time() - start
            is_error, _ = _detect_tool_failure(function_name, result)
            if is_error:
                logger.info("tool %s failed (%.2fs): %s", function_name, duration, result[:200])
            else:
                logger.info("tool %s completed (%.2fs, %d chars)", function_name, duration, len(result))
            results[index] = (function_name, function_args, result, duration, is_error)

        # Start spinner for CLI mode (skip when TUI handles tool progress)
        spinner = None
        if self._should_emit_quiet_tool_messages() and self._should_start_quiet_spinner():
            face = random.choice(KawaiiSpinner.KAWAII_WAITING)
            spinner = KawaiiSpinner(f"{face} 🔧 正在并发执行 {num_tools} 个工具", spinner_type='dots', print_fn=self._print_fn)
            spinner.start()

        try:
            max_workers = min(num_tools, MAX_TOOL_WORKERS)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for i, (tc, name, args) in enumerate(parsed_calls):
                    f = executor.submit(_run_tool, i, tc, name, args)
                    futures.append(f)

                # Wait for all to complete (exceptions are captured inside _run_tool)
                concurrent.futures.wait(futures)
        finally:
            if spinner:
                # Build a summary message for the spinner stop
                completed = sum(1 for r in results if r is not None)
                total_dur = sum(r[3] for r in results if r is not None)
                spinner.stop(f"🔧 {completed}/{num_tools} 个工具已完成，总耗时 {total_dur:.1f}s")

        # ── Post-execution: display per-tool results ─────────────────────
        for i, (tc, name, args) in enumerate(parsed_calls):
            r = results[i]
            if r is None:
                # Shouldn't happen, but safety fallback
                function_result = f"Error executing tool '{name}': thread did not return a result"
                tool_duration = 0.0
            else:
                function_name, function_args, function_result, tool_duration, is_error = r

                if is_error:
                    result_preview = function_result[:200] if len(function_result) > 200 else function_result
                    logger.warning("Tool %s returned error (%.2fs): %s", function_name, tool_duration, result_preview)

                if self.tool_progress_callback:
                    try:
                        self.tool_progress_callback(
                            "tool.completed", function_name, None, None,
                            duration=tool_duration, is_error=is_error,
                        )
                    except Exception as cb_err:
                        logging.debug(f"Tool progress callback error: {cb_err}")

                if self.verbose_logging:
                    logging.debug(f"Tool {function_name} completed in {tool_duration:.2f}s")
                    logging.debug(f"Tool result ({len(function_result)} chars): {function_result}")

            # Print cute message per tool
            if self._should_emit_quiet_tool_messages():
                cute_msg = _get_cute_tool_message_impl(name, args, tool_duration, result=function_result)
                self._safe_print(f"  {cute_msg}")
            elif not self.quiet_mode:
                if self.verbose_logging:
                    print(f"  ✅ Tool {i+1} completed in {tool_duration:.2f}s")
                    print(f"     Result: {function_result}")
                else:
                    response_preview = function_result[:self.log_prefix_chars] + "..." if len(function_result) > self.log_prefix_chars else function_result
                    print(f"  ✅ Tool {i+1} completed in {tool_duration:.2f}s - {response_preview}")

            self._current_tool = None
            self._touch_activity(f"tool completed: {name} ({tool_duration:.1f}s)")

            if self.tool_complete_callback:
                try:
                    self.tool_complete_callback(tc.id, name, args, function_result)
                except Exception as cb_err:
                    logging.debug(f"Tool complete callback error: {cb_err}")

            function_result = maybe_persist_tool_result(
                content=function_result,
                tool_name=name,
                tool_use_id=tc.id,
                env=get_active_env(effective_task_id),
            )

            subdir_hints = self._subdirectory_hints.check_tool_call(name, args)
            if subdir_hints:
                function_result += subdir_hints

            tool_msg = {
                "role": "tool",
                "content": function_result,
                "tool_call_id": tc.id,
            }
            messages.append(tool_msg)

        # ── Per-turn aggregate budget enforcement ─────────────────────────
        num_tools = len(parsed_calls)
        if num_tools > 0:
            turn_tool_msgs = messages[-num_tools:]
            enforce_turn_budget(turn_tool_msgs, env=get_active_env(effective_task_id))

    def _execute_tool_calls_sequential(self, assistant_message, messages: list, effective_task_id: str, api_call_count: int = 0) -> None:
        """Execute tool calls sequentially (original behavior). Used for single calls or interactive tools."""
        for i, tool_call in enumerate(assistant_message.tool_calls, 1):
            # SAFETY: check interrupt BEFORE starting each tool.
            # If the user sent "stop" during a previous tool's execution,
            # do NOT start any more tools -- skip them all immediately.
            if self._interrupt_requested:
                remaining_calls = assistant_message.tool_calls[i-1:]
                if remaining_calls:
                    self._vprint(f"{self.log_prefix}🔧 Interrupt: skipping {len(remaining_calls)} tool call(s)", force=True)
                for skipped_tc in remaining_calls:
                    skipped_name = skipped_tc.function.name
                    skip_msg = {
                        "role": "tool",
                        "content": f"[Tool execution cancelled — {skipped_name} was skipped due to user interrupt]",
                        "tool_call_id": skipped_tc.id,
                    }
                    messages.append(skip_msg)
                break

            function_name = tool_call.function.name

            # Reset nudge counters when the relevant tool is actually used
            if function_name == "memory":
                self._turns_since_memory = 0
            elif function_name == "skill_manage":
                self._iters_since_skill = 0

            try:
                function_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                logging.warning(f"Unexpected JSON error after validation: {e}")
                function_args = {}
            if not isinstance(function_args, dict):
                function_args = {}

            if not self.quiet_mode:
                args_str = json.dumps(function_args, ensure_ascii=False)
                if self.verbose_logging:
                    print(f"  📞 Tool {i}: {function_name}({list(function_args.keys())})")
                    print(f"     Args: {args_str}")
                else:
                    args_preview = args_str[:self.log_prefix_chars] + "..." if len(args_str) > self.log_prefix_chars else args_str
                    print(f"  📞 Tool {i}: {function_name}({list(function_args.keys())}) - {args_preview}")

            self._current_tool = function_name
            self._touch_activity(f"executing tool: {function_name}")

            # Set activity callback for long-running tool execution (terminal
            # commands, etc.) so the gateway's inactivity monitor doesn't kill
            # the agent while a command is running.
            try:
                from tools.environments.base import set_activity_callback
                set_activity_callback(self._touch_activity)
            except Exception:
                pass

            if self.tool_progress_callback:
                try:
                    preview = _build_tool_preview(function_name, function_args)
                    self.tool_progress_callback("tool.started", function_name, preview, function_args)
                except Exception as cb_err:
                    logging.debug(f"Tool progress callback error: {cb_err}")

            if self.tool_start_callback:
                try:
                    self.tool_start_callback(tool_call.id, function_name, function_args)
                except Exception as cb_err:
                    logging.debug(f"Tool start callback error: {cb_err}")

            # Checkpoint: snapshot working dir before file-mutating tools
            if function_name in ("write_file", "patch") and self._checkpoint_mgr.enabled:
                try:
                    file_path = function_args.get("path", "")
                    if file_path:
                        work_dir = self._checkpoint_mgr.get_working_dir_for_path(file_path)
                        self._checkpoint_mgr.ensure_checkpoint(
                            work_dir, f"before {function_name}"
                        )
                except Exception:
                    pass  # never block tool execution

            # Checkpoint before destructive terminal commands
            if function_name == "terminal" and self._checkpoint_mgr.enabled:
                try:
                    cmd = function_args.get("command", "")
                    if is_destructive_command(cmd):
                        cwd = function_args.get("workdir") or os.getenv("TERMINAL_CWD", os.getcwd())
                        self._checkpoint_mgr.ensure_checkpoint(
                            cwd, f"before terminal: {cmd[:60]}"
                        )
                except Exception:
                    pass  # never block tool execution

            tool_start_time = time.time()

            if function_name == "todo":
                from tools.todo_tool import todo_tool as _todo_tool
                function_result = _todo_tool(
                    todos=function_args.get("todos"),
                    merge=function_args.get("merge", False),
                    store=self._todo_store,
                )
                tool_duration = time.time() - tool_start_time
                if self._should_emit_quiet_tool_messages():
                    self._vprint(f"  {_get_cute_tool_message_impl('todo', function_args, tool_duration, result=function_result)}")
            elif function_name == "session_search":
                if not self._session_db:
                    function_result = json.dumps({"success": False, "error": "Session database not available."})
                else:
                    from tools.session_search_tool import session_search as _session_search
                    function_result = _session_search(
                        query=function_args.get("query", ""),
                        role_filter=function_args.get("role_filter"),
                        limit=function_args.get("limit", 3),
                        db=self._session_db,
                        current_session_id=self.session_id,
                    )
                tool_duration = time.time() - tool_start_time
                if self._should_emit_quiet_tool_messages():
                    self._vprint(f"  {_get_cute_tool_message_impl('session_search', function_args, tool_duration, result=function_result)}")
            elif function_name == "memory":
                target = function_args.get("target", "memory")
                from tools.memory_tool import memory_tool as _memory_tool
                function_result = _memory_tool(
                    action=function_args.get("action"),
                    target=target,
                    content=function_args.get("content"),
                    old_text=function_args.get("old_text"),
                    store=self._memory_store,
                )
                tool_duration = time.time() - tool_start_time
                if self._should_emit_quiet_tool_messages():
                    self._vprint(f"  {_get_cute_tool_message_impl('memory', function_args, tool_duration, result=function_result)}")
            elif function_name == "clarify":
                from tools.clarify_tool import clarify_tool as _clarify_tool
                function_result = _clarify_tool(
                    question=function_args.get("question", ""),
                    choices=function_args.get("choices"),
                    callback=self.clarify_callback,
                )
                tool_duration = time.time() - tool_start_time
                if self._should_emit_quiet_tool_messages():
                    self._vprint(f"  {_get_cute_tool_message_impl('clarify', function_args, tool_duration, result=function_result)}")
            elif function_name == "delegate_task":
                from tools.delegate_tool import delegate_task as _delegate_task
                tasks_arg = function_args.get("tasks")
                if tasks_arg and isinstance(tasks_arg, list):
                    spinner_label = f"🔀 正在委派 {len(tasks_arg)} 个任务"
                else:
                    goal_preview = (function_args.get("goal") or "")[:30]
                    spinner_label = f"🔀 {goal_preview}" if goal_preview else "🔀 委派中"
                use_rich_delegate_display = self._print_fn is not None
                spinner = None
                # Rich subagent display is the default for CLI/TUI sessions with
                # a safe print sink. Fall back to the legacy spinner elsewhere.
                if (
                    not use_rich_delegate_display
                    and self._should_emit_quiet_tool_messages()
                    and self._should_start_quiet_spinner()
                ):
                    face = random.choice(KawaiiSpinner.KAWAII_WAITING)
                    spinner = KawaiiSpinner(f"{face} {spinner_label}", spinner_type='dots', print_fn=self._print_fn)
                    spinner.start()
                self._delegate_spinner = spinner
                _delegate_result = None
                try:
                    function_result = _delegate_task(
                        goal=function_args.get("goal"),
                        context=function_args.get("context"),
                        toolsets=function_args.get("toolsets"),
                        tasks=tasks_arg,
                        max_iterations=function_args.get("max_iterations"),
                        parent_agent=self,
                        enable_display=use_rich_delegate_display,
                    )
                    _delegate_result = function_result
                finally:
                    self._delegate_spinner = None
                    tool_duration = time.time() - tool_start_time
                    cute_msg = _get_cute_tool_message_impl('delegate_task', function_args, tool_duration, result=_delegate_result)
                    if spinner:
                        spinner.stop(cute_msg)
                    elif (not use_rich_delegate_display) and self._should_emit_quiet_tool_messages():
                        self._vprint(f"  {cute_msg}")
            elif self._context_engine_tool_names and function_name in self._context_engine_tool_names:
                # Context engine tools (lcm_grep, lcm_describe, lcm_expand, etc.)
                spinner = None
                if self.quiet_mode and not self.tool_progress_callback:
                    face = random.choice(KawaiiSpinner.KAWAII_WAITING)
                    emoji = _get_tool_emoji(function_name)
                    preview = _build_tool_preview(function_name, function_args) or function_name
                    spinner = KawaiiSpinner(f"{face} {emoji} {preview}", spinner_type='dots', print_fn=self._print_fn)
                    spinner.start()
                _ce_result = None
                try:
                    function_result = self.context_compressor.handle_tool_call(function_name, function_args, messages=messages)
                    _ce_result = function_result
                except Exception as tool_error:
                    function_result = json.dumps({"error": f"Context engine tool '{function_name}' failed: {tool_error}"})
                    logger.error("context_engine.handle_tool_call raised for %s: %s", function_name, tool_error, exc_info=True)
                finally:
                    tool_duration = time.time() - tool_start_time
                    cute_msg = _get_cute_tool_message_impl(function_name, function_args, tool_duration, result=_ce_result)
                    if spinner:
                        spinner.stop(cute_msg)
                    elif self.quiet_mode:
                        self._vprint(f"  {cute_msg}")
            elif self._memory_manager and self._memory_manager.has_tool(function_name):
                # Memory provider tools (hindsight_retain, etc.)
                # These are not in the tool registry — route through MemoryManager.
                spinner = None
                if self._should_emit_quiet_tool_messages() and self._should_start_quiet_spinner():
                    face = random.choice(KawaiiSpinner.KAWAII_WAITING)
                    emoji = _get_tool_emoji(function_name)
                    preview = _build_tool_preview(function_name, function_args) or function_name
                    spinner = KawaiiSpinner(f"{face} {emoji} {preview}", spinner_type='dots', print_fn=self._print_fn)
                    spinner.start()
                _mem_result = None
                try:
                    function_result = self._memory_manager.handle_tool_call(function_name, function_args)
                    _mem_result = function_result
                except Exception as tool_error:
                    function_result = json.dumps({"error": f"Memory tool '{function_name}' failed: {tool_error}"})
                    logger.error("memory_manager.handle_tool_call raised for %s: %s", function_name, tool_error, exc_info=True)
                finally:
                    tool_duration = time.time() - tool_start_time
                    cute_msg = _get_cute_tool_message_impl(function_name, function_args, tool_duration, result=_mem_result)
                    if spinner:
                        spinner.stop(cute_msg)
                    elif self._should_emit_quiet_tool_messages():
                        self._vprint(f"  {cute_msg}")
            elif self.quiet_mode:
                spinner = None
                if self._should_emit_quiet_tool_messages() and self._should_start_quiet_spinner():
                    face = random.choice(KawaiiSpinner.KAWAII_WAITING)
                    emoji = _get_tool_emoji(function_name)
                    preview = _build_tool_preview(function_name, function_args) or function_name
                    spinner = KawaiiSpinner(f"{face} {emoji} {preview}", spinner_type='dots', print_fn=self._print_fn)
                    spinner.start()
                _spinner_result = None
                try:
                    function_result = handle_function_call(
                        function_name, function_args, effective_task_id,
                        tool_call_id=tool_call.id,
                        session_id=self.session_id or "",
                        enabled_tools=list(self.valid_tool_names) if self.valid_tool_names else None,
                        main_runtime=self._current_main_runtime(),
                    )
                    _spinner_result = function_result
                except Exception as tool_error:
                    function_result = f"Error executing tool '{function_name}': {tool_error}"
                    logger.error("handle_function_call raised for %s: %s", function_name, tool_error, exc_info=True)
                finally:
                    tool_duration = time.time() - tool_start_time
                    cute_msg = _get_cute_tool_message_impl(function_name, function_args, tool_duration, result=_spinner_result)
                    if spinner:
                        spinner.stop(cute_msg)
                    elif self._should_emit_quiet_tool_messages():
                        self._vprint(f"  {cute_msg}")
            else:
                try:
                    function_result = handle_function_call(
                        function_name, function_args, effective_task_id,
                        tool_call_id=tool_call.id,
                        session_id=self.session_id or "",
                        enabled_tools=list(self.valid_tool_names) if self.valid_tool_names else None,
                        main_runtime=self._current_main_runtime(),
                    )
                except Exception as tool_error:
                    function_result = f"Error executing tool '{function_name}': {tool_error}"
                    logger.error("handle_function_call raised for %s: %s", function_name, tool_error, exc_info=True)
                tool_duration = time.time() - tool_start_time

            result_preview = function_result if self.verbose_logging else (
                function_result[:200] if len(function_result) > 200 else function_result
            )

            # Log tool errors to the persistent error log so [error] tags
            # in the UI always have a corresponding detailed entry on disk.
            _is_error_result, _ = _detect_tool_failure(function_name, function_result)
            if _is_error_result:
                logger.warning("Tool %s returned error (%.2fs): %s", function_name, tool_duration, result_preview)
            else:
                logger.info("tool %s completed (%.2fs, %d chars)", function_name, tool_duration, len(function_result))

            if self.tool_progress_callback:
                try:
                    self.tool_progress_callback(
                        "tool.completed", function_name, None, None,
                        duration=tool_duration, is_error=_is_error_result,
                    )
                except Exception as cb_err:
                    logging.debug(f"Tool progress callback error: {cb_err}")

            self._current_tool = None
            self._touch_activity(f"tool completed: {function_name} ({tool_duration:.1f}s)")

            if self.verbose_logging:
                logging.debug(f"Tool {function_name} completed in {tool_duration:.2f}s")
                logging.debug(f"Tool result ({len(function_result)} chars): {function_result}")

            if self.tool_complete_callback:
                try:
                    self.tool_complete_callback(tool_call.id, function_name, function_args, function_result)
                except Exception as cb_err:
                    logging.debug(f"Tool complete callback error: {cb_err}")

            function_result = maybe_persist_tool_result(
                content=function_result,
                tool_name=function_name,
                tool_use_id=tool_call.id,
                env=get_active_env(effective_task_id),
            )

            # Discover subdirectory context files from tool arguments
            subdir_hints = self._subdirectory_hints.check_tool_call(function_name, function_args)
            if subdir_hints:
                function_result += subdir_hints

            tool_msg = {
                "role": "tool",
                "content": function_result,
                "tool_call_id": tool_call.id
            }
            messages.append(tool_msg)

            if not self.quiet_mode:
                if self.verbose_logging:
                    print(f"  ✅ Tool {i} completed in {tool_duration:.2f}s")
                    print(f"     Result: {function_result}")
                else:
                    response_preview = function_result[:self.log_prefix_chars] + "..." if len(function_result) > self.log_prefix_chars else function_result
                    print(f"  ✅ Tool {i} completed in {tool_duration:.2f}s - {response_preview}")

            if self._interrupt_requested and i < len(assistant_message.tool_calls):
                remaining = len(assistant_message.tool_calls) - i
                self._vprint(f"{self.log_prefix}🔧 Interrupt: skipping {remaining} remaining tool call(s)", force=True)
                for skipped_tc in assistant_message.tool_calls[i:]:
                    skipped_name = skipped_tc.function.name
                    skip_msg = {
                        "role": "tool",
                        "content": f"[Tool execution skipped — {skipped_name} was not started. User sent a new message]",
                        "tool_call_id": skipped_tc.id
                    }
                    messages.append(skip_msg)
                break

            if self.tool_delay > 0 and i < len(assistant_message.tool_calls):
                time.sleep(self.tool_delay)

        # ── Per-turn aggregate budget enforcement ─────────────────────────
        num_tools_seq = len(assistant_message.tool_calls)
        if num_tools_seq > 0:
            enforce_turn_budget(messages[-num_tools_seq:], env=get_active_env(effective_task_id))



    def _emit_context_pressure(self, compaction_progress: float, compressor) -> None:
        """Notify the user that context is approaching the compaction threshold.

        Args:
            compaction_progress: How close to compaction (0.0–1.0, where 1.0 = fires).
            compressor: The ContextCompressor instance (for threshold/context info).

        Purely user-facing — does NOT modify the message stream.
        For CLI: prints a formatted line with a progress bar.
        For gateway: fires status_callback so the platform can send a chat message.
        """
        from agent.display import format_context_pressure, format_context_pressure_gateway

        threshold_pct = compressor.threshold_tokens / compressor.context_length if compressor.context_length else 0.5

        # CLI output — always shown (these are user-facing status notifications,
        # not verbose debug output, so they bypass quiet_mode).
        # Gateway users also get the callback below.
        if self.platform in (None, "cli"):
            line = format_context_pressure(
                compaction_progress=compaction_progress,
                threshold_tokens=compressor.threshold_tokens,
                threshold_percent=threshold_pct,
                compression_enabled=self.compression_enabled,
            )
            self._safe_print(line)

        # Gateway / external consumers
        if self.status_callback:
            try:
                msg = format_context_pressure_gateway(
                    compaction_progress=compaction_progress,
                    threshold_percent=threshold_pct,
                    compression_enabled=self.compression_enabled,
                )
                self.status_callback("context_pressure", msg)
            except Exception:
                logger.debug("status_callback error in context pressure", exc_info=True)

    def _handle_max_iterations(self, messages: list, api_call_count: int) -> str:
        """Request a summary when max iterations are reached. Returns the final response text."""
        print(f"⚠️  Reached maximum iterations ({self.max_iterations}). Requesting summary...")

        summary_request = (
            "You've reached the maximum number of tool-calling iterations allowed. "
            "Please provide a final response summarizing what you've found and accomplished so far, "
            "without calling any more tools."
        )
        messages.append({"role": "user", "content": summary_request})

        try:
            # Build API messages, stripping internal-only fields
            # (finish_reason, reasoning) that strict APIs like Mistral reject with 422
            api_messages = []
            for msg in messages:
                api_msg = msg.copy()
                for internal_field in ("reasoning", "finish_reason", "_thinking_prefill"):
                    api_msg.pop(internal_field, None)
                api_messages.append(api_msg)

            effective_system = self._cached_system_prompt or ""
            if self.ephemeral_system_prompt:
                effective_system = (effective_system + "\n\n" + self.ephemeral_system_prompt).strip()
            if effective_system:
                api_messages = [{"role": "system", "content": effective_system}] + api_messages
            if self.prefill_messages:
                sys_offset = 1 if effective_system else 0
                for idx, pfm in enumerate(self.prefill_messages):
                    api_messages.insert(sys_offset + idx, pfm.copy())

            summary_kwargs = build_chat_completion_kwargs(
                self._chat_request_config(),
                api_messages,
                include_tools=False,
                include_request_overrides=False,
            )

            summary_response = self._ensure_primary_openai_client(reason="iteration_limit_summary").chat.completions.create(**summary_kwargs)
            if summary_response.choices and summary_response.choices[0].message.content:
                final_response = summary_response.choices[0].message.content
            else:
                final_response = ""

            if final_response:
                final_response = strip_thinking_blocks(final_response).strip()
                if final_response:
                    messages.append({"role": "assistant", "content": final_response})
                else:
                    final_response = "I reached the iteration limit and couldn't generate a summary."
            else:
                # Retry summary generation
                summary_response = self._ensure_primary_openai_client(reason="iteration_limit_summary_retry").chat.completions.create(**summary_kwargs)
                if summary_response.choices and summary_response.choices[0].message.content:
                    final_response = summary_response.choices[0].message.content
                else:
                    final_response = ""

                if final_response:
                    final_response = strip_thinking_blocks(final_response).strip()
                    if final_response:
                        messages.append({"role": "assistant", "content": final_response})
                    else:
                        final_response = "I reached the iteration limit and couldn't generate a summary."
                else:
                    final_response = "I reached the iteration limit and couldn't generate a summary."

        except Exception as e:
            logging.warning(f"Failed to get summary response: {e}")
            final_response = f"I reached the maximum iterations ({self.max_iterations}) but couldn't summarize. Error: {str(e)}"

        return final_response

    def run_conversation(
        self,
        user_message: str,
        system_message: str = None,
        conversation_history: List[Dict[str, Any]] = None,
        task_id: str = None,
        stream_callback: Optional[callable] = None,
        persist_user_message: Optional[str] = None,
        trace_id: str = None,
    ) -> Dict[str, Any]:
        """
        Run a complete conversation with tool calling until completion.

        Args:
            user_message (str): The user's message/question
            system_message (str): Custom system message (optional, overrides ephemeral_system_prompt if provided)
            conversation_history (List[Dict]): Previous conversation messages (optional)
            task_id (str): Unique identifier for this task to isolate VMs between concurrent tasks (optional, auto-generated if not provided)
            stream_callback: Optional callback invoked with each text delta during streaming.
                Used by the TTS pipeline to start audio generation before the full response.
                When None (default), API calls use the standard non-streaming path.
            persist_user_message: Optional clean user message to store in
                transcripts/history when user_message contains API-only
                synthetic prefixes.
                    or queuing follow-up prefetch work.
            trace_id (str): Per-interaction trace identifier for end-to-end
                observability (C-03).  Stored in activity logs and passed
                through to the gateway/supervisor chain.

        Returns:
            Dict: Complete conversation result with final response and message history
        """
        # Guard stdio against OSError from broken pipes (systemd/headless/daemon).
        # Installed once, transparent when streams are healthy, prevents crash on write.
        _install_safe_stdio()

        # Tag all log records on this thread with the session ID so
        # ``VoidCube logs --session <id>`` can filter a single conversation.
        from VoidCube_core.logging import set_session_context
        set_session_context(self.session_id)

        # If the previous turn activated fallback, restore the primary
        # runtime so this turn gets a fresh attempt with the preferred model.
        # No-op when _fallback_activated is False (gateway, first turn, etc.).
        self._restore_primary_runtime()

        # Sanitize surrogate characters from user input.  Clipboard paste from
        # rich-text editors (Google Docs, Word, etc.) can inject lone surrogates
        # that are invalid UTF-8 and crash JSON serialization in the OpenAI SDK.
        if isinstance(user_message, str):
            user_message = sanitize_surrogates(user_message)
        if isinstance(persist_user_message, str):
            persist_user_message = sanitize_surrogates(persist_user_message)

        # Store stream callback for _interruptible_api_call to pick up
        self._stream_callback = stream_callback
        self._persist_user_message_idx = None
        self._persist_user_message_override = persist_user_message
        # Generate unique task_id if not provided to isolate VMs between concurrent tasks
        effective_task_id = task_id or str(uuid.uuid4())
        
        # Reset retry counters and iteration budget at the start of each turn
        # so subagent usage from a previous turn doesn't eat into the next one.
        self._invalid_tool_retries = 0
        self._invalid_json_retries = 0
        self._empty_content_retries = 0
        self._thinking_prefill_retries = 0
        self._last_content_with_tools = None
        self._mute_post_response = False
        self._unicode_sanitization_passes = 0

        # Pre-turn connection health check: detect and clean up dead TCP
        # connections left over from provider outages or dropped streams.
        # This prevents the next API call from hanging on a zombie socket.
        try:
            if self._cleanup_dead_connections():
                self._emit_status(
                    "🔌 Detected stale connections from a previous provider "
                    "issue — cleaned up automatically. Proceeding with fresh "
                    "connection."
                )
        except Exception:
            pass
        # Replay compression warning through status_callback for gateway
        # platforms (the callback was not wired during __init__).
        if self._compression_warning:
            self._replay_compression_warning()
            self._compression_warning = None  # send once

        # NOTE: _turns_since_memory and _iters_since_skill are NOT reset here.
        # They are initialized in __init__ and must persist across run_conversation
        # calls so that nudge logic accumulates correctly in CLI mode.
        self.iteration_budget = IterationBudget(self.max_iterations)

        # Log conversation turn start for debugging/observability
        _msg_preview = (user_message[:80] + "...") if len(user_message) > 80 else user_message
        _msg_preview = _msg_preview.replace("\n", " ")
        logger.info(
            "conversation turn: session=%s model=%s provider=%s platform=%s history=%d msg=%r",
            self.session_id or "none", self.model, self.provider or "unknown",
            self.platform or "unknown", len(conversation_history or []),
            _msg_preview,
        )

        # Initialize conversation (copy to avoid mutating the caller's list)
        messages = list(conversation_history) if conversation_history else []

        # Hydrate todo store from conversation history (gateway creates a fresh
        # AIAgent per message, so the in-memory store is empty -- we need to
        # recover the todo state from the most recent todo tool response in history)
        if conversation_history and not self._todo_store.has_items():
            self._hydrate_todo_store(conversation_history)
        
        # Prefill messages (few-shot priming) are injected at API-call time only,
        # never stored in the messages list. This keeps them ephemeral: they won't
        # be saved to session DB, session logs, or batch trajectories, but they're
        # automatically re-applied on every API call (including session continuations).
        
        # Track user turns for memory flush and periodic nudge logic
        self._user_turn_count += 1

        # Preserve the original user message (no nudge injection).
        original_user_message = persist_user_message if persist_user_message is not None else user_message

        # Track memory nudge trigger (turn-based, checked here).
        # Skill trigger is checked AFTER the agent loop completes, based on
        # how many tool iterations THIS turn used.
        _should_review_memory = False
        if (self._memory_nudge_interval > 0
                and "memory" in self.valid_tool_names
                and self._memory_store):
            self._turns_since_memory += 1
            if self._turns_since_memory >= self._memory_nudge_interval:
                _should_review_memory = True
                self._turns_since_memory = 0

        # Add user message
        user_msg = {"role": "user", "content": user_message}
        messages.append(user_msg)
        current_turn_user_idx = len(messages) - 1
        self._persist_user_message_idx = current_turn_user_idx
        
        if not self.quiet_mode:
            self._safe_print(f"💬 Starting conversation: '{user_message[:60]}{'...' if len(user_message) > 60 else ''}'")
        
        # ── System prompt (cached per session for prefix caching) ──
        # Built once on first call, reused for all subsequent calls.
        # Only rebuilt after context compression events (which invalidate
        # the cache and reload memory from disk).
        #
        # For continuing sessions (gateway creates a fresh AIAgent per
        # message), we load the stored system prompt from the session DB
        # instead of rebuilding.  Rebuilding would pick up memory changes
        # from disk that the model already knows about (it wrote them!),
        # producing a different system prompt within the same session.
        if self._cached_system_prompt is None:
            stored_prompt = None
            if conversation_history and self._session_db:
                try:
                    session_row = self._session_db.get_session(self.session_id)
                    if session_row:
                        stored_prompt = session_row.get("system_prompt") or None
                except Exception:
                    pass  # Fall through to build fresh

            if stored_prompt:
                # Continuing session — reuse the exact system prompt from
                # the previous turn so session instructions remain stable.
                self._cached_system_prompt = stored_prompt
            else:
                # First turn of a new session — build from scratch.
                self._cached_system_prompt = self._build_system_prompt(system_message)
                # Plugin hook: on_session_start
                # Fired once when a brand-new session is created (not on
                # continuation).  Plugins can use this to initialise
                # session-scoped state (e.g. warm a memory cache).
                try:
                    from VoidCube_cli.plugins import invoke_hook as _invoke_hook
                    _invoke_hook(
                        "on_session_start",
                        session_id=self.session_id,
                        model=self.model,
                        platform=getattr(self, "platform", None) or "",
                    )
                except Exception as exc:
                    logger.warning("on_session_start hook failed: %s", exc)

                # Store the system prompt snapshot in SQLite
                if self._session_db:
                    try:
                        self._session_db.update_system_prompt(self.session_id, self._cached_system_prompt)
                    except Exception as e:
                        logger.debug("Session DB update_system_prompt failed: %s", e)

        active_system_prompt = self._cached_system_prompt

        # ── Preflight context compression ──
        # Before entering the main loop, check if the loaded conversation
        # history already exceeds the model's context threshold.  This handles
        # cases where a user switches to a model with a smaller context window
        # while having a large existing session — compress proactively rather
        # than waiting for an API error (which might be caught as a non-retryable
        # 4xx and abort the request entirely).
        if (
            self.compression_enabled
            and len(messages) > self.context_compressor.protect_first_n
                                + self.context_compressor.protect_last_n + 1
        ):
            # Include tool schema tokens — with many tools these can add
            # 20-30K+ tokens that the old sys+msg estimate missed entirely.
            _preflight_tokens = estimate_request_tokens_rough(
                messages,
                system_prompt=active_system_prompt or "",
                tools=self.tools or None,
            )

            if _preflight_tokens >= self.context_compressor.threshold_tokens:
                logger.info(
                    "Preflight compression: ~%s tokens >= %s threshold (model %s, ctx %s)",
                    f"{_preflight_tokens:,}",
                    f"{self.context_compressor.threshold_tokens:,}",
                    self.model,
                    f"{self.context_compressor.context_length:,}",
                )
                if not self.quiet_mode:
                    self._safe_print(
                        f"📦 Preflight compression: ~{_preflight_tokens:,} tokens "
                        f">= {self.context_compressor.threshold_tokens:,} threshold"
                    )
                # May need multiple passes for very large sessions with small
                # context windows (each pass summarises the middle N turns).
                for _pass in range(3):
                    _orig_len = len(messages)
                    messages, active_system_prompt = self._compress_context(
                        messages, system_message, approx_tokens=_preflight_tokens,
                        task_id=effective_task_id,
                    )
                    if len(messages) >= _orig_len:
                        break  # Cannot compress further
                    # Compression created a new session — clear the history
                    # reference so _flush_messages_to_session_db writes ALL
                    # compressed messages to the new session's SQLite, not
                    # skipping them because conversation_history is still the
                    # pre-compression length.
                    conversation_history = None
                    # Re-estimate after compression
                    _preflight_tokens = estimate_request_tokens_rough(
                        messages,
                        system_prompt=active_system_prompt or "",
                        tools=self.tools or None,
                    )
                    if _preflight_tokens < self.context_compressor.threshold_tokens:
                        break  # Under threshold

        # Plugin hook: pre_llm_call
        # Fired once per turn before the tool-calling loop.  Plugins can
        # return a dict with a ``context`` key (or a plain string) whose
        # value is appended to the current turn's user message.
        #
        # Context is ALWAYS injected into the user message, never the
        # system prompt.  This preserves the prompt cache prefix — the
        # system prompt stays identical across turns so cached tokens
        # are reused.  The system prompt is Voidcube's territory; plugins
        # contribute context alongside the user's input.
        #
        # All injected context is ephemeral (not persisted to session DB).
        _plugin_user_context = ""
        try:
            from VoidCube_cli.plugins import invoke_hook as _invoke_hook
            _pre_results = _invoke_hook(
                "pre_llm_call",
                session_id=self.session_id,
                user_message=original_user_message,
                conversation_history=list(messages),
                is_first_turn=(not bool(conversation_history)),
                model=self.model,
                platform=getattr(self, "platform", None) or "",
                sender_id=getattr(self, "_user_id", None) or "",
            )
            _ctx_parts: list[str] = []
            for r in _pre_results:
                if isinstance(r, dict) and r.get("context"):
                    _ctx_parts.append(str(r["context"]))
                elif isinstance(r, str) and r.strip():
                    _ctx_parts.append(r)
            if _ctx_parts:
                _plugin_user_context = "\n\n".join(_ctx_parts)
        except Exception as exc:
            logger.warning("pre_llm_call hook failed: %s", exc)

        # Main conversation loop
        api_call_count = 0
        final_response = None
        interrupted = False
        length_continue_retries = 0
        truncated_tool_call_retries = 0
        truncated_response_prefix = ""
        compression_attempts = 0
        _turn_exit_reason = "unknown"  # Diagnostic: why the loop ended
        
        # Record the execution thread so interrupt()/clear_interrupt() can
        # scope the tool-level interrupt signal to THIS agent's thread only.
        # Must be set before clear_interrupt() which uses it.
        self._execution_thread_id = threading.current_thread().ident

        # Clear any stale interrupt state at start
        self.clear_interrupt()

        # External memory provider: prefetch once before the tool loop.
        # Reuse the cached result on every iteration to avoid re-calling
        # prefetch_all() on each tool call (10 tool calls = 10x latency + cost).
        # Use original_user_message (clean input) — user_message may contain
        # injected skill content that bloats / breaks provider queries.
        _ext_prefetch_cache = ""
        if self._memory_manager:
            try:
                _query = original_user_message if isinstance(original_user_message, str) else ""
                _ext_prefetch_cache = self._memory_manager.prefetch_all(_query) or ""
            except Exception:
                pass

        while (api_call_count < self.max_iterations and self.iteration_budget.remaining > 0) or self._budget_grace_call:
            # Reset per-turn checkpoint dedup so each iteration can take one snapshot
            self._checkpoint_mgr.new_turn()

            # Check for interrupt request (e.g., user sent new message)
            if self._interrupt_requested:
                interrupted = True
                _turn_exit_reason = "interrupted_by_user"
                if not self.quiet_mode:
                    self._safe_print("\n🔧 Breaking out of tool loop due to interrupt...")
                break
            
            api_call_count += 1
            self._api_call_count = api_call_count
            self._touch_activity(f"starting API call #{api_call_count}")

            # Grace call: the budget is exhausted but we gave the model one
            # more chance.  Consume the grace flag so the loop exits after
            # this iteration regardless of outcome.
            if self._budget_grace_call:
                self._budget_grace_call = False
            elif not self.iteration_budget.consume():
                _turn_exit_reason = "budget_exhausted"
                if not self.quiet_mode:
                    self._safe_print(f"\n⚠️  Iteration budget exhausted ({self.iteration_budget.used}/{self.iteration_budget.max_total} iterations used)")
                break

            # Fire step_callback for gateway hooks (agent:step event)
            if self.step_callback is not None:
                try:
                    prev_tools = []
                    for _idx, _m in enumerate(reversed(messages)):
                        if _m.get("role") == "assistant" and _m.get("tool_calls"):
                            _fwd_start = len(messages) - _idx
                            _results_by_id = {}
                            for _tm in messages[_fwd_start:]:
                                if _tm.get("role") != "tool":
                                    break
                                _tcid = _tm.get("tool_call_id")
                                if _tcid:
                                    _results_by_id[_tcid] = _tm.get("content", "")
                            prev_tools = [
                                {
                                    "name": tc["function"]["name"],
                                    "result": _results_by_id.get(tc.get("id")),
                                }
                                for tc in _m["tool_calls"]
                                if isinstance(tc, dict)
                            ]
                            break
                    self.step_callback(api_call_count, prev_tools)
                except Exception as _step_err:
                    logger.debug("step_callback error (iteration %s): %s", api_call_count, _step_err)

            # Track tool-calling iterations for skill nudge.
            # Counter resets whenever skill_manage is actually used.
            if (self._skill_nudge_interval > 0
                    and "skill_manage" in self.valid_tool_names):
                self._iters_since_skill += 1
            
            # Prepare messages for API call
            # If we have an ephemeral system prompt, prepend it to the messages
            # Note: Reasoning is embedded in content via <think> tags for trajectory storage.
            # However, providers like Moonshot AI require a separate 'reasoning_content' field
            # on assistant messages with tool_calls. We handle both cases here.
            api_messages = []
            for idx, msg in enumerate(messages):
                api_msg = msg.copy()

                # Inject ephemeral context into the current turn's user message.
                # Sources: memory manager prefetch + plugin pre_llm_call hooks
                # with target="user_message" (the default).  Both are
                # API-call-time only — the original message in `messages` is
                # never mutated, so nothing leaks into session persistence.
                if idx == current_turn_user_idx and msg.get("role") == "user":
                    _injections = []
                    if _ext_prefetch_cache:
                        _fenced = build_memory_context_block(_ext_prefetch_cache)
                        if _fenced:
                            _injections.append(_fenced)
                    if _plugin_user_context:
                        _injections.append(_plugin_user_context)
                    if _injections:
                        _base = api_msg.get("content", "")
                        if isinstance(_base, str):
                            api_msg["content"] = _base + "\n\n" + "\n\n".join(_injections)

                # For ALL assistant messages, pass reasoning back to the API
                # This ensures multi-turn reasoning context is preserved
                if msg.get("role") == "assistant":
                    reasoning_text = msg.get("reasoning")
                    if reasoning_text:
                        # Add reasoning_content for API compatibility (Moonshot AI, Novita, OpenRouter)
                        api_msg["reasoning_content"] = reasoning_text

                # Remove 'reasoning' field - it's for trajectory storage only
                # We've copied it to 'reasoning_content' for the API above
                if "reasoning" in api_msg:
                    api_msg.pop("reasoning")
                # Remove finish_reason - not accepted by strict APIs (e.g. Mistral)
                if "finish_reason" in api_msg:
                    api_msg.pop("finish_reason")
                # Strip internal thinking-prefill marker
                api_msg.pop("_thinking_prefill", None)
                # Keep 'reasoning_details' - OpenRouter uses this for multi-turn reasoning context
                # The signature field helps maintain reasoning continuity
                api_messages.append(api_msg)

            # Build the final system message: cached prompt + ephemeral system prompt.
            # Ephemeral additions are API-call-time only (not persisted to session DB).
            # External recall context is injected into the user message, not the system
            # prompt, so the stable cache prefix remains unchanged.
            effective_system = active_system_prompt or ""
            if self.ephemeral_system_prompt:
                effective_system = (effective_system + "\n\n" + self.ephemeral_system_prompt).strip()
            # NOTE: Plugin context from pre_llm_call hooks is injected into the
            # user message (see injection block above), NOT the system prompt.
            # This is intentional — system prompt modifications break the prompt
            # cache prefix.  The system prompt is reserved for Voidcube internals.
            if effective_system:
                api_messages = [{"role": "system", "content": effective_system}] + api_messages

            # Inject ephemeral prefill messages right after the system prompt
            # but before conversation history. Same API-call-time-only pattern.
            if self.prefill_messages:
                sys_offset = 1 if effective_system else 0
                for idx, pfm in enumerate(self.prefill_messages):
                    api_messages.insert(sys_offset + idx, pfm.copy())

            # Safety net: strip orphaned tool results / add stubs for missing
            # results before sending to the API.  Runs unconditionally — not
            # gated on context_compressor — so orphans from session loading or
            # manual message manipulation are always caught.
            api_messages = self._sanitize_api_messages(api_messages)

            # Normalize message whitespace and tool-call JSON for consistent
            # prefix matching.  Ensures bit-perfect prefixes across turns,
            # which enables KV cache reuse on local inference servers
            # (llama.cpp, vLLM, Ollama) and improves cache hit rates for
            # cloud providers.  Operates on api_messages (the API copy) so
            # the original conversation history in `messages` is untouched.
            for am in api_messages:
                if isinstance(am.get("content"), str):
                    am["content"] = am["content"].strip()
            for am in api_messages:
                tcs = am.get("tool_calls")
                if not tcs:
                    continue
                new_tcs = []
                for tc in tcs:
                    if isinstance(tc, dict) and "function" in tc:
                        try:
                            args_obj = json.loads(tc["function"]["arguments"])
                            tc = {**tc, "function": {
                                **tc["function"],
                                "arguments": json.dumps(
                                    args_obj, separators=(",", ":"),
                                    sort_keys=True,
                                ),
                            }}
                        except Exception:
                            pass
                    new_tcs.append(tc)
                am["tool_calls"] = new_tcs

            # Calculate approximate request size for logging
            total_chars = sum(len(str(msg)) for msg in api_messages)
            approx_tokens = estimate_messages_tokens_rough(api_messages)
            
            # Thinking spinner for quiet mode (animated during API call)
            thinking_spinner = None
            
            if not self.quiet_mode:
                self._vprint(f"\n{self.log_prefix}🔄 正在发起 API 调用 #{api_call_count}/{self.max_iterations}...")
                self._vprint(f"{self.log_prefix}   📊 请求规模: {len(api_messages)} 条消息, ~{approx_tokens:,} tokens (~{total_chars:,} 字符)")
                self._vprint(f"{self.log_prefix}   🔧 可用工具: {len(self.tools) if self.tools else 0}")
            else:
                # Animated thinking spinner in quiet mode
                face = random.choice(KawaiiSpinner.KAWAII_THINKING)
                verb = random.choice(KawaiiSpinner.THINKING_VERBS)
                if self.thinking_callback:
                    # CLI TUI mode: use prompt_toolkit widget instead of raw spinner
                    # (works in both streaming and non-streaming modes)
                    self.thinking_callback(f"{face} {verb}...")
                elif not self._has_stream_consumers() and self._should_start_quiet_spinner():
                    # Raw KawaiiSpinner only when no streaming consumers and the
                    # spinner output has a safe sink.
                    spinner_type = random.choice(['brain', 'sparkle', 'pulse', 'moon', 'star'])
                    thinking_spinner = KawaiiSpinner(f"{face} {verb}...", spinner_type=spinner_type, print_fn=self._print_fn)
                    thinking_spinner.start()
            
            # Log request details if verbose
            if self.verbose_logging:
                logging.debug(f"API Request - Model: {self.model}, Messages: {len(messages)}, Tools: {len(self.tools) if self.tools else 0}")
                logging.debug(f"Last message role: {messages[-1]['role'] if messages else 'none'}")
                logging.debug(f"Total message size: ~{approx_tokens:,} tokens")
            
            api_start_time = time.time()
            retry_count = 0
            max_retries = 3
            primary_recovery_attempted = False
            max_compression_attempts = 3
            nous_auth_retry_attempted=False
            has_retried_429 = False
            restart_with_compressed_messages = False
            restart_with_length_continuation = False

            finish_reason = "stop"
            response = None  # Guard against UnboundLocalError if all retries fail
            api_kwargs = None  # Guard against UnboundLocalError in except handler

            while retry_count < max_retries:
                try:
                    self._reset_stream_delivery_tracking()
                    api_kwargs = build_chat_completion_kwargs(
                        self._chat_request_config(),
                        api_messages,
                    )
                    try:
                        from VoidCube_cli.plugins import invoke_hook as _invoke_hook
                        _invoke_hook(
                            "pre_api_request",
                            task_id=effective_task_id,
                            session_id=self.session_id or "",
                            platform=self.platform or "",
                            model=self.model,
                            provider=self.provider,
                            base_url=self.base_url,
                            api_call_count=api_call_count,
                            message_count=len(api_messages),
                            tool_count=len(self.tools or []),
                            approx_input_tokens=approx_tokens,
                            request_char_count=total_chars,
                            max_tokens=self.max_tokens,
                        )
                    except Exception:
                        pass

                    if env_var_enabled("VOIDCUBE_DUMP_REQUESTS"):
                        self._dump_api_request_debug(api_kwargs, reason="preflight")

                    # Always prefer the streaming path — even without stream
                    # consumers.  Streaming gives us fine-grained health
                    # checking (90s stale-stream detection, 60s read timeout)
                    # that the non-streaming path lacks.  Without this,
                    # subagents and other quiet-mode callers can hang
                    # indefinitely when the provider keeps the connection
                    # alive with SSE pings but never delivers a response.
                    # The streaming path is a no-op for callbacks when no
                    # consumers are registered, and falls back to non-
                    # streaming automatically if the provider doesn't
                    # support it.
                    def _stop_spinner():
                        nonlocal thinking_spinner
                        if thinking_spinner:
                            thinking_spinner.stop("")
                            thinking_spinner = None
                        if self.thinking_callback:
                            self.thinking_callback("")

                    _use_streaming = True
                    if not self._has_stream_consumers():
                        # No display/TTS consumer. Still prefer streaming for
                        # health checking, but skip for Mock clients in tests
                        # (mocks return SimpleNamespace, not stream iterators).
                        from unittest.mock import Mock
                        if isinstance(getattr(self, "client", None), Mock):
                            _use_streaming = False

                    if _use_streaming:
                        response = self._interruptible_streaming_api_call(
                            api_kwargs, on_first_delta=_stop_spinner
                        )
                    else:
                        response = self._interruptible_api_call(api_kwargs)
                    
                    api_duration = time.time() - api_start_time
                    
                    # Stop thinking spinner silently -- the response box or tool
                    # execution messages that follow are more informative.
                    if thinking_spinner:
                        thinking_spinner.stop("")
                        thinking_spinner = None
                    if self.thinking_callback:
                        self.thinking_callback("")
                    
                    if not self.quiet_mode:
                        self._vprint(f"{self.log_prefix}⏱️  API call completed in {api_duration:.2f}s")
                    
                    if self.verbose_logging:
                        # Log response with provider info if available
                        resp_model = getattr(response, 'model', 'N/A') if response else 'N/A'
                        logging.debug(f"API Response received - Model: {resp_model}, Usage: {response.usage if hasattr(response, 'usage') else 'N/A'}")
                    
                    # Validate response shape before proceeding
                    response_invalid = False
                    error_details = []
                    if response is None or not hasattr(response, 'choices') or response.choices is None or not response.choices:
                        response_invalid = True
                        if response is None:
                            error_details.append("response is None")
                        elif not hasattr(response, 'choices'):
                            error_details.append("response has no 'choices' attribute")
                        elif response.choices is None:
                            error_details.append("response.choices is None")
                        else:
                            error_details.append("response.choices is empty")

                    if response_invalid:
                        # Stop spinner before printing error messages
                        if thinking_spinner:
                            thinking_spinner.stop("(´;ω;`) oops, retrying...")
                            thinking_spinner = None
                        if self.thinking_callback:
                            self.thinking_callback("")
                        
                        # Invalid response — could be rate limiting, provider timeout,
                        # upstream server error, or malformed response.
                        retry_count += 1
                        
                        # Eager fallback: empty/malformed responses are a common
                        # rate-limit symptom.  Switch to fallback immediately
                        # rather than retrying with extended backoff.
                        if self._fallback_index < len(self._fallback_chain):
                            self._emit_status("⚠️ Empty/malformed response — switching to fallback...")
                        if self._try_activate_fallback():
                            retry_count = 0
                            compression_attempts = 0
                            primary_recovery_attempted = False
                            continue

                        # Check for error field in response (some providers include this)
                        error_msg = "Unknown"
                        provider_name = "Unknown"
                        if response and hasattr(response, 'error') and response.error:
                            error_msg = str(response.error)
                            # Try to extract provider from error metadata
                            if hasattr(response.error, 'metadata') and response.error.metadata:
                                provider_name = response.error.metadata.get('provider_name', 'Unknown')
                        elif response and hasattr(response, 'message') and response.message:
                            error_msg = str(response.message)
                        
                        # Try to get provider from model field (OpenRouter often returns actual model used)
                        if provider_name == "Unknown" and response and hasattr(response, 'model') and response.model:
                            provider_name = f"model={response.model}"
                        
                        # Check for x-openrouter-provider or similar metadata
                        if provider_name == "Unknown" and response:
                            # Log all response attributes for debugging
                            resp_attrs = {k: str(v)[:100] for k, v in vars(response).items() if not k.startswith('_')}
                            if self.verbose_logging:
                                logging.debug(f"Response attributes for invalid response: {resp_attrs}")
                        
                        # Extract error code from response for contextual diagnostics
                        _resp_error_code = None
                        if response and hasattr(response, 'error') and response.error:
                            _code_raw = getattr(response.error, 'code', None)
                            if _code_raw is None and isinstance(response.error, dict):
                                _code_raw = response.error.get('code')
                            if _code_raw is not None:
                                try:
                                    _resp_error_code = int(_code_raw)
                                except (TypeError, ValueError):
                                    pass

                        # Build a human-readable failure hint from the error code
                        # and response time, instead of always assuming rate limiting.
                        if _resp_error_code == 524:
                            _failure_hint = f"upstream provider timed out (Cloudflare 524, {api_duration:.0f}s)"
                        elif _resp_error_code == 504:
                            _failure_hint = f"upstream gateway timeout (504, {api_duration:.0f}s)"
                        elif _resp_error_code == 429:
                            _failure_hint = f"rate limited by upstream provider (429)"
                        elif _resp_error_code in (500, 502):
                            _failure_hint = f"upstream server error ({_resp_error_code}, {api_duration:.0f}s)"
                        elif _resp_error_code in (503, 529):
                            _failure_hint = f"upstream provider overloaded ({_resp_error_code})"
                        elif _resp_error_code is not None:
                            _failure_hint = f"upstream error (code {_resp_error_code}, {api_duration:.0f}s)"
                        elif api_duration < 10:
                            _failure_hint = f"fast response ({api_duration:.1f}s) — likely rate limited"
                        elif api_duration > 60:
                            _failure_hint = f"slow response ({api_duration:.0f}s) — likely upstream timeout"
                        else:
                            _failure_hint = f"response time {api_duration:.1f}s"

                        self._vprint(f"{self.log_prefix}⚠️  Invalid API response (attempt {retry_count}/{max_retries}): {', '.join(error_details)}", force=True)
                        self._vprint(f"{self.log_prefix}   🏢 Provider: {provider_name}", force=True)
                        cleaned_provider_error = clean_error_message(error_msg)
                        self._vprint(f"{self.log_prefix}   📝 Provider message: {cleaned_provider_error}", force=True)
                        self._vprint(f"{self.log_prefix}   ⏱️  {_failure_hint}", force=True)
                        
                        if retry_count >= max_retries:
                            # Try fallback before giving up
                            self._emit_status(f"⚠️ Max retries ({max_retries}) for invalid responses — trying fallback...")
                            if self._try_activate_fallback():
                                retry_count = 0
                                compression_attempts = 0
                                primary_recovery_attempted = False
                                continue
                            self._emit_status(f"❌ Max retries ({max_retries}) exceeded for invalid responses. Giving up.")
                            logging.error(f"{self.log_prefix}Invalid API response after {max_retries} retries.")
                            self._persist_session(messages, conversation_history)
                            return {
                                "messages": messages,
                                "completed": False,
                                "api_calls": api_call_count,
                                "error": f"Invalid API response after {max_retries} retries: {_failure_hint}",
                                "failed": True  # Mark as failure for filtering
                            }
                        
                        # Backoff before retry — jittered exponential: 5s base, 120s cap
                        wait_time = jittered_backoff(retry_count, base_delay=5.0, max_delay=120.0)
                        self._vprint(f"{self.log_prefix}⏳ Retrying in {wait_time:.1f}s ({_failure_hint})...", force=True)
                        logging.warning(f"Invalid API response (retry {retry_count}/{max_retries}): {', '.join(error_details)} | Provider: {provider_name}")
                        
                        # Sleep in small increments to stay responsive to interrupts
                        sleep_end = time.time() + wait_time
                        while time.time() < sleep_end:
                            if self._interrupt_requested:
                                self._vprint(f"{self.log_prefix}🔧 Interrupt detected during retry wait, aborting.", force=True)
                                self._persist_session(messages, conversation_history)
                                self.clear_interrupt()
                                return {
                                    "final_response": f"Operation interrupted during retry ({_failure_hint}, attempt {retry_count}/{max_retries}).",
                                    "messages": messages,
                                    "api_calls": api_call_count,
                                    "completed": False,
                                    "interrupted": True,
                                }
                            time.sleep(0.2)
                        continue  # Retry the API call

                    # Check finish_reason before proceeding
                    finish_reason = response.choices[0].finish_reason

                    if finish_reason == "length":
                        self._vprint(f"{self.log_prefix}⚠️  Response truncated (finish_reason='length') - model hit max output tokens", force=True)

                        # ── Detect thinking-budget exhaustion ──────────────
                        # When the model spends ALL output tokens on reasoning
                        # and has none left for the response, continuation
                        # retries are pointless.  Detect this early and give a
                        # targeted error instead of wasting 3 API calls.
                        _trunc_content = None
                        _trunc_has_tool_calls = False
                        _trunc_msg = response.choices[0].message if (hasattr(response, "choices") and response.choices) else None
                        _trunc_content = getattr(_trunc_msg, "content", None) if _trunc_msg else None
                        _trunc_has_tool_calls = bool(getattr(_trunc_msg, "tool_calls", None)) if _trunc_msg else False

                        # A response is "thinking exhausted" only when the model
                        # actually produced reasoning blocks but no visible text after
                        # them.  Models that do not use <think> tags (e.g. GLM-4.7 on
                        # NVIDIA Build, minimax) may return content=None or an empty
                        # string for unrelated reasons — treat those as normal
                        # truncations that deserve continuation retries, not as
                        # thinking-budget exhaustion.
                        _has_think_tags = has_thinking_tags(_trunc_content or "")
                        _thinking_exhausted = (
                            not _trunc_has_tool_calls
                            and _has_think_tags
                            and (
                                (_trunc_content is not None and not has_visible_content(_trunc_content))
                                or _trunc_content is None
                            )
                        )

                        if _thinking_exhausted:
                            _exhaust_error = (
                                "Model used all output tokens on reasoning with none left "
                                "for the response. Try lowering reasoning effort or "
                                "increasing max_tokens."
                            )
                            self._vprint(
                                f"{self.log_prefix}💭 Reasoning exhausted the output token budget — "
                                f"no visible response was produced.",
                                force=True,
                            )
                            # Return a user-friendly message as the response so
                            # CLI (response box) and gateway (chat message) both
                            # display it naturally instead of a suppressed error.
                            _exhaust_response = (
                                "⚠️ **Thinking Budget Exhausted**\n\n"
                                "The model used all its output tokens on reasoning "
                                "and had none left for the actual response.\n\n"
                                "To fix this:\n"
                                "→ Lower reasoning effort: `/thinkon low` or `/thinkon minimal`\n"
                                "→ Increase the output token limit: "
                                "set `model.max_tokens` in config.yaml"
                            )
                            self._cleanup_task_resources(effective_task_id)
                            self._persist_session(messages, conversation_history)
                            return {
                                "final_response": _exhaust_response,
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": _exhaust_error,
                            }

                        assistant_message = response.choices[0].message
                        if not assistant_message.tool_calls:
                            length_continue_retries += 1
                            interim_msg = self._build_assistant_message(assistant_message, finish_reason)
                            messages.append(interim_msg)
                            if assistant_message.content:
                                truncated_response_prefix += assistant_message.content

                            if length_continue_retries < 3:
                                self._vprint(
                                    f"{self.log_prefix}↻ Requesting continuation "
                                    f"({length_continue_retries}/3)..."
                                )
                                continue_msg = {
                                    "role": "user",
                                    "content": (
                                        "[System: Your previous response was truncated by the output "
                                        "length limit. Continue exactly where you left off. Do not "
                                        "restart or repeat prior text. Finish the answer directly.]"
                                    ),
                                }
                                messages.append(continue_msg)
                                self._session_messages = messages
                                self._save_session_log(messages)
                                restart_with_length_continuation = True
                                break

                            partial_response = strip_thinking_blocks(truncated_response_prefix).strip()
                            self._cleanup_task_resources(effective_task_id)
                            self._persist_session(messages, conversation_history)
                            return {
                                "final_response": partial_response or None,
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": "Response remained truncated after 3 continuation attempts",
                            }

                        if assistant_message.tool_calls:
                            if truncated_tool_call_retries < 1:
                                truncated_tool_call_retries += 1
                                self._vprint(
                                    f"{self.log_prefix}⚠️  Truncated tool call detected — retrying API call...",
                                    force=True,
                                )
                                # Don't append the broken response to messages;
                                # just re-run the same API call from the current
                                # message state, giving the model another chance.
                                continue
                            self._vprint(
                                f"{self.log_prefix}⚠️  Truncated tool call response detected again — refusing to execute incomplete tool arguments.",
                                force=True,
                            )
                            self._cleanup_task_resources(effective_task_id)
                            self._persist_session(messages, conversation_history)
                            return {
                                "final_response": None,
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": "Response truncated due to output length limit",
                            }

                        # If we have prior messages, roll back to last complete state
                        if len(messages) > 1:
                            self._vprint(f"{self.log_prefix}   ⏪ Rolling back to last complete assistant turn")
                            rolled_back_messages = self._get_messages_up_to_last_assistant(messages)

                            self._cleanup_task_resources(effective_task_id)
                            self._persist_session(messages, conversation_history)

                            return {
                                "final_response": None,
                                "messages": rolled_back_messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": "Response truncated due to output length limit"
                            }
                        else:
                            # First message was truncated - mark as failed
                            self._vprint(f"{self.log_prefix}❌ First response truncated - cannot recover", force=True)
                            self._persist_session(messages, conversation_history)
                            return {
                                "final_response": None,
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "failed": True,
                                "error": "First response truncated due to output length limit"
                            }
                    
                    # Track actual token usage from response for context management
                    if hasattr(response, 'usage') and response.usage:
                        canonical_usage = normalize_usage(response.usage)
                        prompt_tokens = canonical_usage.prompt_tokens
                        completion_tokens = canonical_usage.output_tokens
                        total_tokens = canonical_usage.total_tokens
                        usage_dict = {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                        }
                        self.context_compressor.update_from_response(usage_dict)

                        # Cache discovered context length after successful call.
                        # Only persist limits confirmed by the provider (parsed
                        # from the error message), not guessed probe tiers.
                        if getattr(self.context_compressor, "_context_probed", False):
                            ctx = self.context_compressor.context_length
                            if getattr(self.context_compressor, "_context_probe_persistable", False):
                                save_context_length(self.model, self.base_url, ctx)
                                self._safe_print(f"{self.log_prefix}💾 Cached context length: {ctx:,} tokens for {self.model}")
                            self.context_compressor._context_probed = False
                            self.context_compressor._context_probe_persistable = False

                        self.session_prompt_tokens += prompt_tokens
                        self.session_completion_tokens += completion_tokens
                        self.session_total_tokens += total_tokens
                        self.session_api_calls += 1
                        self.session_input_tokens += canonical_usage.input_tokens
                        self.session_output_tokens += canonical_usage.output_tokens
                        self.session_cache_read_tokens += canonical_usage.cache_read_tokens
                        self.session_cache_write_tokens += canonical_usage.cache_write_tokens
                        self.session_reasoning_tokens += canonical_usage.reasoning_tokens

                        # Log API call details for debugging/observability
                        _cache_pct = ""
                        if canonical_usage.cache_read_tokens and prompt_tokens:
                            _cache_pct = f" cache={canonical_usage.cache_read_tokens}/{prompt_tokens} ({100*canonical_usage.cache_read_tokens/prompt_tokens:.0f}%)"
                        logger.info(
                            "API call #%d: model=%s provider=%s in=%d out=%d total=%d latency=%.1fs%s",
                            self.session_api_calls, self.model, self.provider or "unknown",
                            prompt_tokens, completion_tokens, total_tokens,
                            api_duration, _cache_pct,
                        )

                        cost_result = estimate_usage_cost(
                            self.model,
                            canonical_usage,
                            provider=self.provider,
                            base_url=self.base_url,
                            api_key=getattr(self, "api_key", ""),
                        )
                        if cost_result.amount_usd is not None:
                            self.session_estimated_cost_usd += float(cost_result.amount_usd)
                        self.session_cost_status = cost_result.status
                        self.session_cost_source = cost_result.source

                        # Persist token counts to session DB for /insights.
                        # Do this for every platform with a session_id so non-CLI
                        # sessions (gateway, cron, delegated runs) cannot lose
                        # token/accounting data if a higher-level persistence path
                        # is skipped or fails. Gateway/session-store writes use
                        # absolute totals, so they safely overwrite these per-call
                        # deltas instead of double-counting them.
                        if self._session_db and self.session_id:
                            try:
                                self._session_db.update_token_counts(
                                    self.session_id,
                                    input_tokens=canonical_usage.input_tokens,
                                    output_tokens=canonical_usage.output_tokens,
                                    cache_read_tokens=canonical_usage.cache_read_tokens,
                                    cache_write_tokens=canonical_usage.cache_write_tokens,
                                    reasoning_tokens=canonical_usage.reasoning_tokens,
                                    estimated_cost_usd=float(cost_result.amount_usd)
                                    if cost_result.amount_usd is not None else None,
                                    cost_status=cost_result.status,
                                    cost_source=cost_result.source,
                                    billing_provider=self.provider,
                                    billing_base_url=self.base_url,
                                    billing_mode="subscription_included"
                                    if cost_result.status == "included" else None,
                                    model=self.model,
                                )
                            except Exception:
                                pass  # never block the agent loop
                        
                        if self.verbose_logging:
                            logging.debug(f"Token usage: prompt={usage_dict['prompt_tokens']:,}, completion={usage_dict['completion_tokens']:,}, total={usage_dict['total_tokens']:,}")
                        
                    has_retried_429 = False  # Reset on success
                    self._touch_activity(f"API call #{api_call_count} completed")
                    break  # Success, exit retry loop

                except InterruptedError:
                    if thinking_spinner:
                        thinking_spinner.stop("")
                        thinking_spinner = None
                    if self.thinking_callback:
                        self.thinking_callback("")
                    api_elapsed = time.time() - api_start_time
                    self._vprint(f"{self.log_prefix}🔧 Interrupted during API call.", force=True)
                    self._persist_session(messages, conversation_history)
                    interrupted = True
                    final_response = f"Operation interrupted: waiting for model response ({api_elapsed:.1f}s elapsed)."
                    break

                except Exception as api_error:
                    # Stop spinner before printing error messages
                    if thinking_spinner:
                        thinking_spinner.stop("(╥_╥) error, retrying...")
                        thinking_spinner = None
                    if self.thinking_callback:
                        self.thinking_callback("")

                    # -----------------------------------------------------------
                    # UnicodeEncodeError recovery.  Two common causes:
                    #   1. Lone surrogates (U+D800..U+DFFF) from clipboard paste
                    #      (Google Docs, rich-text editors) — sanitize and retry.
                    #   2. ASCII codec on systems with LANG=C or non-UTF-8 locale
                    #      (e.g. Chromebooks) — any non-ASCII character fails.
                    #      Detect via the error message mentioning 'ascii' codec.
                    # We sanitize messages in-place and may retry twice:
                    # first to strip surrogates, then once more for pure
                    # ASCII-only locale sanitization if needed.
                    # -----------------------------------------------------------
                    if isinstance(api_error, UnicodeEncodeError) and getattr(self, '_unicode_sanitization_passes', 0) < 2:
                        _err_str = str(api_error).lower()
                        _is_ascii_codec = "'ascii'" in _err_str or "ascii" in _err_str
                        _surrogates_found = sanitize_messages_surrogates(messages)
                        if _surrogates_found:
                            self._unicode_sanitization_passes += 1
                            self._vprint(
                                f"{self.log_prefix}⚠️  Stripped invalid surrogate characters from messages. Retrying...",
                                force=True,
                            )
                            continue
                        if _is_ascii_codec:
                            # ASCII codec: the system encoding can't handle
                            # non-ASCII characters at all. Sanitize all
                            # non-ASCII content from messages and retry.
                            if sanitize_messages_non_ascii(messages):
                                self._unicode_sanitization_passes += 1
                                self._vprint(
                                    f"{self.log_prefix}⚠️  System encoding is ASCII — stripped non-ASCII characters from messages. Retrying...",
                                    force=True,
                                )
                                continue
                        # Nothing to sanitize in messages — might be in system
                        # prompt or prefill. Fall through to normal error path.

                    # ── Classify the error for structured recovery decisions ──
                    _compressor = getattr(self, "context_compressor", None)
                    _ctx_len = getattr(_compressor, "context_length", 200000) if _compressor else 200000
                    classified = classify_api_error(
                        api_error,
                        provider=getattr(self, "provider", "") or "",
                        model=getattr(self, "model", "") or "",
                        approx_tokens=approx_tokens,
                        context_length=_ctx_len,
                        num_messages=len(api_messages) if api_messages else 0,
                    )
                    status_code = classified.status_code
                    error_context = classified.error_context
                    logger.debug(
                        "Error classified: reason=%s status=%s retryable=%s compress=%s rotate=%s fallback=%s",
                        classified.reason.value, classified.status_code,
                        classified.retryable, classified.should_compress,
                        classified.should_rotate_credential, classified.should_fallback,
                    )

                    recovered_with_pool, has_retried_429 = self._recover_with_credential_pool(
                        status_code=status_code,
                        has_retried_429=has_retried_429,
                        classified_reason=classified.reason,
                        error_context=error_context,
                    )
                    if recovered_with_pool:
                        continue
                    if (
                        self.provider == "nous"
                        and status_code == 401
                        and not nous_auth_retry_attempted
                    ):
                        nous_auth_retry_attempted = True
                        if self._try_refresh_nous_client_credentials(force=True):
                            print(f"{self.log_prefix}🔐 Nous agent key refreshed after 401. Retrying request...")
                            continue
                    retry_count += 1
                    elapsed_time = time.time() - api_start_time
                    
                    error_type = type(api_error).__name__
                    error_msg = str(api_error).lower()
                    _error_summary = summarize_api_error(api_error)
                    logger.warning(
                        "API call failed (attempt %s/%s) error_type=%s %s summary=%s",
                        retry_count,
                        max_retries,
                        error_type,
                        self._client_log_context(),
                        _error_summary,
                    )

                    _provider = getattr(self, "provider", "unknown")
                    _base = getattr(self, "base_url", "unknown")
                    _model = getattr(self, "model", "unknown")
                    _status_code_str = f" [HTTP {status_code}]" if status_code else ""
                    self._vprint(f"{self.log_prefix}⚠️  API call failed (attempt {retry_count}/{max_retries}): {error_type}{_status_code_str}", force=True)
                    self._vprint(f"{self.log_prefix}   🔌 Provider: {_provider}  Model: {_model}", force=True)
                    self._vprint(f"{self.log_prefix}   🌐 Endpoint: {_base}", force=True)
                    self._vprint(f"{self.log_prefix}   📝 Error: {_error_summary}", force=True)
                    if status_code and status_code < 500:
                        _err_body = getattr(api_error, "body", None)
                        _err_body_str = str(_err_body)[:300] if _err_body else None
                        if _err_body_str:
                            self._vprint(f"{self.log_prefix}   📋 Details: {_err_body_str}", force=True)
                    self._vprint(f"{self.log_prefix}   ⏱️  Elapsed: {elapsed_time:.2f}s  Context: {len(api_messages)} msgs, ~{approx_tokens:,} tokens")

                    # Actionable hint for OpenRouter "no tool endpoints" error.
                    # This fires regardless of whether fallback succeeds — the
                    # user needs to know WHY their model failed so they can fix
                    # their provider routing, not just silently fall back.
                    if (
                        self._is_openrouter_url()
                        and "support tool use" in error_msg
                    ):
                        self._vprint(
                            f"{self.log_prefix}   💡 No OpenRouter providers for {_model} support tool calling with your current settings.",
                            force=True,
                        )
                        if self.providers_allowed:
                            self._vprint(
                                f"{self.log_prefix}      Your provider_routing.only restriction is filtering out tool-capable providers.",
                                force=True,
                            )
                            self._vprint(
                                f"{self.log_prefix}      Try removing the restriction or adding providers that support tools for this model.",
                                force=True,
                            )
                        self._vprint(
                            f"{self.log_prefix}      Check which providers support tools: https://openrouter.ai/models/{_model}",
                            force=True,
                        )

                    # Check for interrupt before deciding to retry
                    if self._interrupt_requested:
                        self._vprint(f"{self.log_prefix}🔧 Interrupt detected during error handling, aborting retries.", force=True)
                        self._persist_session(messages, conversation_history)
                        self.clear_interrupt()
                        return {
                            "final_response": f"Operation interrupted: handling API error ({error_type}: {clean_error_message(str(api_error))}).",
                            "messages": messages,
                            "api_calls": api_call_count,
                            "completed": False,
                            "interrupted": True,
                        }
                    
                    fallback_available = self._fallback_index < len(self._fallback_chain)
                    pool = self._credential_pool
                    pool_may_recover = (
                        fallback_available
                        and classified.reason in (
                            FailoverReason.rate_limit,
                            FailoverReason.billing,
                        )
                        and pool is not None
                        and pool.has_available()
                    )
                    retry_directive = decide_retry_directive(
                        classified,
                        api_error,
                        retry_count=retry_count,
                        max_retries=max_retries,
                        fallback_available=fallback_available,
                        credential_pool_may_recover=pool_may_recover,
                        primary_recovery_attempted=primary_recovery_attempted,
                    )
                    is_rate_limited = retry_directive.is_rate_limited

                    if retry_directive.try_eager_fallback:
                        self._emit_status("⚠️ Rate limited — switching to fallback provider...")
                        if self._try_activate_fallback():
                            retry_count = 0
                            compression_attempts = 0
                            primary_recovery_attempted = False
                            continue

                    if retry_directive.kind is RetryKind.compress_payload:
                        compression_attempt = next_compression_attempt(
                            compression_attempts,
                            max_compression_attempts,
                        )
                        compression_attempts = compression_attempt.number
                        if compression_attempt.exhausted:
                            self._vprint(f"{self.log_prefix}❌ Max compression attempts ({max_compression_attempts}) reached for payload-too-large error.", force=True)
                            self._vprint(f"{self.log_prefix}   💡 Try /new to start a fresh conversation, or /compress to retry compression.", force=True)
                            logging.error(f"{self.log_prefix}413 compression failed after {max_compression_attempts} attempts.")
                            return self._context_recovery_failure_result(
                                messages=messages,
                                conversation_history=conversation_history,
                                api_call_count=api_call_count,
                                error=f"Request payload too large: max compression attempts ({max_compression_attempts}) reached.",
                            )
                        self._emit_status(f"⚠️  Request payload too large (413) — compression attempt {compression_attempts}/{max_compression_attempts}...")

                        compression_result = self._compress_for_api_recovery(
                            messages, system_message, approx_tokens=approx_tokens,
                            task_id=effective_task_id,
                        )
                        messages = compression_result.messages
                        active_system_prompt = compression_result.system_prompt
                        # Compression created a new session — clear history
                        # so _flush_messages_to_session_db writes compressed
                        # messages to the new session, not skipping them.
                        conversation_history = None

                        if compression_result.made_progress:
                            self._emit_status(
                                f"🗜️ Compressed {compression_result.original_message_count} "
                                f"→ {len(messages)} messages, retrying..."
                            )
                            time.sleep(2)  # Brief pause between compression retries
                            restart_with_compressed_messages = True
                            break
                        else:
                            self._vprint(f"{self.log_prefix}❌ Payload too large and cannot compress further.", force=True)
                            self._vprint(f"{self.log_prefix}   💡 Try /new to start a fresh conversation, or /compress to retry compression.", force=True)
                            logging.error(f"{self.log_prefix}413 payload too large. Cannot compress further.")
                            return self._context_recovery_failure_result(
                                messages=messages,
                                conversation_history=conversation_history,
                                api_call_count=api_call_count,
                                error="Request payload too large (413). Cannot compress further.",
                            )

                    # Check for context-length errors BEFORE generic 4xx handler.
                    # The classifier detects context overflow from: explicit error
                    # messages, generic 400 + large session heuristic (#1630), and
                    # server disconnect + large session pattern (#2153).
                    is_context_length_error = (
                        retry_directive.kind is RetryKind.recover_context
                    )

                    if is_context_length_error:
                        compressor = self.context_compressor
                        old_ctx = compressor.context_length
                        recovery_plan = build_context_recovery_plan(
                            error_msg,
                            current_context_length=old_ctx,
                            previous_attempts=compression_attempts,
                            max_attempts=max_compression_attempts,
                        )
                        compression_attempts = recovery_plan.attempt.number
                        if recovery_plan.attempt.exhausted:
                            self._vprint(f"{self.log_prefix}❌ Max compression attempts ({max_compression_attempts}) reached.", force=True)
                            self._vprint(f"{self.log_prefix}   💡 Try /new to start a fresh conversation, or /compress to retry compression.", force=True)
                            logging.error(f"{self.log_prefix}Context compression failed after {max_compression_attempts} attempts.")
                            return self._context_recovery_failure_result(
                                messages=messages,
                                conversation_history=conversation_history,
                                api_call_count=api_call_count,
                                error=f"Context length exceeded: max compression attempts ({max_compression_attempts}) reached.",
                            )

                        if recovery_plan.output_token_limit is not None:
                            self._ephemeral_max_output_tokens = recovery_plan.output_token_limit
                            self._vprint(
                                f"{self.log_prefix}⚠️  Output cap too large for current prompt — "
                                f"retrying with max_tokens={recovery_plan.output_token_limit:,} "
                                f"(available_tokens={recovery_plan.available_output_tokens:,}; "
                                f"context_length unchanged at {old_ctx:,})",
                                force=True,
                            )
                            restart_with_compressed_messages = True
                            break

                        context_length_changed = apply_context_recovery_plan(
                            compressor,
                            recovery_plan,
                            model=self.model,
                            base_url=self.base_url,
                            api_key=getattr(self, "api_key", ""),
                            provider=self.provider,
                        )
                        new_ctx = recovery_plan.next_context_length
                        if context_length_changed:
                            if recovery_plan.parsed_context_limit:
                                self._vprint(
                                    f"{self.log_prefix}⚠️  Context limit detected from API: "
                                    f"{new_ctx:,} tokens (was {old_ctx:,})",
                                    force=True,
                                )
                            self._vprint(
                                f"{self.log_prefix}⚠️  Context length exceeded — "
                                f"stepping down: {old_ctx:,} → {new_ctx:,} tokens",
                                force=True,
                            )
                        else:
                            self._vprint(
                                f"{self.log_prefix}⚠️  Context length exceeded at minimum tier — "
                                f"attempting compression...",
                                force=True,
                            )
                        self._emit_status(f"🗜️ Context too large (~{approx_tokens:,} tokens) — compressing ({compression_attempts}/{max_compression_attempts})...")

                        compression_result = self._compress_for_api_recovery(
                            messages, system_message, approx_tokens=approx_tokens,
                            task_id=effective_task_id,
                            context_length_changed=context_length_changed,
                        )
                        messages = compression_result.messages
                        active_system_prompt = compression_result.system_prompt
                        # Compression created a new session — clear history
                        # so _flush_messages_to_session_db writes compressed
                        # messages to the new session, not skipping them.
                        conversation_history = None

                        if compression_result.made_progress:
                            if compression_result.message_count_reduced:
                                self._emit_status(
                                    f"🗜️ Compressed {compression_result.original_message_count} "
                                    f"→ {len(messages)} messages, retrying..."
                                )
                            time.sleep(2)  # Brief pause between compression retries
                            restart_with_compressed_messages = True
                            break
                        else:
                            # Can't compress further and already at minimum tier
                            self._vprint(f"{self.log_prefix}❌ Context length exceeded and cannot compress further.", force=True)
                            self._vprint(f"{self.log_prefix}   💡 The conversation has accumulated too much content. Try /new to start fresh, or /compress to manually trigger compression.", force=True)
                            logging.error(f"{self.log_prefix}Context length exceeded: {approx_tokens:,} tokens. Cannot compress further.")
                            return self._context_recovery_failure_result(
                                messages=messages,
                                conversation_history=conversation_history,
                                api_call_count=api_call_count,
                                error=f"Context length exceeded ({approx_tokens:,} tokens). Cannot compress further.",
                            )

                    if retry_directive.kind is RetryKind.abort_client_error:
                        # Try fallback before aborting — a different provider
                        # may not have the same issue (rate limit, auth, etc.)
                        self._emit_status(f"⚠️ Non-retryable error (HTTP {status_code}) — trying fallback...")
                        if retry_directive.try_fallback and self._try_activate_fallback():
                            retry_count = 0
                            compression_attempts = 0
                            primary_recovery_attempted = False
                            continue
                        if api_kwargs is not None:
                            self._dump_api_request_debug(
                                api_kwargs, reason="non_retryable_client_error", error=api_error,
                            )
                        self._emit_status(
                            f"❌ Non-retryable error (HTTP {status_code}): "
                            f"{summarize_api_error(api_error)}"
                        )
                        self._vprint(f"{self.log_prefix}❌ Non-retryable client error (HTTP {status_code}). Aborting.", force=True)
                        self._vprint(f"{self.log_prefix}   🔌 Provider: {_provider}  Model: {_model}", force=True)
                        self._vprint(f"{self.log_prefix}   🌐 Endpoint: {_base}", force=True)
                        # Actionable guidance for common auth errors
                        if classified.is_auth or classified.reason == FailoverReason.billing:
                            self._vprint(f"{self.log_prefix}   💡 Your API key was rejected by the provider. Check:", force=True)
                            self._vprint(f"{self.log_prefix}      • Is the key valid? Run: /api", force=True)
                            self._vprint(f"{self.log_prefix}      • Does your account have access to {_model}?", force=True)
                            if "openrouter" in str(_base).lower():
                                self._vprint(f"{self.log_prefix}      • Check credits: https://openrouter.ai/settings/credits", force=True)
                        else:
                            self._vprint(f"{self.log_prefix}   💡 This type of error won't be fixed by retrying.", force=True)
                        logging.error(f"{self.log_prefix}Non-retryable client error: {api_error}")
                        # Skip session persistence when the error is likely
                        # context-overflow related (status 400 + large session).
                        # Persisting the failed user message would make the
                        # session even larger, causing the same failure on the
                        # next attempt. (#1630)
                        if status_code == 400 and (approx_tokens > 50000 or len(api_messages) > 80):
                            self._vprint(
                                f"{self.log_prefix}⚠️  Skipping session persistence "
                                f"for large failed session to prevent growth loop.",
                                force=True,
                            )
                        else:
                            self._persist_session(messages, conversation_history)
                        return {
                            "final_response": None,
                            "messages": messages,
                            "api_calls": api_call_count,
                            "completed": False,
                            "failed": True,
                            "error": str(api_error),
                        }

                    if retry_directive.kind is RetryKind.exhausted:
                        # Before falling back, try rebuilding the primary
                        # client once for transient transport errors (stale
                        # connection pool, TCP reset).  Only attempted once
                        # per API call block.
                        if retry_directive.try_transport_recovery and self._try_recover_primary_transport(
                            api_error, retry_count=retry_count, max_retries=max_retries,
                        ):
                            primary_recovery_attempted = True
                            retry_count = 0
                            continue
                        # Try fallback before giving up entirely
                        self._emit_status(f"⚠️ Max retries ({max_retries}) exhausted — trying fallback...")
                        if retry_directive.try_fallback and self._try_activate_fallback():
                            retry_count = 0
                            compression_attempts = 0
                            primary_recovery_attempted = False
                            continue
                        _final_summary = summarize_api_error(api_error)
                        if is_rate_limited:
                            self._emit_status(f"❌ Rate limited after {max_retries} retries — {_final_summary}")
                        else:
                            self._emit_status(f"❌ API failed after {max_retries} retries — {_final_summary}")
                        self._vprint(f"{self.log_prefix}   💀 Final error: {_final_summary}", force=True)

                        # Detect SSE stream-drop pattern (e.g. "Network
                        # connection lost") and surface actionable guidance.
                        # This typically happens when the model generates a
                        # very large tool call (write_file with huge content)
                        # and the proxy/CDN drops the stream mid-response.
                        _is_stream_drop = is_stream_drop_error(api_error)
                        if _is_stream_drop:
                            self._vprint(
                                f"{self.log_prefix}   💡 The provider's stream "
                                f"connection keeps dropping. This often happens "
                                f"when the model tries to write a very large "
                                f"file in a single tool call.",
                                force=True,
                            )
                            self._vprint(
                                f"{self.log_prefix}      Try asking the model "
                                f"to use execute_code with Python's open() for "
                                f"large files, or to write the file in smaller "
                                f"sections.",
                                force=True,
                            )

                        logging.error(
                            "%sAPI call failed after %s retries. %s | provider=%s model=%s msgs=%s tokens=~%s",
                            self.log_prefix, max_retries, _final_summary,
                            _provider, _model, len(api_messages), f"{approx_tokens:,}",
                        )
                        if api_kwargs is not None:
                            self._dump_api_request_debug(
                                api_kwargs, reason="max_retries_exhausted", error=api_error,
                            )
                        self._persist_session(messages, conversation_history)
                        _final_response = f"API call failed after {max_retries} retries: {_final_summary}"
                        if _is_stream_drop:
                            _final_response += (
                                "\n\nThe provider's stream connection keeps "
                                "dropping — this often happens when generating "
                                "very large tool call responses (e.g. write_file "
                                "with long content). Try asking me to use "
                                "execute_code with Python's open() for large "
                                "files, or to write in smaller sections."
                            )
                        return {
                            "final_response": _final_response,
                            "messages": messages,
                            "api_calls": api_call_count,
                            "completed": False,
                            "failed": True,
                            "error": _final_summary,
                        }

                    # For rate limits, respect the Retry-After header if present
                    _retry_after = retry_after_seconds(api_error) if is_rate_limited else None
                    wait_time = _retry_after if _retry_after else jittered_backoff(retry_count, base_delay=2.0, max_delay=60.0)
                    if is_rate_limited:
                        self._emit_status(f"⏱️ Rate limit reached. Waiting {wait_time}s before retry (attempt {retry_count + 1}/{max_retries})...")
                    else:
                        self._emit_status(f"⏳ Retrying in {wait_time}s (attempt {retry_count}/{max_retries})...")
                    logger.warning(
                        "Retrying API call in %ss (attempt %s/%s) %s error=%s",
                        wait_time,
                        retry_count,
                        max_retries,
                        self._client_log_context(),
                        api_error,
                    )
                    # Sleep in small increments so we can respond to interrupts quickly
                    # instead of blocking the entire wait_time in one sleep() call
                    sleep_end = time.time() + wait_time
                    while time.time() < sleep_end:
                        if self._interrupt_requested:
                            self._vprint(f"{self.log_prefix}🔧 Interrupt detected during retry wait, aborting.", force=True)
                            self._persist_session(messages, conversation_history)
                            self.clear_interrupt()
                            return {
                                "final_response": f"Operation interrupted: retrying API call after error (retry {retry_count}/{max_retries}).",
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "interrupted": True,
                            }
                        time.sleep(0.2)  # Check interrupt every 200ms
            
            # If the API call was interrupted, skip response processing
            if interrupted:
                _turn_exit_reason = "interrupted_during_api_call"
                break

            if restart_with_compressed_messages:
                api_call_count -= 1
                self.iteration_budget.refund()
                # Count compression restarts toward the retry limit to prevent
                # infinite loops when compression reduces messages but not enough
                # to fit the context window.
                retry_count += 1
                restart_with_compressed_messages = False
                continue

            if restart_with_length_continuation:
                continue

            # Guard: if all retries exhausted without a successful response
            # (e.g. repeated context-length errors that exhausted retry_count),
            # the `response` variable is still None. Break out cleanly.
            if response is None:
                _turn_exit_reason = "all_retries_exhausted_no_response"
                print(f"{self.log_prefix}❌ All API retries exhausted with no successful response.")
                self._persist_session(messages, conversation_history)
                break

            try:
                assistant_message = response.choices[0].message
                
                # Normalize content to string — some OpenAI-compatible servers
                # (llama-server, etc.) return content as a dict or list instead
                # of a plain string, which crashes downstream .strip() calls.
                if assistant_message.content is not None and not isinstance(assistant_message.content, str):
                    raw = assistant_message.content
                    if isinstance(raw, dict):
                        assistant_message.content = raw.get("text", "") or raw.get("content", "") or json.dumps(raw)
                    elif isinstance(raw, list):
                        # Multimodal content list — extract text parts
                        parts = []
                        for part in raw:
                            if isinstance(part, str):
                                parts.append(part)
                            elif isinstance(part, dict) and part.get("type") == "text":
                                parts.append(part.get("text", ""))
                            elif isinstance(part, dict) and "text" in part:
                                parts.append(str(part["text"]))
                        assistant_message.content = "\n".join(parts)
                    else:
                        assistant_message.content = str(raw)

                try:
                    from VoidCube_cli.plugins import invoke_hook as _invoke_hook
                    _assistant_tool_calls = getattr(assistant_message, "tool_calls", None) or []
                    _assistant_text = assistant_message.content or ""
                    _invoke_hook(
                        "post_api_request",
                        task_id=effective_task_id,
                        session_id=self.session_id or "",
                        platform=self.platform or "",
                        model=self.model,
                        provider=self.provider,
                        base_url=self.base_url,
                        api_call_count=api_call_count,
                        api_duration=api_duration,
                        finish_reason=finish_reason,
                        message_count=len(api_messages),
                        response_model=getattr(response, "model", None),
                        usage=self._usage_summary_for_api_request_hook(response),
                        assistant_content_chars=len(_assistant_text),
                        assistant_tool_call_count=len(_assistant_tool_calls),
                    )
                except Exception:
                    pass

                # Handle assistant response
                if assistant_message.content and not self.quiet_mode:
                    if self.verbose_logging:
                        self._vprint(f"{self.log_prefix}🤖 Assistant: {assistant_message.content}")
                    else:
                        self._vprint(f"{self.log_prefix}🤖 Assistant: {assistant_message.content[:100]}{'...' if len(assistant_message.content) > 100 else ''}")

                # Notify progress callback of model's thinking (used by subagent
                # delegation to relay the child's reasoning to the parent display).
                if (assistant_message.content and self.tool_progress_callback):
                    _think_text = assistant_message.content.strip()
                    # Strip reasoning XML tags that shouldn't leak to parent display
                    _think_text = strip_thinking_tags(_think_text).strip()
                    # For subagents: relay first line to parent display (existing behaviour).
                    # For all agents with a structured callback: emit reasoning.available event.
                    first_line = _think_text.split('\n')[0][:80] if _think_text else ""
                    if first_line and getattr(self, '_delegate_depth', 0) > 0:
                        try:
                            self.tool_progress_callback("_thinking", first_line)
                        except Exception:
                            pass
                    elif _think_text:
                        try:
                            self.tool_progress_callback("reasoning.available", "_thinking", _think_text[:500], None)
                        except Exception:
                            pass
                
                # Check for tool calls
                if assistant_message.tool_calls:
                    if not self.quiet_mode:
                        self._vprint(f"{self.log_prefix}🔧 Processing {len(assistant_message.tool_calls)} tool call(s)...")
                    
                    if self.verbose_logging:
                        for tc in assistant_message.tool_calls:
                            logging.debug(f"Tool call: {tc.function.name} with args: {tc.function.arguments[:200]}...")
                    
                    # Validate tool call names - detect model hallucinations
                    # Repair mismatched tool names before validating
                    for tc in assistant_message.tool_calls:
                        if tc.function.name not in self.valid_tool_names:
                            repaired = self._repair_tool_call(tc.function.name)
                            if repaired:
                                print(f"{self.log_prefix}🔧 Auto-repaired tool name: '{tc.function.name}' -> '{repaired}'")
                                tc.function.name = repaired
                    invalid_tool_calls = [
                        tc.function.name for tc in assistant_message.tool_calls
                        if tc.function.name not in self.valid_tool_names
                    ]
                    if invalid_tool_calls:
                        # Track retries for invalid tool calls
                        self._invalid_tool_retries += 1

                        # Return helpful error to model — model can self-correct next turn
                        available = ", ".join(sorted(self.valid_tool_names))
                        invalid_name = invalid_tool_calls[0]
                        invalid_preview = invalid_name[:80] + "..." if len(invalid_name) > 80 else invalid_name
                        self._vprint(f"{self.log_prefix}⚠️  Unknown tool '{invalid_preview}' — sending error to model for self-correction ({self._invalid_tool_retries}/3)")

                        if self._invalid_tool_retries >= 3:
                            self._vprint(f"{self.log_prefix}❌ Max retries (3) for invalid tool calls exceeded. Stopping as partial.", force=True)
                            self._invalid_tool_retries = 0
                            self._persist_session(messages, conversation_history)
                            return {
                                "final_response": None,
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": f"Model generated invalid tool call: {invalid_preview}"
                            }

                        assistant_msg = self._build_assistant_message(assistant_message, finish_reason)
                        messages.append(assistant_msg)
                        for tc in assistant_message.tool_calls:
                            if tc.function.name not in self.valid_tool_names:
                                content = f"Tool '{tc.function.name}' does not exist. Available tools: {available}"
                            else:
                                content = "Skipped: another tool call in this turn used an invalid name. Please retry this tool call."
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": content,
                            })
                        continue
                    # Reset retry counter on successful tool call validation
                    self._invalid_tool_retries = 0
                    
                    # Validate tool call arguments are valid JSON
                    # Handle empty strings as empty objects (common model quirk)
                    invalid_json_args = []
                    for tc in assistant_message.tool_calls:
                        args = tc.function.arguments
                        if isinstance(args, (dict, list)):
                            tc.function.arguments = json.dumps(args)
                            continue
                        if args is not None and not isinstance(args, str):
                            tc.function.arguments = str(args)
                            args = tc.function.arguments
                        # Treat empty/whitespace strings as empty object
                        if not args or not args.strip():
                            tc.function.arguments = "{}"
                            continue
                        try:
                            json.loads(args)
                        except json.JSONDecodeError as e:
                            invalid_json_args.append((tc.function.name, str(e)))
                    
                    if invalid_json_args:
                        # Check if the invalid JSON is due to truncation rather
                        # than a model formatting mistake.  Routers sometimes
                        # rewrite finish_reason from "length" to "tool_calls",
                        # hiding the truncation from the length handler above.
                        # Detect truncation: args that don't end with } or ]
                        # (after stripping whitespace) are cut off mid-stream.
                        _truncated = any(
                            not (tc.function.arguments or "").rstrip().endswith(("}", "]"))
                            for tc in assistant_message.tool_calls
                            if tc.function.name in {n for n, _ in invalid_json_args}
                        )
                        if _truncated:
                            self._vprint(
                                f"{self.log_prefix}⚠️  Truncated tool call arguments detected "
                                f"(finish_reason={finish_reason!r}) — refusing to execute.",
                                force=True,
                            )
                            self._invalid_json_retries = 0
                            self._cleanup_task_resources(effective_task_id)
                            self._persist_session(messages, conversation_history)
                            return {
                                "final_response": None,
                                "messages": messages,
                                "api_calls": api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": "Response truncated due to output length limit",
                            }

                        # Track retries for invalid JSON arguments
                        self._invalid_json_retries += 1

                        tool_name, error_msg = invalid_json_args[0]
                        self._vprint(f"{self.log_prefix}⚠️  Invalid JSON in tool call arguments for '{tool_name}': {error_msg}")

                        if self._invalid_json_retries < 3:
                            self._vprint(f"{self.log_prefix}🔄 Retrying API call ({self._invalid_json_retries}/3)...")
                            # Don't add anything to messages, just retry the API call
                            continue
                        else:
                            # Instead of returning partial, inject tool error results so the model can recover.
                            # Using tool results (not user messages) preserves role alternation.
                            self._vprint(f"{self.log_prefix}⚠️  Injecting recovery tool results for invalid JSON...")
                            self._invalid_json_retries = 0  # Reset for next attempt
                            
                            # Append the assistant message with its (broken) tool_calls
                            recovery_assistant = self._build_assistant_message(assistant_message, finish_reason)
                            messages.append(recovery_assistant)
                            
                            # Respond with tool error results for each tool call
                            invalid_names = {name for name, _ in invalid_json_args}
                            for tc in assistant_message.tool_calls:
                                if tc.function.name in invalid_names:
                                    err = next(e for n, e in invalid_json_args if n == tc.function.name)
                                    tool_result = (
                                        f"Error: Invalid JSON arguments. {err}. "
                                        f"For tools with no required parameters, use an empty object: {{}}. "
                                        f"Please retry with valid JSON."
                                    )
                                else:
                                    tool_result = "Skipped: other tool call in this response had invalid JSON."
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": tool_result,
                                })
                            continue
                    
                    # Reset retry counter on successful JSON validation
                    self._invalid_json_retries = 0

                    # ── Post-call guardrails ──────────────────────────
                    assistant_message.tool_calls = self._cap_delegate_task_calls(
                        assistant_message.tool_calls
                    )
                    assistant_message.tool_calls = self._deduplicate_tool_calls(
                        assistant_message.tool_calls
                    )

                    assistant_msg = self._build_assistant_message(assistant_message, finish_reason)
                    
                    # If this turn has both content AND tool_calls, capture the content
                    # as a fallback final response. Common pattern: model delivers its
                    # answer and calls memory/skill tools as a side-effect in the same
                    # turn. If the follow-up turn after tools is empty, we use this.
                    turn_content = assistant_message.content or ""
                    if turn_content and has_visible_content(turn_content):
                        self._last_content_with_tools = turn_content
                        # Only mute subsequent output when EVERY tool call in
                        # this turn is post-response housekeeping (memory, todo,
                        # skill_manage, etc.).  If any substantive tool is present
                        # (search_files, read_file, write_file, terminal, ...),
                        # keep output visible so the user sees progress.
                        _HOUSEKEEPING_TOOLS = frozenset({
                            "memory", "todo", "skill_manage", "session_search",
                        })
                        _all_housekeeping = all(
                            tc.function.name in _HOUSEKEEPING_TOOLS
                            for tc in assistant_message.tool_calls
                        )
                        if _all_housekeeping and self._has_stream_consumers():
                            self._mute_post_response = True
                        elif self.quiet_mode:
                            clean = strip_thinking_blocks(turn_content).strip()
                            if clean:
                                self._vprint(f"  ┊ 💬 {clean}")
                    
                    # Pop thinking-only prefill message(s) before appending
                    # (tool-call path — same rationale as the final-response path).
                    _had_prefill = False
                    while (
                        messages
                        and isinstance(messages[-1], dict)
                        and messages[-1].get("_thinking_prefill")
                    ):
                        messages.pop()
                        _had_prefill = True

                    # Reset prefill counter when tool calls follow a prefill
                    # recovery.  Without this, the counter accumulates across
                    # the whole conversation — a model that intermittently
                    # empties (empty → prefill → tools → empty → prefill →
                    # tools) burns both prefill attempts and the third empty
                    # gets zero recovery.  Resetting here treats each tool-
                    # call success as a fresh start.
                    if _had_prefill:
                        self._thinking_prefill_retries = 0
                        self._empty_content_retries = 0

                    messages.append(assistant_msg)
                    self._emit_interim_assistant_message(assistant_msg)

                    # Close any open streaming display (response box, reasoning
                    # box) before tool execution begins.  Intermediate turns may
                    # have streamed early content that opened the response box;
                    # flushing here prevents it from wrapping tool feed lines.
                    # Only signal the display callback — TTS (_stream_callback)
                    # should NOT receive None (it uses None as end-of-stream).
                    if self.stream_delta_callback:
                        try:
                            self.stream_delta_callback(None)
                        except Exception:
                            pass

                    self._execute_tool_calls(assistant_message, messages, effective_task_id, api_call_count)

                    # Reset per-turn retry counters after successful tool
                    # execution so a single truncation doesn't poison the
                    # entire conversation.
                    truncated_tool_call_retries = 0

                    # Signal that a paragraph break is needed before the next
                    # streamed text.  We don't emit it immediately because
                    # multiple consecutive tool iterations would stack up
                    # redundant blank lines.  Instead, _fire_stream_delta()
                    # will prepend a single "\n\n" the next time real text
                    # arrives.
                    self._stream_needs_break = True

                    # Refund the iteration if the ONLY tool(s) called were
                    # execute_code (programmatic tool calling).  These are
                    # cheap RPC-style calls that shouldn't eat the budget.
                    _tc_names = {tc.function.name for tc in assistant_message.tool_calls}
                    if _tc_names == {"execute_code"}:
                        self.iteration_budget.refund()
                    
                    # Use real token counts from the API response to decide
                    # compression.  prompt_tokens + completion_tokens is the
                    # actual context size the provider reported plus the
                    # assistant turn — a tight lower bound for the next prompt.
                    # Tool results appended above aren't counted yet, but the
                    # threshold (default 50%) leaves ample headroom; if tool
                    # results push past it, the next API call will report the
                    # real total and trigger compression then.
                    #
                    # If last_prompt_tokens is 0 (stale after API disconnect
                    # or provider returned no usage data), fall back to rough
                    # estimate to avoid missing compression.  Without this,
                    # a session can grow unbounded after disconnects because
                    # should_compress(0) never fires.  (#2153)
                    _compressor = self.context_compressor
                    if _compressor.last_prompt_tokens > 0:
                        _real_tokens = (
                            _compressor.last_prompt_tokens
                            + _compressor.last_completion_tokens
                        )
                    else:
                        _real_tokens = estimate_messages_tokens_rough(messages)

                    # ── Context pressure warnings (user-facing only) ──────────
                    # Notify the user (NOT the LLM) as context approaches the
                    # compaction threshold.  Thresholds are relative to where
                    # compaction fires, not the raw context window.
                    # Does not inject into messages — just prints to CLI output
                    # and fires status_callback for gateway platforms.
                    # Tiered: 85% (orange) and 95% (red/critical).
                    if _compressor.threshold_tokens > 0:
                        _compaction_progress = _real_tokens / _compressor.threshold_tokens
                        # Determine the warning tier for this progress level
                        _warn_tier = 0.0
                        if _compaction_progress >= 0.95:
                            _warn_tier = 0.95
                        elif _compaction_progress >= 0.85:
                            _warn_tier = 0.85
                        if _warn_tier > self._context_pressure_warned_at:
                            # Class-level dedup: check if this session was already
                            # warned at this tier within the cooldown window.
                            _sid = self.session_id or "default"
                            _last = AIAgent._context_pressure_last_warned.get(_sid)
                            _now = time.time()
                            if _last is None or _last[0] < _warn_tier or (_now - _last[1]) >= self._CONTEXT_PRESSURE_COOLDOWN:
                                self._context_pressure_warned_at = _warn_tier
                                AIAgent._context_pressure_last_warned[_sid] = (_warn_tier, _now)
                                self._emit_context_pressure(_compaction_progress, _compressor)
                                # Evict stale entries (older than 2x cooldown)
                                _cutoff = _now - self._CONTEXT_PRESSURE_COOLDOWN * 2
                                AIAgent._context_pressure_last_warned = {
                                    k: v for k, v in AIAgent._context_pressure_last_warned.items()
                                    if v[1] > _cutoff
                                }

                    if self.compression_enabled and _compressor.should_compress(_real_tokens):
                        self._safe_print("  ⟳ compacting context…")
                        messages, active_system_prompt = self._compress_context(
                            messages, system_message,
                            approx_tokens=self.context_compressor.last_prompt_tokens,
                            task_id=effective_task_id,
                        )
                        # Compression created a new session — clear history so
                        # _flush_messages_to_session_db writes compressed messages
                        # to the new session (see preflight compression comment).
                        conversation_history = None
                    
                    # Save session log incrementally (so progress is visible even if interrupted)
                    self._session_messages = messages
                    self._save_session_log(messages)
                    
                    # Continue loop for next response
                    continue
                
                else:
                    # No tool calls - this is the final response
                    final_response = assistant_message.content or ""
                    
                    # Check if response only has think block with no actual content after it
                    if not has_visible_content(final_response):
                        # If the previous turn already delivered real content alongside
                        # tool calls (e.g. "You're welcome!" + memory save), the model
                        # has nothing more to say. Use the earlier content immediately
                        # instead of wasting API calls on retries that won't help.
                        fallback = getattr(self, '_last_content_with_tools', None)
                        if fallback:
                            _turn_exit_reason = "fallback_prior_turn_content"
                            logger.info("Empty follow-up after tool calls — using prior turn content as final response")
                            self._emit_status("↻ Empty response after tool calls — using earlier content as final answer")
                            self._last_content_with_tools = None
                            self._empty_content_retries = 0
                            for i in range(len(messages) - 1, -1, -1):
                                msg = messages[i]
                                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                                    tool_names = []
                                    for tc in msg["tool_calls"]:
                                        if not tc or not isinstance(tc, dict): continue
                                        fn = tc.get("function", {})
                                        tool_names.append(fn.get("name", "unknown"))
                                    msg["content"] = f"Calling the {', '.join(tool_names)} tool{'s' if len(tool_names) > 1 else ''}..."
                                    break
                            final_response = strip_thinking_blocks(fallback).strip()
                            self._response_was_previewed = True
                            break

                        # ── Thinking-only prefill continuation ──────────
                        # The model produced structured reasoning (via API
                        # fields) but no visible text content.  Rather than
                        # giving up, append the assistant message as-is and
                        # continue — the model will see its own reasoning
                        # on the next turn and produce the text portion.
                        # Inspired by clawdbot's "incomplete-text" recovery.
                        _has_structured = bool(
                            getattr(assistant_message, "reasoning", None)
                            or getattr(assistant_message, "reasoning_content", None)
                            or getattr(assistant_message, "reasoning_details", None)
                        )
                        if _has_structured and self._thinking_prefill_retries < 2:
                            self._thinking_prefill_retries += 1
                            logger.info(
                                "Thinking-only response (no visible content) — "
                                "prefilling to continue (%d/2)",
                                self._thinking_prefill_retries,
                            )
                            self._emit_status(
                                f"↻ Thinking-only response — prefilling to continue "
                                f"({self._thinking_prefill_retries}/2)"
                            )
                            interim_msg = self._build_assistant_message(
                                assistant_message, "incomplete"
                            )
                            interim_msg["_thinking_prefill"] = True
                            messages.append(interim_msg)
                            self._session_messages = messages
                            self._save_session_log(messages)
                            continue

                        # ── Empty response retry ──────────────────────
                        # Model returned nothing usable.  Retry up to 3
                        # times before attempting fallback.  This covers
                        # both truly empty responses (no content, no
                        # reasoning) AND reasoning-only responses after
                        # prefill exhaustion — models like mimo-v2-pro
                        # always populate reasoning fields via OpenRouter,
                        # so the old `not _has_structured` guard blocked
                        # retries for every reasoning model after prefill.
                        _truly_empty = not strip_thinking_blocks(
                            final_response
                        ).strip()
                        _prefill_exhausted = (
                            _has_structured
                            and self._thinking_prefill_retries >= 2
                        )
                        if _truly_empty and (not _has_structured or _prefill_exhausted) and self._empty_content_retries < 3:
                            self._empty_content_retries += 1
                            logger.warning(
                                "Empty response (no content or reasoning) — "
                                "retry %d/3 (model=%s)",
                                self._empty_content_retries, self.model,
                            )
                            self._emit_status(
                                f"⚠️ Empty response from model — retrying "
                                f"({self._empty_content_retries}/3)"
                            )
                            continue

                        # ── Exhausted retries — try fallback provider ──
                        # Before giving up with "(empty)", attempt to
                        # switch to the next provider in the fallback
                        # chain.  This covers the case where a model
                        # (e.g. GLM-4.5-Air) consistently returns empty
                        # due to context degradation or provider issues.
                        if _truly_empty and self._fallback_chain:
                            logger.warning(
                                "Empty response after %d retries — "
                                "attempting fallback (model=%s, provider=%s)",
                                self._empty_content_retries, self.model,
                                self.provider,
                            )
                            self._emit_status(
                                "⚠️ Model returning empty responses — "
                                "switching to fallback provider..."
                            )
                            if self._try_activate_fallback():
                                self._empty_content_retries = 0
                                self._emit_status(
                                    f"↻ Switched to fallback: {self.model} "
                                    f"({self.provider})"
                                )
                                logger.info(
                                    "Fallback activated after empty responses: "
                                    "now using %s on %s",
                                    self.model, self.provider,
                                )
                                continue

                        # Exhausted retries and fallback chain (or no
                        # fallback configured).  Fall through to the
                        # "(empty)" terminal.
                        _turn_exit_reason = "empty_response_exhausted"
                        reasoning_text = extract_reasoning(assistant_message)
                        assistant_msg = self._build_assistant_message(assistant_message, finish_reason)
                        assistant_msg["content"] = "(empty)"
                        messages.append(assistant_msg)

                        if reasoning_text:
                            reasoning_preview = reasoning_text[:500] + "..." if len(reasoning_text) > 500 else reasoning_text
                            logger.warning(
                                "Reasoning-only response (no visible content) "
                                "after exhausting retries and fallback. "
                                "Reasoning: %s", reasoning_preview,
                            )
                            self._emit_status(
                                "⚠️ Model produced reasoning but no visible "
                                "response after all retries. Returning empty."
                            )
                        else:
                            logger.warning(
                                "Empty response (no content or reasoning) "
                                "after %d retries. No fallback available. "
                                "model=%s provider=%s",
                                self._empty_content_retries, self.model,
                                self.provider,
                            )
                            self._emit_status(
                                "❌ Model returned no content after all retries"
                                + (" and fallback attempts." if self._fallback_chain else
                                   ". No fallback providers configured.")
                            )

                        final_response = "(empty)"
                        break
                    
                    # Reset retry counter/signature on successful content
                    self._empty_content_retries = 0
                    self._thinking_prefill_retries = 0

                    if truncated_response_prefix:
                        final_response = truncated_response_prefix + final_response
                        truncated_response_prefix = ""
                        length_continue_retries = 0
                    
                    # Strip <think> blocks from user-facing response (keep raw in messages for trajectory)
                    final_response = strip_thinking_blocks(final_response).strip()
                    
                    final_msg = self._build_assistant_message(assistant_message, finish_reason)

                    # Pop thinking-only prefill message(s) before appending
                    # the final response.  This avoids consecutive assistant
                    # messages and keeps history clean.
                    while (
                        messages
                        and isinstance(messages[-1], dict)
                        and messages[-1].get("_thinking_prefill")
                    ):
                        messages.pop()

                    messages.append(final_msg)
                    
                    _turn_exit_reason = f"text_response(finish_reason={finish_reason})"
                    if not self.quiet_mode:
                        self._safe_print(f"🎉 Conversation completed after {api_call_count} OpenAI-compatible API call(s)")
                    break
                
            except Exception as e:
                error_msg = f"Error during OpenAI-compatible API call #{api_call_count}: {str(e)}"
                try:
                    print(f"❌ {error_msg}")
                except (OSError, ValueError):
                    logger.error(error_msg)
                
                logger.debug("Outer loop error in API call #%d", api_call_count, exc_info=True)
                
                # If an assistant message with tool_calls was already appended,
                # the API expects a role="tool" result for every tool_call_id.
                # Fill in error results for any that weren't answered yet.
                for idx in range(len(messages) - 1, -1, -1):
                    msg = messages[idx]
                    if not isinstance(msg, dict):
                        break
                    if msg.get("role") == "tool":
                        continue
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        answered_ids = {
                            m["tool_call_id"]
                            for m in messages[idx + 1:]
                            if isinstance(m, dict) and m.get("role") == "tool"
                        }
                        for tc in msg["tool_calls"]:
                            if not tc or not isinstance(tc, dict): continue
                            if tc["id"] not in answered_ids:
                                err_msg = {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": f"Error executing tool: {error_msg}",
                                }
                                messages.append(err_msg)
                    break
                
                # Non-tool errors don't need a synthetic message injected.
                # The error is already printed to the user (line above), and
                # the retry loop continues.  Injecting a fake user/assistant
                # message pollutes history, burns tokens, and risks violating
                # role-alternation invariants.

                # If we're near the limit, break to avoid infinite loops
                if api_call_count >= self.max_iterations - 1:
                    _turn_exit_reason = f"error_near_max_iterations({error_msg[:80]})"
                    final_response = f"I apologize, but I encountered repeated errors: {error_msg}"
                    # Append as assistant so the history stays valid for
                    # session resume (avoids consecutive user messages).
                    messages.append({"role": "assistant", "content": final_response})
                    break
        
        if final_response is None and (
            api_call_count >= self.max_iterations
            or self.iteration_budget.remaining <= 0
        ) and not self._budget_exhausted_injected:
            # Budget exhausted but we haven't tried asking the model to
            # summarise yet.  Inject a user message and give it one grace
            # API call to produce a text response.
            self._budget_exhausted_injected = True
            self._budget_grace_call = True
            _grace_msg = (
                "Your tool budget ran out. Please give me the information "
                "or actions you've completed so far."
            )
            messages.append({"role": "user", "content": _grace_msg})
            self._emit_status(
                f"⚠️ Iteration budget exhausted ({api_call_count}/{self.max_iterations}) "
                "— asking model to summarise"
            )
            if not self.quiet_mode:
                self._safe_print(
                    f"\n⚠️  Iteration budget exhausted ({api_call_count}/{self.max_iterations}) "
                    "— requesting summary..."
                )

        if final_response is None and (
            api_call_count >= self.max_iterations
            or self.iteration_budget.remaining <= 0
        ) and not self._budget_grace_call:
            _turn_exit_reason = f"max_iterations_reached({api_call_count}/{self.max_iterations})"
            if self.iteration_budget.remaining <= 0 and not self.quiet_mode:
                print(f"\n⚠️  Iteration budget exhausted ({self.iteration_budget.used}/{self.iteration_budget.max_total} iterations used)")
            final_response = self._handle_max_iterations(messages, api_call_count)
        
        # Determine if conversation completed successfully
        completed = final_response is not None and api_call_count < self.max_iterations

        # Clean up VM and browser for this task after conversation completes
        self._cleanup_task_resources(effective_task_id)

        # Persist session to both JSON log and SQLite
        self._persist_session(messages, conversation_history)

        # ── Turn-exit diagnostic log ─────────────────────────────────────
        # Always logged at INFO so agent.log captures WHY every turn ended.
        # When the last message is a tool result (agent was mid-work), log
        # at WARNING — this is the "just stops" scenario users report.
        _last_msg_role = messages[-1].get("role") if messages else None
        _last_tool_name = None
        if _last_msg_role == "tool":
            # Walk back to find the assistant message with the tool call
            for _m in reversed(messages):
                if _m.get("role") == "assistant" and _m.get("tool_calls"):
                    _tcs = _m["tool_calls"]
                    if _tcs and isinstance(_tcs[0], dict):
                        _last_tool_name = _tcs[-1].get("function", {}).get("name")
                    break

        _turn_tool_count = sum(
            1 for m in messages
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
        )
        _resp_len = len(final_response) if final_response else 0
        _budget_used = self.iteration_budget.used if self.iteration_budget else 0
        _budget_max = self.iteration_budget.max_total if self.iteration_budget else 0

        _diag_msg = (
            "Turn ended: reason=%s model=%s api_calls=%d/%d budget=%d/%d "
            "tool_turns=%d last_msg_role=%s response_len=%d session=%s"
        )
        _diag_args = (
            _turn_exit_reason, self.model, api_call_count, self.max_iterations,
            _budget_used, _budget_max,
            _turn_tool_count, _last_msg_role, _resp_len,
            self.session_id or "none",
        )

        if _last_msg_role == "tool" and not interrupted:
            # Agent was mid-work — this is the "just stops" case.
            logger.warning(
                "Turn ended with pending tool result (agent may appear stuck). "
                + _diag_msg + " last_tool=%s",
                *_diag_args, _last_tool_name,
            )
        else:
            logger.info(_diag_msg, *_diag_args)

        # Plugin hook: post_llm_call
        # Fired once per turn after the tool-calling loop completes.
        # Plugins can use this to persist conversation data (e.g. sync
        # to an external memory system).
        if final_response and not interrupted:
            try:
                from VoidCube_cli.plugins import invoke_hook as _invoke_hook
                _invoke_hook(
                    "post_llm_call",
                    session_id=self.session_id,
                    user_message=original_user_message,
                    assistant_response=final_response,
                    conversation_history=list(messages),
                    model=self.model,
                    platform=getattr(self, "platform", None) or "",
                )
            except Exception as exc:
                logger.warning("post_llm_call hook failed: %s", exc)

        # Extract reasoning from the last assistant message (if any)
        last_reasoning = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("reasoning"):
                last_reasoning = msg["reasoning"]
                break

        # Build result with interrupt info if applicable
        result = {
            "final_response": final_response,
            "last_reasoning": last_reasoning,
            "messages": messages,
            "api_calls": api_call_count,
            "completed": completed,
            "partial": False,  # True only when stopped due to invalid tool calls
            "interrupted": interrupted,
            "response_previewed": getattr(self, "_response_was_previewed", False),
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "input_tokens": self.session_input_tokens,
            "output_tokens": self.session_output_tokens,
            "cache_read_tokens": self.session_cache_read_tokens,
            "cache_write_tokens": self.session_cache_write_tokens,
            "reasoning_tokens": self.session_reasoning_tokens,
            "prompt_tokens": self.session_prompt_tokens,
            "completion_tokens": self.session_completion_tokens,
            "total_tokens": self.session_total_tokens,
            "last_prompt_tokens": getattr(self.context_compressor, "last_prompt_tokens", 0) or 0,
            "estimated_cost_usd": self.session_estimated_cost_usd,
            "cost_status": self.session_cost_status,
            "cost_source": self.session_cost_source,
        }
        self._response_was_previewed = False
        
        # Include interrupt message if one triggered the interrupt
        if interrupted and self._interrupt_message:
            result["interrupt_message"] = self._interrupt_message
        
        # Clear interrupt state after handling
        self.clear_interrupt()

        # Clear stream callback so it doesn't leak into future calls
        self._stream_callback = None

        # Check skill trigger NOW — based on how many tool iterations THIS turn used.
        _should_review_skills = False
        if (self._skill_nudge_interval > 0
                and self._iters_since_skill >= self._skill_nudge_interval
                and "skill_manage" in self.valid_tool_names):
            _should_review_skills = True
            self._iters_since_skill = 0

        # External memory provider: sync the completed turn + queue next prefetch.
        # Use original_user_message (clean input) — user_message may contain
        # injected skill content that bloats / breaks provider queries.
        if self._memory_manager and final_response and original_user_message:
            try:
                self._memory_manager.sync_all(original_user_message, final_response)
                self._memory_manager.queue_prefetch_all(original_user_message)
            except Exception:
                pass

        # Background memory/skill review — runs AFTER the response is delivered
        # so it never competes with the user's task for model attention.
        if final_response and not interrupted and (_should_review_memory or _should_review_skills):
            try:
                self._spawn_background_review(
                    messages_snapshot=list(messages),
                    review_memory=_should_review_memory,
                    review_skills=_should_review_skills,
                )
            except Exception:
                pass  # Background review is best-effort

        # Note: Memory provider on_session_end() + shutdown_all() are NOT
        # called here — run_conversation() is called once per user message in
        # multi-turn sessions. Shutting down after every turn would kill the
        # provider before the second message. Actual session-end cleanup is
        # handled by the CLI (atexit / /reset) and gateway (session expiry /
        # _reset_session).

        # Plugin hook: on_session_end
        # Fired at the very end of every run_conversation call.
        # Plugins can use this for cleanup, flushing buffers, etc.
        try:
            from VoidCube_cli.plugins import invoke_hook as _invoke_hook
            _invoke_hook(
                "on_session_end",
                session_id=self.session_id,
                completed=completed,
                interrupted=interrupted,
                model=self.model,
                platform=getattr(self, "platform", None) or "",
            )
        except Exception as exc:
            logger.warning("on_session_end hook failed: %s", exc)

        return result

    def chat(self, message: str, stream_callback: Optional[callable] = None) -> str:
        """
        Simple chat interface that returns just the final response.

        Args:
            message (str): User message
            stream_callback: Optional callback invoked with each text delta during streaming.

        Returns:
            str: Final assistant response
        """
        result = self.run_conversation(message, stream_callback=stream_callback)
        return result["final_response"]


def main(
    query: str = None,
    model: str = "",
    api_key: str = None,
    base_url: str = "",
    max_turns: int = 10,
    enabled_toolsets: str = None,
    disabled_toolsets: str = None,
    list_tools: bool = False,
    save_sample: bool = False,
    verbose: bool = False,
    log_prefix_chars: int = 20
):
    """
    Main function for running the agent directly.

    Args:
        query (str): Natural language query for the agent. Defaults to Python 3.13 example.
        model (str): Model name to use (OpenRouter format: provider/model).
        api_key (str): API key for authentication. Uses OPENROUTER_API_KEY env var if not provided.
        base_url (str): Base URL for the model API. Defaults to https://openrouter.ai/api/v1
        max_turns (int): Maximum number of API call iterations. Defaults to 10.
        enabled_toolsets (str): Comma-separated list of toolsets to enable. Supports predefined
                              toolsets (e.g., "research", "development", "safe").
                              Multiple toolsets can be combined: "web,vision"
        disabled_toolsets (str): Comma-separated list of toolsets to disable (e.g., "terminal")
        list_tools (bool): Just list available tools and exit
        save_sample (bool): Save a single trajectory sample to a UUID-named JSONL file for inspection. Defaults to False.
        verbose (bool): Enable verbose logging for debugging. Defaults to False.
        log_prefix_chars (int): Number of characters to show in log previews for tool calls/responses. Defaults to 20.

    Toolset Examples:
        - "research": Web search, extract, crawl + vision tools
    """
    print("🤖 AI Agent with Tool Calling")
    print("=" * 50)
    
    # Handle tool listing
    if list_tools:
        from tools.model_tools import get_all_tool_names, get_toolset_for_tool, get_available_toolsets
        from tools.toolsets import get_all_toolsets, get_toolset_info
        
        print("📋 Available Tools & Toolsets:")
        print("-" * 50)
        
        # Show new toolsets system
        print("\n🎯 Predefined Toolsets (New System):")
        print("-" * 40)
        all_toolsets = get_all_toolsets()
        
        # Group by category
        basic_toolsets = []
        composite_toolsets = []
        scenario_toolsets = []
        
        for name, toolset in all_toolsets.items():
            info = get_toolset_info(name)
            if info:
                entry = (name, info)
                if name in ["web", "terminal", "vision", "creative", "reasoning"]:
                    basic_toolsets.append(entry)
                elif name in ["research", "development", "analysis", "content_creation", "full_stack"]:
                    composite_toolsets.append(entry)
                else:
                    scenario_toolsets.append(entry)
        
        # Print basic toolsets
        print("\n📌 Basic Toolsets:")
        for name, info in basic_toolsets:
            tools_str = ', '.join(info['resolved_tools']) if info['resolved_tools'] else 'none'
            print(f"  • {name:15} - {info['description']}")
            print(f"    Tools: {tools_str}")
        
        # Print composite toolsets
        print("\n📂 Composite Toolsets (built from other toolsets):")
        for name, info in composite_toolsets:
            includes_str = ', '.join(info['includes']) if info['includes'] else 'none'
            print(f"  • {name:15} - {info['description']}")
            print(f"    Includes: {includes_str}")
            print(f"    Total tools: {info['tool_count']}")
        
        # Print scenario-specific toolsets
        print("\n🎭 Scenario-Specific Toolsets:")
        for name, info in scenario_toolsets:
            print(f"  • {name:20} - {info['description']}")
            print(f"    Total tools: {info['tool_count']}")
        
        
        # Show legacy toolset compatibility
        print("\n📦 Legacy Toolsets (for backward compatibility):")
        legacy_toolsets = get_available_toolsets()
        for name, info in legacy_toolsets.items():
            status = "✅" if info["available"] else "❌"
            print(f"  {status} {name}: {info['description']}")
            if not info["available"]:
                print(f"    Requirements: {', '.join(info['requirements'])}")
        
        # Show individual tools
        all_tools = get_all_tool_names()
        print(f"\n🔧 Individual Tools ({len(all_tools)} available):")
        for tool_name in sorted(all_tools):
            toolset = get_toolset_for_tool(tool_name)
            print(f"  📌 {tool_name} (from {toolset})")
        
        print("\n💡 Usage Examples:")
        print("  # Use predefined toolsets")
        print("  python run_agent.py --enabled_toolsets=research --query='search for Python news'")
        print("  python run_agent.py --enabled_toolsets=development --query='debug this code'")
        print("  python run_agent.py --enabled_toolsets=safe --query='analyze without terminal'")
        print("  ")
        print("  # Combine multiple toolsets")
        print("  python run_agent.py --enabled_toolsets=web,vision --query='analyze website'")
        print("  ")
        print("  # Disable toolsets")
        print("  python run_agent.py --disabled_toolsets=terminal --query='no command execution'")
        print("  ")
        return
    
    # Parse toolset selection arguments
    enabled_toolsets_list = None
    disabled_toolsets_list = None
    
    if enabled_toolsets:
        enabled_toolsets_list = [t.strip() for t in enabled_toolsets.split(",")]
        print(f"🎯 Enabled toolsets: {enabled_toolsets_list}")
    
    if disabled_toolsets:
        disabled_toolsets_list = [t.strip() for t in disabled_toolsets.split(",")]
        print(f"🚫 Disabled toolsets: {disabled_toolsets_list}")
    
    # Initialize agent with provided parameters
    try:
        agent = AIAgent(
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_iterations=max_turns,
            enabled_toolsets=enabled_toolsets_list,
            disabled_toolsets=disabled_toolsets_list,
            verbose_logging=verbose,
            log_prefix_chars=log_prefix_chars
        )
    except RuntimeError as e:
        print(f"❌ Failed to initialize agent: {e}")
        return
    
    # Use provided query or default to Python 3.13 example
    if query is None:
        user_query = (
            "Tell me about the latest developments in Python 3.13 and what new features "
            "developers should know about. Please search for current information and try it out."
        )
    else:
        user_query = query
    
    print(f"\n📝 User Query: {user_query}")
    print("\n" + "=" * 50)
    
    # Run conversation
    result = agent.run_conversation(user_query)
    
    print("\n" + "=" * 50)
    print("📋 CONVERSATION SUMMARY")
    print("=" * 50)
    print(f"✅ Completed: {result['completed']}")
    print(f"📞 API Calls: {result['api_calls']}")
    print(f"💬 Messages: {len(result['messages'])}")
    
    if result['final_response']:
        print("\n🎯 FINAL RESPONSE:")
        print("-" * 30)
        print(result['final_response'])
    
    # Save sample trajectory to UUID-named file if requested
    if save_sample:
        sample_id = str(uuid.uuid4())[:8]
        sample_filename = f"sample_{sample_id}.json"
        
        # Convert messages to trajectory format (same as batch_runner)
        trajectory = agent._convert_to_trajectory_format(
            result['messages'],
            user_query,
        )
        
        entry = {
            "conversations": trajectory,
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "completed": result['completed'],
            "query": user_query
        }
        
        try:
            with open(sample_filename, "w", encoding="utf-8") as f:
                # Pretty-print JSON with indent for readability
                f.write(json.dumps(entry, ensure_ascii=False, indent=2))
            print(f"\n💾 Sample trajectory saved to: {sample_filename}")
        except Exception as e:
            print(f"\n⚠️ Failed to save sample: {e}")
    
    print("\n👋 Agent execution completed!")


if __name__ == "__main__":
    fire.Fire(main)
