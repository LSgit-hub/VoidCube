from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.interfaces.cli.tui.prompt_runtime import (
    CliTuiPromptPorts,
    CliTuiPromptRuntime,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _runtime(**ports):
    defaults = {
        "voice_recording": lambda: False,
        "voice_processing": lambda: False,
        "sudo_active": lambda: False,
        "secret_active": lambda: False,
        "approval_active": lambda: False,
        "clarify_freetext": lambda: False,
        "clarify_active": lambda: False,
        "command_running": lambda: False,
        "command_spinner_frame": lambda: "",
        "agent_running": lambda: False,
        "voice_mode": lambda: False,
        "minimal_tui_chrome": lambda _width: False,
        "terminal_width": lambda: 120,
        "audio_status": lambda: {},
    }
    defaults.update(ports)
    return CliTuiPromptRuntime(CliTuiPromptPorts(**defaults))


def test_prompt_symbols_without_profile_use_default_arrow() -> None:
    runtime = _runtime()
    symbol, suffix = runtime.prompt_symbols()
    assert symbol == "❯ "
    assert suffix == "❯ "


def test_prompt_symbols_prefixes_sticky_profile(monkeypatch) -> None:
    import voidcube.interfaces.cli.tui.prompt_runtime as prompt_module

    calls = []

    def _fake_profile():
        calls.append(True)
        return "research"

    monkeypatch.setattr(
        "voidcube.infrastructure.config.profiles.get_active_profile_name",
        _fake_profile,
    )
    runtime = _runtime()
    symbol, suffix = runtime.prompt_symbols()
    assert symbol == "research ❯ "
    assert suffix == "❯ "


def test_prompt_symbols_skips_generic_profiles(monkeypatch) -> None:
    monkeypatch.setattr(
        "voidcube.infrastructure.config.profiles.get_active_profile_name",
        lambda: "default",
    )
    runtime = _runtime()
    assert runtime.prompt_symbols() == ("❯ ", "❯ ")


def test_active_profile_reads_disk_once_and_caches(monkeypatch) -> None:
    calls = []

    def _fake_profile():
        calls.append(True)
        return "research"

    monkeypatch.setattr(
        "voidcube.infrastructure.config.profiles.get_active_profile_name",
        _fake_profile,
    )
    runtime = _runtime()
    assert runtime._active_profile_name() == "research"
    assert runtime._active_profile_name() == "research"
    assert runtime.prompt_symbols() == ("research ❯ ", "❯ ")
    assert len(calls) == 1


def test_active_profile_degrades_gracefully(monkeypatch) -> None:
    def _boom():
        raise OSError("profile file unreadable")

    monkeypatch.setattr(
        "voidcube.infrastructure.config.profiles.get_active_profile_name",
        _boom,
    )
    runtime = _runtime()
    assert runtime._active_profile_name() is None
    assert runtime.prompt_symbols() == ("❯ ", "❯ ")
