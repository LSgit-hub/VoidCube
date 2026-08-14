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


def _mem_project_config() -> dict:
    with (ROOT / "Mem" / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _wheel_metadata(
    *,
    version: str = __version__,
    requires_python: str | None = None,
) -> str:
    project = _project_config()["project"]
    return (
        "Metadata-Version: 2.4\n"
        f"Name: {project['name']}\n"
        f"Version: {version}\n"
        f"Requires-Python: {requires_python or project['requires-python']}\n\n"
    )


@pytest.mark.unit
def test_distribution_includes_runtime_subpackages_and_mem_resources():
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
        "VoidCube_app",
    }
    for package in required_packages:
        assert any(fnmatchcase(package, pattern) for pattern in patterns), package

    assert "prompts/*/*.txt" in setuptools["package-data"]["memai"]
    assert "identity/*.json" in setuptools["package-data"]["memai"]
    assert "identity/*.md" in setuptools["package-data"]["memai"]
    assert "locales/*.json" in setuptools["package-data"]["VoidCube_cli"]
    assert "supervisor/web/*.html" in setuptools["package-data"]["systems"]
    assert "containerfiles/*.Containerfile" in setuptools["package-data"]["tools"]


@pytest.mark.unit
def test_standalone_mem_distribution_includes_identity_resources():
    package_data = _mem_project_config()["tool"]["setuptools"]["package-data"]["memai"]

    assert "prompts/*/*.txt" in package_data
    assert "identity/*.json" in package_data
    assert "identity/*.md" in package_data


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

    assert project["requires-python"] == ">=3.14,<3.15"
    assert "Programming Language :: Python :: 3.14" in classifiers
    assert not any(
        value.startswith("Programming Language :: Python :: 3.")
        and value != "Programming Language :: Python :: 3.14"
        for value in classifiers
    )
    assert "项目固定使用 Python 3.14.x" in development_guide
    assert terminal["docker_image"]


@pytest.mark.unit
def test_optional_dependency_ranges_support_python_314_wheels():
    extras = _project_config()["project"]["optional-dependencies"]
    local = {Requirement(value).name: Requirement(value) for value in extras["local"]}
    image = {Requirement(value).name: Requirement(value) for value in extras["image"]}

    assert Version("6.1.1") in local["lxml"].specifier
    assert Version("5.4.0") not in local["lxml"].specifier
    assert Version("12.3.0") in image["pillow"].specifier
    assert Version("12.2.0") not in image["pillow"].specifier
    assert Version("10.4.0") not in image["pillow"].specifier


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
def test_wheel_contract_accepts_normalized_python_specifier_order(tmp_path):
    wheel = tmp_path / "normalized-python-range.whl"
    expected = expected_wheel_files(ROOT)
    with ZipFile(wheel, "w") as archive:
        for name in expected:
            archive.writestr(name, "")
        archive.writestr(
            f"voidcube_agent-{__version__}.dist-info/METADATA",
            _wheel_metadata(requires_python="<3.15,>=3.14"),
        )

    assert wheel_contract_errors(wheel, ROOT) == []


@pytest.mark.unit
def test_wheel_contract_rejects_python_range_drift(tmp_path):
    wheel = tmp_path / "python-range-drift.whl"
    expected = expected_wheel_files(ROOT)
    with ZipFile(wheel, "w") as archive:
        for name in expected:
            archive.writestr(name, "")
        archive.writestr(
            f"voidcube_agent-{__version__}.dist-info/METADATA",
            _wheel_metadata(requires_python=">=3.13,<3.15"),
        )

    assert wheel_contract_errors(wheel, ROOT) == [
        "wheel Requires-Python does not match pyproject.toml: "
        "'>=3.13,<3.15' != '>=3.14,<3.15'"
    ]


@pytest.mark.unit
def test_wheel_contract_tracks_cli_locale_resources():
    expected = expected_wheel_files(ROOT)

    assert "VoidCube_cli/locales/zh_CN.json" in expected
    assert "VoidCube_cli/locales/en_US.json" in expected


@pytest.mark.unit
def test_wheel_contract_tracks_supervisor_ui_resource():
    expected = expected_wheel_files(ROOT)

    assert "systems/supervisor/web/supervisor.html" in expected
    assert "systems/supervisor/ui_assets.py" in expected
    assert "systems/supervisor/ui_projection.py" in expected
    assert "systems/supervisor/ui_cognition_projection.py" in expected
    assert "systems/supervisor/ui_observation_projection.py" in expected
    assert "systems/supervisor/ui_trace_projection.py" in expected
    assert "systems/supervisor/ui_state_projection.py" in expected
    assert "systems/supervisor/ui_body_projection.py" in expected
    assert "systems/supervisor/ui_autonomous_projection.py" in expected


@pytest.mark.unit
def test_wheel_contract_tracks_podman_containerfile():
    expected = expected_wheel_files(ROOT)

    assert "tools/containerfiles/podman-agent.Containerfile" in expected


@pytest.mark.unit
def test_wheel_contract_tracks_shared_application_package():
    expected = expected_wheel_files(ROOT)

    assert "VoidCube_app/__init__.py" in expected
    assert "VoidCube_app/config.py" in expected
    assert "VoidCube_app/configuration.py" in expected
    assert "VoidCube_app/default_identity.py" in expected
    assert "VoidCube_app/environment.py" in expected
    assert "VoidCube_app/gateway.py" in expected
    assert "VoidCube_app/models.py" in expected
    assert "VoidCube_app/model_normalization.py" in expected
    assert "VoidCube_app/plugins.py" in expected
    assert "VoidCube_app/provider_auth.py" in expected
    assert "VoidCube_app/runtime_provider.py" in expected
    assert "VoidCube_app/session_identity.py" in expected
    assert "VoidCube_app/session_lifecycle.py" in expected
    assert "VoidCube_app/turn_contract.py" in expected
    assert "VoidCube_app/interaction_contract.py" in expected
    assert "VoidCube_app/tool_events.py" in expected
    assert "VoidCube_app/turn_queue.py" in expected
    assert "VoidCube_cli/interaction_adapter.py" in expected
    assert "VoidCube_cli/tool_event_adapter.py" in expected
    assert "VoidCube_cli/tui_layout.py" in expected
    assert "VoidCube_cli/tui_application.py" in expected
    assert "VoidCube_cli/tui_keybindings.py" in expected
    assert "VoidCube_cli/tui_modal_navigation.py" in expected
    assert "VoidCube_cli/command_handlers/__init__.py" in expected
    assert "VoidCube_cli/command_handlers/attachments.py" in expected
    assert "VoidCube_cli/command_handlers/autonomous.py" in expected
    assert "VoidCube_cli/command_handlers/background.py" in expected
    assert "VoidCube_cli/command_handlers/btw.py" in expected
    assert "VoidCube_cli/command_handlers/language.py" in expected
    assert "VoidCube_cli/command_handlers/display.py" in expected
    assert "VoidCube_cli/command_handlers/history.py" in expected
    assert "VoidCube_cli/command_handlers/input.py" in expected
    assert "VoidCube_cli/command_handlers/info.py" in expected
    assert "VoidCube_cli/command_handlers/operations.py" in expected
    assert "VoidCube_cli/command_handlers/plan.py" in expected
    assert "VoidCube_cli/command_handlers/rollback.py" in expected
    assert "VoidCube_cli/command_handlers/registry.py" in expected
    assert "VoidCube_cli/command_handlers/session.py" in expected
    assert "VoidCube_cli/command_handlers/skills.py" in expected
    assert "VoidCube_cli/command_handlers/tasks.py" in expected
    assert "VoidCube_cli/command_handlers/tools.py" in expected
    assert "VoidCube_cli/command_handlers/voice.py" in expected
    assert "VoidCube_cli/command_handlers/preset.py" in expected
    assert "tools/presets/docker-web.yaml" in expected
    assert "VoidCube_cli/clear_command_adapter.py" in expected
    assert "VoidCube_cli/session_command_adapter.py" in expected
    assert "VoidCube_cli/tips.py" not in expected
    assert "VoidCube_cli/session_state.py" not in expected
    assert "VoidCube_cli/style.py" in expected
    assert "VoidCube_cli/skin_engine.py" not in expected


@pytest.mark.unit
def test_wheel_contract_tracks_evolution_foundation_packages():
    expected = expected_wheel_files(ROOT)

    assert {
        "systems/self_cognition/__init__.py",
        "systems/self_cognition/models.py",
        "systems/self_cognition/repository.py",
        "systems/research_knowledge/__init__.py",
        "systems/research_knowledge/models.py",
        "systems/research_knowledge/normalizer.py",
        "systems/research_knowledge/repository.py",
        "systems/evolution_evaluation/__init__.py",
        "systems/evolution_evaluation/executor.py",
        "systems/evolution_evaluation/models.py",
        "systems/evolution_evaluation/repository.py",
        "systems/evolution_authoring/__init__.py",
        "systems/evolution_authoring/executor.py",
        "systems/evolution_authoring/models.py",
        "systems/evolution_authoring/repository.py",
    } <= expected

    assert "systems/supervisor/endogenous_foundation_bridge.py" in expected
    assert "systems/supervisor/evolution_candidate_evaluation_service.py" in expected


@pytest.mark.unit
def test_wheel_contract_excludes_retired_self_learning_conclusion_package():
    expected = expected_wheel_files(ROOT)
    retired_package = "systems/self_" + "learning/"

    assert not (ROOT / retired_package).exists()
    assert not any(path.startswith(retired_package) for path in expected)


@pytest.mark.unit
def test_wheel_contract_tracks_mem_identity_resources():
    expected = expected_wheel_files(ROOT)

    assert "memai/identity/founding_memory.json" in expected
    assert "memai/identity/founding_story.md" in expected
