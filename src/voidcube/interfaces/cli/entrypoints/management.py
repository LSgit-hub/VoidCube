from __future__ import annotations

import os
import sys

from ....infrastructure.config.runtime_paths import get_VoidCube_home
from .session import (
    _relative_time,
    _resolve_session_by_name_or_id,
    _session_browse_picker,
)
from .startup import _require_tty


def cmd_memory(args):
    from ..memory_setup import memory_command
    memory_command(args)

def cmd_tools(args):
    action = getattr(args, "tools_action", None)
    if action in ("list", "disable", "enable"):
        from ..tools_config import tools_disable_enable_command
        tools_disable_enable_command(args)
    else:
        _require_tty("tools")
        from ..tools_config import tools_command
        tools_command(args)

def cmd_mcp(args):
    from ..mcp_config import mcp_command
    mcp_command(args)

def _confirm_prompt(prompt: str) -> bool:
    """Prompt for y/N confirmation, safe against non-TTY environments."""
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False

def cmd_sessions(args):
    import json as _json
    try:
        from ....infrastructure.persistence.session_db import SessionDB
        db = SessionDB()
    except Exception as e:
        print(f"Error: Could not open session database: {e}")
        return

    action = args.sessions_action

    # Hide third-party tool sessions by default, but honour explicit --source
    _source = getattr(args, "source", None)
    _exclude = None if _source else ["tool"]

    if action == "list":
        sessions = db.list_sessions_rich(
            source=args.source,
            exclude_sources=_exclude,
            limit=args.limit,
            exclude_id_prefixes=["scheduled_"],
        )
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
        sessions = db.list_sessions_rich(
            source=source,
            exclude_sources=_browse_exclude,
            limit=limit,
            exclude_id_prefixes=["scheduled_"],
        )
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
        for src in ["cli"]:
            c = db.session_count(source=src)
            if c > 0:
                print(f"  {src}: {c} sessions")
        db_path = db.db_path
        if db_path.exists():
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            print(f"Database size: {size_mb:.1f} MB")

    else:
        args.command_help()

    db.close()

def cmd_insights(args):
    print("Insights feature has been removed in this simplified version.")

def cmd_acp(args):
    """Launch Voidcube Agent as an ACP server."""
    try:
        from acp_adapter.entry import main as acp_main
        acp_main()
    except ImportError:
        print("ACP dependencies not installed.")
        print("Install them with:  pip install -e '.[acp]'")
        sys.exit(1)

def cmd_api(args):
    """Interactive API configuration wizard."""
    _require_tty("api")
    try:
        try:
            from voidcube.interfaces.cli.configuration import run_api_config_wizard
        except (ModuleNotFoundError, ImportError):
            from src.voidcube.interfaces.cli.configuration import run_api_config_wizard
        run_api_config_wizard()
    except ImportError:
        print("API configuration module not available.")
        print("Run 'VoidCube config edit' to configure providers manually.")
        sys.exit(1)

def cmd_gateway(args):
    """Gateway lifecycle — delegates to serve module."""
    action = getattr(args, "gateway_action", None) or "status"
    from ....infrastructure.gateway.service_launcher import start_all, stop_all, print_status
    if action == "start":
        start_all()
    elif action == "stop":
        stop_all()
    else:
        print_status()

def cmd_profile(args):
    """Profile management commands."""
    from ..profiles import (
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
            except EOFError:
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
        args.command_help()

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
        from ....infrastructure.config.runtime_paths import get_default_VoidCube_root
        root = get_default_VoidCube_root()
        print(f"     rm {root / 'active_profile'}")
    except Exception:
        pass
    print()
