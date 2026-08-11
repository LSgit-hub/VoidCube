from __future__ import annotations

import pytest

from VoidCube_cli.app import VoidcubeCLI


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_desktop_cli_uses_single_cell_status_symbols_on_supported_platforms(
    monkeypatch,
    platform: str,
) -> None:
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setenv("VOIDCUBE_DESKTOP", "1")
    monkeypatch.setenv("WT_SESSION", "parent-terminal")

    assert VoidcubeCLI._use_ascii_fallback() is True
