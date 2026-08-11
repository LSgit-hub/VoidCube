from __future__ import annotations

from VoidCube_cli.app import VoidcubeCLI


def test_desktop_cli_uses_single_cell_status_symbols_even_when_launched_from_windows_terminal(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("VOIDCUBE_DESKTOP", "1")
    monkeypatch.setenv("WT_SESSION", "parent-terminal")

    assert VoidcubeCLI._use_ascii_fallback() is True
