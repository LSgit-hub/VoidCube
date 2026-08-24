from __future__ import annotations

from pathlib import Path

import pytest

from scripts.python_architecture import (
    cross_frontend_imports,
    root_cli_imports,
    shared_frontend_imports,
)


ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.unit, pytest.mark.smoke]

def _edge_keys(edges) -> set[tuple[str, str]]:
    return {edge.key for edge in edges}


def test_root_cli_runtime_imports_only_use_declared_migration_exceptions() -> None:
    assert _edge_keys(root_cli_imports(ROOT)) == set()


def test_shared_packages_do_not_import_frontend_packages() -> None:
    assert shared_frontend_imports(ROOT) == []


def test_shared_application_layer_has_no_frontend_imports() -> None:
    violations = [
        edge for edge in shared_frontend_imports(ROOT)
        if edge.source.startswith("voidcube/")
    ]
    assert violations == []


def test_frontend_adapters_do_not_import_each_other() -> None:
    assert cross_frontend_imports(ROOT) == []
