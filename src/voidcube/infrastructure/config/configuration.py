"""
Configuration management for Voidcube Agent.

Config files are stored in ~/.VoidCube/ for easy access:
- ~/.VoidCube/config.yaml  - All settings (model, toolsets, terminal, etc.)
- ~/.VoidCube/.env         - API keys and secrets

This module provides:
- VoidCube config          - Show current configuration
- VoidCube config edit     - Open config in editor
- VoidCube config set      - Set a specific value
- VoidCube config wizard   - Re-run setup wizard
"""

import copy
import logging
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from .runtime_paths import (
    get_VoidCube_home,
    get_config_path,
    get_env_path,
)
from ...application.companion_workers import DEFAULT_COMPANION_WORKER_ROLES
from ...domain.identity.defaults import DEFAULT_SOUL_MD
from ..runtime.environment import is_container, is_placeholder_secret
from ...extensions.tools.backend_helpers import managed_nous_tools_enabled as _managed_nous_tools_enabled


logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Env var names written to .env that aren't in OPTIONAL_ENV_VARS
# (managed by setup/provider flows directly).
_EXTRA_ENV_KEYS = frozenset({
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "TERMINAL_ENV", "TERMINAL_SSH_KEY", "TERMINAL_SSH_PORT",
    "LLM_MODEL", "LLM_BASE_URL", "OPENAI_MODEL",
})
_AUXILIARY_TASK_KEYS = (
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "skills_hub",
    "approval",
    "mcp",
)
_AUXILIARY_ROUTE_FIELDS = ("provider", "model", "base_url", "api_key")
_RETIRED_AUXILIARY_ENV_VARS = tuple(
    f"{prefix}{task.upper()}_{field.upper()}"
    for task in _AUXILIARY_TASK_KEYS
    for field in _AUXILIARY_ROUTE_FIELDS
    for prefix in ("AUXILIARY_", "CONTEXT_")
)
_RETIRED_MODEL_ENV_VARS = (
    "LLM_MODEL",
    "LLM_BASE_URL",
    "OPENAI_MODEL",
    *_RETIRED_AUXILIARY_ENV_VARS,
)
_RETIRED_TOOL_PROGRESS_ENV_VARS = (
    "VOIDCUBE_TOOL_" + "PROGRESS",
    "VOIDCUBE_TOOL_" + "PROGRESS_MODE",
)
_RETIRED_MESSAGING_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "DISCORD_BOT_TOKEN",
    "DISCORD_ALLOWED_USERS",
    "DISCORD_REPLY_TO_MODE",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_ALLOWED_USERS",
    "WHATSAPP_ENABLED",
    "WHATSAPP_MODE",
    "WHATSAPP_ALLOWED_USERS",
    "MATTERMOST_URL",
    "MATTERMOST_TOKEN",
    "MATTERMOST_ALLOWED_USERS",
    "MATTERMOST_REQUIRE_MENTION",
    "MATTERMOST_FREE_RESPONSE_CHANNELS",
    "MATRIX_HOMESERVER",
    "MATRIX_ACCESS_TOKEN",
    "MATRIX_USER_ID",
    "MATRIX_ALLOWED_USERS",
    "MATRIX_REQUIRE_MENTION",
    "MATRIX_FREE_RESPONSE_ROOMS",
    "MATRIX_AUTO_THREAD",
    "MATRIX_DEVICE_ID",
    "MATRIX_RECOVERY_KEY",
    "BLUEBUBBLES_SERVER_URL",
    "BLUEBUBBLES_PASSWORD",
    "BLUEBUBBLES_ALLOWED_USERS",
    "GATEWAY_ALLOW_ALL_USERS",
    "API_SERVER_ENABLED",
    "API_SERVER_KEY",
    "API_SERVER_PORT",
    "API_SERVER_HOST",
    "API_SERVER_MODEL_NAME",
    "WEBHOOK_ENABLED",
    "WEBHOOK_PORT",
    "WEBHOOK_SECRET",
    "MESSAGING_CWD",
)
_RETIRED_PREFILL_ENV_VAR = "VOIDCUBE_PREFILL_" + "MESSAGES_FILE"
_RETIRED_UNUSED_CONFIG_ENV_VARS = (
    "GEMINI_BASE_URL",
    "DASHSCOPE_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "OPENCODE_GO_API_KEY",
    "HF_TOKEN",
    "XIAOMI_API_KEY",
)
import yaml

# =============================================================================
# Managed mode (NixOS declarative config)
# =============================================================================

_MANAGED_TRUE_VALUES = ("true", "1", "yes")
_MANAGED_SYSTEM_NAMES = {
    "brew": "Homebrew",
    "homebrew": "Homebrew",
    "nix": "NixOS",
    "nixos": "NixOS",
}


def get_managed_system() -> Optional[str]:
    """Return the package manager owning this install, if any."""
    raw = os.getenv("VOIDCUBE_MANAGED", "").strip()
    if raw:
        normalized = raw.lower()
        if normalized in _MANAGED_TRUE_VALUES:
            return "NixOS"
        return _MANAGED_SYSTEM_NAMES.get(normalized, raw)

    managed_marker = get_VoidCube_home() / ".managed"
    if managed_marker.exists():
        return "NixOS"
    return None


def is_managed() -> bool:
    """Check if Voidcube is running in package-manager-managed mode.

    Two signals: the VOIDCUBE_MANAGED env var (set by the systemd service),
    or a .managed marker file in VOIDCUBE_HOME (set by the NixOS activation
    script, so interactive shells also see it).
    """
    return get_managed_system() is not None


def format_managed_message(action: str = "modify this Voidcube installation") -> str:
    """Build a user-facing error for managed installs."""
    managed_system = get_managed_system() or "a package manager"
    raw = os.getenv("VOIDCUBE_MANAGED", "").strip().lower()

    if managed_system == "NixOS":
        env_hint = "true" if raw in _MANAGED_TRUE_VALUES else raw or "true"
        return (
            f"Cannot {action}: this Voidcube installation is managed by NixOS "
            f"(VOIDCUBE_MANAGED={env_hint}).\n"
            "Edit services.VoidCube-agent.settings in your configuration.nix and run:\n"
            "  sudo nixos-rebuild switch"
        )

    if managed_system == "Homebrew":
        env_hint = raw or "homebrew"
        return (
            f"Cannot {action}: this Voidcube installation is managed by Homebrew "
            f"(VOIDCUBE_MANAGED={env_hint}).\n"
            "Use:\n"
            "  brew upgrade VoidCube-agent"
        )

    return (
        f"Cannot {action}: this Voidcube installation is managed by {managed_system}.\n"
        "Use your package manager to upgrade or reinstall Voidcube."
    )

def managed_error(action: str = "modify configuration"):
    """Print user-friendly error for managed mode."""
    print(format_managed_message(action), file=sys.stderr)


# =============================================================================
# Container-aware CLI (NixOS container mode)
# =============================================================================

def get_container_exec_info() -> Optional[dict]:
    """Read container mode metadata from VOIDCUBE_HOME/.container-mode.

    Returns a dict with keys: backend, container_name, exec_user, VoidCube_bin
    or None if container mode is not active, we're already inside the
    container, or VOIDCUBE_DEV=1 is set.

    The .container-mode file is written by the NixOS activation script when
    container.enable = true. It tells the host CLI to exec into the container
    instead of running locally.
    """
    if os.environ.get("VOIDCUBE_DEV") == "1":
        return None

    if is_container():
        return None

    container_mode_file = get_VoidCube_home() / ".container-mode"

    try:
        info = {}
        with open(container_mode_file, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    info[key.strip()] = value.strip()
    except FileNotFoundError:
        return None
    # All other exceptions (PermissionError, malformed data, etc.) propagate

    backend = info.get("backend", "docker")
    container_name = info.get("container_name", "VoidCube-agent")
    exec_user = info.get("exec_user", "VoidCube")
    VoidCube_bin = info.get("VoidCube_bin", "/data/current-package/bin/VoidCube")

    return {
        "backend": backend,
        "container_name": container_name,
        "exec_user": exec_user,
        "VoidCube_bin": VoidCube_bin,
    }


# =============================================================================
# Config paths
# =============================================================================

def _secure_dir(path):
    """Set directory to owner-only access (0700 by default). No-op on Windows.

    Skipped in managed mode — the NixOS module sets group-readable
    permissions (0750) so interactive users in the VoidCube group can
    share state with the gateway service.

    The mode can be overridden via the VOIDCUBE_HOME_MODE environment variable
    (e.g. VOIDCUBE_HOME_MODE=0701) for deployments where a web server (nginx,
    caddy, etc.) needs to traverse VOIDCUBE_HOME to reach a served subdirectory.
    The execute-only bit on a directory permits cd-through without exposing
    directory listings.
    """
    if is_managed():
        return
    try:
        mode_str = os.environ.get("VOIDCUBE_HOME_MODE", "").strip()
        mode = int(mode_str, 8) if mode_str else 0o700
    except ValueError:
        mode = 0o700
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass


def _secure_file(path):
    """Set file to owner-only read/write (0600). No-op on Windows.

    Skipped in managed mode — the NixOS activation script sets
    group-readable permissions (0640) on config files.
    """
    if is_managed():
        return
    try:
        if os.path.exists(str(path)):
            os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def _ensure_default_soul_md(home: Path) -> None:
    """Seed a default SOUL.md into VOIDCUBE_HOME if the user doesn't have one yet."""
    soul_path = home / "SOUL.md"
    if soul_path.exists():
        return
    soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
    _secure_file(soul_path)


def ensure_VoidCube_home():
    """Ensure ~/.VoidCube directory structure exists with secure permissions.

    In managed mode (NixOS), dirs are created by the activation script with
    setgid + group-writable (2770). We skip mkdir and set umask(0o007) so
    any files created (e.g. SOUL.md) are group-writable (0660).
    """
    home = get_VoidCube_home()
    if is_managed():
        old_umask = os.umask(0o007)
        try:
            _ensure_VoidCube_home_managed(home)
        finally:
            os.umask(old_umask)
    else:
        home.mkdir(parents=True, exist_ok=True)
        _secure_dir(home)
        for subdir in ("sessions", "logs", "memories"):
            d = home / subdir
            d.mkdir(parents=True, exist_ok=True)
            _secure_dir(d)
        _ensure_default_soul_md(home)


def _ensure_VoidCube_home_managed(home: Path):
    """Managed-mode variant: verify dirs exist (activation creates them), seed SOUL.md."""
    if not home.is_dir():
        raise RuntimeError(
            f"VOIDCUBE_HOME {home} does not exist. "
            "Run 'sudo nixos-rebuild switch' first."
        )
    for subdir in ("sessions", "logs", "memories"):
        d = home / subdir
        if not d.is_dir():
            raise RuntimeError(
                f"{d} does not exist. "
                "Run 'sudo nixos-rebuild switch' first."
            )
    # Inside umask(0o007) scope — SOUL.md will be created as 0660
    _ensure_default_soul_md(home)


# =============================================================================
# Config loading/saving
# =============================================================================

DEFAULT_CONFIG = {
    "runtime": {
        "active_provider": "",
    },
    "providers": {},
    "fallback_providers": [],
    "credential_pool_strategies": {},
    "companion_workers": {
        "default_role": "general",
        "max_concurrent": 4,
        "roles": copy.deepcopy(DEFAULT_COMPANION_WORKER_ROLES),
    },
    "image_generation": {
        "provider": "agnes-ai",
        "api_key_env": "AGNES_API_KEY",
        "endpoint": "https://api.agnes-ai.cn/v1/images/generations",
        "edit_endpoint": "https://api.agnes-ai.cn/v1/images/edits",
        "model": "agnes-image-2.1-flash",
        "request_timeout_seconds": 120,
    },
    "video_generation": {
        "provider": "agnes-ai",
        "api_key_env": "AGNES_API_KEY",
        "endpoint": "https://api.agnes-ai.cn/v1/videos",
        "result_endpoint": "https://api.agnes-ai.cn/agnesapi",
        "model": "agnes-video-v2.0",
        "request_timeout_seconds": 120,
        "poll_interval_seconds": 3,
        "timeout_seconds": 600,
    },
    "toolsets": ["voidcube"],
    "agent": {
        "max_turns": 90,
        # Inactivity timeout for gateway agent execution (seconds).
        # The agent can run indefinitely as long as it's actively calling
        # tools or receiving API responses.  Only fires when the agent has
        # been completely idle for this duration.  0 = unlimited.
        "gateway_timeout": 1800,
        # Graceful drain timeout for gateway stop/restart (seconds).
        # The gateway stops accepting new work, waits for running agents
        # to finish, then interrupts any remaining runs after the timeout.
        # 0 = no drain, interrupt immediately.
        "restart_drain_timeout": 60,
        "service_tier": "",
        # Tool-use enforcement: injects system prompt guidance that tells the
        # model to actually call tools instead of describing intended actions.
        # Values: "auto" (default — applies to GPT-family models), true/false
        # (force on/off for all models), or a list of model-name substrings
        # to match (e.g. ["gpt", "gemini", "qwen"]).
        "tool_use_enforcement": "auto",
        # Staged inactivity warning: send a warning to the user at this
        # threshold before escalating to a full timeout.  The warning fires
        # once per run and does not interrupt the agent.  0 = disable warning.
        "gateway_timeout_warning": 900,
        # Periodic "still working" notification interval (seconds).
        # Sends a status message every N seconds so the user knows the
        # agent hasn't died during long tasks.  0 = disable notifications.
        "gateway_notify_interval": 600,
    },
    
    "terminal": {
        "backend": "podman",
        "fallback_to_local": False,
        "modal_mode": "auto",
        "cwd": ".",  # Use current directory
        "timeout": 180,
        # Environment variables to pass through to sandboxed execution
        # (terminal and execute_code).  Skill-declared required_environment_variables
        # are passed through automatically; this list is for non-skill use cases.
        "env_passthrough": [],
        "docker_image": "nikolaik/python-nodejs:python3.14-nodejs20",
        "podman_image": "localhost/voidcube-project-podman:py314-v1",
        "docker_forward_env": [],
        # Explicit environment variables to set inside Docker containers.
        # Unlike docker_forward_env (which reads values from the host process),
        # docker_env lets you specify exact key-value pairs — useful when Voidcube
        # runs as a systemd service without access to the user's shell environment.
        # Example: {"SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.sock"}
        "docker_env": {},
        "singularity_image": "docker://nikolaik/python-nodejs:python3.14-nodejs20",
        "modal_image": "nikolaik/python-nodejs:python3.14-nodejs20",
        "daytona_image": "nikolaik/python-nodejs:python3.14-nodejs20",
        # Container resource limits (docker, singularity, modal, daytona — ignored for local/ssh)
        "container_cpu": 1,
        "container_memory": 5120,       # MB (default 5GB)
        "container_disk": 51200,        # MB (default 50GB)
        "container_persistent": True,   # Persist filesystem across sessions
        # Docker volume mounts — share host directories with the container.
        # Each entry is "host_path:container_path" (standard Docker -v syntax).
        # Example: ["/home/user/projects:/workspace/projects", "/data:/data"]
        "docker_volumes": [],
        # Mount the selected project into /workspace for agent terminal/file work.
        # execute_code remains a separate, unmounted, network-disabled sandbox.
        "docker_mount_cwd_to_workspace": True,
        # Persistent shell — keep a long-lived bash shell across execute() calls
        # so cwd/env vars/shell variables survive between commands. Backend-specific
        # TERMINAL_LOCAL_PERSISTENT / TERMINAL_SSH_PERSISTENT values can override it.
        "persistent_shell": True,
    },
    
    "browser": {
        "inactivity_timeout": 120,
        "command_timeout": 30,  # Timeout for browser commands in seconds (screenshot, navigate, etc.)
        "record_sessions": False,  # Auto-record browser sessions as WebM videos
        "allow_private_urls": False,  # Allow navigating to private/internal IPs (localhost, 192.168.x.x, etc.)
        "camofox": {
            # When true, Voidcube sends a stable profile-scoped userId to Camofox
            # so the server can map it to a persistent browser profile directory.
            # Requires Camofox server to be configured with CAMOFOX_PROFILE_DIR.
            # When false (default), each session gets a random userId (ephemeral).
            "managed_persistence": False,
        },
    },

    # Filesystem checkpoints — automatic snapshots before destructive file ops.
    # When enabled, the agent takes a snapshot of the working directory once per
    # conversation turn (on first write_file/patch call).  Use /rollback to restore.
    "checkpoints": {
        "enabled": True,
        "max_snapshots": 50,  # Max checkpoints to keep per directory
    },

    # Maximum characters returned by a single read_file call.  Reads that
    # exceed this are rejected with guidance to use offset+limit.
    # 100K chars ≈ 25–35K tokens across typical tokenisers.
    "file_read_max_chars": 100_000,
    
    "compression": {
        "enabled": True,
        "threshold": 0.50,            # compress when context usage exceeds this ratio
        "target_ratio": 0.20,         # fraction of threshold to preserve as recent tail
        "protect_last_n": 20,         # minimum recent messages to keep uncompressed
    },
    "smart_model_routing": {
        "enabled": False,
        "max_simple_chars": 160,
        "max_simple_words": 28,
        "cheap_model": {},
    },
    
    # Auxiliary model config — provider:model for each side task.
    # Format: provider is the provider name, model is the model slug.
    # "auto" for provider = auto-detect best available provider.
    # Empty model = use provider's default auxiliary model.
    # Auto-routed tasks can try another configured provider after a transport
    # or payment failure. Explicit provider choices remain hard constraints.
    "auxiliary": {
        "vision": {
            "provider": "auto",    # auto | openrouter | nous | custom
            "model": "",           # exact model ID returned by the provider API
            "base_url": "",        # direct OpenAI-compatible endpoint (takes precedence over provider)
            "api_key": "",         # optional task-specific key for the direct endpoint
            "timeout": 120,        # seconds — LLM API call timeout; vision payloads need generous timeout
            "download_timeout": 30,  # seconds — image HTTP download timeout; increase for slow connections
        },
        "web_extract": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 360,        # seconds (6min) — per-attempt LLM summarization timeout; increase for slow local models
        },
        "compression": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 120,        # seconds — compression summarises large contexts; increase for local models
        },
        "session_search": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "skills_hub": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "approval": {
            "provider": "auto",
            "model": "",           # fast/cheap model recommended
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "mcp": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
    },
    
    "display": {
        "compact": False,
        "personality": "kawaii",
        "resume_display": "full",
        "bell_on_complete": False,
        "show_reasoning": False,
        "streaming": False,
        "inline_diffs": True,     # Show inline diff previews for write actions (write_file, patch, skill_manage)
        "show_cost": False,       # Show $ cost in the status bar (off by default)
        "interim_assistant_messages": True,  # Gateway: show natural mid-turn assistant status messages
        "tool_progress": "all",  # off | new | all | verbose
        "tool_preview_length": 0,  # Max chars for tool call previews (0 = no limit, show full paths/commands)
        "platforms": {},  # Per-platform display overrides
    },

    "clarify": {
        "timeout": 120,
    },

    # Privacy settings
    "privacy": {
        "redact_pii": False,  # When True, hash user IDs and strip phone numbers from LLM context
    },
    
    # Text-to-speech configuration
    "tts": {
        "provider": "edge",  # "edge" (free) | "elevenlabs" (premium) | "openai" | "minimax" | "mistral" | "neutts" (local)
        "edge": {
            "voice": "en-US-AriaNeural",
            # Popular: AriaNeural, JennyNeural, AndrewNeural, BrianNeural, SoniaNeural
        },
        "elevenlabs": {
            "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam
            "model_id": "eleven_multilingual_v2",
        },
        "openai": {
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            # Voices: alloy, echo, fable, onyx, nova, shimmer
        },
        "mistral": {
            "model": "voxtral-mini-tts-2603",
            "voice_id": "c69964a6-ab8b-4f8a-9465-ec0925096ec8",  # Paul - Neutral
        },
        "neutts": {
            "ref_audio": "",  # Path to reference voice audio (empty = bundled default)
            "ref_text": "",   # Path to reference voice transcript (empty = bundled default)
            "model": "neuphonic/neutts-air-q4-gguf",  # HuggingFace model repo
            "device": "cpu",  # cpu, cuda, or mps
        },
    },
    
    "stt": {
        "enabled": True,
        "provider": "local",  # "local" (free, faster-whisper) | "groq" | "openai" (Whisper API) | "mistral" (Voxtral Transcribe)
        "local": {
            "model": "base",  # tiny, base, small, medium, large-v3
            "language": "",  # auto-detect by default; set to "en", "es", "fr", etc. to force
        },
        "openai": {
            "model": "whisper-1",  # whisper-1, gpt-4o-mini-transcribe, gpt-4o-transcribe
        },
        "mistral": {
            "model": "voxtral-mini-latest",  # voxtral-mini-latest, voxtral-mini-2602
        },
    },

    "voice": {
        "record_key": "ctrl+b",
        "max_recording_seconds": 120,
        "auto_tts": False,
        "silence_threshold": 200,     # RMS below this = silence (0-32767)
        "silence_duration": 3.0,      # Seconds of silence before auto-stop
    },
    
    "human_delay": {
        "mode": "off",
        "min_ms": 800,
        "max_ms": 2500,
    },
    
    # Context engine -- controls how the context window is managed when
    # approaching the model's token limit.
    # "compressor" = built-in lossy summarization (default).
    # Set to a plugin name to activate an alternative engine (e.g. "lcm"
    # for Lossless Context Management).  The engine must be installed as
    # a plugin in plugins/context_engine/<name>/ or ~/.VoidCube/plugins/.
    "context": {
        "engine": "compressor",
    },

    # Canonical Memory Service integration.
    "memory": {
        # Mem provider configuration
        "mem": {
            # Empty means use systems.config Agent gateway_address.
            "gateway_address": "",
            "request_timeout_seconds": 2.0,
            # Auto-sync conversations to Mem
            "auto_sync": True,
            # Automatic recall is bounded before it is injected into a turn.
            "prefetch_limit": 5,
            "prefetch_max_context_chars": 3500,
            "outbox_max_attempts": 12,
            "outbox_health_report_interval_seconds": 10.0,
            "outbox_shutdown_drain_timeout_seconds": 5.0,
            # Optional Memory-only redaction. Logs and tool output use a separate policy.
            "redact_before_store": False,
        },
        # Optional true semantic retrieval. This is deliberately independent
        # from memory.llm: a chat model is never treated as an embedding model.
        "semantic_recall": {
            # Enabled by default via the zero-dependency local CharNgramEmbedder
            # (empty provider). Configure an external embedding provider/model
            # (or Ollama) to upgrade to real semantic embeddings.
            "enabled": True,
            "provider": "",
            "model": "",
            "api_key_env": "",
            "base_url": "",
            "dimensions": None,
            "timeout_seconds": 5.0,
            "backfill_batch_size": 64,
        },
        # Mem-side LLM configuration. CLI is the user-facing configuration
        # entry; Mem interprets this block when it needs a model.
        "llm": {
            "provider": "openai",
            "model": "",
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "",
            "provider_profile": "openai",
            "provider_profile_file": "",
            "chat_completions_path": "",
            "system_prompt_style": "",
            "response_format_style": "",
            "response_content_style": "",
            # Optional role-specific overrides. Missing fields inherit from
            # memory.llm. CLI can expose these gradually.
            "roles": {
                "extraction": {},
                "summarization": {},
                "governance_summary": {},
                "governance_reasoner": {},
            },
        },
    },

    # Subagent delegation — override the provider:model used by delegate_task
    # so child agents can run on a different (cheaper/faster) provider and model.
    # Uses the same runtime provider resolution as CLI/gateway startup, so all
    # configured providers (OpenRouter, Nous, Z.ai, Kimi, etc.) are supported.
    "delegation": {
        "model": "",       # exact provider model ID (empty = inherit parent model)
        "provider": "",    # e.g. "openrouter" (empty = inherit parent provider + credentials)
        "base_url": "",    # direct OpenAI-compatible endpoint for subagents
        "api_key": "",     # API key for delegation.base_url (falls back to OPENAI_API_KEY)
        "max_iterations": 50,  # per-subagent iteration cap (each subagent gets its own budget,
                               # independent of the parent's max_iterations)
        "reasoning_effort": "",  # reasoning effort for subagents: "xhigh", "high", "medium",
                                 # "low", "minimal", "none" (empty = inherit parent's level)
    },

    # Ephemeral prefill messages file — JSON list of {role, content} dicts
    # injected at the start of every API call for few-shot priming.
    # Never saved to sessions, logs, or trajectories.
    "prefill_messages_file": "",
    
    # Skills — external skill directories for sharing skills across tools/agents.
    # Each path is expanded (~, ${VAR}) and resolved.  Read-only — skill creation
    # always goes to ~/.VoidCube/skills/.
    "skills": {
        "external_dirs": [],   # e.g. ["~/.agents/skills", "/shared/team-skills"]
    },

    # IANA timezone (e.g. "Asia/Kolkata", "America/New_York").
    # Empty string means use server-local time.
    "timezone": "",

    # Approval mode for dangerous commands:
    #   manual — always prompt the user (default)
    #   smart  — use auxiliary LLM to auto-approve low-risk commands, prompt for high-risk
    #   off    — skip all approval prompts (equivalent to --yolo)
    "approvals": {
        "mode": "manual",
        "timeout": 60,
    },

    # Permanently allowed dangerous command patterns (added via "always" approval)
    "command_allowlist": [],
    # User-defined quick commands that bypass the agent loop (type: exec only)
    "quick_commands": {},
    # Custom personalities — add your own entries here
    # Supports string format: {"name": "system prompt"}
    # Or dict format: {"name": {"description": "...", "system_prompt": "...", "tone": "...", "style": "..."}}
    "personalities": {},

    # Pre-exec security scanning via tirith
    "security": {
        "redact_secrets": True,
        "tirith_enabled": True,
        "tirith_path": "tirith",
        "tirith_timeout": 5,
        "tirith_fail_open": True,
        "website_blocklist": {
            "enabled": False,
            "domains": [],
            "shared_files": [],
        },
    },

    # Logging — controls file logging to ~/.VoidCube/logs/.
    # agent.log captures INFO+ (all agent activity); errors.log captures WARNING+.
    "logging": {
        "level": "INFO",       # Minimum level for agent.log: DEBUG, INFO, WARNING
        "max_size_mb": 5,      # Max size per log file before rotation
        "backup_count": 3,     # Number of rotated backup files to keep
    },

    # Network settings — workarounds for connectivity issues.
    "network": {
        # Force IPv4 connections.  On servers with broken or unreachable IPv6,
        # Python tries AAAA records first and hangs for the full TCP timeout
        # before falling back to IPv4.  Set to true to skip IPv6 entirely.
        "force_ipv4": False,
    },

    # Config schema version - bump this when adding new required fields
    "_config_version": 21,
}

# =============================================================================
# Config Migration System
# =============================================================================

# Track which env vars were introduced in each config version.
# Migration only mentions vars new since the user's previous version.
ENV_VARS_BY_VERSION: Dict[int, List[str]] = {
    3: ["FIRECRAWL_API_KEY", "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
    4: ["VOICE_TOOLS_OPENAI_KEY", "ELEVENLABS_API_KEY"],
    10: ["TAVILY_API_KEY"],
    11: ["TERMINAL_MODAL_MODE"],
    21: ["AGNES_API_KEY"],
}

_LEGACY_CACHE_DIRS = {
    "document_cache": "documents",
    "image_cache": "images",
    "audio_cache": "audio",
    "browser_screenshots": "screenshots",
}

# Required environment variables with metadata for migration prompts.
# LLM provider is required but handled in the setup wizard's provider
# selection step (Nous Portal / OpenRouter / Custom endpoint), so this
# dict is intentionally empty — no single env var is universally required.
REQUIRED_ENV_VARS: Dict[str, dict] = {}

# Optional environment variables that enhance functionality
OPTIONAL_ENV_VARS = {
    # ── Provider (handled in provider selection, not shown in checklists) ──
    "NOUS_BASE_URL": {
        "description": "Nous Portal base URL override",
        "prompt": "Nous Portal base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OPENROUTER_API_KEY": {
        "description": "OpenRouter API key (for vision, web scraping helpers, and MoA)",
        "prompt": "OpenRouter API key",
        "url": "https://openrouter.ai/keys",
        "password": True,
        "tools": ["vision_analyze", "mixture_of_agents"],
        "category": "provider",
        "advanced": True,
    },
    "GOOGLE_API_KEY": {
        "description": "Google AI Studio API key (also recognized as GEMINI_API_KEY)",
        "prompt": "Google AI Studio API key",
        "url": "https://aistudio.google.com/app/apikey",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "GEMINI_API_KEY": {
        "description": "Google AI Studio API key (alias for GOOGLE_API_KEY)",
        "prompt": "Gemini API key",
        "url": "https://aistudio.google.com/app/apikey",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "GLM_API_KEY": {
        "description": "Z.AI / GLM API key (also recognized as ZAI_API_KEY / Z_AI_API_KEY)",
        "prompt": "Z.AI / GLM API key",
        "url": "https://z.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "ZAI_API_KEY": {
        "description": "Z.AI API key (alias for GLM_API_KEY)",
        "prompt": "Z.AI API key",
        "url": "https://z.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "Z_AI_API_KEY": {
        "description": "Z.AI API key (alias for GLM_API_KEY)",
        "prompt": "Z.AI API key",
        "url": "https://z.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "GLM_BASE_URL": {
        "description": "Z.AI / GLM base URL override",
        "prompt": "Z.AI / GLM base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "KIMI_API_KEY": {
        "description": "Kimi / Moonshot API key",
        "prompt": "Kimi API key",
        "url": "https://platform.moonshot.cn/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "KIMI_BASE_URL": {
        "description": "Kimi / Moonshot base URL override",
        "prompt": "Kimi base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_API_KEY": {
        "description": "MiniMax API key (international)",
        "prompt": "MiniMax API key",
        "url": "https://www.minimax.io/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_BASE_URL": {
        "description": "MiniMax base URL override",
        "prompt": "MiniMax base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_CN_API_KEY": {
        "description": "MiniMax API key (China endpoint)",
        "prompt": "MiniMax (China) API key",
        "url": "https://www.minimaxi.com/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_CN_BASE_URL": {
        "description": "MiniMax (China) base URL override",
        "prompt": "MiniMax (China) base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "DEEPSEEK_API_KEY": {
        "description": "DeepSeek API key for direct DeepSeek access",
        "prompt": "DeepSeek API Key",
        "url": "https://platform.deepseek.com/api_keys",
        "password": True,
        "category": "provider",
    },
    "DEEPSEEK_BASE_URL": {
        "description": "Custom DeepSeek API base URL (advanced)",
        "prompt": "DeepSeek Base URL",
        "url": "",
        "password": False,
        "category": "provider",
    },
    "DASHSCOPE_BASE_URL": {
        "description": "Custom DashScope base URL (default: coding-intl OpenAI-compat endpoint)",
        "prompt": "DashScope Base URL",
        "url": "",
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "VOIDCUBE_QWEN_BASE_URL": {
        "description": "Qwen Portal base URL override (default: https://portal.qwen.ai/v1)",
        "prompt": "Qwen Portal base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OPENCODE_ZEN_BASE_URL": {
        "description": "OpenCode Zen base URL override",
        "prompt": "OpenCode Zen base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OPENCODE_GO_BASE_URL": {
        "description": "OpenCode Go base URL override",
        "prompt": "OpenCode Go base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "HF_BASE_URL": {
        "description": "Hugging Face Inference Providers base URL override",
        "prompt": "HF base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "XIAOMI_BASE_URL": {
        "description": "Xiaomi MiMo base URL override (default: https://api.xiaomimimo.com/v1)",
        "prompt": "Xiaomi base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },

    # ── Tool API keys ──
    "EXA_API_KEY": {
        "description": "Exa API key for AI-native web search and contents",
        "prompt": "Exa API key",
        "url": "https://exa.ai/",
        "tools": ["web_search", "web_extract"],
        "password": True,
        "category": "tool",
    },
    "PARALLEL_API_KEY": {
        "description": "Parallel API key for AI-native web search and extract",
        "prompt": "Parallel API key",
        "url": "https://parallel.ai/",
        "tools": ["web_search", "web_extract"],
        "password": True,
        "category": "tool",
    },
    "FIRECRAWL_API_KEY": {
        "description": "Firecrawl API key for web search and scraping",
        "prompt": "Firecrawl API key",
        "url": "https://firecrawl.dev/",
        "tools": ["web_search", "web_extract"],
        "password": True,
        "category": "tool",
    },
    "FIRECRAWL_API_URL": {
        "description": "Firecrawl API URL for self-hosted instances (optional)",
        "prompt": "Firecrawl API URL (leave empty for cloud)",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "FIRECRAWL_GATEWAY_URL": {
        "description": "Exact Firecrawl tool-gateway origin override for Nous Subscribers only (optional)",
        "prompt": "Firecrawl gateway URL (leave empty to derive from domain)",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "TOOL_GATEWAY_DOMAIN": {
        "description": "Shared tool-gateway domain suffix for Nous Subscribers only, used to derive vendor hosts, e.g. nousresearch.com -> firecrawl-gateway.nousresearch.com",
        "prompt": "Tool-gateway domain suffix",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "TOOL_GATEWAY_SCHEME": {
        "description": "Shared tool-gateway URL scheme for Nous Subscribers only, used to derive vendor hosts (`https` by default, set `http` for local gateway testing)",
        "prompt": "Tool-gateway URL scheme",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "TOOL_GATEWAY_USER_TOKEN": {
        "description": "Explicit Nous Subscriber access token for tool-gateway requests (optional; otherwise read from the Voidcube auth store)",
        "prompt": "Tool-gateway user token",
        "url": None,
        "password": True,
        "category": "tool",
        "advanced": True,
    },
    "TAVILY_API_KEY": {
        "description": "Tavily API key for AI-native web search, extract, and crawl",
        "prompt": "Tavily API key",
        "url": "https://app.tavily.com/home",
        "tools": ["web_search", "web_extract", "web_crawl"],
        "password": True,
        "category": "tool",
    },
    "BROWSERBASE_API_KEY": {
        "description": "Browserbase API key for cloud browser (optional — local browser works without this)",
        "prompt": "Browserbase API key",
        "url": "https://browserbase.com/",
        "tools": ["browser_navigate", "browser_click"],
        "password": True,
        "category": "tool",
    },
    "BROWSERBASE_PROJECT_ID": {
        "description": "Browserbase project ID (optional — only needed for cloud browser)",
        "prompt": "Browserbase project ID",
        "url": "https://browserbase.com/",
        "tools": ["browser_navigate", "browser_click"],
        "password": False,
        "category": "tool",
    },
    "BROWSER_USE_API_KEY": {
        "description": "Browser Use API key for cloud browser (optional — local browser works without this)",
        "prompt": "Browser Use API key",
        "url": "https://browser-use.com/",
        "tools": ["browser_navigate", "browser_click"],
        "password": True,
        "category": "tool",
    },
    "FIRECRAWL_BROWSER_TTL": {
        "description": "Firecrawl browser session TTL in seconds (optional, default 300)",
        "prompt": "Browser session TTL (seconds)",
        "tools": ["browser_navigate", "browser_click"],
        "password": False,
        "category": "tool",
    },
    "CAMOFOX_URL": {
        "description": "Camofox browser server URL for local anti-detection browsing (e.g. http://localhost:9377)",
        "prompt": "Camofox server URL",
        "url": "https://github.com/jo-inc/camofox-browser",
        "tools": ["browser_navigate", "browser_click"],
        "password": False,
        "category": "tool",
    },
    "AGNES_API_KEY": {
        "description": "Shared Agnes-AI key for image and video generation",
        "prompt": "Agnes-AI API key",
        "url": "https://api.agnes-ai.cn/",
        "tools": ["image_generate", "image_edit", "video_generate"],
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "TINKER_API_KEY": {
        "description": "Tinker API key for RL training",
        "prompt": "Tinker API key",
        "url": "https://tinker-console.thinkingmachines.ai/keys",
        "tools": ["rl_start_training", "rl_check_status", "rl_stop_training"],
        "password": True,
        "category": "tool",
    },
    "WANDB_API_KEY": {
        "description": "Weights & Biases API key for experiment tracking",
        "prompt": "WandB API key",
        "url": "https://wandb.ai/authorize",
        "tools": ["rl_get_results", "rl_check_status"],
        "password": True,
        "category": "tool",
    },
    "VOICE_TOOLS_OPENAI_KEY": {
        "description": "OpenAI API key for voice transcription (Whisper) and OpenAI TTS",
        "prompt": "OpenAI API Key (for Whisper STT + TTS)",
        "url": "https://platform.openai.com/api-keys",
        "tools": ["voice_transcription", "openai_tts"],
        "password": True,
        "category": "tool",
    },
    "ELEVENLABS_API_KEY": {
        "description": "ElevenLabs API key for premium text-to-speech voices",
        "prompt": "ElevenLabs API key",
        "url": "https://elevenlabs.io/",
        "password": True,
        "category": "tool",
    },
    "MISTRAL_API_KEY": {
        "description": "Mistral API key for Voxtral TTS and transcription (STT)",
        "prompt": "Mistral API key",
        "url": "https://console.mistral.ai/",
        "password": True,
        "category": "tool",
    },
    "GITHUB_TOKEN": {
        "description": "GitHub token for Skills Hub (higher API rate limits, skill publish)",
        "prompt": "GitHub Token",
        "url": "https://github.com/settings/tokens",
        "password": True,
        "category": "tool",
    },

    # ── Agent settings ──
    "SUDO_PASSWORD": {
        "description": "Sudo password for terminal commands requiring root access; set to an explicit empty string to try empty without prompting",
        "prompt": "Sudo password",
        "url": None,
        "password": True,
        "category": "setting",
    },
    "VOIDCUBE_MAX_ITERATIONS": {
        "description": "Maximum tool-calling iterations per conversation (default: 90)",
        "prompt": "Max iterations",
        "url": None,
        "password": False,
        "category": "setting",
    },
    "VOIDCUBE_EPHEMERAL_SYSTEM_PROMPT": {
        "description": "Ephemeral system prompt injected at API-call time (never persisted to sessions)",
        "prompt": "Ephemeral system prompt",
        "url": None,
        "password": False,
        "category": "setting",
    },
}

if not _managed_nous_tools_enabled():
    for _hidden_var in (
        "FIRECRAWL_GATEWAY_URL",
        "TOOL_GATEWAY_DOMAIN",
        "TOOL_GATEWAY_SCHEME",
        "TOOL_GATEWAY_USER_TOKEN",
    ):
        OPTIONAL_ENV_VARS.pop(_hidden_var, None)


def get_missing_env_vars(required_only: bool = False) -> List[Dict[str, Any]]:
    """
    Check which environment variables are missing.
    
    Returns list of dicts with var info for missing variables.
    """
    missing = []
    
    # Check required vars
    for var_name, info in REQUIRED_ENV_VARS.items():
        if not get_env_value(var_name):
            missing.append({"name": var_name, **info, "is_required": True})
    
    # Check optional vars (if not required_only)
    if not required_only:
        for var_name, info in OPTIONAL_ENV_VARS.items():
            if not get_env_value(var_name):
                missing.append({"name": var_name, **info, "is_required": False})
    
    return missing


def _set_nested(config: dict, dotted_key: str, value):
    """Set a value at an arbitrarily nested dotted key path.

    Creates intermediate dicts as needed, e.g. ``_set_nested(c, "a.b.c", 1)``
    ensures ``c["a"]["b"]["c"] == 1``.
    """
    parts = dotted_key.split(".")
    current = config
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def get_missing_config_fields() -> List[Dict[str, Any]]:
    """
    Check which config fields are missing or outdated (recursive).
    
    Walks the DEFAULT_CONFIG tree at arbitrary depth and reports any keys
    present in defaults but absent from the user's loaded config.
    """
    config = load_config()
    missing = []

    def _check(defaults: dict, current: dict, prefix: str = ""):
        for key, default_value in defaults.items():
            if key.startswith('_'):
                continue
            full_key = key if not prefix else f"{prefix}.{key}"
            if key not in current:
                missing.append({
                    "key": full_key,
                    "default": default_value,
                    "description": f"New config option: {full_key}",
                })
            elif isinstance(default_value, dict) and isinstance(current.get(key), dict):
                _check(default_value, current[key], full_key)

    _check(DEFAULT_CONFIG, config)
    return missing


def get_missing_skill_config_vars() -> List[Dict[str, Any]]:
    """Return skill-declared config vars that are missing or empty in config.yaml.

    Scans all enabled skills for ``metadata.VoidCube.config`` entries, then checks
    which ones are absent or empty under ``skills.config.<key>`` in the user's
    config.yaml.  Returns a list of dicts suitable for prompting.
    """
    try:
        from ...extensions.skills.catalog import discover_all_skill_config_vars, SKILL_CONFIG_PREFIX
    except Exception:
        return []

    all_vars = discover_all_skill_config_vars()
    if not all_vars:
        return []

    config = load_config()
    missing: List[Dict[str, Any]] = []
    for var in all_vars:
        # Skill config is stored under skills.config.<logical_key>
        storage_key = f"{SKILL_CONFIG_PREFIX}.{var['key']}"
        parts = storage_key.split(".")
        current = config
        value = None
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
                value = current
            else:
                value = None
                break
        # Missing = key doesn't exist or is empty string
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(var)
    return missing


def check_config_version() -> Tuple[int, int]:
    """
    Check config version.
    
    Returns (current_version, latest_version).
    """
    config = load_config()
    current = config.get("_config_version", 0)
    latest = DEFAULT_CONFIG.get("_config_version", 1)
    return current, latest


# =============================================================================
# Config structure validation
# =============================================================================

# Fields that are valid at root level of config.yaml
_KNOWN_ROOT_KEYS = {
    "_config_version", "model", "runtime", "providers",
    "fallback_providers", "credential_pool_strategies", "companion_workers", "image_generation",
    "video_generation", "toolsets",
    "agent", "terminal", "display", "clarify", "compression", "delegation",
    "auxiliary", "context", "memory", "gateway", "supervisor",
}

# Fields that look like they should be inside a provider entry, not at root
_PROVIDER_ENTRY_LIKE_FIELDS = {"base_url", "api_key", "rate_limit_delay"}


@dataclass
class ConfigWarning:
    """A detected config structure problem."""

    severity: str  # "error", "warning"
    message: str
    hint: str


def validate_config_structure(config: Optional[Dict[str, Any]] = None) -> List["ConfigWarning"]:
    """Validate config.yaml structure and return a list of detected issues.

    Catches common YAML formatting mistakes that produce confusing runtime
    errors (like "Unknown provider") instead of clear diagnostics.

    Can be called with a pre-loaded config dict, or will load from disk.
    """
    if config is None:
        try:
            config = load_config()
        except Exception:
            return [ConfigWarning("error", "Could not load config.yaml", "Run '/api' to create a valid config")]

    issues: List[ConfigWarning] = []

    # ── Root-level keys that look misplaced ──────────────────────────────
    for key in config:
        if key.startswith("_"):
            continue
        if key not in _KNOWN_ROOT_KEYS and key in _PROVIDER_ENTRY_LIKE_FIELDS:
            issues.append(ConfigWarning(
                "warning",
                f"Root-level key '{key}' looks misplaced — should it be under 'model:' or inside 'providers.<name>'?",
                f"Move '{key}' under the appropriate section",
            ))

    return issues


def print_config_warnings(config: Optional[Dict[str, Any]] = None) -> None:
    """Print config structure warnings to stderr at startup.

    Called early in CLI and gateway init so users see problems before
    they hit cryptic "Unknown provider" errors.  Prints nothing if
    config is healthy.
    """
    try:
        issues = validate_config_structure(config)
    except Exception:
        return
    if not issues:
        return

    import sys
    lines = ["\033[33m⚠ Config issues detected in config.yaml:\033[0m"]
    for ci in issues:
        marker = "\033[31m✗\033[0m" if ci.severity == "error" else "\033[33m⚠\033[0m"
        lines.append(f"  {marker} {ci.message}")
    lines.append("  \033[2mRun 'VoidCube doctor' for fix suggestions.\033[0m")
    sys.stderr.write("\n".join(lines) + "\n\n")


def _next_legacy_path(path: Path) -> Path:
    """Return the first deterministic, unused ``.legacy-N`` sibling."""
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.legacy-{index}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def _merge_legacy_cache_tree(source: Path, destination: Path) -> None:
    """Move one legacy cache tree without overwriting canonical content."""
    destination.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.iterdir(), key=lambda path: path.name):
        target = destination / item.name
        target_exists = target.exists() or target.is_symlink()
        if not target_exists:
            shutil.move(str(item), str(target))
            continue
        if (
            item.is_dir()
            and not item.is_symlink()
            and target.is_dir()
            and not target.is_symlink()
        ):
            _merge_legacy_cache_tree(item, target)
            continue
        shutil.move(str(item), str(_next_legacy_path(target)))
    source.rmdir()


def _migrate_legacy_cache_dirs(home: Path) -> list[str]:
    """Move pre-v20 cache directories into the canonical cache tree."""
    migrated: list[str] = []
    cache_root = home / "cache"
    for legacy_name, canonical_name in _LEGACY_CACHE_DIRS.items():
        source = home / legacy_name
        if not source.is_dir() or source.is_symlink():
            continue
        destination = cache_root / canonical_name
        if not destination.exists() and not destination.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        elif destination.is_dir() and not destination.is_symlink():
            _merge_legacy_cache_tree(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(_next_legacy_path(destination)))
        migrated.append(f"{legacy_name} -> cache/{canonical_name}")
    return migrated


def migrate_config(interactive: bool = True, quiet: bool = False) -> Dict[str, Any]:
    """
    Migrate config to latest version, prompting for new required fields.
    
    Args:
        interactive: If True, prompt user for missing values
        quiet: If True, suppress output
        
    Returns:
        Dict with migration results: {"env_added": [...], "config_added": [...], "warnings": [...]}
    """
    results: Dict[str, list] = {"env_added": [], "config_added": [], "warnings": []}

    # ── Always: sanitize .env (split concatenated keys) ──
    try:
        fixes = sanitize_env_file()
        if fixes and not quiet:
            print(f"  ✓ Repaired .env file ({fixes} corrupted entries fixed)")
    except Exception:
        pass  # best-effort; don't block migration on sanitize failure

    # Check config version
    current_ver, latest_ver = check_config_version()
    
    # Retired tool-progress env settings are migrated regardless of the
    # recorded config version, then removed so config.yaml is the only source.
    old_progress_values = {
        name: get_env_value(name) for name in _RETIRED_TOOL_PROGRESS_ENV_VARS
    }
    if any(value is not None for value in old_progress_values.values()):
        config = read_raw_config()
        raw_display = config.get("display")
        display = dict(raw_display) if isinstance(raw_display, dict) else {}
        if "tool_progress" not in display:
            old_enabled = old_progress_values[_RETIRED_TOOL_PROGRESS_ENV_VARS[0]]
            old_mode = old_progress_values[_RETIRED_TOOL_PROGRESS_ENV_VARS[1]]
            enabled = str(old_enabled or "").strip().casefold()
            mode = str(old_mode or "").strip().casefold()
            if enabled in ("false", "0", "no", "off"):
                migrated_mode = "off"
            elif mode in ("off", "new", "all", "verbose"):
                migrated_mode = mode
            else:
                migrated_mode = "all"
            display["tool_progress"] = migrated_mode
            config["display"] = display
            save_config(config)
            results["config_added"].append(
                f"display.tool_progress={migrated_mode} (from retired environment setting)"
            )
            if not quiet:
                print(f"  ✓ Migrated tool progress to config.yaml: {migrated_mode}")

    for retired_var in _RETIRED_TOOL_PROGRESS_ENV_VARS:
        try:
            if remove_env_value(retired_var) and not quiet:
                print(f"  ✓ Removed {retired_var} from .env (retired)")
        except Exception:
            pass

    # Remove settings previously managed by retired platform commands or
    # registered without any runtime consumer.
    configured_env_names = set(load_env()) | set(os.environ)
    retired_config_vars = configured_env_names.intersection(
        (*_RETIRED_MESSAGING_ENV_VARS, *_RETIRED_UNUSED_CONFIG_ENV_VARS)
    )
    for retired_var in sorted(retired_config_vars):
        try:
            if remove_env_value(retired_var) and not quiet:
                print(f"  ✓ Removed {retired_var} from .env (retired setting)")
        except Exception:
            pass

    old_prefill_path = get_env_value(_RETIRED_PREFILL_ENV_VAR)
    if old_prefill_path is not None:
        config = read_raw_config()
        raw_agent = config.get("agent")
        agent_config = dict(raw_agent) if isinstance(raw_agent, dict) else {}
        if "prefill_messages_file" not in agent_config and old_prefill_path.strip():
            agent_config["prefill_messages_file"] = old_prefill_path.strip()
            config["agent"] = agent_config
            save_config(config)
            results["config_added"].append(
                "agent.prefill_messages_file (from retired environment setting)"
            )
            if not quiet:
                print("  ✓ Migrated prefill messages file path to config.yaml")
        try:
            if remove_env_value(_RETIRED_PREFILL_ENV_VAR) and not quiet:
                print("  ✓ Removed retired prefill messages environment setting")
        except Exception:
            pass
    
    # ── Version 4 → 5: add timezone field ──
    if current_ver < 5:
        config = load_config()
        if "timezone" not in config:
            old_tz = os.getenv("VOIDCUBE_TIMEZONE", "")
            if old_tz and old_tz.strip():
                config["timezone"] = old_tz.strip()
                results["config_added"].append(f"timezone={old_tz.strip()} (from VOIDCUBE_TIMEZONE)")
            else:
                config["timezone"] = ""
                results["config_added"].append("timezone= (empty, uses server-local)")
            save_config(config)
            if not quiet:
                tz_display = config["timezone"] or "(server-local)"
                print(f"  ✓ Added timezone to config.yaml: {tz_display}")

    # ── Version 18 → 19: make auxiliary.<task> the only side-model route ──
    if current_ver < 19:
        config = read_raw_config()
        auxiliary = config.get("auxiliary", {})
        if not isinstance(auxiliary, dict):
            auxiliary = {}
        compression = config.get("compression", {})
        if not isinstance(compression, dict):
            compression = {}

        old_compression_fields = {
            "provider": compression.pop("summary_provider", None),
            "model": compression.pop("summary_model", None),
            "base_url": compression.pop("summary_base_url", None),
        }
        changed = any(value is not None for value in old_compression_fields.values())

        for task in _AUXILIARY_TASK_KEYS:
            task_config = auxiliary.get(task, {})
            if not isinstance(task_config, dict):
                task_config = {}
            for field in _AUXILIARY_ROUTE_FIELDS:
                current_value = str(task_config.get(field) or "").strip()
                has_current_value = bool(current_value) and not (
                    field == "provider" and current_value.casefold() == "auto"
                )
                if has_current_value:
                    continue

                migrated_value = ""
                if task == "compression":
                    migrated_value = str(old_compression_fields.get(field) or "").strip()
                    if field == "provider" and migrated_value.casefold() == "auto":
                        migrated_value = ""
                if not migrated_value:
                    for prefix in ("AUXILIARY_", "CONTEXT_"):
                        env_name = f"{prefix}{task.upper()}_{field.upper()}"
                        migrated_value = str(get_env_value(env_name) or "").strip()
                        if migrated_value:
                            break
                if migrated_value:
                    task_config[field] = migrated_value
                    changed = True
                    results["config_added"].append(
                        f"auxiliary.{task}.{field} (migrated from retired config)"
                    )

            auxiliary[task] = task_config

        if changed:
            config["auxiliary"] = auxiliary
            config["compression"] = compression
            save_config(config)
            if not quiet:
                print("  ✓ Consolidated auxiliary model routes under auxiliary.<task>")

    # ── Version 19 → 20: move legacy cache directories once ──
    if current_ver < 20:
        for migration in _migrate_legacy_cache_dirs(get_VoidCube_home()):
            results["config_added"].append(f"cache layout ({migration})")
            if not quiet:
                print(f"  ✓ Migrated cache directory: {migration}")

    # ── Always: remove retired model and auxiliary-route env vars from .env ──
    # These env vars were written by the old setup wizard but nothing reads
    # them anymore.  Delete them rather than preserving empty compatibility
    # placeholders, so they cannot be mistaken for API-A/API-B config later.
    for dead_var in _RETIRED_MODEL_ENV_VARS:
        try:
            old_val = get_env_value(dead_var)
            if old_val is not None and remove_env_value(dead_var):
                if not quiet:
                    print(
                        f"  ✓ Removed {dead_var} from .env "
                        "(retired; config.yaml and provider credentials are source of truth)"
                    )
        except Exception:
            pass

    # ── Version 13 → 14: migrate legacy flat stt.model to provider section ──
    # Old configs had a flat `stt.model` key
    # that was provider-agnostic.  When the provider was "local" this caused
    # OpenAI model names (e.g. "whisper-1") to be fed to faster-whisper,
    # crashing with "Invalid model size".  Move the value into the correct
    # provider-specific section and remove the flat key.
    if current_ver < 14:
        # Read raw config (no defaults merged) to check what the user actually
        # wrote, then apply changes to the merged config for saving.
        raw = read_raw_config()
        raw_stt = raw.get("stt", {})
        if isinstance(raw_stt, dict) and "model" in raw_stt:
            legacy_model = raw_stt["model"]
            provider = raw_stt.get("provider", "local")
            config = load_config()
            stt = config.get("stt", {})
            # Remove the legacy flat key
            stt.pop("model", None)
            # Place it in the appropriate provider section only if the
            # user didn't already set a model there
            if provider in ("local", "local_command"):
                # Don't migrate an OpenAI model name into the local section
                _local_models = {
                    "tiny.en", "tiny", "base.en", "base", "small.en", "small",
                    "medium.en", "medium", "large-v1", "large-v2", "large-v3",
                    "large", "distil-large-v2", "distil-medium.en",
                    "distil-small.en", "distil-large-v3", "distil-large-v3.5",
                    "large-v3-turbo", "turbo",
                }
                if legacy_model in _local_models:
                    # Check raw config — only set if user didn't already
                    # have a nested local.model
                    raw_local = raw_stt.get("local", {})
                    if not isinstance(raw_local, dict) or "model" not in raw_local:
                        local_cfg = stt.setdefault("local", {})
                        local_cfg["model"] = legacy_model
                # else: drop it — it was an OpenAI model name, local section
                # already defaults to "base" via DEFAULT_CONFIG
            else:
                # Cloud provider — put it in that provider's section only
                # if user didn't already set a nested model
                raw_provider = raw_stt.get(provider, {})
                if not isinstance(raw_provider, dict) or "model" not in raw_provider:
                    provider_cfg = stt.setdefault(provider, {})
                    provider_cfg["model"] = legacy_model
            config["stt"] = stt
            save_config(config)
            if not quiet:
                print(f"  ✓ Migrated legacy stt.model to provider-specific config")

    # ── Version 14 → 15: add explicit gateway interim-message gate ──
    if current_ver < 15:
        config = read_raw_config()
        display = config.get("display", {})
        if not isinstance(display, dict):
            display = {}
        if "interim_assistant_messages" not in display:
            display["interim_assistant_messages"] = True
            config["display"] = display
            results["config_added"].append("display.interim_assistant_messages=true (default)")
            save_config(config)
            if not quiet:
                print("  ✓ Added display.interim_assistant_messages=true")

    # Retired display overrides are migrated idempotently regardless of the
    # recorded config version, then removed from the saved configuration.
    raw_config = read_raw_config()
    (
        config,
        migrated_overrides,
        removed_override_key,
        removed_command_key,
    ) = _migrate_retired_display_config(raw_config)
    display_settings_changed = config.get("display") != raw_config.get("display")
    if display_settings_changed:
        save_config(config)
        if removed_override_key and not quiet:
            migrated = ", ".join(f"{p}={m}" for p, m in migrated_overrides.items())
            detail = f": {migrated}" if migrated else ""
            print(f"  ✓ Migrated retired display overrides → display.platforms{detail}")
        if removed_override_key:
            results["config_added"].append(
                "display.platforms (migrated from retired display overrides)"
            )
        if removed_command_key:
            if not quiet:
                print("  ✓ Removed unused display progress command flag")
            results["config_added"].append(
                "removed unused display progress command flag"
            )

    if current_ver < latest_ver and not quiet:
        print(f"Config version: {current_ver} → {latest_ver}")
    
    # Check for missing required env vars
    missing_env = get_missing_env_vars(required_only=True)
    
    if missing_env and not quiet:
        print("\n⚠️  Missing required environment variables:")
        for var in missing_env:
            print(f"   • {var['name']}: {var['description']}")
    
    if interactive and missing_env:
        print("\nLet's configure them now:\n")
        for var in missing_env:
            if var.get("url"):
                print(f"  Get your key at: {var['url']}")
            
            if var.get("password"):
                import getpass
                value = getpass.getpass(f"  {var['prompt']}: ")
            else:
                value = input(f"  {var['prompt']}: ").strip()
            
            if value:
                save_env_value(var["name"], value)
                results["env_added"].append(var["name"])
                print(f"  ✓ Saved {var['name']}")
            else:
                results["warnings"].append(f"Skipped {var['name']} - some features may not work")
            print()
    
    # Check for missing optional env vars and offer to configure interactively
    # Skip "advanced" vars (like OPENAI_BASE_URL) -- those are for power users
    missing_optional = get_missing_env_vars(required_only=False)
    required_names = {v["name"] for v in missing_env} if missing_env else set()
    missing_optional = [
        v for v in missing_optional
        if v["name"] not in required_names and not v.get("advanced")
    ]
    
    # Only offer to configure env vars that are NEW since the user's previous version
    new_var_names = set()
    for ver in range(current_ver + 1, latest_ver + 1):
        new_var_names.update(ENV_VARS_BY_VERSION.get(ver, []))

    if new_var_names and interactive and not quiet:
        new_and_unset = [
            (name, OPTIONAL_ENV_VARS[name])
            for name in sorted(new_var_names)
            if not get_env_value(name) and name in OPTIONAL_ENV_VARS
        ]
        if new_and_unset:
            print(f"\n  {len(new_and_unset)} new optional key(s) in this update:")
            for name, info in new_and_unset:
                print(f"    • {name} — {info.get('description', '')}")
            print()
            try:
                answer = input("  Configure new keys? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

            if answer in ("y", "yes"):
                print()
                for name, info in new_and_unset:
                    if info.get("url"):
                        print(f"  {info.get('description', name)}")
                        print(f"  Get your key at: {info['url']}")
                    else:
                        print(f"  {info.get('description', name)}")
                    if info.get("password"):
                        import getpass
                        value = getpass.getpass(f"  {info.get('prompt', name)} (Enter to skip): ")
                    else:
                        value = input(f"  {info.get('prompt', name)} (Enter to skip): ").strip()
                    if value:
                        save_env_value(name, value)
                        results["env_added"].append(name)
                        print(f"  ✓ Saved {name}")
                    print()
            else:
                print("  Set later with: VoidCube config set <key> <value>")
    
    # Check for missing config fields
    missing_config = get_missing_config_fields()
    
    if missing_config:
        config = load_config()
        
        for field in missing_config:
            key = field["key"]
            default = field["default"]
            
            _set_nested(config, key, default)
            results["config_added"].append(key)
            if not quiet:
                print(f"  ✓ Added {key} = {default}")
        
        # Update version and save
        config["_config_version"] = latest_ver
        save_config(config)
    elif current_ver < latest_ver:
        # Just update version
        config = load_config()
        config["_config_version"] = latest_ver
        save_config(config)

    # ── Skill-declared config vars ──────────────────────────────────────
    # Skills can declare config.yaml settings they need via
    # metadata.VoidCube.config in their SKILL.md frontmatter.
    # Prompt for any that are missing/empty.
    missing_skill_config = get_missing_skill_config_vars()
    if missing_skill_config and interactive and not quiet:
        print(f"\n  {len(missing_skill_config)} skill setting(s) not configured:")
        for var in missing_skill_config:
            skill_name = var.get("skill", "unknown")
            print(f"    • {var['key']} — {var['description']} (from skill: {skill_name})")
        print()
        try:
            answer = input("  Configure skill settings? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("y", "yes"):
            print()
            config = load_config()
            try:
                from ...extensions.skills.catalog import SKILL_CONFIG_PREFIX
            except Exception:
                SKILL_CONFIG_PREFIX = "skills.config"
            for var in missing_skill_config:
                default = var.get("default", "")
                default_hint = f" (default: {default})" if default else ""
                value = input(f"  {var['prompt']}{default_hint}: ").strip()
                if not value and default:
                    value = str(default)
                if value:
                    storage_key = f"{SKILL_CONFIG_PREFIX}.{var['key']}"
                    _set_nested(config, storage_key, value)
                    results["config_added"].append(var["key"])
                    print(f"  ✓ Saved {var['key']} = {value}")
                else:
                    results["warnings"].append(
                        f"Skipped {var['key']} — skill '{var.get('skill', '?')}' may ask for it later"
                    )
                print()
            save_config(config)
        else:
            print("  Set later with: VoidCube config set <key> <value>")

    return results


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, preserving nested defaults.

    Keys in *override* take precedence. If both values are dicts the merge
    recurses, so a user who overrides only ``tts.elevenlabs.voice_id`` will
    keep the default ``tts.elevenlabs.model_id`` intact.
    """
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand_env_vars(obj):
    """Recursively expand ``${VAR}`` references in config values.

    Only string values are processed; dict keys, numbers, booleans, and
    None are left untouched.  Unresolved references (variable not in
    ``os.environ``) are kept verbatim so callers can detect them.
    """
    if isinstance(obj, str):
        return re.sub(
            r"\${([^}]+)}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


_RUNTIME_MAPPING_SECTIONS = (
    "runtime",
    "providers",
    "image_generation",
    "video_generation",
    "agent",
    "display",
    "terminal",
    "checkpoints",
    "compression",
    "delegation",
    "companion_workers",
    "auxiliary",
    "clarify",
)
_RETIRED_DISPLAY_OVERRIDE_KEY = "tool_progress_" + "overrides"
_UNUSED_DISPLAY_COMMAND_KEY = "tool_progress_" + "command"


def _normalize_runtime_mapping_sections(config: Dict[str, Any]) -> Dict[str, Any]:
    """Restore required mapping sections when user YAML gives them another type."""
    normalized = dict(config or {})
    normalized.pop("multimodal", None)
    for section in _RUNTIME_MAPPING_SECTIONS:
        if isinstance(normalized.get(section), dict):
            continue
        default = DEFAULT_CONFIG.get(section)
        normalized[section] = copy.deepcopy(default) if isinstance(default, dict) else {}
    return normalized


def _migrate_retired_display_config(
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], bool, bool]:
    """Migrate retired display keys without discarding unrelated settings."""
    normalized = dict(config or {})
    display = normalized.get("display")
    if not isinstance(display, dict):
        return normalized, {}, False, False

    display = dict(display)
    display.pop("skin", None)
    removed_override_key = _RETIRED_DISPLAY_OVERRIDE_KEY in display
    removed_command_key = _UNUSED_DISPLAY_COMMAND_KEY in display
    retired_overrides = display.pop(_RETIRED_DISPLAY_OVERRIDE_KEY, None)
    display.pop(_UNUSED_DISPLAY_COMMAND_KEY, None)
    migrated_overrides = retired_overrides if isinstance(retired_overrides, dict) else {}

    if migrated_overrides:
        raw_platforms = display.get("platforms")
        platforms = dict(raw_platforms) if isinstance(raw_platforms, dict) else {}
        for platform, mode in migrated_overrides.items():
            platform_config = platforms.get(platform)
            platform_config = dict(platform_config) if isinstance(platform_config, dict) else {}
            platform_config.setdefault("tool_progress", mode)
            platforms[platform] = platform_config
        display["platforms"] = platforms

    normalized["display"] = display
    return normalized, migrated_overrides, removed_override_key, removed_command_key


def _drop_root_model_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """Drop retired root-level model/provider/base_url fields."""
    config = dict(config)
    for key in ("model", "provider", "base_url"):
        config.pop(key, None)
    return config


def _normalize_max_turns_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy root-level max_turns into agent.max_turns."""
    config = dict(config)
    agent_config = dict(config.get("agent") or {})

    if "max_turns" in config and "max_turns" not in agent_config:
        agent_config["max_turns"] = config["max_turns"]

    if "max_turns" not in agent_config:
        agent_config["max_turns"] = DEFAULT_CONFIG["agent"]["max_turns"]

    config["agent"] = agent_config
    config.pop("max_turns", None)
    return config


def _provider_type_from_key(provider_key: str, provider_entry: Dict[str, Any]) -> str:
    """Infer provider type for unified provider entries."""
    api_url = str(
        provider_entry.get("base_url")
        or provider_entry.get("api")
        or provider_entry.get("url")
        or ""
    ).strip().lower()
    key = (provider_key or "").strip().lower()
    if key in {"openrouter", "openai", "deepseek", "ollama", "lm-studio"}:
        return key
    if "openrouter.ai" in api_url:
        return "openrouter"
    if "api.openai.com" in api_url:
        return "openai"
    if "api.deepseek.com" in api_url:
        return "deepseek"
    if "localhost:11434" in api_url or "ollama" in api_url:
        return "ollama"
    return "openai_compatible"


def _provider_env_var_for_key(provider_key: str) -> str:
    """Return the canonical API key env var for a known provider key."""
    mapping = {
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "ollama": "",
        "lm-studio": "",
    }
    return mapping.get((provider_key or "").strip().lower(), "")


def _normalize_provider_entry(
    provider_key: str,
    entry: Any,
) -> Optional[Dict[str, Any]]:
    """Normalize a provider entry to the unified schema."""
    if not isinstance(entry, dict):
        return None

    key = (provider_key or "").strip()
    api_url = str(entry.get("base_url") or entry.get("api") or entry.get("url") or "").strip()
    selected_model = str(
        entry.get("selected_model")
        or entry.get("default_model")
        or entry.get("model")
        or ""
    ).strip()
    api_key_env = str(entry.get("api_key_env") or entry.get("key_env") or "").strip()
    api_key = str(entry.get("api_key") or "").strip()
    auth_mode = str(entry.get("auth_mode") or "").strip().lower()

    normalized = dict(entry)
    normalized["label"] = str(entry.get("label") or entry.get("name") or key).strip() or key
    normalized["type"] = str(entry.get("type") or _provider_type_from_key(key, entry)).strip() or "openai_compatible"
    normalized["base_url"] = api_url
    normalized["selected_model"] = selected_model
    if not auth_mode:
        if api_key:
            auth_mode = "stored"
        elif api_key_env:
            auth_mode = "env"
        elif normalized["type"] in {"ollama", "lm-studio"}:
            auth_mode = "none"
        else:
            auth_mode = "env"
    normalized["auth_mode"] = auth_mode

    if not api_key_env:
        api_key_env = _provider_env_var_for_key(key)
    if api_key_env:
        normalized["api_key_env"] = api_key_env
    else:
        normalized.pop("api_key_env", None)

    if api_key:
        normalized["api_key"] = api_key
    else:
        normalized.pop("api_key", None)

    return normalized


def _normalize_provider_runtime_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config to unified runtime.active_provider + providers schema."""
    config = dict(config or {})
    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    else:
        runtime = dict(runtime)

    raw_providers = config.get("providers")
    if not isinstance(raw_providers, dict):
        raw_providers = {}
    normalized_providers: Dict[str, Any] = {}

    for provider_key, provider_entry in raw_providers.items():
        normalized = _normalize_provider_entry(str(provider_key), provider_entry)
        if normalized is not None:
            normalized_providers[str(provider_key)] = normalized

    active_provider = str(runtime.get("active_provider") or "").strip()
    if active_provider and active_provider not in normalized_providers:
        active_provider = ""

    runtime["active_provider"] = active_provider
    config["runtime"] = runtime
    config["providers"] = normalized_providers
    config.pop("custom_providers", None)
    config.pop("model", None)
    return config


def get_active_provider_key(config: Optional[Dict[str, Any]] = None) -> str:
    """Return the configured active provider key."""
    cfg = _normalize_provider_runtime_config(config or load_config())
    runtime = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    return str(runtime.get("active_provider") or "").strip()


def get_configured_providers(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return configured providers from normalized config."""
    cfg = _normalize_provider_runtime_config(config or load_config())
    providers = cfg.get("providers")
    return providers if isinstance(providers, dict) else {}


def get_active_provider_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the configured active provider entry."""
    cfg = _normalize_provider_runtime_config(config or load_config())
    active_provider = get_active_provider_key(cfg)
    providers = get_configured_providers(cfg)
    entry = providers.get(active_provider)
    return dict(entry) if isinstance(entry, dict) else {}


def get_active_model_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the active model/runtime fields derived from the unified provider config."""
    cfg = _normalize_provider_runtime_config(config or load_config())
    active_provider = get_active_provider_key(cfg)
    provider_cfg = get_active_provider_config(cfg)
    if active_provider and provider_cfg:
        return {
            "provider": active_provider,
            "default": str(provider_cfg.get("selected_model") or "").strip(),
            "model": str(provider_cfg.get("selected_model") or "").strip(),
            "base_url": str(provider_cfg.get("base_url") or "").strip(),
            "api_key": str(provider_cfg.get("api_key") or "").strip(),
        }
    return {}


def set_active_provider(config: Dict[str, Any], provider_key: str) -> Dict[str, Any]:
    """Set runtime.active_provider if the provider exists."""
    normalized = _normalize_provider_runtime_config(config)
    providers = normalized.get("providers") if isinstance(normalized.get("providers"), dict) else {}
    key = str(provider_key or "").strip()
    runtime = dict(normalized.get("runtime") or {})
    runtime["active_provider"] = key if key and key in providers else ""
    normalized["runtime"] = runtime
    normalized.pop("model", None)
    return normalized


def upsert_provider(
    config: Dict[str, Any],
    provider_key: str,
    provider_data: Dict[str, Any],
    *,
    make_active: bool = False,
) -> Dict[str, Any]:
    """Create or update a provider entry in the unified providers map."""
    normalized = _normalize_provider_runtime_config(config)
    providers = dict(normalized.get("providers") or {})
    key = str(provider_key or "").strip()
    entry = _normalize_provider_entry(key, provider_data)
    if not key or entry is None:
        return normalized
    providers[key] = entry
    normalized["providers"] = providers
    if make_active:
        normalized = set_active_provider(normalized, key)
    normalized.pop("model", None)
    return normalized


def set_provider_model(
    config: Dict[str, Any],
    provider_key: str,
    model_name: str,
    *,
    make_active: bool = False,
) -> Dict[str, Any]:
    """Set selected_model for a configured provider."""
    normalized = _normalize_provider_runtime_config(config)
    providers = dict(normalized.get("providers") or {})
    key = str(provider_key or "").strip()
    if not key or key not in providers or not isinstance(providers.get(key), dict):
        return normalized
    entry = dict(providers[key])
    entry["selected_model"] = str(model_name or "").strip()
    providers[key] = entry
    normalized["providers"] = providers
    if make_active:
        normalized = set_active_provider(normalized, key)
    normalized.pop("model", None)
    return normalized



def read_raw_config() -> Dict[str, Any]:
    """Read ~/.VoidCube/config.yaml as-is, without merging defaults or migrating.

    Returns the raw YAML dict, or ``{}`` if the file doesn't exist or can't
    be parsed.  Use this for lightweight config reads where you just need a
    single value and don't want the overhead of ``load_config()``'s deep-merge
    + migration pipeline.
    """
    try:
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def load_config() -> Dict[str, Any]:
    """Load configuration from ~/.VoidCube/config.yaml."""
    ensure_VoidCube_home()
    config_path = get_config_path()
    
    config = copy.deepcopy(DEFAULT_CONFIG)
    
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}

            if "max_turns" in user_config:
                raw_agent_config = user_config.get("agent")
                agent_user_config = (
                    dict(raw_agent_config)
                    if isinstance(raw_agent_config, dict)
                    else {}
                )
                if agent_user_config.get("max_turns") is None:
                    agent_user_config["max_turns"] = user_config["max_turns"]
                user_config["agent"] = agent_user_config
                user_config.pop("max_turns", None)

            config = _deep_merge(config, user_config)
        except Exception as e:
            print(f"Warning: Failed to load config: {e}")

    normalized = _normalize_runtime_mapping_sections(config)
    normalized, _, _, _ = _migrate_retired_display_config(normalized)
    normalized = _drop_root_model_keys(_normalize_max_turns_config(normalized))
    normalized = _normalize_provider_runtime_config(normalized)
    return _expand_env_vars(normalized)


_SECURITY_COMMENT = """
# ── Security ──────────────────────────────────────────────────────────
# API keys, tokens, and passwords are redacted from tool output by default.
# Set to false to see full values (useful for debugging auth issues).
# tirith pre-exec scanning is enabled by default when the tirith binary
# is available. Configure via security.tirith_* keys or env vars
# (TIRITH_ENABLED, TIRITH_BIN, TIRITH_TIMEOUT, TIRITH_FAIL_OPEN).
#
# security:
#   redact_secrets: false
#   tirith_enabled: true
#   tirith_path: "tirith"
#   tirith_timeout: 5
#   tirith_fail_open: true
"""

_FALLBACK_COMMENT = """
# ── Fallback Providers ───────────────────────────────────────────────
# Automatic provider failover when primary is unavailable.
# Uncomment and configure to enable. Triggers on rate limits (429),
# overload (529), service errors (503), or connection failures.
#
# Supported providers:
#   openrouter   (OPENROUTER_API_KEY)  — routes to any model
#   nous         (OAuth — VoidCube auth) — Nous Portal
#   zai          (ZAI_API_KEY)         — Z.AI / GLM
#   kimi-coding  (KIMI_API_KEY)        — Kimi / Moonshot
#   minimax      (MINIMAX_API_KEY)     — MiniMax
#   minimax-cn   (MINIMAX_CN_API_KEY)  — MiniMax (China)
#
# For custom OpenAI-compatible endpoints, add base_url and api_key_env.
#
# fallback_providers:
#   - provider: openrouter
#     model: <provider-model-id>
#
# ── Smart Model Routing ────────────────────────────────────────────────
# Optional cheap-vs-strong routing for simple turns.
# Keeps the primary model for complex work, but can route short/simple
# messages to a cheaper model across providers.
#
# smart_model_routing:
#   enabled: true
#   max_simple_chars: 160
#   max_simple_words: 28
#   cheap_model:
#     provider: openrouter
#     model: <provider-model-id>
"""


_COMMENTED_SECTIONS = """
# ── Security ──────────────────────────────────────────────────────────
# API keys, tokens, and passwords are redacted from tool output by default.
# Set to false to see full values (useful for debugging auth issues).
#
# security:
#   redact_secrets: false

# ── Fallback Providers ───────────────────────────────────────────────
# Automatic provider failover when primary is unavailable.
# Uncomment and configure to enable. Triggers on rate limits (429),
# overload (529), service errors (503), or connection failures.
#
# Supported providers:
#   openrouter   (OPENROUTER_API_KEY)  — routes to any model
#   nous         (OAuth — VoidCube auth) — Nous Portal
#   zai          (ZAI_API_KEY)         — Z.AI / GLM
#   kimi-coding  (KIMI_API_KEY)        — Kimi / Moonshot
#   minimax      (MINIMAX_API_KEY)     — MiniMax
#   minimax-cn   (MINIMAX_CN_API_KEY)  — MiniMax (China)
#
# For custom OpenAI-compatible endpoints, add base_url and api_key_env.
#
# fallback_providers:
#   - provider: openrouter
#     model: <provider-model-id>
#
# ── Smart Model Routing ────────────────────────────────────────────────
# Optional cheap-vs-strong routing for simple turns.
# Keeps the primary model for complex work, but can route short/simple
# messages to a cheaper model across providers.
#
# smart_model_routing:
#   enabled: true
#   max_simple_chars: 160
#   max_simple_words: 28
#   cheap_model:
#     provider: openrouter
#     model: <provider-model-id>
"""


def save_config(config: Dict[str, Any], *, preserve_structure: bool = False):
    """Save configuration to ~/.VoidCube/config.yaml.

    ``preserve_structure`` is reserved for narrow editors that must update one
    canonical subtree without normalizing unrelated user configuration.
    """
    if is_managed():
        managed_error("save configuration")
        return
    from ..persistence.file_store import atomic_yaml_write

    ensure_VoidCube_home()
    config_path = get_config_path()
    if preserve_structure:
        normalized = copy.deepcopy(config)
    else:
        normalized = _normalize_runtime_mapping_sections(config)
        normalized, _, _, _ = _migrate_retired_display_config(normalized)
        normalized = _drop_root_model_keys(_normalize_max_turns_config(normalized))
        normalized = _normalize_provider_runtime_config(normalized)

    # Build optional commented-out sections for features that are off by
    # default or only relevant when explicitly configured.
    parts = []
    sec = normalized.get("security", {})
    if not sec or sec.get("redact_secrets") is None:
        parts.append(_SECURITY_COMMENT)
    fb = normalized.get("fallback_providers", [])
    if not isinstance(fb, list) or not fb:
        parts.append(_FALLBACK_COMMENT)

    atomic_yaml_write(
        config_path,
        normalized,
        extra_content="".join(parts) if parts else None,
    )
    _secure_file(config_path)


def save_config_value(key_path: str, value: Any) -> bool:
    """Persist one dotted configuration value and report whether it succeeded."""
    try:
        config = load_config()
        _set_nested(config, key_path, value)
        save_config(config)
        return True
    except Exception as exc:
        logger.error("Failed to save config value %s: %s", key_path, exc)
        return False


def load_env() -> Dict[str, str]:
    """Load environment variables from ~/.VoidCube/.env."""
    env_path = get_env_path()
    env_vars = {}
    
    if env_path.exists():
        # Always use UTF-8 encoding for .env files
        with open(env_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    env_vars[key.strip()] = value.strip().strip('"\'')
    
    return env_vars


def _sanitize_env_lines(lines: list) -> list:
    """Fix corrupted .env lines before writing.

    Handles two known corruption patterns:
    1. Concatenated KEY=VALUE pairs on a single line (missing newline between
       entries, e.g. ``OPENROUTER_API_KEY=sk-...OPENAI_BASE_URL=https://...``).
    2. Stale ``KEY=***`` placeholder entries left by incomplete setup runs.

    Uses a known-keys set (OPTIONAL_ENV_VARS + _EXTRA_ENV_KEYS) so we only
    split on real Voidcube env var names, avoiding false positives from values
    that happen to contain uppercase text with ``=``.
    """
    # Build the known keys set lazily from OPTIONAL_ENV_VARS + extras.
    # Done inside the function so OPTIONAL_ENV_VARS is guaranteed to be defined.
    known_keys = (
        set(OPTIONAL_ENV_VARS.keys())
        | _EXTRA_ENV_KEYS
        | set(_RETIRED_TOOL_PROGRESS_ENV_VARS)
        | set(_RETIRED_MESSAGING_ENV_VARS)
        | {_RETIRED_PREFILL_ENV_VAR}
        | set(_RETIRED_UNUSED_CONFIG_ENV_VARS)
    )

    sanitized: list[str] = []
    for line in lines:
        raw = line.rstrip("\r\n")
        stripped = raw.strip()

        # Preserve blank lines and comments
        if not stripped or stripped.startswith("#"):
            sanitized.append(raw + "\n")
            continue

        key, sep, value = stripped.partition("=")
        if sep and key in known_keys and is_placeholder_secret(value):
            sanitized.append(f"# {stripped}\n")
            continue

        # Detect concatenated KEY=VALUE pairs on one line.
        # Search for known KEY= patterns at any position in the line.
        split_positions = []
        for key_name in known_keys:
            needle = key_name + "="
            idx = stripped.find(needle)
            while idx >= 0:
                split_positions.append(idx)
                idx = stripped.find(needle, idx + len(needle))

        if len(split_positions) > 1:
            split_positions.sort()
            # Deduplicate (shouldn't happen, but be safe)
            split_positions = sorted(set(split_positions))
            for i, pos in enumerate(split_positions):
                end = split_positions[i + 1] if i + 1 < len(split_positions) else len(stripped)
                part = stripped[pos:end].strip()
                if part:
                    sanitized.append(part + "\n")
        else:
            sanitized.append(stripped + "\n")

    return sanitized



def sanitize_env_file() -> int:
    """Read, sanitize, and rewrite ~/.VoidCube/.env in place.

    Returns the number of lines that were fixed (concatenation splits +
    placeholder removals).  Returns 0 when no changes are needed.
    """
    env_path = get_env_path()
    if not env_path.exists():
        return 0

    # Always use UTF-8 encoding for .env files
    with open(env_path, encoding="utf-8", errors="replace") as f:
        original_lines = f.readlines()

    sanitized = _sanitize_env_lines(original_lines)

    if sanitized == original_lines:
        return 0

    # Count fixes: difference in line count (from splits) + removed lines
    fixes = abs(len(sanitized) - len(original_lines))
    if fixes == 0:
        # Lines changed content (e.g. *** removal) even if count is same
        fixes = sum(1 for a, b in zip(original_lines, sanitized) if a != b)
        fixes += abs(len(sanitized) - len(original_lines))

    fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix=".tmp", prefix=".env_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(sanitized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, env_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _secure_file(env_path)
    return fixes


def save_env_value(key: str, value: str):
    """Save or update a value in ~/.VoidCube/.env."""
    if is_managed():
        managed_error(f"set {key}")
        return
    if not _ENV_VAR_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name: {key!r}")
    value = value.replace("\n", "").replace("\r", "")
    ensure_VoidCube_home()
    env_path = get_env_path()
    
    # Always use UTF-8 encoding for .env files
    lines = []
    if env_path.exists():
        with open(env_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Sanitize on every read: split concatenated keys, drop stale placeholders
        lines = _sanitize_env_lines(lines)
    
    # Find and update or append
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    
    if not found:
        # Ensure there's a newline at the end of the file before appending
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    
    fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix='.tmp', prefix='.env_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, env_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _secure_file(env_path)

    os.environ[key] = value

    # Restrict .env permissions to owner-only (contains API keys)
    if not _IS_WINDOWS:
        try:
            os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def remove_env_value(key: str) -> bool:
    """Remove a key from ~/.VoidCube/.env and os.environ.

    Returns True if the key was found and removed, False otherwise.
    """
    if is_managed():
        managed_error(f"remove {key}")
        return False
    if not _ENV_VAR_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name: {key!r}")
    env_path = get_env_path()
    if not env_path.exists():
        os.environ.pop(key, None)
        return False

    # Always use UTF-8 encoding for .env files
    read_kw = {"encoding": "utf-8", "errors": "replace"}
    write_kw = {"encoding": "utf-8"}

    with open(env_path, **read_kw) as f:
        lines = f.readlines()
    lines = _sanitize_env_lines(lines)

    new_lines = [line for line in lines if not line.strip().startswith(f"{key}=")]
    found = len(new_lines) < len(lines)

    if found:
        fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix='.tmp', prefix='.env_')
        try:
            with os.fdopen(fd, 'w', **write_kw) as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, env_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _secure_file(env_path)

    os.environ.pop(key, None)
    return found


def get_env_value(key: str) -> Optional[str]:
    """Get a value from ~/.VoidCube/.env or environment."""
    # Check environment first
    if key in os.environ:
        return os.environ[key]
    
    # Then check .env file
    env_vars = load_env()
    return env_vars.get(key)


# =============================================================================
# Config display
# =============================================================================

def redact_key(key: str) -> str:
    """Redact an API key for display."""
    if not key:
        return "(not set)"
    if len(key) < 12:
        return "***"
    return key[:4] + "..." + key[-4:]
