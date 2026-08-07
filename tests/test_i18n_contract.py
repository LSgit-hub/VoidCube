from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import VoidCube_core
from scripts.compare_locales import audit_files, compare_catalogs
from VoidCube_cli.i18n import get_i18n, init_i18n, set_locale, t
from VoidCube_cli.commands import COMMAND_REGISTRY
from VoidCube_cli.commands import COMMANDS, resolve_command


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit


def test_canonical_chinese_catalog_covers_nonempty_english_structure():
    issues = audit_files(
        ROOT / "VoidCube_cli" / "locales" / "en_US.json",
        ROOT / "VoidCube_cli" / "locales" / "zh_CN.json",
    )

    assert issues == {
        "missing": [],
        "empty_reference": [],
        "empty_translation": [],
        "type_mismatch": [],
    }


def test_locale_audit_reports_missing_empty_and_type_drift():
    issues = compare_catalogs(
        {"missing": "source", "empty": "source", "group": {"key": "value"}},
        {"empty": "", "group": "not-an-object"},
    )

    assert issues["missing"] == [("missing", "source")]
    assert issues["empty_translation"] == [("empty", "source")]
    assert issues["type_mismatch"][0][0] == "group"


def test_browser_tip_uses_the_canonical_json_locales():
    init_i18n()
    original_locale = get_i18n().get_current_locale()
    try:
        set_locale("en_US")
        assert t("tips.browser_local_mode") == (
            "Tip: set browser.cloud_provider to 'local' to use free local mode instead"
        )

        set_locale("zh_CN")
        assert t("tips.browser_local_mode") == (
            "提示：将 browser.cloud_provider 设置为 'local' 以使用免费本地模式"
        )
    finally:
        set_locale(original_locale)


def test_discoverable_command_descriptions_are_localized_in_both_catalogs():
    discoverable = [command for command in COMMAND_REGISTRY if not command.gateway_only]
    for locale in ("en_US", "zh_CN"):
        catalog = json.loads(
            (ROOT / "VoidCube_cli" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )
        commands = catalog["translations"]["commands"]
        missing = [
            command.name
            for command in discoverable
            if not isinstance(commands.get(command.name), dict)
            or not commands[command.name].get("description")
        ]
        assert missing == [], (locale, missing)


def test_slash_command_registry_has_no_alias_surface():
    assert all(not hasattr(command, "aliases") for command in COMMAND_REGISTRY)
    for removed in ("reset", "r", "pk", "autonomous", "auto-quit", "auto-stop"):
        assert resolve_command(removed) is None
        assert f"/{removed}" not in COMMANDS


def test_legacy_core_i18n_surface_is_absent():
    assert not (ROOT / "VoidCube_core" / "i18n.py").exists()
    assert not hasattr(VoidCube_core, "t")
    assert not hasattr(VoidCube_core, "get_lang")
    assert not hasattr(VoidCube_core, "set_lang")

    active_sources = (
        ROOT / "cli.py",
        ROOT / "run_agent.py",
        *(ROOT / "agent").rglob("*.py"),
        *(ROOT / "tools").rglob("*.py"),
        *(ROOT / "VoidCube_cli").rglob("*.py"),
    )
    violations = [
        path.relative_to(ROOT).as_posix()
        for path in active_sources
        if "VoidCube_core.i18n" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert violations == []


def test_core_package_root_has_no_implicit_reexport_surface():
    init_source = (ROOT / "VoidCube_core" / "__init__.py").read_text(encoding="utf-8")

    assert VoidCube_core.__all__ == ()
    assert "import *" not in init_source
    for retired_name in (
        "APP_VERSION",
        "ConfigurationError",
        "VoidCubeLogger",
        "SessionState",
        "get_current_time",
        "to_utc",
    ):
        assert not hasattr(VoidCube_core, retired_name)


def test_core_runtime_has_no_legacy_cache_or_unused_exception_surface():
    from VoidCube_core import constants

    assert not (ROOT / "VoidCube_core" / "exceptions.py").exists()
    assert not hasattr(constants, "get_VoidCube_dir")
    assert constants.get_cache_dir("images").parts[-2:] == ("cache", "images")

    active_sources = (
        ROOT / "VoidCube_core" / "constants.py",
        ROOT / "tools" / "browser_tool.py",
        ROOT / "tools" / "browser_camofox.py",
        ROOT / "tools" / "credential_files.py",
    )
    retired_runtime_markers = (
        "get_VoidCube_dir",
        "document_cache",
        "image_cache",
        "audio_cache",
        "browser_screenshots",
    )
    violations = {
        path.relative_to(ROOT).as_posix(): [
            marker
            for marker in retired_runtime_markers
            if marker in path.read_text(encoding="utf-8")
        ]
        for path in active_sources
    }
    assert {path: markers for path, markers in violations.items() if markers} == {}


def test_config_module_has_no_path_reexport_consumers_or_dead_public_wrappers():
    config_path = ROOT / "VoidCube_cli" / "config.py"
    config_tree = ast.parse(config_path.read_text(encoding="utf-8"))
    top_level_names = {
        node.name
        for node in config_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert not {
        "get_managed_update_command",
        "save_env_value_secure",
        "_provider_key_from_name",
    } & top_level_names

    path_helpers = {"get_VoidCube_home", "get_config_path", "get_env_path"}
    active_sources = [ROOT / "cli.py", ROOT / "run_agent.py", ROOT / "voidcube.py"]
    for directory in (
        "agent",
        "tools",
        "VoidCube_cli",
        "VoidCube_core",
        "systems",
        "plugins",
        "Mem/src",
    ):
        active_sources.extend((ROOT / directory).rglob("*.py"))

    violations = []
    for path in active_sources:
        if path == config_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "VoidCube_cli.config":
                imported = {alias.name for alias in node.names}
                if imported & path_helpers:
                    violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []
