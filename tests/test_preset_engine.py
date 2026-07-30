from __future__ import annotations

import pytest

from tools.preset_engine import apply_preset, list_presets, load_preset


pytestmark = pytest.mark.unit


def test_preset_catalog_loads_packaged_resources_without_path_traversal() -> None:
    presets = list_presets()

    assert [preset["file"] for preset in presets] == sorted(
        preset["file"] for preset in presets
    )
    assert "docker-web" in {preset["file"] for preset in presets}
    assert load_preset("docker-web")["name"] == "Docker Web 开发"
    assert load_preset("../docker-web") is None


def test_preset_apply_reports_missing_approved_execution_runtime() -> None:
    assert apply_preset("missing") == {
        "success": False,
        "reason": "preset_not_found",
        "results": [],
    }
    result = apply_preset("docker-web")

    assert result["success"] is False
    assert result["reason"] == "execution_not_available"
    assert result["preset"]["name"] == "Docker Web 开发"
