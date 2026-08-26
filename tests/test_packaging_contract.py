from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import tomllib
from zipfile import ZipFile

import pytest

from scripts.build_wheel import expected_wheel_files, wheel_contract_errors
from scripts.verify_clean_install import isolated_environment, verification_commands
from voidcube.interfaces.cli.banner import format_banner_version_label
from voidcube.version import __version__


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke


def _project_config() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _wheel_metadata(*, version: str = __version__, requires_python: str | None = None) -> str:
    project = _project_config()["project"]
    return (
        "Metadata-Version: 2.4\n"
        f"Name: {project['name']}\n"
        f"Version: {version}\n"
        f"Requires-Python: {requires_python or project['requires-python']}\n\n"
    )


@pytest.mark.unit
def test_distribution_contains_only_canonical_runtime_packages():
    config = _project_config()
    setuptools = config["tool"]["setuptools"]
    patterns = setuptools["packages"]["find"]["include"]

    assert patterns == ["voidcube*", "plugins*", "memai*"]
    for package in (
        "voidcube",
        "voidcube.application",
        "voidcube.domain",
        "voidcube.interfaces.cli",
        "voidcube.infrastructure",
        "voidcube.extensions",
        "voidcube.runtime",
        "voidcube.systems",
        "plugins.memory.mem",
        "memai",
    ):
        assert any(fnmatchcase(package, pattern) for pattern in patterns), package

    assert not any(
        any(fnmatchcase(name, pattern) for pattern in patterns)
        for name in (
            "agent",
            "tools",
            "systems",
            "VoidCube_app",
            "VoidCube_cli",
            "VoidCube_core",
        )
    )


@pytest.mark.unit
def test_package_data_uses_canonical_asset_owners():
    package_data = _project_config()["tool"]["setuptools"]["package-data"]
    assert package_data["voidcube.interfaces.cli"] == ["locales/*.json"]
    assert package_data["voidcube.systems.supervisor"] == ["web/*.html"]
    assert package_data["voidcube.infrastructure.execution"] == [
        "containerfiles/*.Containerfile"
    ]
    assert package_data["voidcube.extensions.tools"] == [
        "presets/*.yaml",
        "dependency_manifest.yaml",
    ]
    assert package_data["plugins"] == [
        "*/plugin.json",
        "*/web/dist/*",
        "*/web/dist/**/*",
    ]


@pytest.mark.unit
def test_distribution_version_comes_from_canonical_package():
    project = _project_config()["project"]
    dynamic = _project_config()["tool"]["setuptools"]["dynamic"]
    assert project["dynamic"] == ["version"]
    assert dynamic["version"] == {"attr": "voidcube.version.__version__"}
    assert format_banner_version_label() == f"v{__version__}"


@pytest.mark.unit
def test_public_scripts_target_canonical_launcher():
    assert _project_config()["project"]["scripts"] == {
        "voidcube": "voidcube.interfaces.cli:main",
        "vc": "voidcube.interfaces.cli:main",
    }
    assert "py-modules" not in _project_config()["tool"]["setuptools"]


@pytest.mark.unit
def test_expected_wheel_files_have_no_retired_package_roots():
    expected = expected_wheel_files(ROOT)
    assert all(
        not path.startswith(("agent/", "tools/", "systems/", "VoidCube_"))
        for path in expected
    )
    assert "voidcube/interfaces/cli/locales/zh_CN.json" in expected
    assert "voidcube/interfaces/cli/locales/en_US.json" in expected
    assert "voidcube/systems/supervisor/web/supervisor.html" in expected
    assert "voidcube/infrastructure/execution/containerfiles/podman-agent.Containerfile" in expected
    assert "plugins/memory/plugin.json" in expected


@pytest.mark.unit
def test_wheel_contract_rejects_missing_current_source(tmp_path):
    wheel = tmp_path / "incomplete.whl"
    expected = expected_wheel_files(ROOT)
    omitted = "voidcube/version.py"
    with ZipFile(wheel, "w") as archive:
        for name in expected - {omitted}:
            archive.writestr(name, "")
        archive.writestr(
            f"voidcube_agent-{__version__}.dist-info/METADATA",
            _wheel_metadata(),
        )
    assert wheel_contract_errors(wheel, ROOT) == [
        f"wheel is missing current source files: {omitted}"
    ]


@pytest.mark.unit
def test_wheel_contract_rejects_retired_integration_content(tmp_path):
    wheel = tmp_path / "retired-integration.whl"
    expected = expected_wheel_files(ROOT)
    marker = "".join(("anthro", "pic"))
    contaminated = "voidcube/version.py"
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
    assert wheel_contract_errors(wheel, ROOT) == [
        f"wheel contains project-retired integration markers: {contaminated}"
    ]


@pytest.mark.unit
def test_clean_install_verifier_uses_canonical_project(tmp_path):
    python = tmp_path / "venv" / "python"
    assert verification_commands(
        python, ROOT, extras="all,dev", run_smoke=True
    ) == [
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
    env = isolated_environment(
        {"PYTHONPATH": "unsafe", "KEEP_ME": "yes"},
        tmp_path / "home",
    )
    assert "PYTHONPATH" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["VOIDCUBE_HOME"] == str(tmp_path / "home")
