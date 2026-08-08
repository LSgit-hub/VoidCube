#!/usr/bin/env python3
"""
Voidcube CLI - Main entry point.

Usage:
    VoidCube                     # Interactive chat (default)
    VoidCube chat                # Interactive chat
    VoidCube gateway             # Run gateway in foreground
    VoidCube gateway start       # Start gateway as service
    VoidCube gateway stop        # Stop gateway service
    VoidCube gateway status      # Show gateway status
    VoidCube gateway install     # Install gateway service
    VoidCube gateway uninstall   # Uninstall gateway service
    VoidCube api                  # Configure API settings
    VoidCube logout              # Clear stored authentication
    VoidCube status              # Show status of all components
    VoidCube doctor              # Check configuration and dependencies
    VoidCube honcho setup                    # Configure Honcho AI memory integration
    VoidCube honcho status                   # Show Honcho config and connection status
    VoidCube honcho sessions                 # List directory → session name mappings
    VoidCube honcho map <name>               # Map current directory to a session name
    VoidCube honcho peer                     # Show peer names and dialectic settings
    VoidCube honcho peer --user NAME         # Set user peer name
    VoidCube honcho peer --ai NAME           # Set AI peer name
    VoidCube honcho peer --reasoning LEVEL   # Set dialectic reasoning level
    VoidCube honcho mode                     # Show current memory mode
    VoidCube honcho mode [hybrid|honcho|local]  # Set memory mode
    VoidCube honcho tokens                   # Show token budget settings
    VoidCube honcho tokens --context N       # Set session.context() token cap
    VoidCube honcho tokens --dialectic N     # Set dialectic result char cap
    VoidCube honcho identity                 # Show AI peer identity representation
    VoidCube honcho identity <file>          # Seed AI peer identity from a file (SOUL.md etc.)
    VoidCube honcho migrate                  # Step-by-step migration guide: OpenClaw native → Voidcube + Honcho
    VoidCube version             Show version
    VoidCube update              Update to latest version
    VoidCube uninstall           Uninstall Voidcube Agent
    VoidCube acp                 Run as an ACP server for editor integration
    VoidCube sessions browse     Interactive session picker with search

    VoidCube claw migrate --dry-run  # Preview migration without changes
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

def _require_tty(command_name: str) -> None:
    """Exit with a clear error if stdin is not a terminal.

    Interactive TUI commands (VoidCube tools, VoidCube api, VoidCube model) use
    curses or input() prompts that spin at 100% CPU when stdin is a pipe.
    This guard prevents accidental non-interactive invocation.
    """
    if not sys.stdin.isatty():
        try:
            from VoidCube_cli.i18n import t
            error_msg = t('errors.no_tty', default="Voidcube CLI requires an interactive terminal (TTY). Do not pipe or redirect input.")
        except Exception:
            error_msg = f"Error: 'VoidCube {command_name}' requires an interactive terminal.\nIt cannot be run through a pipe or non-interactive subprocess.\nRun it directly in your terminal instead."
        print(error_msg, file=sys.stderr)
        sys.exit(1)


# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Profile override — MUST happen before any VoidCube module import.
#
# Many modules cache VOIDCUBE_HOME at import time (module-level constants).
# We intercept --profile/-p from sys.argv here and set the env var so that
# every subsequent ``os.getenv("VOIDCUBE_HOME", ...)`` resolves correctly.
# The flag is stripped from sys.argv so argparse never sees it.
# Falls back to ~/.VoidCube/active_profile for sticky default.
# ---------------------------------------------------------------------------
def _apply_profile_override() -> None:
    """Pre-parse --profile/-p and set VOIDCUBE_HOME before module imports."""
    argv = sys.argv[1:]
    profile_name = None
    consume = 0

    # 1. Check for explicit -p / --profile flag
    for i, arg in enumerate(argv):
        if arg in ("--profile", "-p") and i + 1 < len(argv):
            profile_name = argv[i + 1]
            consume = 2
            break
        elif arg.startswith("--profile="):
            profile_name = arg.split("=", 1)[1]
            consume = 1
            break

    # 2. If no flag, check active_profile in the VoidCube root
    if profile_name is None:
        try:
            from VoidCube_core.constants import get_default_VoidCube_root
            active_path = get_default_VoidCube_root() / "active_profile"
            if active_path.exists():
                name = active_path.read_text().strip()
                if name and name != "default":
                    profile_name = name
                    consume = 0  # don't strip anything from argv
        except (UnicodeDecodeError, OSError):
            pass  # corrupted file, skip

    # 3. If we found a profile, resolve and set VOIDCUBE_HOME
    if profile_name is not None:
        try:
            from VoidCube_cli.profiles import resolve_profile_env
            VoidCube_home = resolve_profile_env(profile_name)
        except (ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            # A bug in profiles.py must NEVER prevent VoidCube from starting
            print(f"Warning: profile override failed ({exc}), using default", file=sys.stderr)
            return
        os.environ["VOIDCUBE_HOME"] = VoidCube_home
        # Strip the flag from argv so argparse doesn't choke
        if consume > 0:
            for i, arg in enumerate(argv):
                if arg in ("--profile", "-p"):
                    start = i + 1  # +1 because argv is sys.argv[1:]
                    sys.argv = sys.argv[:start] + sys.argv[start + consume:]
                    break
                elif arg.startswith("--profile="):
                    start = i + 1
                    sys.argv = sys.argv[:start] + sys.argv[start + 1:]
                    break

_apply_profile_override()

# Load .env from ~/.VoidCube/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from VoidCube_core.constants import get_VoidCube_home
from VoidCube_app.environment import load_VoidCube_dotenv
load_VoidCube_dotenv(project_env=PROJECT_ROOT / '.env')

# Initialize centralized file logging early — all `VoidCube` subcommands
# (chat, setup, gateway, config, etc.) write to agent.log + errors.log.
try:
    from VoidCube_core.logging import setup_logging as _setup_logging
    _setup_logging(mode="cli")
except Exception:
    pass  # best-effort — don't crash the CLI if logging setup fails

# Apply IPv4 preference early, before any HTTP clients are created.
try:
    from VoidCube_app.config import load_config as _load_config_early
    from VoidCube_core.constants import apply_ipv4_preference as _apply_ipv4
    _early_cfg = _load_config_early()
    _net = _early_cfg.get("network", {})
    if isinstance(_net, dict) and _net.get("force_ipv4"):
        _apply_ipv4(force=True)
    del _early_cfg, _net
except Exception:
    pass  # best-effort — don't crash if config isn't available yet

import logging
import time as _time
from datetime import datetime

from VoidCube_cli import __version__
from VoidCube_core.constants import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)


