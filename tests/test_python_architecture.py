from __future__ import annotations

from pathlib import Path

import pytest

from scripts.python_architecture import (
    cross_frontend_imports,
    p0_growth_errors,
    root_cli_imports,
    shared_frontend_imports,
)


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.unit, pytest.mark.smoke]

# Temporary CLI-0 exceptions. Each entry names the actual runtime import edge;
# migrations may remove entries but must never add new ones.
ROOT_CLI_IMPORT_EXCEPTIONS = {
    ("VoidCube_cli/autonomous_runner.py", "cli"),
    ("VoidCube_cli/main.py", "cli"),
}

def _edge_keys(edges) -> set[tuple[str, str]]:
    return {edge.key for edge in edges}


def test_root_cli_runtime_imports_only_use_declared_migration_exceptions() -> None:
    assert _edge_keys(root_cli_imports(ROOT)) <= ROOT_CLI_IMPORT_EXCEPTIONS


def test_shared_packages_do_not_import_frontend_packages() -> None:
    assert shared_frontend_imports(ROOT) == []


def test_shared_application_layer_has_no_frontend_imports() -> None:
    violations = [
        edge for edge in shared_frontend_imports(ROOT)
        if edge.source.startswith("VoidCube_app/")
    ]
    assert violations == []


def test_frontend_adapters_do_not_import_each_other() -> None:
    assert cross_frontend_imports(ROOT) == []


def test_p0_files_and_large_methods_do_not_grow_during_migration() -> None:
    assert p0_growth_errors(ROOT) == []
