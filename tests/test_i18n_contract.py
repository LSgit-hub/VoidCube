from __future__ import annotations

from pathlib import Path

import pytest

import VoidCube_core
from VoidCube_cli.i18n import get_i18n, init_i18n, set_locale, t


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.unit


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
