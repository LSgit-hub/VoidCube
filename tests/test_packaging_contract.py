from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import tomllib
from zipfile import ZipFile

import pytest

from scripts.build_wheel import (
    clean_build_state,
    expected_wheel_files,
    wheel_contract_errors,
)


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke


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
    assert "locales/*.json" in setuptools["package-data"]["VoidCube_cli"]


@pytest.mark.unit
def test_default_launcher_service_dependencies_are_core_dependencies():
    dependencies = _project_config()["project"]["dependencies"]
    dependency_names = {
        dependency.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].lower()
        for dependency in dependencies
    }

    assert {"aiohttp", "fastapi", "uvicorn", "psutil"} <= dependency_names


@pytest.mark.unit
def test_project_uses_spdx_license_metadata():
    project = _project_config()["project"]

    assert project["license"] == "MIT"
    assert not any(
        classifier.startswith("License ::")
        for classifier in project.get("classifiers", [])
    )


@pytest.mark.unit
def test_clean_build_state_removes_only_root_build_outputs(tmp_path):
    root = tmp_path / "repo"
    build_dir = root / "build"
    egg_info = root / "voidcube_agent.egg-info"
    unrelated = root / "keep" / "build"
    for directory in (build_dir, egg_info, unrelated):
        directory.mkdir(parents=True)
        (directory / "sentinel.txt").write_text("keep", encoding="utf-8")

    clean_build_state(root, distribution_name="voidcube_agent")

    assert not build_dir.exists()
    assert not egg_info.exists()
    assert (unrelated / "sentinel.txt").is_file()


@pytest.mark.unit
def test_wheel_contract_rejects_source_less_cached_module(tmp_path):
    wheel = tmp_path / "stale.whl"
    expected = expected_wheel_files(ROOT)
    with ZipFile(wheel, "w") as archive:
        for name in expected:
            archive.writestr(name, "")
        archive.writestr("agent/stale_deleted_module.py", "")

    errors = wheel_contract_errors(wheel, ROOT)

    assert errors == [
        "wheel contains files without current source: agent/stale_deleted_module.py"
    ]


@pytest.mark.unit
def test_wheel_contract_rejects_missing_current_source(tmp_path):
    wheel = tmp_path / "incomplete.whl"
    expected = expected_wheel_files(ROOT)
    omitted = "run_agent.py"
    assert omitted in expected
    with ZipFile(wheel, "w") as archive:
        for name in expected - {omitted}:
            archive.writestr(name, "")

    errors = wheel_contract_errors(wheel, ROOT)

    assert errors == [f"wheel is missing current source files: {omitted}"]


@pytest.mark.unit
def test_wheel_contract_rejects_retired_integration_content(tmp_path):
    wheel = tmp_path / "retired-integration.whl"
    expected = expected_wheel_files(ROOT)
    marker = "".join(("anthro", "pic"))
    contaminated = "run_agent.py"
    with ZipFile(wheel, "w") as archive:
        for name in expected:
            archive.writestr(
                name,
                f"provider = {marker!r}\n" if name == contaminated else "",
            )

    errors = wheel_contract_errors(wheel, ROOT)

    assert errors == [
        "wheel contains project-retired integration markers: run_agent.py"
    ]


@pytest.mark.unit
def test_wheel_contract_tracks_cli_locale_resources():
    expected = expected_wheel_files(ROOT)

    assert "VoidCube_cli/locales/zh_CN.json" in expected
    assert "VoidCube_cli/locales/en_US.json" in expected
