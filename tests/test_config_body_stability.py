from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.config import load_config_from_env


@pytest.mark.unit
def test_body_stability_config_can_be_loaded_from_env(monkeypatch):
    monkeypatch.setenv("BODY_STABLE_WINDOW_DAYS", "3")
    monkeypatch.setenv("BODY_STABLE_HEALTH_CHECKS", "5")

    config = load_config_from_env()

    assert config.supervisor.body_runtime.stable_window_days == 3
    assert config.supervisor.body_runtime.stable_health_checks == 5
