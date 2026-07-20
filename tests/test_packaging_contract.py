from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import tomllib
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.version import Version
import pytest
import pytest_asyncio

from VoidCube_cli import __version__
from VoidCube_cli.banner import format_banner_version_label
from VoidCube_cli.config import DEFAULT_CONFIG
from scripts.build_wheel import (
    clean_build_state,
    expected_wheel_files,
    wheel_contract_errors,
)
from scripts.verify_clean_install import isolated_environment, verification_commands


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke


def _project_config() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _wheel_metadata(*, version: str = __version__) -> str:
    project = _project_config()["project"]
    return (
        "Metadata-Version: 2.4\n"
        f"Name: {project['name']}\n"
        f"Version: {version}\n"
        f"Requires-Python: {project['requires-python']}\n\n"
    )


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
def test_distribution_version_comes_from_the_cli_package():
    config = _project_config()
    project = config["project"]
    dynamic = config["tool"]["setuptools"]["dynamic"]

    assert "version" not in project
    assert project["dynamic"] == ["version"]
    assert dynamic["version"] == {"attr": "VoidCube_cli.__version__"}
    assert str(Version(__version__)) == __version__
    assert format_banner_version_label() == f"v{__version__}"


@pytest.mark.unit
def test_python_baseline_matches_metadata_docs_and_runtime_images():
    project = _project_config()["project"]
    classifiers = set(project["classifiers"])
    development_guide = (ROOT / "docs" / "开发与验证.md").read_text(encoding="utf-8")
    terminal = DEFAULT_CONFIG["terminal"]

    assert project["requires-python"] == ">=3.11"
    assert "Programming Language :: Python :: 3.11" in classifiers
    assert "Python 3.11 或更高版本" in development_guide
    for field in (
        "docker_image",
        "podman_image",
        "singularity_image",
        "modal_image",
        "daytona_image",
    ):
        assert "python3.11" in terminal[field]


@pytest.mark.unit
def test_dev_test_dependencies_accept_the_supported_runner_and_async_plugin():
    dev_requirements = _project_config()["project"]["optional-dependencies"]["dev"]
    requirements = {Requirement(value).name: Requirement(value) for value in dev_requirements}

    assert Version(pytest.__version__) in requirements["pytest"].specifier
    assert Version(pytest_asyncio.__version__) in requirements["pytest-asyncio"].specifier


@pytest.mark.unit
def test_clean_install_verifier_uses_declared_extras_and_isolated_smoke(tmp_path):
    python = tmp_path / "venv" / "python"
    commands = verification_commands(
        python,
        ROOT,
        extras="all,dev",
        run_smoke=True,
    )

    assert commands == [
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-e",
            f"{ROOT.resolve()}[all,dev]",
        ],
        [str(python), "-m", "pip", "check"],
        [str(python), "-m", "pytest", "-m", "smoke", "-q"],
    ]

    home = tmp_path / "home"
    env = isolated_environment(
        {"PYTHONPATH": "unsafe", "KEEP_ME": "yes"},
        home,
    )
    assert "PYTHONPATH" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PIP_NO_INPUT"] == "1"
    assert env["VOIDCUBE_HOME"] == str(home)
    assert env["KEEP_ME"] == "yes"


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
        archive.writestr(
            f"voidcube_agent-{__version__}.dist-info/METADATA",
            _wheel_metadata(),
        )
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
        archive.writestr(
            f"voidcube_agent-{__version__}.dist-info/METADATA",
            _wheel_metadata(),
        )

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
        archive.writestr(
            f"voidcube_agent-{__version__}.dist-info/METADATA",
            _wheel_metadata(),
        )

    errors = wheel_contract_errors(wheel, ROOT)

    assert errors == [
        "wheel contains project-retired integration markers: run_agent.py"
    ]


@pytest.mark.unit
def test_wheel_contract_rejects_distribution_metadata_drift(tmp_path):
    wheel = tmp_path / "metadata-drift.whl"
    expected = expected_wheel_files(ROOT)
    drifted_metadata = _wheel_metadata(version="9.9.9")
    with ZipFile(wheel, "w") as archive:
        for name in expected:
            archive.writestr(name, "")
        archive.writestr(
            "voidcube_agent-9.9.9.dist-info/METADATA",
            drifted_metadata,
        )

    errors = wheel_contract_errors(wheel, ROOT)

    assert errors == [
        "wheel version does not match VoidCube_cli.__version__: "
        f"'9.9.9' != {__version__!r}"
    ]


@pytest.mark.unit
def test_wheel_contract_tracks_cli_locale_resources():
    expected = expected_wheel_files(ROOT)

    assert "VoidCube_cli/locales/zh_CN.json" in expected
    assert "VoidCube_cli/locales/en_US.json" in expected
