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
    if sub == "redaction":
        cmd_redaction(args)
        return
    cmd_status(args)


def cmd_redaction(args) -> None:
    """Show or persist the independent Memory redaction switch."""
    from VoidCube_app.config import load_config

    state = str(getattr(args, "state", "status") or "status").strip().lower()
    if state == "status":
        enabled = bool(
            load_config().get("memory", {})
            .get("mem", {})
            .get("redact_before_store", False)
        )
        print(f"Memory redaction: {'on' if enabled else 'off'}")
        print("  scope: Memory persistence and recall context only")
        return

    enabled = state == "on"
    if not _save_redaction_setting(enabled):
        print("Unable to update Memory redaction setting")
        return
    print(f"Memory redaction: {'on' if enabled else 'off'}")
    print("  applies to newly started Agent/Memory processes")


def _save_redaction_setting(enabled: bool) -> bool:
    from VoidCube_app.config import (
        is_managed,
        managed_error,
        read_raw_config,
        save_config,
    )

    if is_managed():
        managed_error("change Memory redaction")
        return False
    raw = read_raw_config()
    memory = raw.setdefault("memory", {})
    if not isinstance(memory, dict):
        memory = {}
        raw["memory"] = memory
    provider = memory.setdefault("mem", {})
    if not isinstance(provider, dict):
        provider = {}
        memory["mem"] = provider
    provider["redact_before_store"] = bool(enabled)
    try:
        save_config(raw, preserve_structure=True)
    except Exception:
        return False
    return True


def _print_status() -> None:
    layout = get_runtime_layout()
    from VoidCube_app.config import load_config

    memory_config = load_config().get("memory", {}).get("mem", {})
    redaction = bool(memory_config.get("redact_before_store", False))
    print("\nCanonical Mem is always active")
    print(f"  database: {layout.memory_db}")
    print("  tools: mem_search, mem_timeline, mem_remember")
    print(f"  redaction: {'on' if redaction else 'off'}")
    print("  recall audit: Memory Service /recall/traces\n")
