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
    VoidCube autonomous          # Debug the API-A autonomous execution component
    VoidCube cron                # Manage cron jobs
    VoidCube cron list           # List cron jobs
    VoidCube cron status         # Check if cron scheduler is running
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
from VoidCube_cli.env_loader import load_VoidCube_dotenv
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
    from VoidCube_cli.config import load_config as _load_config_early
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


def _relative_time(ts) -> str:
    """Format a timestamp as relative time (e.g., '2h ago', 'yesterday')."""
    if not ts:
        return "?"
    delta = _time.time() - ts
    if delta < 60:
        try:
            from VoidCube_cli.i18n import t
            return t('time.just_now', default='刚刚')
        except Exception:
            return "刚刚"
    if delta < 3600:
        minutes = int(delta / 60)
        try:
            from VoidCube_cli.i18n import t
            return t('time.minutes_ago', count=minutes, default=f'{minutes} 分钟前')
        except Exception:
            return f"{minutes} 分钟前"
    if delta < 86400:
        hours = int(delta / 3600)
        try:
            from VoidCube_cli.i18n import t
            return t('time.hours_ago', count=hours, default=f'{hours} 小时前')
        except Exception:
            return f"{hours} 小时前"
    if delta < 172800:
        try:
            from VoidCube_cli.i18n import t
            return t('time.yesterday', default='昨天')
        except Exception:
            return "昨天"
    if delta < 604800:
        days = int(delta / 86400)
        try:
            from VoidCube_cli.i18n import t
            return t('time.days_ago', count=days, default=f'{days} 天前')
        except Exception:
            return f"{days} 天前"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _session_browse_picker(sessions: list) -> Optional[str]:
    """Interactive curses-based session browser with live search filtering.

    Returns the selected session ID, or None if cancelled.
    Uses curses (not simple_term_menu) to avoid the ghost-duplication rendering
    bug in tmux/iTerm when arrow keys are used.
    """
    if not sessions:
        print("No sessions found.")
        return None

    # Try curses-based picker first
    try:
        import curses

        result_holder = [None]

        def _format_row(s, max_x):
            """Format a session row for display."""
            title = (s.get("title") or "").strip()
            preview = (s.get("preview") or "").strip()
            source = s.get("source", "")[:6]
            last_active = _relative_time(s.get("last_active"))
            sid = s["id"][:18]

            # Adaptive column widths based on terminal width
            # Layout: [arrow 3] [title/preview flexible] [active 12] [src 6] [id 18]
            fixed_cols = 3 + 12 + 6 + 18 + 6  # arrow + active + src + id + padding
            name_width = max(20, max_x - fixed_cols)

            if title:
                name = title[:name_width]
            elif preview:
                name = preview[:name_width]
            else:
                name = sid

            return f"{name:<{name_width}}  {last_active:<10}  {source:<5} {sid}"

        def _match(s, query):
            """Check if a session matches the search query (case-insensitive)."""
            q = query.lower()
            return (
                q in (s.get("title") or "").lower()
                or q in (s.get("preview") or "").lower()
                or q in s.get("id", "").lower()
                or q in (s.get("source") or "").lower()
            )

        def _curses_browse(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)   # selected
                curses.init_pair(2, curses.COLOR_YELLOW, -1)  # header
                curses.init_pair(3, curses.COLOR_CYAN, -1)    # search
                curses.init_pair(4, 8, -1)                    # dim

            cursor = 0
            scroll_offset = 0
            search_text = ""
            filtered = list(sessions)

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()
                if max_y < 5 or max_x < 40:
                    # Terminal too small
                    try:
                        stdscr.addstr(0, 0, "Terminal too small")
                    except curses.error:
                        pass
                    stdscr.refresh()
                    stdscr.getch()
                    return

                # Header line
                if search_text:
                    header = f"  Browse sessions — filter: {search_text}█"
                    header_attr = curses.A_BOLD
                    if curses.has_colors():
                        header_attr |= curses.color_pair(3)
                else:
                    header = "  Browse sessions — ↑↓ navigate  Enter select  Type to filter  Esc quit"
                    header_attr = curses.A_BOLD
                    if curses.has_colors():
                        header_attr |= curses.color_pair(2)
                try:
                    stdscr.addnstr(0, 0, header, max_x - 1, header_attr)
                except curses.error:
                    pass

                # Column header line
                fixed_cols = 3 + 12 + 6 + 18 + 6
                name_width = max(20, max_x - fixed_cols)
                col_header = f"   {'Title / Preview':<{name_width}}  {'Active':<10}  {'Src':<5} {'ID'}"
                try:
                    dim_attr = curses.color_pair(4) if curses.has_colors() else curses.A_DIM
                    stdscr.addnstr(1, 0, col_header, max_x - 1, dim_attr)
                except curses.error:
                    pass

                # Compute visible area
                visible_rows = max_y - 4  # header + col header + blank + footer
                if visible_rows < 1:
                    visible_rows = 1

                # Clamp cursor and scroll
                if not filtered:
                    try:
                        msg = "  No sessions match the filter."
                        stdscr.addnstr(3, 0, msg, max_x - 1, curses.A_DIM)
                    except curses.error:
                        pass
                else:
                    if cursor >= len(filtered):
                        cursor = len(filtered) - 1
                    if cursor < 0:
                        cursor = 0
                    if cursor < scroll_offset:
                        scroll_offset = cursor
                    elif cursor >= scroll_offset + visible_rows:
                        scroll_offset = cursor - visible_rows + 1

                    for draw_i, i in enumerate(range(
                        scroll_offset,
                        min(len(filtered), scroll_offset + visible_rows)
                    )):
                        y = draw_i + 3
                        if y >= max_y - 1:
                            break
                        s = filtered[i]
                        arrow = " → " if i == cursor else "   "
                        row = arrow + _format_row(s, max_x - 3)
                        attr = curses.A_NORMAL
                        if i == cursor:
                            attr = curses.A_BOLD
                            if curses.has_colors():
                                attr |= curses.color_pair(1)
                        try:
                            stdscr.addnstr(y, 0, row, max_x - 1, attr)
                        except curses.error:
                            pass

                # Footer
                footer_y = max_y - 1
                if filtered:
                    footer = f"  {cursor + 1}/{len(filtered)} sessions"
                    if len(filtered) < len(sessions):
                        footer += f" (filtered from {len(sessions)})"
                else:
                    footer = f"  0/{len(sessions)} sessions"
                try:
                    stdscr.addnstr(footer_y, 0, footer, max_x - 1,
                                   curses.color_pair(4) if curses.has_colors() else curses.A_DIM)
                except curses.error:
                    pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ):
                    if filtered:
                        cursor = (cursor - 1) % len(filtered)
                elif key in (curses.KEY_DOWN, ):
                    if filtered:
                        cursor = (cursor + 1) % len(filtered)
                elif key in (curses.KEY_ENTER, 10, 13):
                    if filtered:
                        result_holder[0] = filtered[cursor]["id"]
                    return
                elif key == 27:  # Esc
                    if search_text:
                        # First Esc clears the search
                        search_text = ""
                        filtered = list(sessions)
                        cursor = 0
                        scroll_offset = 0
                    else:
                        # Second Esc exits
                        return
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    if search_text:
                        search_text = search_text[:-1]
                        if search_text:
                            filtered = [s for s in sessions if _match(s, search_text)]
                        else:
                            filtered = list(sessions)
                        cursor = 0
                        scroll_offset = 0
                elif key == ord('q') and not search_text:
                    return
                elif 32 <= key <= 126:
                    # Printable character → add to search filter
                    search_text += chr(key)
                    filtered = [s for s in sessions if _match(s, search_text)]
                    cursor = 0
                    scroll_offset = 0

        curses.wrapper(_curses_browse)
        return result_holder[0]

    except Exception:
        pass

    # Fallback: numbered list (Windows without curses, etc.)
    print("\n  Browse sessions  (enter number to resume, q to cancel)\n")
    for i, s in enumerate(sessions):
        title = (s.get("title") or "").strip()
        preview = (s.get("preview") or "").strip()
        label = title or preview or s["id"]
        if len(label) > 50:
            label = label[:47] + "..."
        last_active = _relative_time(s.get("last_active"))
        src = s.get("source", "")[:6]
        print(f"  {i + 1:>3}. {label:<50}  {last_active:<10}  {src}")

    while True:
        try:
            val = input(f"\n  Select [1-{len(sessions)}]: ").strip()
            if not val or val.lower() in ("q", "quit", "exit"):
                return None
            idx = int(val) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["id"]
            print(f"  Invalid selection. Enter 1-{len(sessions)} or q to cancel.")
        except ValueError:
            print("  Invalid input. Enter a number or q to cancel.")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def _resolve_last_cli_session() -> Optional[str]:
    """Look up the most recent CLI session ID from SQLite. Returns None if unavailable."""
    try:
        from VoidCube_core.state import SessionDB
        db = SessionDB()
        sessions = db.search_sessions(source="cli", limit=1)
        db.close()
        if sessions:
            return sessions[0]["id"]
    except Exception:
        pass
    return None


def _probe_container(cmd: list, backend: str, via_sudo: bool = False):
    """Run a container inspect probe, returning the CompletedProcess.

    Catches TimeoutExpired specifically for a human-readable message;
    all other exceptions propagate naturally.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        label = f"sudo {backend}" if via_sudo else backend
        print(
            f"Error: timed out waiting for {label} to respond.\n"
            f"The {backend} daemon may be unresponsive or starting up.",
            file=sys.stderr,
        )
        sys.exit(1)


def _exec_in_container(container_info: dict, cli_args: list):
    """Replace the current process with a command inside the managed container.

    Probes whether sudo is needed (rootful containers), then os.execvp
    into the container. On success the Python process is replaced entirely
    and the container's exit code becomes the process exit code (OS semantics).
    On failure, OSError propagates naturally.

    Args:
        container_info: dict with backend, container_name, exec_user, VoidCube_bin
        cli_args: the original CLI arguments (everything after 'VoidCube')
    """
    import shutil

    backend = container_info["backend"]
    container_name = container_info["container_name"]
    exec_user = container_info["exec_user"]
    VoidCube_bin = container_info["VoidCube_bin"]

    runtime = shutil.which(backend)
    if not runtime:
        print(f"Error: {backend} not found on PATH. Cannot route to container.",
              file=sys.stderr)
        sys.exit(1)

    # Rootful containers (NixOS systemd service) are invisible to unprivileged
    # users — Podman uses per-user namespaces, Docker needs group access.
    # Probe whether the runtime can see the container; if not, try via sudo.
    sudo_path = None
    probe = _probe_container(
        [runtime, "inspect", "--format", "ok", container_name], backend,
    )
    if probe.returncode != 0:
        sudo_path = shutil.which("sudo")
        if sudo_path:
            probe2 = _probe_container(
                [sudo_path, "-n", runtime, "inspect", "--format", "ok", container_name],
                backend, via_sudo=True,
            )
            if probe2.returncode != 0:
                print(
                    f"Error: container '{container_name}' not found via {backend}.\n"
                    f"\n"
                    f"The container is likely running as root. Your user cannot see it\n"
                    f"because {backend} uses per-user namespaces. Grant passwordless\n"
                    f"sudo for {backend} — the -n (non-interactive) flag is required\n"
                    f"because a password prompt would hang or break piped commands.\n"
                    f"\n"
                    f"On NixOS:\n"
                    f"\n"
                    f'  security.sudo.extraRules = [{{\n'
                    f'    users = [ "{os.getenv("USER", "your-user")}" ];\n'
                    f'    commands = [{{ command = "{runtime}"; options = [ "NOPASSWD" ]; }}];\n'
                    f'  }}];\n'
                    f"\n"
                    f"Or run: sudo VoidCube {' '.join(cli_args)}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            print(
                f"Error: container '{container_name}' not found via {backend}.\n"
                f"The container may be running under root. Try: sudo VoidCube {' '.join(cli_args)}",
                file=sys.stderr,
            )
            sys.exit(1)

    is_tty = sys.stdin.isatty()
    tty_flags = ["-it"] if is_tty else ["-i"]

    env_flags = []
    for var in ("TERM", "COLORTERM", "LANG", "LC_ALL"):
        val = os.environ.get(var)
        if val:
            env_flags.extend(["-e", f"{var}={val}"])

    cmd_prefix = [sudo_path, "-n", runtime] if sudo_path else [runtime]
    exec_cmd = (
        cmd_prefix + ["exec"]
        + tty_flags
        + ["-u", exec_user]
        + env_flags
        + [container_name, VoidCube_bin]
        + cli_args
    )

    os.execvp(exec_cmd[0], exec_cmd)


def _resolve_session_by_name_or_id(name_or_id: str) -> Optional[str]:
    """Resolve a session name (title) or ID to a session ID.

    - If it looks like a session ID (contains underscore + hex), try direct lookup first.
    - Otherwise, treat it as a title and use resolve_session_by_title (auto-latest).
    - Falls back to the other method if the first doesn't match.
    """
    try:
        from VoidCube_core.state import SessionDB
        db = SessionDB()

        # Try as exact session ID first
        session = db.get_session(name_or_id)
        if session:
            db.close()
            return session["id"]

        # Try as title (with auto-latest for lineage)
        session_id = db.resolve_session_by_title(name_or_id)
        db.close()
        return session_id
    except Exception:
        pass
    return None


def cmd_chat(args):
    """Run interactive chat CLI."""
    # Resolve --continue into --resume with the latest CLI session or by name
    continue_val = getattr(args, "continue_last", None)
    if continue_val and not getattr(args, "resume", None):
        if isinstance(continue_val, str):
            # -c "session name" — resolve by title or ID
            resolved = _resolve_session_by_name_or_id(continue_val)
            if resolved:
                args.resume = resolved
            else:
                print(f"No session found matching '{continue_val}'.")
                print("Use 'VoidCube sessions list' to see available sessions.")
                sys.exit(1)
        else:
            # -c with no argument — continue the most recent session
            last_id = _resolve_last_cli_session()
            if last_id:
                args.resume = last_id
            else:
                print("No previous CLI session found to continue.")
                sys.exit(1)

    # Resolve --resume by title if it's not a direct session ID
    resume_val = getattr(args, "resume", None)
    if resume_val:
        resolved = _resolve_session_by_name_or_id(resume_val)
        if resolved:
            args.resume = resolved
        # If resolution fails, keep the original value — _init_agent will
        # report "Session not found" with the original input

    # Sync bundled skills on every CLI launch (fast -- skips unchanged skills)
    try:
        from tools.skills_sync import sync_skills
        sync_skills(quiet=True)
    except Exception:
        pass

    # --yolo: bypass all dangerous command approvals
    if getattr(args, "yolo", False):
        os.environ["VOIDCUBE_YOLO_MODE"] = "1"

    # --source: tag session source for filtering (e.g. 'tool' for third-party integrations)
    if getattr(args, "source", None):
        os.environ["VOIDCUBE_SESSION_SOURCE"] = args.source

    # Import and run the CLI
    from cli import main as cli_main
    
    # Build kwargs from args
    kwargs = {
        "model": args.model,
        "provider": getattr(args, "provider", None),
        "toolsets": args.toolsets,
        "skills": getattr(args, "skills", None),
        "verbose": args.verbose,
        "quiet": getattr(args, "quiet", False),
        "query": args.query,
        "image": getattr(args, "image", None),
        "resume": getattr(args, "resume", None),
        "worktree": getattr(args, "worktree", False),
        "checkpoints": getattr(args, "checkpoints", False),
        "pass_session_id": getattr(args, "pass_session_id", False),
        "max_turns": getattr(args, "max_turns", None),
    }
    # Filter out None values
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    
    try:
        cli_main(**kwargs)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)



def cmd_whatsapp(args):
    """Set up WhatsApp: choose mode, configure, install bridge, pair via QR."""
    _require_tty("whatsapp")
    import subprocess
    from pathlib import Path
    from VoidCube_cli.config import get_env_value, save_env_value

    print()
    print("> WhatsApp Setup")
    print("=" * 50)

    # ── Step 1: Choose mode ──────────────────────────────────────────────
    current_mode = get_env_value("WHATSAPP_MODE") or ""
    if not current_mode:
        print()
        print("How will you use WhatsApp with Voidcube?")
        print()
        print("  1. Separate bot number (recommended)")
        print("     People message the bot's number directly — cleanest experience.")
        print("     Requires a second phone number with WhatsApp installed on a device.")
        print()
        print("  2. Personal number (self-chat)")
        print("     You message yourself to talk to the agent.")
        print("     Quick to set up, but the UX is less intuitive.")
        print()
        try:
            choice = input("  Choose [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            return

        if choice == "1":
            save_env_value("WHATSAPP_MODE", "bot")
            wa_mode = "bot"
            print("  ✓ Mode: separate bot number")
            print()
            print("  ┌─────────────────────────────────────────────────┐")
            print("  │  Getting a second number for the bot:           │")
            print("  │                                                 │")
            print("  │  Easiest: Install WhatsApp Business (free app)  │")
            print("  │  on your phone with a second number:            │")
            print("  │    • Dual-SIM: use your 2nd SIM slot            │")
            print("  │    • Google Voice: free US number (voice.google) │")
            print("  │    • Prepaid SIM: $3-10, verify once            │")
            print("  │                                                 │")
            print("  │  WhatsApp Business runs alongside your personal │")
            print("  │  WhatsApp — no second phone needed.             │")
            print("  └─────────────────────────────────────────────────┘")
        else:
            save_env_value("WHATSAPP_MODE", "self-chat")
            wa_mode = "self-chat"
            print("  ✓ Mode: personal number (self-chat)")
    else:
        wa_mode = current_mode
        mode_label = "separate bot number" if wa_mode == "bot" else "personal number (self-chat)"
        print(f"\n✓ Mode: {mode_label}")

    # ── Step 2: Enable WhatsApp ──────────────────────────────────────────
    print()
    current = get_env_value("WHATSAPP_ENABLED")
    if current and current.lower() == "true":
        print("✓ WhatsApp is already enabled")
    else:
        save_env_value("WHATSAPP_ENABLED", "true")
        print("✓ WhatsApp enabled")

    # ── Step 3: Allowed users ────────────────────────────────────────────
    current_users = get_env_value("WHATSAPP_ALLOWED_USERS") or ""
    if current_users:
        print(f"✓ Allowed users: {current_users}")
        try:
            response = input("\n  Update allowed users? [y/N] ").strip()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response.lower() in ("y", "yes"):
            if wa_mode == "bot":
                phone = input("  Phone numbers that can message the bot (comma-separated): ").strip()
            else:
                phone = input("  Your phone number (e.g. 15551234567): ").strip()
            if phone:
                save_env_value("WHATSAPP_ALLOWED_USERS", phone.replace(" ", ""))
                print(f"  ✓ Updated to: {phone}")
    else:
        print()
        if wa_mode == "bot":
            print("  Who should be allowed to message the bot?")
            phone = input("  Phone numbers (comma-separated, or * for anyone): ").strip()
        else:
            phone = input("  Your phone number (e.g. 15551234567): ").strip()
        if phone:
            save_env_value("WHATSAPP_ALLOWED_USERS", phone.replace(" ", ""))
            print(f"  ✓ Allowed users set: {phone}")
        else:
            print("  ⚠ No allowlist — the agent will respond to ALL incoming messages")

    # ── Step 4: Install bridge dependencies ──────────────────────────────
    project_root = Path(__file__).resolve().parents[1]
    bridge_dir = project_root / "scripts" / "whatsapp-bridge"
    bridge_script = bridge_dir / "bridge.js"

    if not bridge_script.exists():
        print(f"\n✗ Bridge script not found at {bridge_script}")
        return

    if not (bridge_dir / "node_modules").exists():
        print("\n→ Installing WhatsApp bridge dependencies...")
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(bridge_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"  ✗ npm install failed: {result.stderr}")
            return
        print("  ✓ Dependencies installed")
    else:
        print("✓ Bridge dependencies already installed")

    # ── Step 5: Check for existing session ───────────────────────────────
    session_dir = get_VoidCube_home() / "whatsapp" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    if (session_dir / "creds.json").exists():
        print("✓ Existing WhatsApp session found")
        try:
            response = input("\n  Re-pair? This will clear the existing session. [y/N] ").strip()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response.lower() in ("y", "yes"):
            import shutil
            shutil.rmtree(session_dir, ignore_errors=True)
            session_dir.mkdir(parents=True, exist_ok=True)
            print("  ✓ Session cleared")
        else:
            print("\n✓ WhatsApp is configured and paired!")
            print("  Start the gateway with: VoidCube gateway")
            return

    # ── Step 6: QR code pairing ──────────────────────────────────────────
    print()
    print("─" * 50)
    if wa_mode == "bot":
        print("📱 Open WhatsApp (or WhatsApp Business) on the")
        print("   phone with the BOT's number, then scan:")
    else:
        print("📱 Open WhatsApp on your phone, then scan:")
    print()
    print("   Settings → Linked Devices → Link a Device")
    print("─" * 50)
    print()

    try:
        subprocess.run(
            ["node", str(bridge_script), "--pair-only", "--session", str(session_dir)],
            cwd=str(bridge_dir),
        )
    except KeyboardInterrupt:
        pass

    # ── Step 7: Post-pairing ─────────────────────────────────────────────
    print()
    if (session_dir / "creds.json").exists():
        print(f"✓ {t('auth.whatsapp_paired', default='WhatsApp paired successfully!')}")
        print()
        if wa_mode == "bot":
            print(f"  {t('auth.next_steps', default='Next steps:')}")
            print("    1. Start the gateway:  VoidCube gateway")
            print("    2. Send a message to the bot's WhatsApp number")
            print("    3. The agent will reply automatically")
            print()
            try:
                from VoidCube_cli.i18n import t
                _default_msg = "Tip: Agent responses are prefixed with '> Voidcube Agent'"
                print(f"  {t('tips.agent_prefix', prefix='> Voidcube Agent', default=_default_msg)}")
            except Exception:
                _default_msg2 = "Tip: Agent responses are prefixed with '> Voidcube Agent'"
                print(f"  {t('tips.agent_prefix', prefix='> Voidcube Agent', default=_default_msg2)}")
        else:
            print(f"  {t('auth.next_steps', default='Next steps:')}")
            print("    1. Start the gateway:  VoidCube gateway")
            print("    2. Open WhatsApp → Message Yourself")
            print("    3. Type a message — the agent will reply")
            print()
            try:
                from VoidCube_cli.i18n import t
                _default_msg = "Tip: Agent responses are prefixed with '> Voidcube Agent'"
                print(f"  {t('tips.agent_prefix', prefix='> Voidcube Agent', default=_default_msg)}")
            except Exception:
                _default_msg2 = "Tip: Agent responses are prefixed with '> Voidcube Agent'"
                print(f"  {t('tips.agent_prefix', prefix='> Voidcube Agent', default=_default_msg2)}")
            print("  so you can tell them apart from your own messages.")
        print()
        print("  Or install as a service: VoidCube gateway install")
    else:
        print("⚠ Pairing may not have completed. Run 'VoidCube whatsapp' to try again.")



def cmd_model(args):
    """Switch model/provider within the configured provider list."""
    _require_tty("model")
    select_provider_and_model(args=args)


def select_provider_and_model(args=None):
    """Switch active provider/model only within the saved provider config."""
    from VoidCube_cli.config import (
        get_active_provider_key,
        get_configured_providers,
        load_config,
        save_config,
        set_active_provider,
        set_provider_model,
    )
    from VoidCube_cli.models import curated_models_for_provider

    config = load_config()
    providers = get_configured_providers(config)
    active_provider = get_active_provider_key(config)

    if not providers:
        print()
        print("No configured providers found.")
        print("Run `VoidCube api` first to add a provider, then use `VoidCube model` to switch.")
        print()
        return

    ordered_keys = sorted(
        providers.keys(),
        key=lambda key: (key != active_provider, str(providers.get(key, {}).get("label") or key).lower()),
    )
    provider_choices = []
    default_idx = 0
    for idx, provider_key in enumerate(ordered_keys):
        provider_cfg = providers.get(provider_key, {})
        label = str(provider_cfg.get("label") or provider_key)
        current_model = str(provider_cfg.get("selected_model") or "").strip()
        suffix = f" [{current_model}]" if current_model else ""
        if provider_key == active_provider:
            provider_choices.append(f"{label} ({provider_key}){suffix}  ← active")
            default_idx = idx
        else:
            provider_choices.append(f"{label} ({provider_key}){suffix}")

    print()
    print(f"  Active provider:  {active_provider or 'not configured'}")
    current_active_model = ""
    if active_provider and active_provider in providers:
        current_active_model = str(providers[active_provider].get("selected_model") or "").strip()
    print(f"  Active model:     {current_active_model or 'not set'}")
    print()

    provider_idx = _prompt_provider_choice(provider_choices, default=default_idx)
    if provider_idx is None:
        print("No change.")
        return

    selected_provider = ordered_keys[provider_idx]
    provider_cfg = providers.get(selected_provider, {})
    saved_model = str(provider_cfg.get("selected_model") or "").strip()

    curated_models = []
    try:
        curated_models = [mid for mid, _ in curated_models_for_provider(selected_provider)]
    except Exception:
        curated_models = []

    model_choices: list[str] = []
    if saved_model:
        model_choices.append(saved_model)
    for model_id in curated_models:
        if model_id and model_id not in model_choices:
            model_choices.append(model_id)
    model_choices = model_choices[:20]

    print(f"Selected provider: {selected_provider}")
    if provider_cfg.get("base_url"):
        print(f"Endpoint: {provider_cfg.get('base_url')}")
    print()

    selected_model = saved_model
    if model_choices:
        numbered_choices = list(model_choices)
        numbered_choices.append("Enter custom model name")
        numbered_choices.append("Cancel")
        model_idx = _prompt_provider_choice(
            [
                f"{choice}  ← current" if choice == saved_model and saved_model else choice
                for choice in numbered_choices
            ],
            default=0,
        )
        if model_idx is None or model_idx == len(numbered_choices) - 1:
            print("No change.")
            return
        if model_idx == len(numbered_choices) - 2:
            try:
                selected_model = input("Model name: ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                print("No change.")
                return
        else:
            selected_model = numbered_choices[model_idx]
    else:
        prompt = "Model name"
        if saved_model:
            prompt += f" [{saved_model}]"
        prompt += ": "
        try:
            entered = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print("No change.")
            return
        selected_model = entered or saved_model

    if not selected_model:
        print("No model selected. Run `VoidCube api` to configure a provider or choose a model here.")
        return

    config = set_provider_model(config, selected_provider, selected_model, make_active=True)
    config = set_active_provider(config, selected_provider)
    save_config(config)

    print()
    print(f"Saved active provider: {selected_provider}")
    print(f"Saved active model:    {selected_model}")
    print()


def _prompt_provider_choice(choices, *, default=0):
    """Show provider selection menu with curses arrow-key navigation.

    Falls back to a numbered list when curses is unavailable (e.g. piped
    stdin, non-TTY environments).  Returns the selected index, or None
    if the user cancels.
    """
    try:
        from VoidCube_cli.curses_ui import curses_single_select
        idx = curses_single_select("Select provider:", choices, default)
        if idx is not None:
            print()
            return idx
    except Exception:
        pass

    # Fallback: numbered list
    print("Select provider:")
    for i, c in enumerate(choices, 1):
        marker = "→" if i - 1 == default else " "
        print(f"  {marker} {i}. {c}")
    print()
    while True:
        try:
            val = input(f"Choice [1-{len(choices)}] ({default + 1}): ").strip()
            if not val:
                return default
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            print(f"Please enter 1-{len(choices)}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def cmd_login(args):
    """Authenticate Voidcube CLI with a provider."""
    from VoidCube_cli.auth import login_command
    login_command(args)


def cmd_logout(args):
    """Clear provider authentication."""
    from VoidCube_cli.auth import logout_command
    logout_command(args)




def cmd_status(args):
    """Show status of all components."""
    from VoidCube_cli.status import show_status
    show_status(args)


def cmd_autonomous(args):
    """Run a debug surface for the embedded API-A autonomous component."""
    from VoidCube_cli.autonomous_runner import run_autonomous_component_debug

    run_autonomous_component_debug(
        model=getattr(args, "model", None),
        provider=getattr(args, "provider", None),
        interval=float(getattr(args, "interval", 2.0) or 2.0),
        once=bool(getattr(args, "once", False)),
        clear=not bool(getattr(args, "no_clear", False)),
        show_idle=bool(getattr(args, "show_idle", False)),
    )


def cmd_doctor(args):
    """Run configuration and agent runtime diagnostics."""
    from VoidCube_cli.config_validator import print_diagnosis

    print_diagnosis()


def _print_ops_json(payload) -> None:
    import json as _json
    print(_json.dumps(payload, ensure_ascii=False, indent=2))


def _executor_ops_client(args):
    from VoidCube_cli.ops.executor import ExecutorOpsClient

    return ExecutorOpsClient(
        gateway_url=getattr(args, "gateway_url", None) or "http://127.0.0.1:8000",
        timeout=float(getattr(args, "timeout", 30.0) or 30.0),
    )


def _exit_executor_ops_error(command_name: str, exc: Exception) -> None:
    import requests

    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    detail = str(exc).strip()

    if isinstance(exc, requests.HTTPError):
        if status_code is not None:
            print(f"{command_name} failed: executor returned HTTP {status_code}.", file=sys.stderr)
        else:
            print(f"{command_name} failed: executor request failed.", file=sys.stderr)
    else:
        print(f"{command_name} failed: executor route unavailable.", file=sys.stderr)

    if detail:
        print(detail, file=sys.stderr)

    print("Check the gateway / executor chain and try again.", file=sys.stderr)
    raise SystemExit(1)


def cmd_body(args):
    """Body lifecycle operations routed through gateway executor."""
    import requests

    client = _executor_ops_client(args)
    action = getattr(args, "body_action", None)

    try:
        if action == "status":
            result = {
                "registry": client.get_body_registry(),
                "active_target": client.get_active_body_target(),
            }
            _print_ops_json(result)
            return

        if action == "upgrade":
            payload = {
                "body_version": getattr(args, "body_version", None),
                "watch_window_seconds": getattr(args, "watch_window_seconds", None),
            }
            payload = {key: value for key, value in payload.items() if value is not None}
            result = client.execute_body_upgrade(payload)
            _print_ops_json(result)
            return

        if action == "consent":
            payload = {
                "slot_id": getattr(args, "slot_id", None),
                "approved": True,
                "watch_window_seconds": getattr(args, "watch_window_seconds", None),
            }
            payload = {key: value for key, value in payload.items() if value is not None}
            result = client.confirm_body_switch(payload)
            _print_ops_json(result)
            return
    except requests.RequestException as exc:
        _exit_executor_ops_error("Body command", exc)

    print("Unknown body command. Use: VoidCube body --help")


def cmd_serve(args):
    """Integrated system launcher — start/stop/status of gateway + supervisor."""
    from VoidCube_cli.ops.serve import start_all, stop_all, print_status

    action = getattr(args, "serve_action", None) or "status"
    if action == "start":
        foreground = getattr(args, "foreground", False)
        start_all(foreground=foreground)
    elif action == "stop":
        stop_all()
    else:
        print_status()


def cmd_debug(args):
    """Debug tools (share report, etc.)."""
    from VoidCube_cli.debug import run_debug
    run_debug(args)


def cmd_config(args):
    """Configuration management."""
    from VoidCube_cli.config_commands import config_command
    config_command(args)






def cmd_version(args):
    """Show version."""
    print(f"Voidcube Agent v{__version__}")
    print(f"Project: {PROJECT_ROOT}")
    
    # Show Python version
    print(f"Python: {sys.version.split()[0]}")
    
    # Check for key dependencies
    try:
        import openai
        print(f"OpenAI SDK: {openai.__version__}")
    except ImportError:
        print("OpenAI SDK: Not installed")

def _coalesce_session_name_args(argv: list) -> list:
    """Join unquoted multi-word session names after -c/--continue and -r/--resume.

    When a user types ``VoidCube -c Pokemon Agent Dev`` without quoting the
    session name, argparse sees three separate tokens.  This function merges
    them into a single argument so argparse receives
    ``['-c', 'Pokemon Agent Dev']`` instead.

    Tokens are collected after the flag until we hit another flag (``-*``)
    or a known top-level subcommand.
    """
    _SUBCOMMANDS = {
        "chat", "model", "gateway", "whatsapp", "login", "logout",
        "body", "agent", "serve",
        "status", "doctor", "config", "tools",
        "mcp", "sessions", "insights", "version",
        "api", "acp", "logs", "memory", "profile", "update", "uninstall",
    }
    _SESSION_FLAGS = {"-c", "--continue", "-r", "--resume"}

    result = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _SESSION_FLAGS:
            result.append(token)
            i += 1
            # Collect subsequent non-flag, non-subcommand tokens as one name
            parts: list = []
            while i < len(argv) and not argv[i].startswith("-") and argv[i] not in _SUBCOMMANDS:
                parts.append(argv[i])
                i += 1
            if parts:
                result.append(" ".join(parts))
        else:
            result.append(token)
            i += 1
    return result




def cmd_logs(args):
    """View and filter Voidcube log files."""
    from VoidCube_cli.logs import tail_log, list_logs

    log_name = getattr(args, "log_name", "agent") or "agent"

    if log_name == "list":
        list_logs()
        return

    tail_log(
        log_name,
        num_lines=getattr(args, "lines", 50),
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        session=getattr(args, "session", None),
        since=getattr(args, "since", None),
        component=getattr(args, "component", None),
    )


def main():
    """Main entry point for VoidCube CLI."""
    # Initialize i18n system
    try:
        from VoidCube_cli.i18n import init_i18n
        init_i18n()
    except Exception:
        pass  # Don't break CLI if i18n fails
    
    parser = argparse.ArgumentParser(
        prog="VoidCube",
        description="Voidcube Agent - AI assistant with tool-calling capabilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    VoidCube                        Start interactive chat
    VoidCube chat -q "Hello"        Single query mode
    VoidCube -c                     Resume the most recent session
    VoidCube -c "my project"        Resume a session by name (latest in lineage)
    VoidCube --resume <session_id>  Resume a specific session by ID
    VoidCube logout                 Clear stored authentication
    VoidCube model                  Select default model
    VoidCube config                 View configuration
    VoidCube config edit            Edit config in $EDITOR
    VoidCube config set model gpt-4 Set a config value
    VoidCube -s VoidCube-agent-dev,github-auth
    VoidCube -w                     Start in isolated git worktree
    VoidCube sessions list          List past sessions
    VoidCube sessions browse        Interactive session picker
    VoidCube sessions rename ID T   Rename/title a session
    VoidCube logs                   View agent.log (last 50 lines)
    VoidCube logs -f                Follow agent.log in real time
    VoidCube logs errors            View errors.log
    VoidCube logs --since 1h        Lines from the last hour
    VoidCube debug share             Upload debug report for support
    VoidCube update                 Update to latest version

For more help on a command:
    VoidCube <command> --help
"""
    )
    
    parser.add_argument(
        "--version", "-V",
        action="store_true",
        help="Show version and exit"
    )
    parser.add_argument(
        "--resume", "-r",
        metavar="SESSION",
        default=None,
        help="Resume a previous session by ID or title"
    )
    parser.add_argument(
        "--continue", "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=None,
        metavar="SESSION_NAME",
        help="Resume a session by name, or the most recent if no name given"
    )
    parser.add_argument(
        "--worktree", "-w",
        action="store_true",
        default=False,
        help="Run in an isolated git worktree (for parallel agents)"
    )
    parser.add_argument(
        "--skills", "-s",
        action="append",
        default=None,
        help="Preload one or more skills for the session (repeat flag or comma-separate)"
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        default=False,
        help="Bypass all dangerous command approval prompts (use at your own risk)"
    )
    parser.add_argument(
        "--pass-session-id",
        action="store_true",
        default=False,
        help="Include the session ID in the agent's system prompt"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # =========================================================================
    # chat command
    # =========================================================================
    chat_parser = subparsers.add_parser(
        "chat",
        help="Interactive chat with the agent",
        description="Start an interactive chat session with Voidcube Agent"
    )
    chat_parser.add_argument(
        "-q", "--query",
        help="Single query (non-interactive mode)"
    )
    chat_parser.add_argument(
        "--image",
        help="Optional local image path to attach to a single query"
    )
    chat_parser.add_argument(
        "-m", "--model",
        help="Model to use (e.g., deepseek/deepseek-chat)"
    )
    chat_parser.add_argument(
        "-t", "--toolsets",
        help="Comma-separated toolsets to enable"
    )
    chat_parser.add_argument(
        "-s", "--skills",
        action="append",
        default=argparse.SUPPRESS,
        help="Preload one or more skills for the session (repeat flag or comma-separate)"
    )
    chat_parser.add_argument(
        "--provider",
        choices=["auto", "openrouter", "nous", "copilot-acp", "copilot", "gemini", "huggingface", "zai", "kimi-coding", "minimax", "minimax-cn", "kilocode", "xiaomi"],
        default=None,
        help="Inference provider (default: auto)"
    )
    chat_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    chat_parser.add_argument(
        "-Q", "--quiet",
        action="store_true",
        help="Quiet mode for programmatic use: suppress banner, spinner, and tool previews. Only output the final response and session info."
    )
    chat_parser.add_argument(
        "--resume", "-r",
        metavar="SESSION_ID",
        default=argparse.SUPPRESS,
        help="Resume a previous session by ID (shown on exit)"
    )
    chat_parser.add_argument(
        "--continue", "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=argparse.SUPPRESS,
        metavar="SESSION_NAME",
        help="Resume a session by name, or the most recent if no name given"
    )
    chat_parser.add_argument(
        "--worktree", "-w",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run in an isolated git worktree (for parallel agents on the same repo)"
    )
    chat_parser.add_argument(
        "--checkpoints",
        action="store_true",
        default=False,
        help="Enable filesystem checkpoints before destructive file operations (use /rollback to restore)"
    )
    chat_parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="Maximum tool-calling iterations per conversation turn (default: 90, or agent.max_turns in config)"
    )
    chat_parser.add_argument(
        "--yolo",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Bypass all dangerous command approval prompts (use at your own risk)"
    )
    chat_parser.add_argument(
        "--pass-session-id",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Include the session ID in the agent's system prompt"
    )
    chat_parser.add_argument(
        "--source",
        default=None,
        help="Session source tag for filtering (default: cli). Use 'tool' for third-party integrations that should not appear in user session lists."
    )
    chat_parser.set_defaults(func=cmd_chat)

    # =========================================================================
    # model command
    # =========================================================================
    model_parser = subparsers.add_parser(
        "model",
        help="Switch model or active provider from saved configuration",
        description="Switch among providers already configured by `VoidCube api`"
    )
    model_parser.add_argument(
        "--portal-url",
        help="Portal base URL for Nous login (default: production portal)"
    )
    model_parser.add_argument(
        "--inference-url",
        help="Inference API base URL for Nous login (default: production inference API)"
    )
    model_parser.add_argument(
        "--client-id",
        default=None,
        help="OAuth client id to use for Nous login (default: VoidCube-cli)"
    )
    model_parser.add_argument(
        "--scope",
        default=None,
        help="OAuth scope to request for Nous login"
    )
    model_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically during Nous login"
    )
    model_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds for Nous login (default: 15)"
    )
    model_parser.add_argument(
        "--ca-bundle",
        help="Path to CA bundle PEM file for Nous TLS verification"
    )
    model_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for Nous login (testing only)"
    )
    model_parser.set_defaults(func=cmd_model)

    # =========================================================================
    # whatsapp command
    # =========================================================================
    whatsapp_parser = subparsers.add_parser(
        "whatsapp",
        help="Set up WhatsApp integration",
        description="Configure WhatsApp and pair via QR code"
    )
    whatsapp_parser.set_defaults(func=cmd_whatsapp)

    # =========================================================================
    # login command
    # =========================================================================
    login_parser = subparsers.add_parser(
        "login",
        help="Authenticate with an inference provider",
        description="Run OAuth device authorization flow for Voidcube CLI"
    )
    login_parser.add_argument(
        "--provider",
        choices=["nous"],
        default=None,
        help="Provider to authenticate with (default: nous)"
    )
    login_parser.add_argument(
        "--portal-url",
        help="Portal base URL (default: production portal)"
    )
    login_parser.add_argument(
        "--inference-url",
        help="Inference API base URL (default: production inference API)"
    )
    login_parser.add_argument(
        "--client-id",
        default=None,
        help="OAuth client id to use (default: VoidCube-cli)"
    )
    login_parser.add_argument(
        "--scope",
        default=None,
        help="OAuth scope to request"
    )
    login_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically"
    )
    login_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds (default: 15)"
    )
    login_parser.add_argument(
        "--ca-bundle",
        help="Path to CA bundle PEM file for TLS verification"
    )
    login_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (testing only)"
    )
    login_parser.set_defaults(func=cmd_login)

    # =========================================================================
    # logout command
    # =========================================================================
    logout_parser = subparsers.add_parser(
        "logout",
        help="Clear authentication for an inference provider",
        description="Remove stored credentials and reset provider config"
    )
    logout_parser.add_argument(
        "--provider",
        choices=["nous"],
        default=None,
        help="Provider to log out from (default: active provider)"
    )
    logout_parser.set_defaults(func=cmd_logout)

    # =========================================================================
    # status command
    # =========================================================================
    status_parser = subparsers.add_parser(
        "status",
        help="Show status of all components",
        description="Display status of Voidcube Agent components"
    )
    status_parser.add_argument(
        "--all",
        action="store_true",
        help="Show all details (redacted for sharing)"
    )
    status_parser.add_argument(
        "--deep",
        action="store_true",
        help="Run deep checks (may take longer)"
    )
    status_parser.set_defaults(func=cmd_status)

    autonomous_parser = subparsers.add_parser(
        "autonomous",
        aliases=["auto-cli"],
        help="Debug the embedded API-A autonomous execution component",
        description="Debug-only surface for autonomous-chain API-A task execution and observation; normal use is /auto inside the main CLI.",
    )
    autonomous_parser.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds")
    autonomous_parser.add_argument("--once", action="store_true", help="Poll once, then exit; no output when idle unless --show-idle is set")
    autonomous_parser.add_argument("--no-clear", action="store_true", help="Do not clear the terminal between refreshes")
    autonomous_parser.add_argument("--show-idle", action="store_true", help="Show the idle observation panel for debugging")
    autonomous_parser.add_argument("-m", "--model", help="Model override for the autonomous API-A executor")
    autonomous_parser.add_argument(
        "--provider",
        choices=["auto", "openrouter", "nous", "copilot-acp", "copilot", "gemini", "huggingface", "zai", "kimi-coding", "minimax", "minimax-cn", "kilocode", "xiaomi"],
        default=None,
        help="Inference provider for the autonomous API-A executor",
    )
    autonomous_parser.set_defaults(func=cmd_autonomous)

    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run configuration and agent runtime diagnostics",
        description="Check configuration, tool registration, backend health, and agent tool-call smoke paths",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    # =========================================================================
    # body command
    # =========================================================================
    body_parser = subparsers.add_parser(
        "body",
        help="Operate body lifecycle through gateway executor",
        description=(
            "Inspect or upgrade VoidCube body slots through gateway /api/executor."
        ),
    )
    body_parser.add_argument(
        "--gateway-url",
        default="http://127.0.0.1:8000",
        help="Gateway base URL (default: http://127.0.0.1:8000)",
    )
    body_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30)",
    )
    body_subparsers = body_parser.add_subparsers(dest="body_action")

    body_subparsers.add_parser(
        "status",
        help="Show body registry and active launch target",
    )
    body_upgrade = body_subparsers.add_parser(
        "upgrade",
        help="Run the executor body upgrade pipeline via the preferred gateway path",
    )
    body_upgrade.add_argument(
        "--body-version",
        default=None,
        help="Candidate body version label",
    )
    body_upgrade.add_argument(
        "--watch-window-seconds",
        type=int,
        default=None,
        help="Watch-window duration after switch",
    )
    body_consent = body_subparsers.add_parser(
        "consent",
        help="Approve activating a probe-passed body slot waiting at the user-consent gate",
    )
    body_consent.add_argument(
        "--slot-id",
        default=None,
        help="Body slot to activate; optional when exactly one slot is awaiting consent",
    )
    body_consent.add_argument(
        "--watch-window-seconds",
        type=int,
        default=None,
        help="Watch-window duration after switch",
    )
    body_parser.set_defaults(func=cmd_body)

    # =========================================================================
    # serve command — integrated system launcher (Phase 1 multi-process)
    # =========================================================================
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start or manage VoidCube background services (gateway + supervisor)",
        description="Launch the full VoidCube multi-process system: gateway, supervisor.",
    )
    serve_subparsers = serve_parser.add_subparsers(dest="serve_action")
    serve_start = serve_subparsers.add_parser("start", help="Start all services in background")
    serve_start.add_argument("--foreground", action="store_true", help="Run in foreground (single-process)")
    serve_stop = serve_subparsers.add_parser("stop", help="Stop all running services")
    serve_status = serve_subparsers.add_parser("status", help="Show service status")
    serve_parser.set_defaults(func=cmd_serve)

    # =========================================================================
    # debug command
    # =========================================================================
    debug_parser = subparsers.add_parser(
        "debug",
        help="Debug tools — upload logs and system info for support",
        description="Debug utilities for Voidcube Agent. Use 'VoidCube debug share' to "
                    "upload a debug report (system info + recent logs) to a paste "
                    "service and get a shareable URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    VoidCube debug share              Upload debug report and print URL
    VoidCube debug share --lines 500  Include more log lines
    VoidCube debug share --expire 30  Keep paste for 30 days
    VoidCube debug share --local      Print report locally (no upload)
""",
    )
    debug_sub = debug_parser.add_subparsers(dest="debug_command")
    share_parser = debug_sub.add_parser(
        "share",
        help="Upload debug report to a paste service and print a shareable URL",
    )
    share_parser.add_argument(
        "--lines", type=int, default=200,
        help="Number of log lines to include per log file (default: 200)",
    )
    share_parser.add_argument(
        "--expire", type=int, default=7,
        help="Paste expiry in days (default: 7)",
    )
    share_parser.add_argument(
        "--local", action="store_true",
        help="Print the report locally instead of uploading",
    )
    debug_parser.set_defaults(func=cmd_debug)

    # =========================================================================
    # config command
    # =========================================================================
    config_parser = subparsers.add_parser(
        "config",
        help="View and edit configuration",
        description="Manage Voidcube Agent configuration"
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    
    # config show (default)
    config_subparsers.add_parser("show", help="Show current configuration")
    
    # config edit
    config_subparsers.add_parser("edit", help="Open config file in editor")
    
    # config set
    config_set = config_subparsers.add_parser("set", help="Set a configuration value")
    config_set.add_argument("key", nargs="?", help="Configuration key (e.g., model, terminal.backend)")
    config_set.add_argument("value", nargs="?", help="Value to set")
    
    # config path
    config_subparsers.add_parser("path", help="Print config file path")
    
    # config env-path
    config_subparsers.add_parser("env-path", help="Print .env file path")
    
    # config check
    config_subparsers.add_parser("check", help="Check for missing/outdated config")
    
    # config migrate
    config_subparsers.add_parser("migrate", help="Update config with new options")
    
    config_parser.set_defaults(func=cmd_config)
    
    # =========================================================================
    # memory command
    # =========================================================================
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Available providers: honcho, openviking, mem0, hindsight,\n"
            "holographic, retaindb, byterover.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    memory_sub.add_parser("setup", help="Interactive provider selection and configuration")
    memory_sub.add_parser("status", help="Show current memory provider config")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")

    def cmd_memory(args):
        sub = getattr(args, "memory_command", None)
        if sub == "off":
            from VoidCube_cli.config import load_config, save_config
            config = load_config()
            if not isinstance(config.get("memory"), dict):
                config["memory"] = {}
            config["memory"]["provider"] = ""
            save_config(config)
            print("\n  ✓ Memory provider: built-in only")
            print("  Saved to config.yaml\n")
        else:
            from VoidCube_cli.memory_setup import memory_command
            memory_command(args)

    memory_parser.set_defaults(func=cmd_memory)

    # =========================================================================
    # tools command
    # =========================================================================
    tools_parser = subparsers.add_parser(
        "tools",
        help="Configure which tools are enabled per platform",
        description=(
            "Enable, disable, or list tools for CLI, Telegram, Discord, etc.\n\n"
            "Built-in toolsets use plain names (e.g. web, memory).\n"
            "MCP tools use server:tool notation (e.g. github:create_issue).\n\n"
            "Run 'VoidCube tools' with no subcommand for the interactive configuration UI."
        ),
    )
    tools_parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of enabled tools per platform and exit"
    )
    tools_sub = tools_parser.add_subparsers(dest="tools_action")

    # VoidCube tools list [--platform cli]
    tools_list_p = tools_sub.add_parser(
        "list",
        help="Show all tools and their enabled/disabled status",
    )
    tools_list_p.add_argument(
        "--platform", default="cli",
        help="Platform to show (default: cli)",
    )

    # VoidCube tools disable <name...> [--platform cli]
    tools_disable_p = tools_sub.add_parser(
        "disable",
        help="Disable toolsets or MCP tools",
    )
    tools_disable_p.add_argument(
        "names", nargs="+", metavar="NAME",
        help="Toolset name (e.g. web) or MCP tool in server:tool form",
    )
    tools_disable_p.add_argument(
        "--platform", default="cli",
        help="Platform to apply to (default: cli)",
    )

    # VoidCube tools enable <name...> [--platform cli]
    tools_enable_p = tools_sub.add_parser(
        "enable",
        help="Enable toolsets or MCP tools",
    )
    tools_enable_p.add_argument(
        "names", nargs="+", metavar="NAME",
        help="Toolset name or MCP tool in server:tool form",
    )
    tools_enable_p.add_argument(
        "--platform", default="cli",
        help="Platform to apply to (default: cli)",
    )

    def cmd_tools(args):
        action = getattr(args, "tools_action", None)
        if action in ("list", "disable", "enable"):
            from VoidCube_cli.tools_config import tools_disable_enable_command
            tools_disable_enable_command(args)
        else:
            _require_tty("tools")
            from VoidCube_cli.tools_config import tools_command
            tools_command(args)

    tools_parser.set_defaults(func=cmd_tools)
    # =========================================================================
    # mcp command — manage MCP server connections
    # =========================================================================
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Manage MCP servers and run Voidcube as an MCP server",
        description=(
            "Manage MCP server connections and run Voidcube as an MCP server.\n\n"
            "MCP servers provide additional tools via the Model Context Protocol.\n"
            "Use 'VoidCube mcp add' to connect to a new server, or\n"
            "'VoidCube mcp serve' to expose Voidcube conversations over MCP."
        ),
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")

    mcp_serve_p = mcp_sub.add_parser(
        "serve",
        help="Run Voidcube as an MCP server (expose conversations to other agents)",
    )
    mcp_serve_p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging on stderr",
    )

    mcp_add_p = mcp_sub.add_parser("add", help="Add an MCP server (discovery-first install)")
    mcp_add_p.add_argument("name", help="Server name (used as config key)")
    mcp_add_p.add_argument("--url", help="HTTP/SSE endpoint URL")
    mcp_add_p.add_argument("--command", help="Stdio command (e.g. npx)")
    mcp_add_p.add_argument("--args", nargs="*", default=[], help="Arguments for stdio command")
    mcp_add_p.add_argument("--auth", choices=["oauth", "header"], help="Auth method")
    mcp_add_p.add_argument("--preset", help="Known MCP preset name")
    mcp_add_p.add_argument("--env", nargs="*", default=[], help="Environment variables for stdio servers (KEY=VALUE)")

    mcp_rm_p = mcp_sub.add_parser("remove", aliases=["rm"], help="Remove an MCP server")
    mcp_rm_p.add_argument("name", help="Server name to remove")

    mcp_sub.add_parser("list", aliases=["ls"], help="List configured MCP servers")

    mcp_test_p = mcp_sub.add_parser("test", help="Test MCP server connection")
    mcp_test_p.add_argument("name", help="Server name to test")

    mcp_cfg_p = mcp_sub.add_parser("configure", aliases=["config"], help="Toggle tool selection")
    mcp_cfg_p.add_argument("name", help="Server name to configure")

    def cmd_mcp(args):
        from VoidCube_cli.mcp_config import mcp_command
        mcp_command(args)

    mcp_parser.set_defaults(func=cmd_mcp)

    # =========================================================================
    # sessions command
    # =========================================================================
    sessions_parser = subparsers.add_parser(
        "sessions",
        help="Manage session history (list, rename, export, prune, delete)",
        description="View and manage the SQLite session store"
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

    sessions_list = sessions_subparsers.add_parser("list", help="List recent sessions")
    sessions_list.add_argument("--source", help="Filter by source (cli, telegram, discord, etc.)")
    sessions_list.add_argument("--limit", type=int, default=20, help="Max sessions to show")

    sessions_export = sessions_subparsers.add_parser("export", help="Export sessions to a JSONL file")
    sessions_export.add_argument("output", help="Output JSONL file path (use - for stdout)")
    sessions_export.add_argument("--source", help="Filter by source")
    sessions_export.add_argument("--session-id", help="Export a specific session")

    sessions_delete = sessions_subparsers.add_parser("delete", help="Delete a specific session")
    sessions_delete.add_argument("session_id", help="Session ID to delete")
    sessions_delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    sessions_prune = sessions_subparsers.add_parser("prune", help="Delete old sessions")
    sessions_prune.add_argument("--older-than", type=int, default=90, help="Delete sessions older than N days (default: 90)")
    sessions_prune.add_argument("--source", help="Only prune sessions from this source")
    sessions_prune.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    sessions_subparsers.add_parser("stats", help="Show session store statistics")

    sessions_rename = sessions_subparsers.add_parser("rename", help="Set or change a session's title")
    sessions_rename.add_argument("session_id", help="Session ID to rename")
    sessions_rename.add_argument("title", nargs="+", help="New title for the session")

    sessions_browse = sessions_subparsers.add_parser(
        "browse",
        help="Interactive session picker — browse, search, and resume sessions",
    )
    sessions_browse.add_argument("--source", help="Filter by source (cli, telegram, discord, etc.)")
    sessions_browse.add_argument("--limit", type=int, default=50, help="Max sessions to load (default: 50)")

    def _confirm_prompt(prompt: str) -> bool:
        """Prompt for y/N confirmation, safe against non-TTY environments."""
        try:
            return input(prompt).strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def cmd_sessions(args):
        import json as _json
        try:
            from VoidCube_core.state import SessionDB
            db = SessionDB()
        except Exception as e:
            print(f"Error: Could not open session database: {e}")
            return

        action = args.sessions_action

        # Hide third-party tool sessions by default, but honour explicit --source
        _source = getattr(args, "source", None)
        _exclude = None if _source else ["tool"]

        if action == "list":
            sessions = db.list_sessions_rich(source=args.source, exclude_sources=_exclude, limit=args.limit)
            if not sessions:
                print("No sessions found.")
                return
            has_titles = any(s.get("title") for s in sessions)
            if has_titles:
                print(f"{'Title':<32} {'Preview':<40} {'Last Active':<13} {'ID'}")
                print("─" * 110)
            else:
                print(f"{'Preview':<50} {'Last Active':<13} {'Src':<6} {'ID'}")
                print("─" * 95)
            for s in sessions:
                last_active = _relative_time(s.get("last_active"))
                preview = s.get("preview", "")[:38] if has_titles else s.get("preview", "")[:48]
                if has_titles:
                    title = (s.get("title") or "—")[:30]
                    sid = s["id"]
                    print(f"{title:<32} {preview:<40} {last_active:<13} {sid}")
                else:
                    sid = s["id"]
                    print(f"{preview:<50} {last_active:<13} {s['source']:<6} {sid}")

        elif action == "export":
            if args.session_id:
                resolved_session_id = db.resolve_session_id(args.session_id)
                if not resolved_session_id:
                    print(f"Session '{args.session_id}' not found.")
                    return
                data = db.export_session(resolved_session_id)
                if not data:
                    print(f"Session '{args.session_id}' not found.")
                    return
                line = _json.dumps(data, ensure_ascii=False) + "\n"
                if args.output == "-":
                    import sys
                    sys.stdout.write(line)
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(line)
                    print(f"Exported 1 session to {args.output}")
            else:
                sessions = db.export_all(source=args.source)
                if args.output == "-":
                    import sys
                    for s in sessions:
                        sys.stdout.write(_json.dumps(s, ensure_ascii=False) + "\n")
                else:
                    with open(args.output, "w", encoding="utf-8") as f:
                        for s in sessions:
                            f.write(_json.dumps(s, ensure_ascii=False) + "\n")
                    print(f"Exported {len(sessions)} sessions to {args.output}")

        elif action == "delete":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            if not args.yes:
                if not _confirm_prompt(f"Delete session '{resolved_session_id}' and all its messages? [y/N] "):
                    print("Cancelled.")
                    return
            if db.delete_session(resolved_session_id):
                print(f"Deleted session '{resolved_session_id}'.")
            else:
                print(f"Session '{args.session_id}' not found.")

        elif action == "prune":
            days = args.older_than
            source_msg = f" from '{args.source}'" if args.source else ""
            if not args.yes:
                if not _confirm_prompt(f"Delete all ended sessions older than {days} days{source_msg}? [y/N] "):
                    print("Cancelled.")
                    return
            count = db.prune_sessions(older_than_days=days, source=args.source)
            print(f"Pruned {count} session(s).")

        elif action == "rename":
            resolved_session_id = db.resolve_session_id(args.session_id)
            if not resolved_session_id:
                print(f"Session '{args.session_id}' not found.")
                return
            title = " ".join(args.title)
            try:
                if db.set_session_title(resolved_session_id, title):
                    print(f"Session '{resolved_session_id}' renamed to: {title}")
                else:
                    print(f"Session '{args.session_id}' not found.")
            except ValueError as e:
                print(f"Error: {e}")

        elif action == "browse":
            limit = getattr(args, "limit", 50) or 50
            source = getattr(args, "source", None)
            _browse_exclude = None if source else ["tool"]
            sessions = db.list_sessions_rich(source=source, exclude_sources=_browse_exclude, limit=limit)
            db.close()
            if not sessions:
                print("No sessions found.")
                return

            selected_id = _session_browse_picker(sessions)
            if not selected_id:
                print("Cancelled.")
                return

            # Launch VoidCube --resume <id> by replacing the current process
            print(f"Resuming session: {selected_id}")
            import shutil
            VoidCube_bin = shutil.which("VoidCube")
            if VoidCube_bin:
                os.execvp(VoidCube_bin, ["VoidCube", "--resume", selected_id])
            else:
                # Fallback: re-invoke via python -m
                os.execvp(
                    sys.executable,
                    [sys.executable, "-m", "VoidCube_cli.main", "--resume", selected_id],
                )
            return  # won't reach here after execvp

        elif action == "stats":
            total = db.session_count()
            msgs = db.message_count()
            print(f"Total sessions: {total}")
            print(f"Total messages: {msgs}")
            for src in ["cli", "telegram", "discord", "whatsapp", "slack"]:
                c = db.session_count(source=src)
                if c > 0:
                    print(f"  {src}: {c} sessions")
            db_path = db.db_path
            if db_path.exists():
                size_mb = os.path.getsize(db_path) / (1024 * 1024)
                print(f"Database size: {size_mb:.1f} MB")

        else:
            sessions_parser.print_help()

        db.close()

    sessions_parser.set_defaults(func=cmd_sessions)

    # =========================================================================
    # insights command
    # =========================================================================
    insights_parser = subparsers.add_parser(
        "insights",
        help="Show usage insights and analytics",
        description="Analyze session history to show token usage, costs, tool patterns, and activity trends"
    )
    insights_parser.add_argument("--days", type=int, default=30, help="Number of days to analyze (default: 30)")
    insights_parser.add_argument("--source", help="Filter by platform (cli, telegram, discord, etc.)")

    def cmd_insights(args):
        print("Insights feature has been removed in this simplified version.")

    insights_parser.set_defaults(func=cmd_insights)

    # =========================================================================
    # version command
    # =========================================================================
    version_parser = subparsers.add_parser(
        "version",
        help="Show version information"
    )
    version_parser.set_defaults(func=cmd_version)
    
    # =========================================================================
    # acp command
    # =========================================================================
    acp_parser = subparsers.add_parser(
        "acp",
        help="Run Voidcube Agent as an ACP (Agent Client Protocol) server",
        description="Start Voidcube Agent in ACP mode for editor integration (VS Code, Zed, JetBrains)",
    )

    def cmd_acp(args):
        """Launch Voidcube Agent as an ACP server."""
        try:
            from acp_adapter.entry import main as acp_main
            acp_main()
        except ImportError:
            print("ACP dependencies not installed.")
            print("Install them with:  pip install -e '.[acp]'")
            sys.exit(1)

    acp_parser.set_defaults(func=cmd_acp)

    # =========================================================================
    # logs command
    # =========================================================================
    logs_parser = subparsers.add_parser(
        "logs",
        help="View and filter Voidcube log files",
        description="View, tail, and filter agent.log / errors.log / gateway.log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    VoidCube logs                    Show last 50 lines of agent.log
    VoidCube logs -f                 Follow agent.log in real time
    VoidCube logs errors             Show last 50 lines of errors.log
    VoidCube logs gateway -n 100     Show last 100 lines of gateway.log
    VoidCube logs --level WARNING    Only show WARNING and above
    VoidCube logs --session abc123   Filter by session ID
    VoidCube logs --component tools  Only show tool-related lines
    VoidCube logs --since 1h         Lines from the last hour
    VoidCube logs --since 30m -f     Follow, starting from 30 min ago
    VoidCube logs list               List available log files with sizes
""",
    )
    logs_parser.add_argument(
        "log_name", nargs="?", default="agent",
        help="Log to view: agent (default), errors, gateway, or 'list' to show available files",
    )
    logs_parser.add_argument(
        "-n", "--lines", type=int, default=50,
        help="Number of lines to show (default: 50)",
    )
    logs_parser.add_argument(
        "-f", "--follow", action="store_true",
        help="Follow the log in real time (like tail -f)",
    )
    logs_parser.add_argument(
        "--level", metavar="LEVEL",
        help="Minimum log level to show (DEBUG, INFO, WARNING, ERROR)",
    )
    logs_parser.add_argument(
        "--session", metavar="ID",
        help="Filter lines containing this session ID substring",
    )
    logs_parser.add_argument(
        "--since", metavar="TIME",
        help="Show lines since TIME ago (e.g. 1h, 30m, 2d)",
    )
    logs_parser.add_argument(
        "--component", metavar="NAME",
        help="Filter by component: gateway, agent, tools, cli, cron",
    )
    logs_parser.set_defaults(func=cmd_logs)

    # =========================================================================
    # api command
    # =========================================================================
    api_parser = subparsers.add_parser(
        "api",
        help="Configure API settings for inference providers",
        description="Interactive wizard for adding and configuring inference providers",
    )

    def cmd_api(args):
        """Interactive API configuration wizard."""
        _require_tty("api")
        try:
            from VoidCube_cli.api_config import run_api_config_wizard
            run_api_config_wizard()
        except ImportError:
            print("API configuration module not available.")
            print("Run 'VoidCube config edit' to configure providers manually.")
            sys.exit(1)

    api_parser.set_defaults(func=cmd_api)

    # =========================================================================
    # gateway command
    # =========================================================================
    gateway_parser = subparsers.add_parser(
        "gateway",
        help="Manage the VoidCube gateway service",
        description="Start, stop, or check the internal gateway and supervisor services",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    VoidCube gateway              Show gateway + supervisor status
    VoidCube gateway start        Start background services
    VoidCube gateway stop         Stop all running services
""",
    )
    gateway_sub = gateway_parser.add_subparsers(dest="gateway_action")
    gateway_sub.add_parser("start", help="Start gateway and supervisor in background")
    gateway_sub.add_parser("stop", help="Stop all running services")
    gateway_sub.add_parser("status", help="Show service status")

    def cmd_gateway(args):
        """Gateway lifecycle — delegates to serve module."""
        action = getattr(args, "gateway_action", None) or "status"
        from VoidCube_cli.ops.serve import start_all, stop_all, print_status
        if action == "start":
            start_all()
        elif action == "stop":
            stop_all()
        else:
            print_status()

    gateway_parser.set_defaults(func=cmd_gateway)

    # =========================================================================
    # profile command
    # =========================================================================
    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage VoidCube configuration profiles",
        description="Create, list, switch, and delete isolated VoidCube profiles",
    )
    profile_sub = profile_parser.add_subparsers(dest="profile_action")
    profile_sub.add_parser("list", help="List all profiles")
    profile_create = profile_sub.add_parser("create", help="Create a new profile")
    profile_create.add_argument("name", help="Profile name")
    profile_use = profile_sub.add_parser("use", help="Switch to a profile")
    profile_use.add_argument("name", help="Profile name")
    profile_delete = profile_sub.add_parser("delete", help="Delete a profile")
    profile_delete.add_argument("name", help="Profile name")
    profile_delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    def cmd_profile(args):
        """Profile management commands."""
        from VoidCube_cli.profiles import (
            list_profiles, create_profile, delete_profile, set_active_profile, resolve_profile_env,
        )

        action = getattr(args, "profile_action", None) or "list"

        if action == "list":
            profiles = list_profiles()
            print()
            print("  Available profiles:")
            print()
            for name, path in sorted(profiles.items()):
                active = " ← active" if os.environ.get("VOIDCUBE_HOME") == path else ""
                print(f"    {name:<20} {path}{active}")
            print()

        elif action == "create":
            try:
                path = create_profile(args.name)
                print(f"\n  ✓ Profile '{args.name}' created at {path}")
                print(f"  Switch to it: VoidCube profile use {args.name}")
            except (ValueError, FileExistsError) as e:
                print(f"  ✗ {e}")

        elif action == "use":
            try:
                path = resolve_profile_env(args.name)
                set_active_profile(args.name)
                print(f"\n  ✓ Active profile: {args.name}")
                print(f"  VOIDCUBE_HOME: {path}")
                print(f"  Restart VoidCube to apply.")
            except (ValueError, FileNotFoundError) as e:
                print(f"  ✗ {e}")

        elif action == "delete":
            if args.name.lower() == "default":
                print("  ✗ Cannot delete the default profile.")
                return
            if not args.yes:
                try:
                    confirm = input(f"  Delete profile '{args.name}'? This removes all its data. [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("  Cancelled.")
                    return
                if confirm not in ("y", "yes"):
                    print("  Cancelled.")
                    return
            if delete_profile(args.name):
                print(f"  ✓ Profile '{args.name}' deleted.")
            else:
                print(f"  ✗ Profile '{args.name}' not found.")
        else:
            profile_parser.print_help()

    profile_parser.set_defaults(func=cmd_profile)

    # =========================================================================
    # update command
    # =========================================================================
    update_parser = subparsers.add_parser(
        "update",
        help="Update VoidCube to the latest version",
        description="Upgrade VoidCube Agent via pip",
    )

    def cmd_update(args):
        """Upgrade VoidCube via pip."""
        import subprocess as _sp

        print()
        print("  Updating VoidCube Agent...")
        print()
        result = _sp.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "voidcube-agent"],
            capture_output=False,
        )
        if result.returncode == 0:
            print()
            print("  ✓ Update complete. Restart VoidCube to use the new version.")
        else:
            print()
            print("  ✗ Update failed. Try manually:")
            print("    pip install --upgrade voidcube-agent")

    update_parser.set_defaults(func=cmd_update)

    # =========================================================================
    # uninstall command
    # =========================================================================
    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Uninstall VoidCube Agent",
        description="Remove VoidCube Agent from your system",
    )

    def cmd_uninstall(args):
        """Guide for uninstalling VoidCube."""
        print()
        print("  To uninstall VoidCube Agent:")
        print()
        print("  1. Stop running services:")
        print("     VoidCube serve stop")
        print()
        print("  2. Uninstall via pip:")
        print("     pip uninstall voidcube-agent")
        print()
        print("  3. Remove configuration (optional):")
        home = get_VoidCube_home()
        print(f"     rm -rf {home}")
        print()
        print("  4. Remove the active_profile file (optional):")
        try:
            from VoidCube_core.constants import get_default_VoidCube_root
            root = get_default_VoidCube_root()
            print(f"     rm {root / 'active_profile'}")
        except Exception:
            pass
        print()

    uninstall_parser.set_defaults(func=cmd_uninstall)

    # =========================================================================
    # =========================================================================
    # Parse and execute
    # =========================================================================
    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``VoidCube -c Pokemon Agent Dev`` → ``VoidCube -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
    from VoidCube_cli.config import get_container_exec_info
    container_info = get_container_exec_info()
    if container_info:
        try:
            _exec_in_container(container_info, sys.argv[1:])
        except OSError as exc:
            print(f"Error: cannot exec into container: {exc}", file=sys.stderr)
            sys.exit(1)
        # Unreachable: os.execvp replaces the process on success.
        sys.exit(1)

    _processed_argv = _coalesce_session_name_args(sys.argv[1:])
    args = parser.parse_args(_processed_argv)

    # Fix -c / --continue greedy consumption of subcommand names.
    # When ``VoidCube -c chat`` is typed, nargs="?" on -c consumes "chat"
    # as the session name rather than recognising it as a subcommand.
    # Detect this and swap: treat the value as the subcommand instead.
    # Only swap when NO session with that name exists — a real session
    # name that happens to match a subcommand takes priority.
    _KNOWN_COMMANDS = {
        "chat", "model", "gateway", "whatsapp", "login", "logout",
        "body", "agent", "serve", "status", "autonomous", "auto-cli", "doctor", "config", "tools",
        "mcp", "sessions", "insights", "version", "api", "acp", "logs",
        "memory", "profile", "update", "uninstall",
    }
    continue_val = getattr(args, "continue_last", None)
    if (
        continue_val is not None
        and continue_val is not True  # ``-c`` with no argument → const=True
        and isinstance(continue_val, str)
        and continue_val.lower() in _KNOWN_COMMANDS
        and args.command is None
    ):
        # Only swap if NO session with that name actually exists
        resolved = _resolve_session_by_name_or_id(continue_val)
        if resolved is None:
            args.command = continue_val.lower()
            args.continue_last = None
        else:
            # Session exists — keep as session name, route to chat
            args.command = "chat"
            args.continue_last = continue_val

    # Handle --version flag
    if args.version:
        cmd_version(args)
        return
    
    # Handle top-level --resume / --continue as shortcut to chat
    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"
        args.query = None
        args.model = None
        args.provider = None
        args.toolsets = None
        args.verbose = False
        if not hasattr(args, "worktree"):
            args.worktree = False
        cmd_chat(args)
        return
    
    # Default to chat if no command specified
    if args.command is None:
        args.query = None
        args.model = None
        args.provider = None
        args.toolsets = None
        args.verbose = False
        args.resume = None
        args.continue_last = None
        if not hasattr(args, "worktree"):
            args.worktree = False
        cmd_chat(args)
        return
    
    # Execute the command
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
