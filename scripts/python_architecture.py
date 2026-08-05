"""Static Python dependency checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


PRODUCTION_PACKAGE_DIRS = (
    "agent",
    "plugins",
    "systems",
    "tools",
    "VoidCube_app",
    "VoidCube_cli",
    "VoidCube_core",
)
TOP_LEVEL_MODULES = ("cli.py", "run_agent.py", "voidcube.py")
SHARED_PACKAGE_NAMES = {"agent", "systems", "VoidCube_app", "VoidCube_core"}
FRONTEND_PACKAGE_NAMES = {"VoidCube_cli", "VoidCube_windows"}

@dataclass(frozen=True, order=True, slots=True)
class ImportEdge:
    source: str
    target: str
    line: int

    @property
    def key(self) -> tuple[str, str]:
        return self.source, self.target


class _RuntimeImportVisitor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.edges: list[ImportEdge] = []

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking_guard(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.edges.extend(
            ImportEdge(self.source, alias.name, node.lineno)
            for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self.edges.append(ImportEdge(self.source, node.module, node.lineno))

    @staticmethod
    def _is_type_checking_guard(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Name)
            and node.id == "TYPE_CHECKING"
        ) or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "typing"
            and node.attr == "TYPE_CHECKING"
        )


def production_python_files(root: Path) -> list[Path]:
    files = [root / name for name in TOP_LEVEL_MODULES if (root / name).is_file()]
    for relative in PRODUCTION_PACKAGE_DIRS:
        package = root / relative
        if package.is_dir():
            files.extend(path for path in package.rglob("*.py") if path.is_file())
    return sorted(set(files))


def runtime_import_edges(root: Path) -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for path in production_python_files(root):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        visitor = _RuntimeImportVisitor(relative)
        visitor.visit(tree)
        edges.extend(visitor.edges)
    return sorted(edges)


def root_cli_imports(root: Path) -> list[ImportEdge]:
    return [edge for edge in runtime_import_edges(root) if edge.target == "cli"]


def shared_frontend_imports(root: Path) -> list[ImportEdge]:
    violations: list[ImportEdge] = []
    for edge in runtime_import_edges(root):
        source_package = edge.source.split("/", 1)[0]
        target_package = edge.target.split(".", 1)[0]
        if (
            source_package in SHARED_PACKAGE_NAMES
            and target_package in FRONTEND_PACKAGE_NAMES
        ):
            violations.append(edge)
    return violations


def cross_frontend_imports(root: Path) -> list[ImportEdge]:
    violations: list[ImportEdge] = []
    for edge in runtime_import_edges(root):
        source_package = edge.source.split("/", 1)[0]
        target_package = edge.target.split(".", 1)[0]
        if (
            source_package in FRONTEND_PACKAGE_NAMES
            and target_package in FRONTEND_PACKAGE_NAMES
            and source_package != target_package
        ):
            violations.append(edge)
    return violations
