from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _project_config() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


@pytest.mark.unit
def test_distribution_includes_runtime_subpackages_and_mem_prompts():
    config = _project_config()
    setuptools = config["tool"]["setuptools"]
    patterns = setuptools["packages"]["find"]["include"]

    required_packages = {
        "systems.gateway",
        "systems.memory",
        "systems.supervisor",
        "tools.browser_providers",
        "tools.environments",
        "plugins.memory.mem",
        "memai",
    }
    for package in required_packages:
        assert any(fnmatchcase(package, pattern) for pattern in patterns), package

    assert "prompts/*/*.txt" in setuptools["package-data"]["memai"]


@pytest.mark.unit
def test_default_launcher_service_dependencies_are_core_dependencies():
    dependencies = _project_config()["project"]["dependencies"]
    dependency_names = {
        dependency.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].lower()
        for dependency in dependencies
    }

    assert {"aiohttp", "fastapi", "uvicorn", "psutil"} <= dependency_names
