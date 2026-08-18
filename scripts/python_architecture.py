"""Static Python dependency checks."""

from __future__ import annotations

import ast
import argparse
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath


PRODUCTION_PACKAGE_DIRS = (
    "src/voidcube",
    "plugins",
    "scripts",
)
RETIRED_PACKAGE_DIRS = (
    "VoidCube_app",
    "VoidCube_cli",
    "VoidCube_core",
    "agent",
    "systems",
    "tools",
)
TOP_LEVEL_MODULES: tuple[str, ...] = ()
SHARED_PACKAGE_NAMES = {"voidcube"}
FRONTEND_PACKAGE_NAMES: set[str] = set()
LEGACY_IMPORT_ROOTS = (
    "VoidCube_app",
    "VoidCube_cli",
    "VoidCube_core",
    "agent",
    "systems",
    "tools",
)
SOURCE_LAYOUT_IMPORT_ROOT = "src.voidcube"

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
        if node.level == 0:
            if node.module:
                self.edges.append(ImportEdge(self.source, node.module, node.lineno))
            return

        source_path = PurePosixPath(self.source)
        source_parts = list(source_path.with_suffix("").parts)
        package_parts = source_parts[:-1]
        base_parts = package_parts[: len(package_parts) - node.level + 1]
        if base_parts[:2] != ["src", "voidcube"]:
            return
        target_parts = base_parts
        if node.module:
            target_parts.extend(node.module.split("."))
        target = ".".join(target_parts[1:])
        if target:
            self.edges.append(ImportEdge(self.source, target, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        """Track literal dynamic imports as runtime dependency edges."""
        function = node.func
        is_import_module = (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "importlib"
            and function.attr == "import_module"
        ) or (isinstance(function, ast.Name) and function.id == "__import__")
        if is_import_module and node.args and isinstance(node.args[0], ast.Constant):
            module_name = node.args[0].value
            if isinstance(module_name, str):
                self.edges.append(ImportEdge(self.source, module_name, node.lineno))
        self.generic_visit(node)

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


def canonical_legacy_imports(root: Path) -> list[ImportEdge]:
    """Return legacy absolute imports from production Python sources."""
    return [
        edge
        for edge in runtime_import_edges(root)
        if any(
            edge.target == prefix or edge.target.startswith(prefix + ".")
            for prefix in LEGACY_IMPORT_ROOTS
        )
    ]


def canonical_source_layout_imports(root: Path) -> list[ImportEdge]:
    """Return source-checkout imports that cannot work from an installed wheel."""
    return [
        edge
        for edge in runtime_import_edges(root)
        if edge.source.startswith("src/voidcube/")
        and (
            edge.target == SOURCE_LAYOUT_IMPORT_ROOT
            or edge.target.startswith(SOURCE_LAYOUT_IMPORT_ROOT + ".")
        )
    ]


def forbidden_layer_imports(root: Path) -> list[ImportEdge]:
    """Return imports that violate the outer-layer dependency direction."""
    violations: list[ImportEdge] = []
    for edge in runtime_import_edges(root):
        if not edge.source.startswith("src/voidcube/"):
            continue
        source_parts = edge.source.split("/")
        if len(source_parts) < 3:
            continue
        source_layer = source_parts[2]
        if not edge.target.startswith("voidcube."):
            continue
        target_layer = edge.target.split(".")[1]
        if source_layer == "domain" and target_layer != "domain":
            violations.append(edge)
        elif source_layer == "application" and target_layer not in {"application", "domain"}:
            violations.append(edge)
        elif source_layer in {"runtime", "infrastructure", "extensions"} and target_layer == "interfaces":
            violations.append(edge)
    return violations


def canonical_missing_imports(root: Path) -> list[ImportEdge]:
    """Return canonical imports whose module is absent from the source tree."""
    missing: list[ImportEdge] = []
    source_root = root / "src"
    for edge in runtime_import_edges(root):
        if not edge.target.startswith("voidcube"):
            continue
        relative = Path(*edge.target.split("."))
        if (source_root / f"{relative}.py").is_file():
            continue
        if (source_root / relative / "__init__.py").is_file():
            continue
        missing.append(edge)
    return missing


def retired_package_sources(root: Path) -> list[Path]:
    """Return Python files left behind in retired top-level package roots."""
    files: list[Path] = []
    for relative in RETIRED_PACKAGE_DIRS:
        package = root / relative
        if package.is_dir():
            files.extend(path for path in package.rglob("*.py") if path.is_file())
    return sorted(files)


def _format_edges(title: str, edges: list[ImportEdge]) -> str:
    lines = [f"{title}: {len(edges)} violation(s)"]
    lines.extend(f"  {edge.source}:{edge.line} -> {edge.target}" for edge in edges)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of scripts/)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    checks = {
        "root cli imports": root_cli_imports(root),
        "shared/frontend imports": shared_frontend_imports(root),
        "cross-frontend imports": cross_frontend_imports(root),
        "canonical legacy imports": canonical_legacy_imports(root),
        "canonical source-layout imports": canonical_source_layout_imports(root),
        "forbidden layer imports": forbidden_layer_imports(root),
        "canonical missing imports": canonical_missing_imports(root),
    }
    failures = [edges for edges in checks.values() if edges]
    retired_sources = retired_package_sources(root)
    if retired_sources:
        print("retired package source files: %d violation(s)" % len(retired_sources))
        for path in retired_sources:
            print(f"  {path.relative_to(root).as_posix()}")
        failures.append(retired_sources)
    else:
        print("retired package source files: ok")
    for title, edges in checks.items():
        if edges:
            print(_format_edges(title, edges))
        else:
            print(f"{title}: ok")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
