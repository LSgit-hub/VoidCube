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
    RetryRecoveryKind,
    decide_retry_directive,
    execute_retry_recovery,
    jittered_backoff,
    wait_for_retry,
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
    ContextRecoveryAction,
    ContextRecoveryKind,
    execute_context_recovery,
)
from agent.api_attempt import ApiAttemptState
from agent.client_lifecycle import ChatClientLifecycle
from agent.chat_transport import ChatTransport
from agent.stream_response import StreamChunkUpdate
from agent.tool_execution import (
    PreparedToolCall,
    ToolCallOutcome,
    ToolExecutionCoordinator,
)
from agent.tool_turn import (
    context_pressure_tracker,
    execute_successful_tool_turn,
)
from agent.turn_finalization import finalize_conversation_turn
from agent.conversation_turn import ConversationTurnState
from agent.iteration_control import IterationBudget
from agent.subdirectory_hints import SubdirectoryHintTracker
from agent.prompt_builder import build_skills_system_prompt, build_context_files_prompt, build_environment_hints, ensure_persistent_identity_guidance, has_canonical_memory_tools, load_soul_md, TOOL_USE_ENFORCEMENT_GUIDANCE, TOOL_USE_ENFORCEMENT_MODELS, GOOGLE_MODEL_OPERATIONAL_GUIDANCE, OPENAI_MODEL_EXECUTION_GUIDANCE
from agent.api_request import (
    ChatRequestConfig,
    build_chat_completion_kwargs,
    prepare_chat_messages,
)
from agent.api_response import (
    TruncationAction,
    decide_truncation_recovery,
    inspect_chat_response,
    normalize_assistant_message,
    strip_thinking_blocks,
    strip_thinking_tags,
)
from agent.response_disposition import (
    ResponseLoopControl,
    apply_text_response_disposition,
    apply_tool_call_inspection,
    decide_text_response_disposition,
    inspect_tool_calls,
    normalize_assistant_content,
)
from agent.usage_pricing import estimate_usage_cost, normalize_usage
from agent.tool_scheduler import (
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
from agent.session_persistence import (
    SessionPersistence,
)
from agent.tool_schema import normalize_tool_definitions
from VoidCube_core.utils import env_var_enabled



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
        self._tool_thread_ids: set[int] = set()
        self._tool_thread_ids_lock = threading.Lock()
        
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
                    'VoidCube_cli',         # CLI helpers
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

        self.api_key = client_kwargs.get("api_key", "")
        self.base_url = client_kwargs.get("base_url", self.base_url)
        self._client_lifecycle = ChatClientLifecycle(
            client_kwargs=client_kwargs,
            provider=lambda: self.provider or "",
            model=lambda: self.model or "",
            base_url=lambda: self.base_url or "",
        )
        try:
            self._client_lifecycle.initialize_primary(reason="agent_init")
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
            raise RuntimeError(f"Failed to initialize OpenAI-compatible client: {e}")
        self._chat_transport = ChatTransport(
            client_lifecycle=self._client_lifecycle,
            base_url=lambda: self.base_url or "",
            model=lambda: self.model or "",
            interrupted=lambda: self._interrupt_requested,
            activity=self._touch_activity,
            capture_rate_limits=self._capture_rate_limits,
            emit_status=self._emit_status,
            emit_warning=lambda message: self._safe_print(f"\n⚠ {message}\n"),
        )
        
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

        self._session_persistence = SessionPersistence(
            enabled=persist_session,
            logs_dir=self.logs_dir,
            session_db=self._session_db,
            session_start=self.session_start,
            session_id=lambda: self.session_id,
            model=lambda: self.model,
            base_url=lambda: self.base_url,
            platform=lambda: self.platform,
            system_prompt=lambda: self._cached_system_prompt,
            tools=lambda: self.tools,
            user_message_override=lambda: (
                self._persist_user_message_idx,
                self._persist_user_message_override,
            ),
            verbose_logging=self.verbose_logging,
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

        self._iters_since_skill = 0

        # Canonical Memory Service provider.
        self._memory_manager = None
        if not skip_memory:
            try:
                from agent.memory_manager import MemoryManager as _MemoryManager
                from plugins.memory.mem import MemMemoryProvider

                self._memory_manager = _MemoryManager()
                self._memory_manager.add_provider(MemMemoryProvider())
                from VoidCube_core.constants import get_VoidCube_home as _ghh
                _init_kwargs = {
                    "session_id": self.session_id,
                    "platform": platform or "cli",
                    "VoidCube_home": str(_ghh()),
                    "agent_context": "primary",
                }
                if self._user_id:
                    _init_kwargs["user_id"] = self._user_id
                try:
                    from VoidCube_cli.profiles import get_active_profile_name
                    _profile = get_active_profile_name()
                    _init_kwargs["agent_identity"] = _profile
                    _init_kwargs["agent_workspace"] = "VoidCube"
                except Exception:
                    pass
                self._memory_manager.initialize_all(**_init_kwargs)
                logger.info("Canonical Mem provider activated")
            except Exception as _mpe:
                logger.warning("Canonical Mem provider init failed: %s", _mpe)
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
            "client_kwargs": self._client_lifecycle.snapshot_kwargs(),
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
        old_base_url = self.base_url
        old_api_key = self.api_key

        # ── Swap core runtime fields ──
        self.model = new_model
        self.provider = new_provider
        self.base_url = base_url or self.base_url
        if api_key:
            self.api_key = api_key

        # ── Build new client ──
        effective_key = api_key or self.api_key
        effective_base = base_url or self.base_url
        client_kwargs = {
            "api_key": effective_key,
            "base_url": effective_base,
        }
        if not self._client_lifecycle.configure(client_kwargs, reason="switch_model"):
            self.model = old_model
            self.provider = old_provider
            self.base_url = old_base_url
            self.api_key = old_api_key
            raise RuntimeError("Failed to initialize client for the selected model")

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
            "client_kwargs": self._client_lifecycle.snapshot_kwargs(),
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
        can crash background runs and lose completed work.

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
    # Background skill review
    # ------------------------------------------------------------------

    _SKILL_REVIEW_PROMPT = (
        "Review the conversation above and consider saving or updating a skill if appropriate.\n\n"
        "Focus on: was a non-trivial approach used to complete a task that required trial "
        "and error, or changing course due to experiential findings along the way, or did "
        "the user expect or desire a different method or outcome?\n\n"
        "If a relevant skill already exists, update it with what you learned. "
        "Otherwise, create a new skill if the approach is reusable.\n"
        "If nothing is worth saving, just say 'Nothing to save.' and stop."
    )

    def _spawn_background_review(
        self,
        messages_snapshot: List[Dict],
    ) -> None:
        """Spawn a background thread to review the conversation for skill saves.

        Creates a full AIAgent fork with the same model, tools, and context as the
        main session. The review prompt is appended as the next user turn in the
        forked conversation. Writes directly to the shared skill store.
        Never modifies the main conversation history or produces user-visible output.
        """
        import threading
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
                    review_agent._skill_nudge_interval = 0

                    review_agent.run_conversation(
                        user_message=prompt,
                        conversation_history=messages_snapshot,
                    )

                # Scan the review agent's messages for successful tool actions
                # and surface a compact summary to the user.
                actions = []
                for msg in review_agent._session_persistence.messages:
                    if not isinstance(msg, dict) or msg.get("role") != "tool":
                        continue
                    try:
                        data = json.loads(msg.get("content", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not data.get("success"):
                        continue
                    message = data.get("message", "")
                    if "created" in message.lower():
                        actions.append(message)
                    elif "updated" in message.lower():
                        actions.append(message)
                    elif "added" in message.lower():
                        actions.append(message)
                    elif "removed" in message.lower() or "replaced" in message.lower():
                        actions.append(message)

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
                logger.debug("Background skill review failed: %s", e)
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
                api_key = self._client_lifecycle.active_api_key()
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
        # Scope the interrupt to this agent's execution and tool worker threads
        # so other agents running in the same process are not affected.
        with self._tool_thread_ids_lock:
            for thread_id in self._tool_thread_ids:
                _set_interrupt(True, thread_id)
        if self._execution_thread_id is not None:
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
        with self._tool_thread_ids_lock:
            for thread_id in self._tool_thread_ids:
                _set_interrupt(False, thread_id)
        if self._execution_thread_id is not None:
            _set_interrupt(False, self._execution_thread_id)

    def _register_tool_thread(self) -> int:
        """Register the actual invoking thread and inherit pending interrupt state."""
        thread_id = threading.current_thread().ident
        if thread_id is None:
            raise RuntimeError("Tool execution thread has no identifier")
        with self._tool_thread_ids_lock:
            self._tool_thread_ids.add(thread_id)
            _set_interrupt(self._interrupt_requested, thread_id)
        return thread_id

    def _unregister_tool_thread(self, thread_id: int) -> None:
        with self._tool_thread_ids_lock:
            self._tool_thread_ids.discard(thread_id)
            _set_interrupt(False, thread_id)

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

        # 5. Close the OpenAI-compatible/httpx client
        try:
            self._client_lifecycle.close_primary(reason="agent_close")
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
        #   3. Tool-aware behavior guidance
        #   4. Skills guidance (if skills tools are loaded)
        #   5. Context files (AGENTS.md, .cursorrules — SOUL.md excluded here when used as identity)
        #   6. Current date & time (frozen at build time)
        #   7. Platform-specific formatting hint

        # Try SOUL.md as primary identity (unless context files are skipped)
        _soul_loaded = False
        if not self.skip_context_files:
            _soul_content = load_soul_md()
            if _soul_content:
                prompt_parts = [ensure_persistent_identity_guidance(_soul_content)]
                _soul_loaded = True

        if not _soul_loaded:
            # Fallback to hardcoded identity
            prompt_parts = [ensure_persistent_identity_guidance(DEFAULT_AGENT_IDENTITY)]

        # Tool-aware behavioral guidance: only inject when the tools are loaded
        tool_guidance = []
        if has_canonical_memory_tools(self.valid_tool_names):
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

        # Canonical Mem provider system prompt block.
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
        
        Called after context compression events.
        """
        self._cached_system_prompt = None

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

        next_api_key = api_key.strip()
        next_base_url = base_url.strip().rstrip("/")
        if not self._client_lifecycle.configure(
            {"api_key": next_api_key, "base_url": next_base_url},
            reason="nous_credential_refresh",
        ):
            return False
        self.api_key = next_api_key
        self.base_url = next_base_url
        return True

    @staticmethod
    def _client_kwargs_for_credentials(api_key: str, base_url: str) -> dict:
        from agent.auxiliary_client import _OR_HEADERS

        client_kwargs = {"api_key": api_key, "base_url": base_url}
        normalized = (base_url or "").lower()
        if "openrouter" in normalized:
            client_kwargs["default_headers"] = dict(_OR_HEADERS)
        elif "api.kimi.com" in normalized:
            client_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
        elif "portal.qwen.ai" in normalized:
            client_kwargs["default_headers"] = _qwen_portal_headers()
        return client_kwargs

    def _swap_credential(self, entry) -> bool:
        runtime_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
        runtime_base = getattr(entry, "runtime_base_url", None) or getattr(entry, "base_url", None) or self.base_url

        next_base_url = runtime_base.rstrip("/") if isinstance(runtime_base, str) else runtime_base
        client_kwargs = self._client_kwargs_for_credentials(runtime_key, next_base_url)
        if not self._client_lifecycle.configure(
            client_kwargs,
            reason="credential_rotation",
        ):
            return False
        self.api_key = runtime_key
        self.base_url = next_base_url
        return True

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
                if self._swap_credential(next_entry):
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
                if self._swap_credential(next_entry):
                    return True, False
            return False, True

        if effective_reason == FailoverReason.auth:
            refreshed = pool.try_refresh_current()
            if refreshed is not None:
                logger.info(f"Credential auth failure — refreshed pool entry {getattr(refreshed, 'id', '?')}")
                if self._swap_credential(refreshed):
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
                if self._swap_credential(next_entry):
                    return True, False

        return False, has_retried_429

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

    def _deliver_stream_update(self, update: StreamChunkUpdate) -> None:
        """Adapt transport-neutral stream events to Agent UI callbacks."""
        if update.reasoning:
            self._fire_reasoning_delta(update.reasoning)
        if update.content:
            if update.stream_content:
                self._fire_stream_delta(update.content)
            elif self.stream_delta_callback:
                try:
                    self.stream_delta_callback(update.content)
                    self._record_streamed_assistant_text(update.content)
                except Exception:
                    pass
        for tool_name in update.started_tools:
            self._fire_tool_gen_started(tool_name)

    def _has_stream_consumers(self) -> bool:
        """Return True if any streaming consumer is registered."""
        return (
            self.stream_delta_callback is not None
            or getattr(self, "_stream_callback", None) is not None
        )

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

            # Adopt the resolved fallback transport and its request parameters.
            self.api_key = fb_client.api_key
            fb_headers = getattr(fb_client, "_custom_headers", None)
            if not fb_headers:
                fb_headers = getattr(fb_client, "default_headers", None)
            fallback_client_kwargs = {
                "api_key": fb_client.api_key,
                "base_url": fb_base_url,
                **({"default_headers": dict(fb_headers)} if fb_headers else {}),
            }
            self._client_lifecycle.adopt(
                fb_client,
                fallback_client_kwargs,
                reason="fallback_activation",
            )

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
        fallback_runtime = {
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": self.api_key,
        }
        try:
            # ── Core runtime state ──
            self.model = rt["model"]
            self.provider = rt["provider"]
            self.base_url = rt["base_url"]           # setter updates _base_url_lower
            self.api_key = rt["api_key"]
            if not self._client_lifecycle.configure(
                rt["client_kwargs"],
                reason="restore_primary",
            ):
                self.model = fallback_runtime["model"]
                self.provider = fallback_runtime["provider"]
                self.base_url = fallback_runtime["base_url"]
                self.api_key = fallback_runtime["api_key"]
                raise RuntimeError("Failed to restore primary chat client")

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
            # Rebuild from primary snapshot
            rt = self._primary_runtime
            self.model = rt["model"]
            self.provider = rt["provider"]
            self.base_url = rt["base_url"]
            self.api_key = rt["api_key"]

            if not self._client_lifecycle.configure(
                rt["client_kwargs"],
                reason="primary_recovery",
            ):
                return False

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
        # Notify canonical Mem before compression discards context.
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
                self._session_persistence.reset_flush_cursor()
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
                context_pressure_tracker.reset(self.session_id or "default")

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
        self._session_persistence.persist(messages, conversation_history)
        return {
            "messages": messages,
            "completed": False,
            "api_calls": api_call_count,
            "error": error,
            "partial": True,
        }

    def _execute_tool_calls(
        self,
        assistant_message,
        messages: list,
        effective_task_id: str,
    ) -> None:
        """Execute one assistant tool batch through the canonical coordinator."""
        tool_calls = tuple(assistant_message.tool_calls or ())
        prepared = ToolExecutionCoordinator.prepare(tool_calls)
        if not prepared:
            return
        parallel = should_parallelize_tool_batch(tool_calls)
        announced_skips: set[str] = set()
        batch_spinner: dict[str, Any] = {"value": None}

        if parallel and not self.quiet_mode:
            names = ", ".join(call.name for call in prepared)
            print(f"  🔧 Concurrent: {len(prepared)} tool calls - {names}")

        coordinator = ToolExecutionCoordinator(
            invoke=lambda call: self._invoke_prepared_tool(
                call,
                messages=messages,
                effective_task_id=effective_task_id,
                parallel=parallel,
            ),
            is_interrupted=lambda: self._interrupt_requested,
            classify_failure=_detect_tool_failure,
            max_workers=MAX_TOOL_WORKERS,
            delay=self.tool_delay,
        )

        self._executing_tools = True
        try:
            coordinator.execute(
                prepared,
                parallel=parallel,
                before_call=lambda call: self._prepare_tool_call(
                    call, parallel=parallel
                ),
                after_call=lambda outcome: self._complete_tool_call(
                    outcome,
                    messages=messages,
                    effective_task_id=effective_task_id,
                    parallel=parallel,
                    announced_skips=announced_skips,
                ),
                batch_started=lambda calls: self._start_parallel_tool_batch(
                    calls, batch_spinner
                ),
                batch_completed=lambda outcomes: self._finish_parallel_tool_batch(
                    outcomes, batch_spinner
                ),
            )
        finally:
            self._executing_tools = False

        enforce_turn_budget(
            messages[-len(prepared) :],
            env=get_active_env(effective_task_id),
        )

    def _prepare_tool_call(
        self,
        call: PreparedToolCall,
        *,
        parallel: bool,
    ) -> None:
        if call.name == "skill_manage":
            self._iters_since_skill = 0

        self._checkpoint_tool_call(call)
        args_str = json.dumps(call.arguments, ensure_ascii=False)
        if not self.quiet_mode:
            if self.verbose_logging:
                print(
                    f"  📞 Tool {call.position}: "
                    f"{call.name}({list(call.arguments.keys())})"
                )
                print(f"     Args: {args_str}")
            else:
                preview = (
                    args_str[: self.log_prefix_chars] + "..."
                    if len(args_str) > self.log_prefix_chars
                    else args_str
                )
                print(
                    f"  📞 Tool {call.position}: "
                    f"{call.name}({list(call.arguments.keys())}) - {preview}"
                )

        if not parallel:
            self._current_tool = call.name
            self._touch_activity(f"executing tool: {call.name}")

        if self.tool_progress_callback:
            try:
                preview = _build_tool_preview(call.name, call.arguments)
                self.tool_progress_callback(
                    "tool.started", call.name, preview, call.arguments
                )
            except Exception as exc:
                logger.debug("Tool progress callback error: %s", exc)
        if self.tool_start_callback:
            try:
                self.tool_start_callback(call.call_id, call.name, call.arguments)
            except Exception as exc:
                logger.debug("Tool start callback error: %s", exc)

    def _checkpoint_tool_call(self, call: PreparedToolCall) -> None:
        if not self._checkpoint_mgr.enabled:
            return
        try:
            if call.name in ("write_file", "patch"):
                file_path = call.arguments.get("path", "")
                if file_path:
                    work_dir = self._checkpoint_mgr.get_working_dir_for_path(
                        file_path
                    )
                    self._checkpoint_mgr.ensure_checkpoint(
                        work_dir, f"before {call.name}"
                    )
            elif call.name == "terminal":
                command = call.arguments.get("command", "")
                if is_destructive_command(command):
                    work_dir = call.arguments.get("workdir") or os.getenv(
                        "TERMINAL_CWD", os.getcwd()
                    )
                    self._checkpoint_mgr.ensure_checkpoint(
                        work_dir, f"before terminal: {command[:60]}"
                    )
        except Exception:
            logger.debug("Tool checkpoint failed", exc_info=True)

    def _invoke_prepared_tool(
        self,
        call: PreparedToolCall,
        *,
        messages: list,
        effective_task_id: str,
        parallel: bool,
    ) -> str:
        thread_id = self._register_tool_thread()
        try:
            return self._invoke_prepared_tool_in_current_thread(
                call,
                messages=messages,
                effective_task_id=effective_task_id,
                parallel=parallel,
            )
        finally:
            self._unregister_tool_thread(thread_id)

    def _invoke_prepared_tool_in_current_thread(
        self,
        call: PreparedToolCall,
        *,
        messages: list,
        effective_task_id: str,
        parallel: bool,
    ) -> str:
        try:
            from tools.environments.base import set_activity_callback

            set_activity_callback(self._touch_activity)
        except Exception:
            pass

        if call.name == "delegate_task":
            return self._invoke_delegate_tool(call)

        spinner = None
        display_started = time.time()
        fast_tools = {"todo", "session_search", "memory", "clarify"}
        if (
            not parallel
            and call.name not in fast_tools
            and self._should_emit_quiet_tool_messages()
            and self._should_start_quiet_spinner()
        ):
            face = random.choice(KawaiiSpinner.KAWAII_WAITING)
            emoji = _get_tool_emoji(call.name)
            preview = _build_tool_preview(call.name, call.arguments) or call.name
            spinner = KawaiiSpinner(
                f"{face} {emoji} {preview}",
                spinner_type="dots",
                print_fn=self._print_fn,
            )
            spinner.start()

        display_result = None
        try:
            raw_result = self._route_tool_call(
                call,
                messages=messages,
                effective_task_id=effective_task_id,
            )
            display_result = (
                raw_result if isinstance(raw_result, str) else str(raw_result)
            )
            return display_result
        except Exception as exc:
            display_result = f"Error executing tool '{call.name}': {exc}"
            raise
        finally:
            if not parallel:
                duration = time.time() - display_started
                cute_message = _get_cute_tool_message_impl(
                    call.name,
                    call.arguments,
                    duration,
                    result=display_result,
                )
                if spinner:
                    spinner.stop(cute_message)
                elif self._should_emit_quiet_tool_messages():
                    self._vprint(f"  {cute_message}")

    def _route_tool_call(
        self,
        call: PreparedToolCall,
        *,
        messages: list,
        effective_task_id: str,
    ) -> str:
        function_name = call.name
        function_args = call.arguments
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
        elif (
            self._context_engine_tool_names
            and function_name in self._context_engine_tool_names
        ):
            try:
                return self.context_compressor.handle_tool_call(
                    function_name,
                    function_args,
                    messages=messages,
                )
            except Exception as exc:
                logger.error(
                    "context_engine.handle_tool_call raised for %s: %s",
                    function_name,
                    exc,
                    exc_info=True,
                )
                return json.dumps(
                    {"error": f"Context engine tool '{function_name}' failed: {exc}"}
                )
        elif self._memory_manager and self._memory_manager.has_tool(function_name):
            try:
                return self._memory_manager.handle_tool_call(
                    function_name, function_args
                )
            except Exception as exc:
                logger.error(
                    "memory_manager.handle_tool_call raised for %s: %s",
                    function_name,
                    exc,
                    exc_info=True,
                )
                return json.dumps(
                    {"error": f"Memory tool '{function_name}' failed: {exc}"}
                )
        elif function_name == "clarify":
            from tools.clarify_tool import clarify_tool as _clarify_tool
            return _clarify_tool(
                question=function_args.get("question", ""),
                choices=function_args.get("choices"),
                callback=self.clarify_callback,
            )
        else:
            return handle_function_call(
                function_name, function_args, effective_task_id,
                tool_call_id=call.call_id,
                session_id=self.session_id or "",
                enabled_tools=list(self.valid_tool_names) if self.valid_tool_names else None,
                main_runtime=self._current_main_runtime(),
            )

    def _invoke_delegate_tool(self, call: PreparedToolCall) -> str:
        from tools.delegate_tool import delegate_task as _delegate_task

        tasks = call.arguments.get("tasks")
        if tasks and isinstance(tasks, list):
            spinner_label = f"🔀 正在委派 {len(tasks)} 个任务"
        else:
            goal_preview = (call.arguments.get("goal") or "")[:30]
            spinner_label = f"🔀 {goal_preview}" if goal_preview else "🔀 委派中"
        use_rich_display = self._print_fn is not None
        spinner = None
        if (
            not use_rich_display
            and self._should_emit_quiet_tool_messages()
            and self._should_start_quiet_spinner()
        ):
            face = random.choice(KawaiiSpinner.KAWAII_WAITING)
            spinner = KawaiiSpinner(
                f"{face} {spinner_label}",
                spinner_type="dots",
                print_fn=self._print_fn,
            )
            spinner.start()

        self._delegate_spinner = spinner
        result = None
        started = time.time()
        try:
            raw_result = _delegate_task(
                goal=call.arguments.get("goal"),
                context=call.arguments.get("context"),
                toolsets=call.arguments.get("toolsets"),
                tasks=tasks,
                max_iterations=call.arguments.get("max_iterations"),
                parent_agent=self,
                enable_display=use_rich_display,
            )
            result = raw_result if isinstance(raw_result, str) else str(raw_result)
            return result
        except Exception as exc:
            result = f"Error executing tool 'delegate_task': {exc}"
            raise
        finally:
            self._delegate_spinner = None
            cute_message = _get_cute_tool_message_impl(
                "delegate_task",
                call.arguments,
                time.time() - started,
                result=result,
            )
            if spinner:
                spinner.stop(cute_message)
            elif not use_rich_display and self._should_emit_quiet_tool_messages():
                self._vprint(f"  {cute_message}")

    def _complete_tool_call(
        self,
        outcome: ToolCallOutcome,
        *,
        messages: list,
        effective_task_id: str,
        parallel: bool,
        announced_skips: set[str],
    ) -> None:
        call = outcome.call
        if outcome.skipped:
            if outcome.skip_reason not in announced_skips:
                announced_skips.add(outcome.skip_reason or "")
                self._vprint(
                    f"{self.log_prefix}🔧 Interrupt: skipping tool calls",
                    force=True,
                )
            messages.append(
                {
                    "role": "tool",
                    "content": outcome.content,
                    "tool_call_id": call.call_id,
                }
            )
            return

        result = outcome.content
        preview = result if self.verbose_logging else result[:200]
        if outcome.is_error:
            logger.warning(
                "Tool %s returned error (%.2fs): %s",
                call.name,
                outcome.duration,
                preview,
            )
        else:
            logger.info(
                "tool %s completed (%.2fs, %d chars)",
                call.name,
                outcome.duration,
                len(result),
            )

        if self.tool_progress_callback:
            try:
                self.tool_progress_callback(
                    "tool.completed",
                    call.name,
                    None,
                    None,
                    duration=outcome.duration,
                    is_error=outcome.is_error,
                )
            except Exception as exc:
                logger.debug("Tool progress callback error: %s", exc)

        self._current_tool = None
        self._touch_activity(
            f"tool completed: {call.name} ({outcome.duration:.1f}s)"
        )
        if self.tool_complete_callback:
            try:
                self.tool_complete_callback(
                    call.call_id, call.name, call.arguments, result
                )
            except Exception as exc:
                logger.debug("Tool complete callback error: %s", exc)

        persisted_result = maybe_persist_tool_result(
            content=result,
            tool_name=call.name,
            tool_use_id=call.call_id,
            env=get_active_env(effective_task_id),
        )
        result = (
            persisted_result
            if isinstance(persisted_result, str)
            else str(persisted_result)
        )
        subdir_hints = self._subdirectory_hints.check_tool_call(
            call.name, call.arguments
        )
        if subdir_hints:
            result += subdir_hints
        messages.append(
            {
                "role": "tool",
                "content": result,
                "tool_call_id": call.call_id,
            }
        )

        if parallel and self._should_emit_quiet_tool_messages():
            self._safe_print(
                "  "
                + _get_cute_tool_message_impl(
                    call.name,
                    call.arguments,
                    outcome.duration,
                    result=outcome.content,
                )
            )
        elif not self.quiet_mode:
            if self.verbose_logging:
                print(
                    f"  ✅ Tool {call.position} completed in "
                    f"{outcome.duration:.2f}s"
                )
                print(f"     Result: {result}")
            else:
                result_preview = (
                    result[: self.log_prefix_chars] + "..."
                    if len(result) > self.log_prefix_chars
                    else result
                )
                print(
                    f"  ✅ Tool {call.position} completed in "
                    f"{outcome.duration:.2f}s - {result_preview}"
                )

    def _start_parallel_tool_batch(
        self,
        calls: tuple[PreparedToolCall, ...],
        holder: dict[str, Any],
    ) -> None:
        if not (
            self._should_emit_quiet_tool_messages()
            and self._should_start_quiet_spinner()
        ):
            return
        face = random.choice(KawaiiSpinner.KAWAII_WAITING)
        spinner = KawaiiSpinner(
            f"{face} 🔧 正在并发执行 {len(calls)} 个工具",
            spinner_type="dots",
            print_fn=self._print_fn,
        )
        holder["value"] = spinner
        spinner.start()

    @staticmethod
    def _finish_parallel_tool_batch(
        outcomes: tuple[ToolCallOutcome, ...],
        holder: dict[str, Any],
    ) -> None:
        spinner = holder.get("value")
        if spinner is None:
            return
        spinner.stop(
            f"🔧 {len(outcomes)}/{len(outcomes)} 个工具已完成，"
            f"总耗时 {sum(item.duration for item in outcomes):.1f}s"
        )

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
            api_messages = prepare_chat_messages(
                messages,
                system_prompt=self._cached_system_prompt or "",
                ephemeral_system_prompt=self.ephemeral_system_prompt or "",
                prefill_messages=self.prefill_messages or (),
            )

            summary_kwargs = build_chat_completion_kwargs(
                self._chat_request_config(),
                api_messages,
                include_tools=False,
                include_request_overrides=False,
            )

            summary_response = self._chat_transport.complete(summary_kwargs)
            summary_inspection = inspect_chat_response(summary_response)
            final_response = (
                summary_inspection.message.content
                if summary_inspection.valid and summary_inspection.message.content
                else ""
            )

            if final_response:
                final_response = strip_thinking_blocks(final_response).strip()
                if final_response:
                    messages.append({"role": "assistant", "content": final_response})
                else:
                    final_response = "I reached the iteration limit and couldn't generate a summary."
            else:
                # Retry summary generation
                summary_response = self._chat_transport.complete(summary_kwargs)
                summary_inspection = inspect_chat_response(summary_response)
                final_response = (
                    summary_inspection.message.content
                    if summary_inspection.valid and summary_inspection.message.content
                    else ""
                )

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

        # Store the per-turn stream callback consumed by _deliver_stream_update.
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
            if self._client_lifecycle.cleanup_dead_connections():
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

        # Skill review cadence persists across run_conversation calls in CLI mode.
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
        
        # Preserve the original user message (no nudge injection).
        original_user_message = persist_user_message if persist_user_message is not None else user_message

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
                    # reference so SessionPersistence writes ALL
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
        turn_state = ConversationTurnState()
        
        # Record the execution thread so interrupt()/clear_interrupt() can
        # scope the tool-level interrupt signal to THIS agent's thread only.
        # Must be set before clear_interrupt() which uses it.
        self._execution_thread_id = threading.current_thread().ident

        # Clear any stale interrupt state at start
        self.clear_interrupt()

        # Canonical Mem: prefetch once before the tool loop.
        # Reuse the cached result on every iteration to avoid re-calling
        # prefetch_all() on each tool call (10 tool calls = 10x latency + cost).
        # Use original_user_message (clean input) — user_message may contain
        # injected skill content that bloats / breaks provider queries.
        _ext_prefetch_cache = ""
        if self._memory_manager:
            try:
                _query = original_user_message if isinstance(original_user_message, str) else ""
                _ext_prefetch_cache = self._memory_manager.prefetch_all(
                    _query,
                    session_id=self.session_id,
                ) or ""
            except Exception:
                pass

        while turn_state.can_continue(
            max_iterations=self.max_iterations,
            iteration_budget=self.iteration_budget,
        ):
            # Reset per-turn checkpoint dedup so each iteration can take one snapshot
            self._checkpoint_mgr.new_turn()

            # Check for interrupt request (e.g., user sent new message)
            if self._interrupt_requested:
                turn_state.interrupted = True
                turn_state.exit_reason = "interrupted_by_user"
                if not self.quiet_mode:
                    self._safe_print("\n🔧 Breaking out of tool loop due to interrupt...")
                break

            iteration_started = turn_state.begin_iteration(self.iteration_budget)
            self._api_call_count = turn_state.api_call_count
            if not iteration_started:
                turn_state.exit_reason = "budget_exhausted"
                if not self.quiet_mode:
                    self._safe_print(f"\n⚠️  Iteration budget exhausted ({self.iteration_budget.used}/{self.iteration_budget.max_total} iterations used)")
                break
            self._touch_activity(
                f"starting API call #{turn_state.api_call_count}"
            )

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
                    self.step_callback(turn_state.api_call_count, prev_tools)
                except Exception as _step_err:
                    logger.debug(
                        "step_callback error (iteration %s): %s",
                        turn_state.api_call_count,
                        _step_err,
                    )

            # Track tool-calling iterations for skill nudge.
            # Counter resets whenever skill_manage is actually used.
            if (self._skill_nudge_interval > 0
                    and "skill_manage" in self.valid_tool_names):
                self._iters_since_skill += 1
            
            user_contexts = []
            if _ext_prefetch_cache:
                fenced_memory = build_memory_context_block(_ext_prefetch_cache)
                if fenced_memory:
                    user_contexts.append(fenced_memory)
            if _plugin_user_context:
                user_contexts.append(_plugin_user_context)
            api_messages = prepare_chat_messages(
                messages,
                system_prompt=active_system_prompt or "",
                ephemeral_system_prompt=self.ephemeral_system_prompt or "",
                prefill_messages=self.prefill_messages or (),
                user_message_index=current_turn_user_idx,
                user_contexts=user_contexts,
            )

            # Calculate approximate request size for logging
            total_chars = sum(len(str(msg)) for msg in api_messages)
            approx_tokens = estimate_messages_tokens_rough(api_messages)
            
            # Thinking spinner for quiet mode (animated during API call)
            thinking_spinner = None
            
            if not self.quiet_mode:
                self._vprint(
                    f"\n{self.log_prefix}🔄 正在发起 API 调用 "
                    f"#{turn_state.api_call_count}/{self.max_iterations}..."
                )
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
            
            attempt_state = ApiAttemptState(started_at=time.time())

            while attempt_state.can_retry:
                try:
                    self._reset_stream_delivery_tracking()
                    attempt_state.request_kwargs = build_chat_completion_kwargs(
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
                            api_call_count=turn_state.api_call_count,
                            message_count=len(api_messages),
                            tool_count=len(self.tools or []),
                            approx_input_tokens=approx_tokens,
                            request_char_count=total_chars,
                            max_tokens=self.max_tokens,
                        )
                    except Exception:
                        pass

                    if env_var_enabled("VOIDCUBE_DUMP_REQUESTS"):
                        self._dump_api_request_debug(
                            attempt_state.request_kwargs,
                            reason="preflight",
                        )

                    # Always prefer the streaming path — even without stream
                    # consumers.  Streaming gives us fine-grained health
                    # checking (configurable stale-stream and read timeouts)
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

                    attempt_state.response = self._chat_transport.stream(
                        attempt_state.request_kwargs,
                        on_update=self._deliver_stream_update,
                        on_first_delta=_stop_spinner,
                    )
                    
                    api_duration = time.time() - attempt_state.started_at
                    
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
                        resp_model = (
                            getattr(attempt_state.response, "model", "N/A")
                            if attempt_state.response
                            else "N/A"
                        )
                        response_usage = (
                            attempt_state.response.usage
                            if hasattr(attempt_state.response, "usage")
                            else "N/A"
                        )
                        logging.debug(
                            "API Response received - Model: %s, Usage: %s",
                            resp_model,
                            response_usage,
                        )
                    
                    attempt_state.response_inspection = inspect_chat_response(
                        attempt_state.response,
                        duration_seconds=api_duration,
                    )
                    if not attempt_state.response_inspection.valid:
                        error_details = list(
                            attempt_state.response_inspection.errors
                        )
                        # Stop spinner before printing error messages
                        if thinking_spinner:
                            thinking_spinner.stop("(´;ω;`) oops, retrying...")
                            thinking_spinner = None
                        if self.thinking_callback:
                            self.thinking_callback("")
                        
                        # Invalid response — could be rate limiting, provider timeout,
                        # upstream server error, or malformed response.
                        attempt_state.record_failure()
                        
                        # Eager fallback: empty/malformed responses are a common
                        # rate-limit symptom.  Switch to fallback immediately
                        # rather than retrying with extended backoff.
                        if self._fallback_index < len(self._fallback_chain):
                            self._emit_status("⚠️ Empty/malformed response — switching to fallback...")
                        if self._try_activate_fallback():
                            attempt_state.reset_retry_cycle()
                            turn_state.compression_attempts = 0
                            continue

                        error_msg = attempt_state.response_inspection.provider_error
                        provider_name = attempt_state.response_inspection.provider_name
                        _failure_hint = attempt_state.response_inspection.failure_hint

                        self._vprint(f"{self.log_prefix}⚠️  Invalid API response (attempt {attempt_state.retry_count}/{attempt_state.max_retries}): {', '.join(error_details)}", force=True)
                        self._vprint(f"{self.log_prefix}   🏢 Provider: {provider_name}", force=True)
                        cleaned_provider_error = clean_error_message(error_msg)
                        self._vprint(f"{self.log_prefix}   📝 Provider message: {cleaned_provider_error}", force=True)
                        self._vprint(f"{self.log_prefix}   ⏱️  {_failure_hint}", force=True)
                        
                        if not attempt_state.can_retry:
                            # Try fallback before giving up
                            self._emit_status(f"⚠️ Max retries ({attempt_state.max_retries}) for invalid responses — trying fallback...")
                            if self._try_activate_fallback():
                                attempt_state.reset_retry_cycle()
                                turn_state.compression_attempts = 0
                                continue
                            self._emit_status(f"❌ Max retries ({attempt_state.max_retries}) exceeded for invalid responses. Giving up.")
                            logging.error(f"{self.log_prefix}Invalid API response after {attempt_state.max_retries} retries.")
                            self._session_persistence.persist(messages, conversation_history)
                            return {
                                "messages": messages,
                                "completed": False,
                                "api_calls": turn_state.api_call_count,
                                "error": f"Invalid API response after {attempt_state.max_retries} retries: {_failure_hint}",
                                "failed": True  # Mark as failure for filtering
                            }
                        
                        # Backoff before retry — jittered exponential: 5s base, 120s cap
                        wait_time = jittered_backoff(attempt_state.retry_count, base_delay=5.0, max_delay=120.0)
                        self._vprint(f"{self.log_prefix}⏳ Retrying in {wait_time:.1f}s ({_failure_hint})...", force=True)
                        logging.warning(f"Invalid API response (retry {attempt_state.retry_count}/{attempt_state.max_retries}): {', '.join(error_details)} | Provider: {provider_name}")
                        
                        if not wait_for_retry(
                            wait_time,
                            interrupted=lambda: self._interrupt_requested,
                        ):
                            self._vprint(
                                f"{self.log_prefix}🔧 Interrupt detected during "
                                "retry wait, aborting.",
                                force=True,
                            )
                            self._session_persistence.persist(
                                messages,
                                conversation_history,
                            )
                            self.clear_interrupt()
                            return {
                                "final_response": (
                                    "Operation interrupted during retry "
                                    f"({_failure_hint}, attempt "
                                    f"{attempt_state.retry_count}/"
                                    f"{attempt_state.max_retries})."
                                ),
                                "messages": messages,
                                "api_calls": turn_state.api_call_count,
                                "completed": False,
                                "interrupted": True,
                            }
                        continue  # Retry the API call

                    # Check finish_reason before proceeding
                    attempt_state.finish_reason = (
                        attempt_state.response_inspection.finish_reason
                    )

                    if attempt_state.finish_reason == "length":
                        self._vprint(f"{self.log_prefix}⚠️  Response truncated (finish_reason='length') - model hit max output tokens", force=True)
                        assistant_message = attempt_state.response_inspection.message
                        truncation = decide_truncation_recovery(
                            assistant_message,
                            attempt_state.finish_reason,
                            text_truncation_count=turn_state.length_continue_retries,
                            tool_truncation_count=turn_state.truncated_tool_call_retries,
                        )

                        if truncation.action is TruncationAction.fail_thinking_budget:
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
                            self._session_persistence.persist(messages, conversation_history)
                            return {
                                "final_response": _exhaust_response,
                                "messages": messages,
                                "api_calls": turn_state.api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": _exhaust_error,
                            }

                        if truncation.action in {
                            TruncationAction.continue_text,
                            TruncationAction.return_partial_text,
                        }:
                            turn_state.length_continue_retries = (
                                truncation.text_truncation_count
                            )
                            interim_msg = self._build_assistant_message(
                                assistant_message,
                                attempt_state.finish_reason,
                            )
                            messages.append(interim_msg)
                            if truncation.content:
                                turn_state.truncated_response_prefix += truncation.content

                            if truncation.action is TruncationAction.continue_text:
                                self._vprint(
                                    f"{self.log_prefix}↻ Requesting continuation "
                                    f"({turn_state.length_continue_retries}/3)..."
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
                                self._session_persistence.save_log(messages)
                                attempt_state.request_length_continuation()
                                break

                            partial_response = strip_thinking_blocks(
                                turn_state.truncated_response_prefix
                            ).strip()
                            self._cleanup_task_resources(effective_task_id)
                            self._session_persistence.persist(messages, conversation_history)
                            return {
                                "final_response": partial_response or None,
                                "messages": messages,
                                "api_calls": turn_state.api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": "Response remained truncated after 3 continuation attempts",
                            }

                        if truncation.action is TruncationAction.retry_tool_call:
                            turn_state.truncated_tool_call_retries = (
                                truncation.tool_truncation_count
                            )
                            self._vprint(
                                f"{self.log_prefix}⚠️  Truncated tool call detected — retrying API call...",
                                force=True,
                            )
                            # Don't append the broken response to messages; rerun
                            # the same request so incomplete arguments never execute.
                            continue

                        if truncation.action is TruncationAction.fail_tool_call:
                            turn_state.truncated_tool_call_retries = (
                                truncation.tool_truncation_count
                            )
                            self._vprint(
                                f"{self.log_prefix}⚠️  Truncated tool call response detected again — refusing to execute incomplete tool arguments.",
                                force=True,
                            )
                            self._cleanup_task_resources(effective_task_id)
                            self._session_persistence.persist(messages, conversation_history)
                            return {
                                "final_response": None,
                                "messages": messages,
                                "api_calls": turn_state.api_call_count,
                                "completed": False,
                                "partial": True,
                                "error": "Response truncated due to output length limit",
                            }
                    
                    # Track actual token usage from response for context management
                    if (
                        hasattr(attempt_state.response, "usage")
                        and attempt_state.response.usage
                    ):
                        canonical_usage = normalize_usage(
                            attempt_state.response.usage
                        )
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
                        # sessions (gateway and delegated runs) cannot lose
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
                        
                    attempt_state.rate_limit_retry_attempted = False
                    self._touch_activity(
                        f"API call #{turn_state.api_call_count} completed"
                    )
                    break  # Success, exit retry loop

                except InterruptedError:
                    if thinking_spinner:
                        thinking_spinner.stop("")
                        thinking_spinner = None
                    if self.thinking_callback:
                        self.thinking_callback("")
                    api_elapsed = time.time() - attempt_state.started_at
                    self._vprint(f"{self.log_prefix}🔧 Interrupted during API call.", force=True)
                    self._session_persistence.persist(messages, conversation_history)
                    turn_state.interrupted = True
                    turn_state.final_response = (
                        "Operation interrupted: waiting for model response "
                        f"({api_elapsed:.1f}s elapsed)."
                    )
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

                    (
                        recovered_with_pool,
                        attempt_state.rate_limit_retry_attempted,
                    ) = self._recover_with_credential_pool(
                        status_code=status_code,
                        has_retried_429=attempt_state.rate_limit_retry_attempted,
                        classified_reason=classified.reason,
                        error_context=error_context,
                    )
                    if recovered_with_pool:
                        continue
                    if (
                        self.provider == "nous"
                        and status_code == 401
                        and not attempt_state.subscription_auth_retry_attempted
                    ):
                        attempt_state.subscription_auth_retry_attempted = True
                        if self._try_refresh_nous_client_credentials(force=True):
                            print(f"{self.log_prefix}🔐 Nous agent key refreshed after 401. Retrying request...")
                            continue
                    attempt_state.record_failure()
                    elapsed_time = time.time() - attempt_state.started_at
                    
                    error_type = type(api_error).__name__
                    error_msg = str(api_error).lower()
                    _error_summary = summarize_api_error(api_error)
                    logger.warning(
                        "API call failed (attempt %s/%s) error_type=%s %s summary=%s",
                        attempt_state.retry_count,
                        attempt_state.max_retries,
                        error_type,
                        self._client_lifecycle.log_context(),
                        _error_summary,
                    )

                    _provider = getattr(self, "provider", "unknown")
                    _base = getattr(self, "base_url", "unknown")
                    _model = getattr(self, "model", "unknown")
                    _status_code_str = f" [HTTP {status_code}]" if status_code else ""
                    self._vprint(f"{self.log_prefix}⚠️  API call failed (attempt {attempt_state.retry_count}/{attempt_state.max_retries}): {error_type}{_status_code_str}", force=True)
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
                        self._session_persistence.persist(messages, conversation_history)
                        self.clear_interrupt()
                        return {
                            "final_response": f"Operation interrupted: handling API error ({error_type}: {clean_error_message(str(api_error))}).",
                            "messages": messages,
                            "api_calls": turn_state.api_call_count,
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
                        retry_count=attempt_state.retry_count,
                        max_retries=attempt_state.max_retries,
                        fallback_available=fallback_available,
                        credential_pool_may_recover=pool_may_recover,
                        primary_recovery_attempted=(
                            attempt_state.primary_recovery_attempted
                        ),
                    )
                    is_rate_limited = retry_directive.is_rate_limited

                    def _activate_directive_fallback() -> bool:
                        if retry_directive.try_eager_fallback:
                            self._emit_status(
                                "⚠️ Rate limited — switching to fallback provider..."
                            )
                        elif retry_directive.kind is RetryKind.abort_client_error:
                            self._emit_status(
                                f"⚠️ Non-retryable error (HTTP {status_code}) — "
                                "trying fallback..."
                            )
                        else:
                            self._emit_status(
                                f"⚠️ Max retries ({attempt_state.max_retries}) "
                                "exhausted — trying fallback..."
                            )
                        return self._try_activate_fallback()

                    recovery_result = execute_retry_recovery(
                        retry_directive,
                        api_error,
                        retry_count=attempt_state.retry_count,
                        max_retries=attempt_state.max_retries,
                        activate_fallback=_activate_directive_fallback,
                        recover_transport=lambda error, count, maximum: (
                            self._try_recover_primary_transport(
                                error,
                                retry_count=count,
                                max_retries=maximum,
                            )
                        ),
                    )
                    if recovery_result.kind is RetryRecoveryKind.fallback:
                        attempt_state.reset_retry_cycle()
                        turn_state.compression_attempts = 0
                        continue
                    if recovery_result.kind is RetryRecoveryKind.transport:
                        attempt_state.primary_recovery_attempted = True
                        attempt_state.retry_count = 0
                        continue

                    if retry_directive.kind in {
                        RetryKind.compress_payload,
                        RetryKind.recover_context,
                    }:
                        recovery_kind = (
                            ContextRecoveryKind.payload_too_large
                            if retry_directive.kind is RetryKind.compress_payload
                            else ContextRecoveryKind.context_overflow
                        )
                        expected_attempt = turn_state.compression_attempts + 1
                        if recovery_kind is ContextRecoveryKind.payload_too_large:
                            self._emit_status(
                                "⚠️  Request payload too large (413) — compression "
                                f"attempt {expected_attempt}/"
                                f"{attempt_state.max_compression_attempts}..."
                            )
                        else:
                            self._emit_status(
                                f"🗜️ Context too large (~{approx_tokens:,} tokens) — "
                                f"recovery attempt {expected_attempt}/"
                                f"{attempt_state.max_compression_attempts}..."
                            )

                        recovery = execute_context_recovery(
                            recovery_kind,
                            error_message=error_msg,
                            messages=messages,
                            system_prompt=system_message,
                            approx_tokens=approx_tokens,
                            task_id=effective_task_id,
                            previous_attempts=turn_state.compression_attempts,
                            max_attempts=attempt_state.max_compression_attempts,
                            compressor=self.context_compressor,
                            model=self.model,
                            base_url=self.base_url,
                            api_key=getattr(self, "api_key", ""),
                            provider=self.provider,
                            compress=self._compress_for_api_recovery,
                        )
                        turn_state.compression_attempts = recovery.attempt.number

                        if recovery.compression is not None:
                            messages = recovery.compression.messages
                            active_system_prompt = (
                                recovery.compression.system_prompt
                            )
                            conversation_history = None

                        if recovery.action is ContextRecoveryAction.fail:
                            if recovery.attempt.exhausted:
                                self._vprint(
                                    f"{self.log_prefix}❌ Max compression attempts "
                                    f"({attempt_state.max_compression_attempts}) "
                                    "reached.",
                                    force=True,
                                )
                            elif recovery_kind is ContextRecoveryKind.payload_too_large:
                                self._vprint(
                                    f"{self.log_prefix}❌ Payload too large and "
                                    "cannot compress further.",
                                    force=True,
                                )
                            else:
                                self._vprint(
                                    f"{self.log_prefix}❌ Context length exceeded "
                                    "and cannot compress further.",
                                    force=True,
                                )
                            self._vprint(
                                f"{self.log_prefix}   💡 Try /new to start a "
                                "fresh conversation, or /compress to retry "
                                "compression.",
                                force=True,
                            )
                            logging.error("%s%s", self.log_prefix, recovery.error)
                            return self._context_recovery_failure_result(
                                messages=messages,
                                conversation_history=conversation_history,
                                api_call_count=turn_state.api_call_count,
                                error=recovery.error,
                            )

                        if recovery.output_token_limit is not None:
                            self._ephemeral_max_output_tokens = (
                                recovery.output_token_limit
                            )
                            self._vprint(
                                f"{self.log_prefix}⚠️  Output cap too large for "
                                "current prompt — retrying with "
                                f"max_tokens={recovery.output_token_limit:,} "
                                f"(available_tokens="
                                f"{recovery.available_output_tokens:,}; "
                                "context_length unchanged at "
                                f"{recovery.current_context_length:,})",
                                force=True,
                            )
                            attempt_state.request_compressed_restart()
                            break

                        if recovery_kind is ContextRecoveryKind.context_overflow:
                            if recovery.context_length_changed:
                                if recovery.parsed_context_limit:
                                    self._vprint(
                                        f"{self.log_prefix}⚠️  Context limit "
                                        "detected from API: "
                                        f"{recovery.next_context_length:,} tokens "
                                        f"(was {recovery.current_context_length:,})",
                                        force=True,
                                    )
                                self._vprint(
                                    f"{self.log_prefix}⚠️  Context length "
                                    "exceeded — stepping down: "
                                    f"{recovery.current_context_length:,} → "
                                    f"{recovery.next_context_length:,} tokens",
                                    force=True,
                                )
                            else:
                                self._vprint(
                                    f"{self.log_prefix}⚠️  Context length "
                                    "exceeded at minimum tier — attempting "
                                    "compression...",
                                    force=True,
                                )

                        compression = recovery.compression
                        if compression and compression.message_count_reduced:
                            self._emit_status(
                                f"🗜️ Compressed {compression.original_message_count} "
                                f"→ {len(messages)} messages, retrying..."
                            )
                        time.sleep(2)
                        attempt_state.request_compressed_restart()
                        break

                    if retry_directive.kind is RetryKind.abort_client_error:
                        if attempt_state.request_kwargs is not None:
                            self._dump_api_request_debug(
                                attempt_state.request_kwargs,
                                reason="non_retryable_client_error",
                                error=api_error,
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
                            self._session_persistence.persist(messages, conversation_history)
                        return {
                            "final_response": None,
                            "messages": messages,
                            "api_calls": turn_state.api_call_count,
                            "completed": False,
                            "failed": True,
                            "error": str(api_error),
                        }

                    if retry_directive.kind is RetryKind.exhausted:
                        _final_summary = summarize_api_error(api_error)
                        if is_rate_limited:
                            self._emit_status(f"❌ Rate limited after {attempt_state.max_retries} retries — {_final_summary}")
                        else:
                            self._emit_status(f"❌ API failed after {attempt_state.max_retries} retries — {_final_summary}")
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
                            self.log_prefix, attempt_state.max_retries, _final_summary,
                            _provider, _model, len(api_messages), f"{approx_tokens:,}",
                        )
                        if attempt_state.request_kwargs is not None:
                            self._dump_api_request_debug(
                                attempt_state.request_kwargs,
                                reason="max_retries_exhausted",
                                error=api_error,
                            )
                        self._session_persistence.persist(messages, conversation_history)
                        _final_response = f"API call failed after {attempt_state.max_retries} retries: {_final_summary}"
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
                            "api_calls": turn_state.api_call_count,
                            "completed": False,
                            "failed": True,
                            "error": _final_summary,
                        }

                    # For rate limits, respect the Retry-After header if present
                    _retry_after = retry_after_seconds(api_error) if is_rate_limited else None
                    wait_time = _retry_after if _retry_after else jittered_backoff(attempt_state.retry_count, base_delay=2.0, max_delay=60.0)
                    if is_rate_limited:
                        self._emit_status(f"⏱️ Rate limit reached. Waiting {wait_time}s before retry (attempt {attempt_state.retry_count + 1}/{attempt_state.max_retries})...")
                    else:
                        self._emit_status(f"⏳ Retrying in {wait_time}s (attempt {attempt_state.retry_count}/{attempt_state.max_retries})...")
                    logger.warning(
                        "Retrying API call in %ss (attempt %s/%s) %s error=%s",
                        wait_time,
                        attempt_state.retry_count,
                        attempt_state.max_retries,
                        self._client_lifecycle.log_context(),
                        api_error,
                    )
                    if not wait_for_retry(
                        wait_time,
                        interrupted=lambda: self._interrupt_requested,
                    ):
                        self._vprint(
                            f"{self.log_prefix}🔧 Interrupt detected during "
                            "retry wait, aborting.",
                            force=True,
                        )
                        self._session_persistence.persist(
                            messages,
                            conversation_history,
                        )
                        self.clear_interrupt()
                        return {
                            "final_response": (
                                "Operation interrupted: retrying API call "
                                f"after error (retry {attempt_state.retry_count}/"
                                f"{attempt_state.max_retries})."
                            ),
                            "messages": messages,
                            "api_calls": turn_state.api_call_count,
                            "completed": False,
                            "interrupted": True,
                        }
            
            # If the API call was interrupted, skip response processing
            if turn_state.interrupted:
                turn_state.exit_reason = "interrupted_during_api_call"
                break

            if attempt_state.restart_with_compressed_messages:
                turn_state.api_call_count -= 1
                self.iteration_budget.refund()
                # Compression attempts are retained by the turn state across
                # the refunded outer-loop restart.
                continue

            if attempt_state.restart_with_length_continuation:
                continue

            # Guard: if all retries exhausted without a successful response
            # (e.g. repeated context-length errors that exhausted retry_count),
            # the attempt has no valid response inspection. Break out cleanly.
            if (
                attempt_state.response_inspection is None
                or not attempt_state.response_inspection.valid
            ):
                turn_state.exit_reason = "all_retries_exhausted_no_response"
                print(f"{self.log_prefix}❌ All API retries exhausted with no successful response.")
                self._session_persistence.persist(messages, conversation_history)
                break

            try:
                assistant_message = attempt_state.response_inspection.message
                assistant_message.content = normalize_assistant_content(
                    assistant_message.content
                )

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
                        api_call_count=turn_state.api_call_count,
                        api_duration=api_duration,
                        finish_reason=attempt_state.finish_reason,
                        message_count=len(api_messages),
                        response_model=getattr(
                            attempt_state.response,
                            "model",
                            None,
                        ),
                        usage=self._usage_summary_for_api_request_hook(
                            attempt_state.response
                        ),
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
                    
                    tool_inspection = inspect_tool_calls(
                        assistant_message.tool_calls,
                        valid_tool_names=self.valid_tool_names,
                        repair_tool_name=self._repair_tool_call,
                    )
                    tool_action = apply_tool_call_inspection(
                        self,
                        tool_inspection,
                        assistant_message=assistant_message,
                        finish_reason=attempt_state.finish_reason,
                        messages=messages,
                        conversation_history=conversation_history,
                        api_call_count=turn_state.api_call_count,
                        task_id=effective_task_id,
                    )
                    if tool_action.control is ResponseLoopControl.terminal:
                        return tool_action.terminal_result
                    if tool_action.control is ResponseLoopControl.continue_loop:
                        continue

                    tool_turn = execute_successful_tool_turn(
                        self,
                        state=turn_state,
                        assistant_message=assistant_message,
                        finish_reason=attempt_state.finish_reason,
                        messages=messages,
                        system_message=system_message,
                        active_system_prompt=active_system_prompt,
                        task_id=effective_task_id,
                    )
                    messages = tool_turn.messages
                    active_system_prompt = tool_turn.system_prompt
                    if tool_turn.conversation_history_reset:
                        conversation_history = None
                    continue
                
                else:
                    # No tool calls - this is the final response
                    turn_state.final_response = assistant_message.content or ""
                    structured_reasoning = bool(
                        getattr(assistant_message, "reasoning", None)
                        or getattr(assistant_message, "reasoning_content", None)
                        or getattr(assistant_message, "reasoning_details", None)
                    )
                    prior_content = getattr(
                        self,
                        "_last_content_with_tools",
                        None,
                    )
                    disposition = decide_text_response_disposition(
                        turn_state.final_response,
                        structured_reasoning=structured_reasoning,
                        thinking_prefill_retries=self._thinking_prefill_retries,
                        empty_content_retries=self._empty_content_retries,
                        prior_content_available=bool(prior_content),
                        fallback_available=bool(self._fallback_chain),
                    )

                    text_action = apply_text_response_disposition(
                        self,
                        disposition,
                        assistant_message=assistant_message,
                        finish_reason=attempt_state.finish_reason,
                        state=turn_state,
                        messages=messages,
                    )
                    if text_action.control is ResponseLoopControl.continue_loop:
                        continue
                    if text_action.control is ResponseLoopControl.break_loop:
                        break
                    
                    # Reset retry counter/signature on successful content
                    self._empty_content_retries = 0
                    self._thinking_prefill_retries = 0

                    if turn_state.truncated_response_prefix:
                        turn_state.final_response = (
                            turn_state.truncated_response_prefix
                            + turn_state.final_response
                        )
                        turn_state.clear_text_continuation()
                    
                    # Strip <think> blocks from user-facing response (keep raw in messages for trajectory)
                    turn_state.final_response = strip_thinking_blocks(
                        turn_state.final_response
                    ).strip()
                    
                    final_msg = self._build_assistant_message(
                        assistant_message,
                        attempt_state.finish_reason,
                    )

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
                    
                    turn_state.exit_reason = (
                        "text_response("
                        f"finish_reason={attempt_state.finish_reason})"
                    )
                    if not self.quiet_mode:
                        self._safe_print(
                            "🎉 Conversation completed after "
                            f"{turn_state.api_call_count} OpenAI-compatible "
                            "API call(s)"
                        )
                    break
                
            except Exception as e:
                error_msg = (
                    "Error during OpenAI-compatible API call "
                    f"#{turn_state.api_call_count}: {str(e)}"
                )
                try:
                    print(f"❌ {error_msg}")
                except (OSError, ValueError):
                    logger.error(error_msg)
                
                logger.debug(
                    "Outer loop error in API call #%d",
                    turn_state.api_call_count,
                    exc_info=True,
                )
                
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
                if turn_state.api_call_count >= self.max_iterations - 1:
                    turn_state.exit_reason = (
                        f"error_near_max_iterations({error_msg[:80]})"
                    )
                    turn_state.final_response = (
                        "I apologize, but I encountered repeated errors: "
                        f"{error_msg}"
                    )
                    # Append as assistant so the history stays valid for
                    # session resume (avoids consecutive user messages).
                    messages.append(
                        {
                            "role": "assistant",
                            "content": turn_state.final_response,
                        }
                    )
                    break

        if (
            turn_state.final_response is None
            and turn_state.exhausted(
                max_iterations=self.max_iterations,
                iteration_budget=self.iteration_budget,
            )
        ):
            turn_state.exit_reason = (
                "max_iterations_reached("
                f"{turn_state.api_call_count}/{self.max_iterations})"
            )
            if self.iteration_budget.remaining <= 0 and not self.quiet_mode:
                print(f"\n⚠️  Iteration budget exhausted ({self.iteration_budget.used}/{self.iteration_budget.max_total} iterations used)")
            turn_state.final_response = self._handle_max_iterations(
                messages,
                turn_state.api_call_count,
            )

        return finalize_conversation_turn(
            self,
            state=turn_state,
            messages=messages,
            conversation_history=conversation_history,
            task_id=effective_task_id,
            original_user_message=original_user_message,
        )

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
