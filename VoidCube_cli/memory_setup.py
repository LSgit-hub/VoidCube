"""Canonical Mem setup and status commands."""

from __future__ import annotations

from VoidCube_core.runtime_paths import get_runtime_layout


def cmd_setup_provider(provider_name: str) -> None:
    if str(provider_name or "").strip().lower() != "mem":
        raise ValueError("VoidCube uses canonical Mem; alternate providers are retired")
    _print_status()


def cmd_setup(args) -> None:
    del args
    _print_status()


def cmd_status(args) -> None:
    del args
    _print_status()


def memory_command(args) -> None:
    sub = getattr(args, "memory_command", None)
    if sub == "setup":
        cmd_setup(args)
        return
    cmd_status(args)


def _print_status() -> None:
    layout = get_runtime_layout()
    print("\nCanonical Mem is always active")
    print(f"  database: {layout.memory_db}")
    print("  tools: mem_search, mem_timeline, mem_remember")
    print("  recall audit: Memory Service /recall/traces\n")
