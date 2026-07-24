"""Command-line presentation and dispatch for Voidcube configuration."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict

import yaml

from VoidCube_cli.colors import Colors, color
from VoidCube_cli.config import (
    DEFAULT_CONFIG,
    OPTIONAL_ENV_VARS,
    REQUIRED_ENV_VARS,
    check_config_version,
    ensure_VoidCube_home,
    get_env_value,
    get_missing_config_fields,
    get_missing_env_vars,
    is_managed,
    load_config,
    managed_error,
    migrate_config,
    redact_key,
    save_config,
    save_env_value,
)
from VoidCube_core.constants import get_config_path, get_env_path


def show_config() -> None:
    """Display current configuration."""
    config = load_config()
    runtime_cfg = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    providers_cfg = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    active_provider = str(runtime_cfg.get("active_provider") or "").strip()
    active_provider_cfg = (
        providers_cfg.get(active_provider, {}) if active_provider in providers_cfg else {}
    )
    active_model = str(active_provider_cfg.get("selected_model") or "").strip()

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│              > Voidcube Configuration                    │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))

    print()
    print(color("◆ Paths", Colors.CYAN, Colors.BOLD))
    print(f"  Config:       {get_config_path()}")
    print(f"  Secrets:      {get_env_path()}")
    print(f"  Install:      {Path(__file__).parent.parent.resolve()}")

    print()
    print(color("◆ API Keys", Colors.CYAN, Colors.BOLD))
    keys = [
        ("OPENROUTER_API_KEY", "OpenRouter"),
        ("VOICE_TOOLS_OPENAI_KEY", "OpenAI (STT/TTS)"),
        ("EXA_API_KEY", "Exa"),
        ("PARALLEL_API_KEY", "Parallel"),
        ("FIRECRAWL_API_KEY", "Firecrawl"),
        ("TAVILY_API_KEY", "Tavily"),
        ("BROWSERBASE_API_KEY", "Browserbase"),
        ("BROWSER_USE_API_KEY", "Browser Use"),
        ("FAL_KEY", "FAL"),
    ]
    for env_key, name in keys:
        print(f"  {name:<14} {redact_key(get_env_value(env_key))}")

    print()
    print(color("◆ Model", Colors.CYAN, Colors.BOLD))
    print(f"  Active provider: {active_provider or color('(not set)', Colors.DIM)}")
    print(f"  Active model:    {active_model or color('(not set)', Colors.DIM)}")
    print(f"  Providers:       {len(providers_cfg)} configured")
    print(
        "  Max turns:    "
        f"{config.get('agent', {}).get('max_turns', DEFAULT_CONFIG['agent']['max_turns'])}"
    )

    print()
    print(color("◆ Display", Colors.CYAN, Colors.BOLD))
    display = config.get("display", {})
    print(f"  Personality:  {display.get('personality', 'kawaii')}")
    print(f"  Reasoning:    {'on' if display.get('show_reasoning', False) else 'off'}")
    print(f"  Bell:         {'on' if display.get('bell_on_complete', False) else 'off'}")

    print()
    print(color("◆ Terminal", Colors.CYAN, Colors.BOLD))
    terminal = config.get("terminal", {})
    print(f"  Backend:      {terminal.get('backend', 'local')}")
    print(f"  Working dir:  {terminal.get('cwd', '.')}")
    print(f"  Timeout:      {terminal.get('timeout', 60)}s")

    if terminal.get("backend") == "docker":
        print(
            "  Docker image: "
            f"{terminal.get('docker_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}"
        )
    elif terminal.get("backend") == "podman":
        print(
            "  Podman image: "
            f"{terminal.get('podman_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}"
        )
    elif terminal.get("backend") == "singularity":
        print(
            "  Image:        "
            f"{terminal.get('singularity_image', 'docker://nikolaik/python-nodejs:python3.11-nodejs20')}"
        )
    elif terminal.get("backend") == "modal":
        print(
            "  Modal image:  "
            f"{terminal.get('modal_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}"
        )
        print(
            "  Modal token:  "
            f"{'configured' if get_env_value('MODAL_TOKEN_ID') else '(not set)'}"
        )
    elif terminal.get("backend") == "daytona":
        print(
            "  Daytona image: "
            f"{terminal.get('daytona_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}"
        )
        print(
            "  API key:      "
            f"{'configured' if get_env_value('DAYTONA_API_KEY') else '(not set)'}"
        )
    elif terminal.get("backend") == "ssh":
        print(f"  SSH host:     {get_env_value('TERMINAL_SSH_HOST') or '(not set)'}")
        print(f"  SSH user:     {get_env_value('TERMINAL_SSH_USER') or '(not set)'}")

    print()
    print(color("◆ Timezone", Colors.CYAN, Colors.BOLD))
    timezone_name = config.get("timezone", "")
    print(
        f"  Timezone:     {timezone_name}"
        if timezone_name
        else f"  Timezone:     {color('(server-local)', Colors.DIM)}"
    )

    print()
    print(color("◆ Context Compression", Colors.CYAN, Colors.BOLD))
    compression = config.get("compression", {})
    auxiliary = config.get("auxiliary", {})
    if not isinstance(auxiliary, dict):
        auxiliary = {}
    compression_route = auxiliary.get("compression", {})
    if not isinstance(compression_route, dict):
        compression_route = {}
    enabled = compression.get("enabled", True)
    print(f"  Enabled:      {'yes' if enabled else 'no'}")
    if enabled:
        print(f"  Threshold:    {compression.get('threshold', 0.50) * 100:.0f}%")
        print(
            "  Target ratio: "
            f"{compression.get('target_ratio', 0.20) * 100:.0f}% of threshold preserved"
        )
        print(f"  Protect last: {compression.get('protect_last_n', 20)} messages")
        print(f"  Model:        {compression_route.get('model', '') or '(auto auxiliary model)'}")
        compression_provider = compression_route.get("provider", "auto")
        if compression_provider != "auto":
            print(f"  Provider:     {compression_provider}")

    auxiliary_tasks = {
        "Vision": auxiliary.get("vision", {}),
        "Web extract": auxiliary.get("web_extract", {}),
    }
    has_overrides = any(
        task.get("provider", "auto") != "auto" or task.get("model", "")
        for task in auxiliary_tasks.values()
    )
    if has_overrides:
        print()
        print(color("◆ Auxiliary Models (overrides)", Colors.CYAN, Colors.BOLD))
        for label, task_config in auxiliary_tasks.items():
            provider = task_config.get("provider", "auto")
            model = task_config.get("model", "")
            if provider != "auto" or model:
                parts = [f"provider={provider}"]
                if model:
                    parts.append(f"model={model}")
                print(f"  {label:12s}  {', '.join(parts)}")

    try:
        from agent.skill_utils import discover_all_skill_config_vars, resolve_skill_config_values

        skill_vars = discover_all_skill_config_vars()
        if skill_vars:
            resolved = resolve_skill_config_values(skill_vars)
            print()
            print(color("◆ Skill Settings", Colors.CYAN, Colors.BOLD))
            for var in skill_vars:
                key = var["key"]
                value = resolved.get(key, "")
                skill_name = var.get("skill", "")
                display_value = str(value) if value else color("(not set)", Colors.DIM)
                print(
                    f"  {key:<20s} {display_value}  "
                    f"{color(f'[{skill_name}]', Colors.DIM)}"
                )
    except Exception:
        pass

    print()
    print(color("─" * 60, Colors.DIM))
    print(color("  VoidCube config edit     # Edit config file", Colors.DIM))
    print(color("  VoidCube config set <key> <value>", Colors.DIM))
    print(color("  /api                     # API configuration", Colors.DIM))
    print()


def edit_config() -> None:
    """Open the config file in the user's editor."""
    if is_managed():
        managed_error("edit configuration")
        return
    config_path = get_config_path()
    if not config_path.exists():
        save_config(DEFAULT_CONFIG)
        print(f"Created {config_path}")

    editor = os.getenv("EDITOR") or os.getenv("VISUAL")
    if not editor:
        for command in ("nano", "vim", "vi", "code", "notepad"):
            if shutil.which(command):
                editor = command
                break
    if not editor:
        print("No editor found. Config file is at:")
        print(f"  {config_path}")
        return

    print(f"Opening {config_path} in {editor}...")
    subprocess.run([editor, str(config_path)], check=False)


def _coerce_config_value(value: str) -> Any:
    normalized = value.lower()
    if normalized in ("true", "yes", "on"):
        return True
    if normalized in ("false", "no", "off"):
        return False
    if value.isdigit():
        return int(value)
    if value.replace(".", "", 1).isdigit():
        return float(value)
    return value


def set_config_value(key: str, value: str) -> None:
    """Set one config or secret value from the CLI."""
    if is_managed():
        managed_error("set configuration values")
        return

    api_keys = {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "VOICE_TOOLS_OPENAI_KEY",
        "EXA_API_KEY",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "FIRECRAWL_GATEWAY_URL",
        "TOOL_GATEWAY_DOMAIN",
        "TOOL_GATEWAY_SCHEME",
        "TOOL_GATEWAY_USER_TOKEN",
        "TAVILY_API_KEY",
        "BROWSERBASE_API_KEY",
        "BROWSERBASE_PROJECT_ID",
        "BROWSER_USE_API_KEY",
        "FAL_KEY",
        "TERMINAL_SSH_HOST",
        "TERMINAL_SSH_USER",
        "TERMINAL_SSH_KEY",
        "SUDO_PASSWORD",
        "GITHUB_TOKEN",
        "WANDB_API_KEY",
        "TINKER_API_KEY",
    }
    normalized_key = key.upper()
    if (
        normalized_key in api_keys
        or normalized_key.endswith(("_API_KEY", "_TOKEN"))
        or normalized_key.startswith("TERMINAL_SSH")
    ):
        save_env_value(normalized_key, value)
        print(f"Set {key} in {get_env_path()}")
        return

    config_path = get_config_path()
    user_config: Dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as handle:
                user_config = yaml.safe_load(handle) or {}
        except Exception:
            user_config = {}

    parts = key.split(".")
    current = user_config
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    coerced_value = _coerce_config_value(value)
    current[parts[-1]] = coerced_value

    ensure_VoidCube_home()
    from VoidCube_core.utils import atomic_yaml_write

    atomic_yaml_write(config_path, user_config, sort_keys=False)

    config_to_env_sync = {
        "terminal.backend": "TERMINAL_ENV",
        "terminal.modal_mode": "TERMINAL_MODAL_MODE",
        "terminal.docker_image": "TERMINAL_DOCKER_IMAGE",
        "terminal.podman_image": "TERMINAL_PODMAN_IMAGE",
        "terminal.singularity_image": "TERMINAL_SINGULARITY_IMAGE",
        "terminal.modal_image": "TERMINAL_MODAL_IMAGE",
        "terminal.daytona_image": "TERMINAL_DAYTONA_IMAGE",
        "terminal.fallback_to_local": "TERMINAL_FALLBACK_TO_LOCAL",
        "terminal.docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
        "terminal.cwd": "TERMINAL_CWD",
        "terminal.timeout": "TERMINAL_TIMEOUT",
        "terminal.sandbox_dir": "TERMINAL_SANDBOX_DIR",
        "terminal.persistent_shell": "TERMINAL_PERSISTENT_SHELL",
        "terminal.container_cpu": "TERMINAL_CONTAINER_CPU",
        "terminal.container_memory": "TERMINAL_CONTAINER_MEMORY",
        "terminal.container_disk": "TERMINAL_CONTAINER_DISK",
        "terminal.container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
    }
    if key in config_to_env_sync:
        save_env_value(config_to_env_sync[key], str(coerced_value))

    print(f"Set {key} = {coerced_value} in {config_path}")


def config_command(args) -> None:
    """Handle ``VoidCube config`` subcommands."""
    subcommand = getattr(args, "config_command", None)
    if subcommand is None or subcommand == "show":
        show_config()
        return
    if subcommand == "edit":
        edit_config()
        return
    if subcommand == "set":
        key = getattr(args, "key", None)
        value = getattr(args, "value", None)
        if not key or value is None:
            print("Usage: VoidCube config set <key> <value>")
            print()
            print("Examples:")
            print("  VoidCube config set model <provider-model-id>")
            print("  VoidCube config set terminal.backend docker")
            print("  VoidCube config set terminal.backend podman")
            print("  VoidCube config set OPENROUTER_API_KEY sk-or-...")
            raise SystemExit(1)
        set_config_value(key, value)
        return
    if subcommand == "path":
        print(get_config_path())
        return
    if subcommand == "env-path":
        print(get_env_path())
        return
    if subcommand == "migrate":
        _run_migrate_command()
        return
    if subcommand == "check":
        _run_check_command()
        return

    print(f"Unknown config command: {subcommand}")
    print()
    print("Available commands:")
    print("  VoidCube config           Show current configuration")
    print("  VoidCube config edit      Open config in editor")
    print("  VoidCube config set <key> <value>   Set a config value")
    print("  VoidCube config check     Check for missing/outdated config")
    print("  VoidCube config migrate   Update config with new options")
    print("  VoidCube config path      Show config file path")
    print("  VoidCube config env-path  Show .env file path")
    raise SystemExit(1)


def _run_migrate_command() -> None:
    print()
    print(color("🔄 Checking configuration for updates...", Colors.CYAN, Colors.BOLD))
    print()

    missing_env = get_missing_env_vars(required_only=False)
    missing_config = get_missing_config_fields()
    current_version, latest_version = check_config_version()
    if not missing_env and not missing_config and current_version >= latest_version:
        print(color("✓ Configuration is up to date!", Colors.GREEN))
        print()
        return

    if current_version < latest_version:
        print(f"  Config version: {current_version} → {latest_version}")
    if missing_config:
        print(f"\n  {len(missing_config)} new config option(s) will be added with defaults")

    required_missing = [var for var in missing_env if var.get("is_required")]
    optional_missing = [
        var
        for var in missing_env
        if not var.get("is_required") and not var.get("advanced")
    ]
    if required_missing:
        print(f"\n  ⚠️  {len(required_missing)} required API key(s) missing:")
        for var in required_missing:
            print(f"     • {var['name']}")
    if optional_missing:
        print(f"\n  ℹ️  {len(optional_missing)} optional API key(s) not configured:")
        for var in optional_missing:
            tools = var.get("tools", [])
            tools_text = f" (enables: {', '.join(tools[:2])})" if tools else ""
            print(f"     • {var['name']}{tools_text}")

    print()
    results = migrate_config(interactive=True, quiet=False)
    print()
    if results["env_added"] or results["config_added"]:
        print(color("✓ Configuration updated!", Colors.GREEN))
    if results["warnings"]:
        print()
        for warning in results["warnings"]:
            print(color(f"  ⚠️  {warning}", Colors.YELLOW))
    print()


def _run_check_command() -> None:
    print()
    print(color("📋 Configuration Status", Colors.CYAN, Colors.BOLD))
    print()

    current_version, latest_version = check_config_version()
    if current_version >= latest_version:
        print(f"  Config version: {current_version} ✓")
    else:
        print(
            color(
                f"  Config version: {current_version} → {latest_version} (update available)",
                Colors.YELLOW,
            )
        )

    print()
    print(color("  Required:", Colors.BOLD))
    for variable_name in REQUIRED_ENV_VARS:
        if get_env_value(variable_name):
            print(f"    ✓ {variable_name}")
        else:
            print(color(f"    ✗ {variable_name} (missing)", Colors.RED))

    print()
    print(color("  Optional:", Colors.BOLD))
    for variable_name, info in OPTIONAL_ENV_VARS.items():
        if get_env_value(variable_name):
            print(f"    ✓ {variable_name}")
        else:
            tools = info.get("tools", [])
            tools_text = f" → {', '.join(tools[:2])}" if tools else ""
            print(color(f"    ○ {variable_name}{tools_text}", Colors.DIM))

    missing_config = get_missing_config_fields()
    if missing_config:
        print()
        print(
            color(
                f"  {len(missing_config)} new config option(s) available",
                Colors.YELLOW,
            )
        )
        print("    Run 'VoidCube config migrate' to add them")
    print()
