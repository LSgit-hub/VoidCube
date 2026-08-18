"""
Debug tools for VoidCube CLI.

Provides utilities for collecting system information, recent logs, and
configuration summaries for sharing with support.

Usage::

    VoidCube debug share              Upload debug report and print URL
    VoidCube debug share --lines 500  Include more log lines
    VoidCube debug share --expire 30  Keep paste for 30 days
    VoidCube debug share --local      Print report locally (no upload)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from VoidCube_core.constants import get_VoidCube_home


def _tail_file(path: Path, n: int) -> list[str]:
    """Return the last *n* lines of a file without loading it entirely into memory.

    Uses a streaming approach with a bounded deque — memory usage is O(n)
    regardless of file size.
    """
    from collections import deque
    ring: deque[str] = deque(maxlen=n)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ring.append(line.rstrip("\n\r"))
    except OSError:
        pass
    return list(ring)


def _collect_debug_report(lines: int = 200) -> str:
    """Collect system info and recent logs into a debug report string."""
    parts: list[str] = []

    # Header
    parts.append("=" * 60)
    parts.append("VoidCube Debug Report")
    parts.append(f"Generated: {datetime.now().isoformat()}")
    parts.append("=" * 60)
    parts.append("")

    # System info
    parts.append("--- System ---")
    parts.append(f"Platform: {sys.platform}")
    parts.append(f"Python:   {sys.version}")
    try:
        from VoidCube_cli import __version__
        parts.append(f"VoidCube: v{__version__}")
    except Exception:
        parts.append("VoidCube: (version unavailable)")
    parts.append(f"Home:     {get_VoidCube_home()}")
    parts.append("")

    # Environment (redacted)
    parts.append("--- Environment (redacted) ---")
    _safe_vars = {
        "VOIDCUBE_HOME", "VOIDCUBE_DAEMONS_STARTED",
        "VOIDCUBE_YOLO_MODE", "VOIDCUBE_SESSION_SOURCE",
        "LANG", "LC_ALL", "TERM", "COLORTERM",
        "SHELL", "USER", "HOME",
    }
    for key in sorted(_safe_vars):
        val = os.environ.get(key)
        if val is not None:
            parts.append(f"  {key}={val}")
    parts.append("")

    # Config (redacted)
    parts.append("--- Config ---")
    try:
        from VoidCube_app.config import load_config, get_active_provider_key
        config = load_config()
        # Show active provider/model but redact keys
        active = get_active_provider_key(config)
        parts.append(f"  active_provider: {active or 'not set'}")
        if active and active in config.get("providers", {}):
            p = config["providers"][active]
            parts.append(f"  model: {p.get('selected_model', 'not set')}")
            parts.append(f"  base_url: {p.get('base_url', 'not set')}")
        # Show tools config
        tools = config.get("tools", {})
        if tools:
            parts.append(f"  tools_enabled: {list(tools.keys())}")
    except Exception as exc:
        parts.append(f"  (config unavailable: {exc})")
    parts.append("")

    # Logs — stream-read the tail to avoid loading multi-GB files into memory
    parts.append("--- Recent Logs ---")
    log_dir = get_VoidCube_home() / "logs"
    for log_name in ("agent.log", "errors.log", "gateway.log"):
        log_path = log_dir / log_name
        if log_path.exists():
            parts.append(f"\n  [{log_name}]  (last {lines} lines)")
            parts.append("  " + "-" * 50)
            try:
                tail_lines = _tail_file(log_path, lines)
                for line in tail_lines:
                    parts.append(f"    {line}")
            except Exception as exc:
                parts.append(f"    (read error: {exc})")
        else:
            parts.append(f"\n  [{log_name}]  (file not found)")

    parts.append("")
    parts.append("=" * 60)
    parts.append("End of Report")
    parts.append("=" * 60)

    return "\n".join(parts)


def _upload_report(report: str, expire_days: int = 7) -> Optional[str]:
    """Upload the report to a paste service and return the URL.

    Returns None if upload fails.
    """
    import urllib.request
    import urllib.parse

    try:
        data = urllib.parse.urlencode({
            "content": report,
            "expiry_days": expire_days,
            "format": "url",
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://dpaste.org/api/",
            data=data,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def run_debug(args) -> None:
    """Run the debug command based on parsed arguments.

    Args:
        args: argparse namespace with ``debug_command``, ``lines``,
              ``expire``, and ``local`` attributes.
    """
    sub = getattr(args, "debug_command", None)

    if sub == "share":
        lines = getattr(args, "lines", 200) or 200
        expire = getattr(args, "expire", 7) or 7
        local_only = getattr(args, "local", False)

        print()
        print("  Collecting debug report...")
        report = _collect_debug_report(lines=lines)

        if local_only:
            print()
            print(report)
            print()
            return

        print(f"  Uploading ({len(report)} chars)...")
        url = _upload_report(report, expire_days=expire)

        if url:
            print(f"  ✓ Report uploaded: {url}")
            print(f"    Expires in {expire} day(s)")
        else:
            print("  ✗ Upload failed.")
            print("    Use --local to print the report locally instead.")
    else:
        # Print the help for the debug subparser
        print("Usage: VoidCube debug share [--lines N] [--expire DAYS] [--local]")
        print("")
        print("Debug utilities for VoidCube Agent.")
        print("")
        print("Subcommands:")
        print("  share   Upload debug report to a paste service and print a shareable URL")
        print("")
        print("Examples:")
        print("  VoidCube debug share              Upload debug report and print URL")
        print("  VoidCube debug share --lines 500  Include more log lines")
        print("  VoidCube debug share --expire 30  Keep paste for 30 days")
        print("  VoidCube debug share --local      Print report locally (no upload)")
