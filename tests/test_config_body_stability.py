from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.config import load_config_from_env
from scripts.sync_repo_config import REPO_CONFIG_PATH, render_repo_config


@pytest.mark.unit
def test_body_stability_config_can_be_loaded_from_env(monkeypatch):
    monkeypatch.setenv("BODY_STABLE_WINDOW_DAYS", "3")
    monkeypatch.setenv("BODY_STABLE_HEALTH_CHECKS", "5")

    config = load_config_from_env()

    assert config.supervisor.body_runtime.stable_window_days == 3
    assert config.supervisor.body_runtime.stable_health_checks == 5


@pytest.mark.unit
@pytest.mark.smoke
def test_repository_body_config_is_generated_from_product_defaults():
    assert REPO_CONFIG_PATH.read_text(encoding="utf-8") == render_repo_config()


@pytest.mark.unit
@pytest.mark.smoke
def test_cli_has_no_project_level_config_fallback():
    root = Path(__file__).resolve().parents[1]
    cli_source = (root / "cli.py").read_text(encoding="utf-8")

    assert not (root / ("cli-" + "config.yaml")).exists()
    stale_markers = (
        "project_config" + "_path",
        "project config" + " - fallback",
        "Failed to load canonical" + " CLI config",
        "_file_has_terminal" + "_config",
    )
    assert all(marker not in cli_source for marker in stale_markers)
