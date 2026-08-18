"""Build a wheel from clean generated state and verify its source contract."""

from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

from packaging.specifiers import InvalidSpecifier, SpecifierSet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.integration_policy import matching_retired_integrations
from VoidCube_cli import __version__


PACKAGE_DIRS = (
    "agent",
    "tools",
    "VoidCube_app",
    "VoidCube_cli",
    "VoidCube_core",
    "systems",
    "plugins",
)
TOP_LEVEL_MODULES = ("cli.py", "run_agent.py")
SOURCE_PACKAGES = (Path("src") / "voidcube",)
MEM_SOURCE_ROOT = Path("Mem/src")
MEM_PACKAGE = MEM_SOURCE_ROOT / "memai"
MEM_FORBIDDEN_IMPORT_ROOTS = frozenset({"agent", "VoidCube_app", "systems"})
RETIRED_MEMORY_PACKAGE_PREFIX = "systems/memory/"
SUPERVISOR_UI_RESOURCE = Path("systems/supervisor/web/supervisor.html")
CANONICAL_SUPERVISOR_UI_RESOURCE = Path("voidcube/systems/supervisor/web/supervisor.html")
PODMAN_CONTAINERFILE_RESOURCE = Path("tools/containerfiles/podman-agent.Containerfile")
PLUGIN_MANIFEST_GLOB = "plugins/*/plugin.json"


class WheelContractError(RuntimeError):
    """Raised when a built wheel does not match the current source tree."""


def _distribution_name(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        project_name = tomllib.load(handle)["project"]["name"]
    return str(project_name).replace("-", "_").replace(".", "_")


def _remove_root_generated_dir(path: Path, root: Path) -> None:
    root = root.resolve()
    resolved = path.resolve(strict=False)
    if resolved.parent != root:
        raise ValueError(f"Refusing to clean non-root build path: {resolved}")
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def clean_build_state(root: Path = ROOT, *, distribution_name: str | None = None) -> None:
    """Remove only setuptools state that can retain deleted source modules."""
    root = root.resolve()
    normalized_name = distribution_name or _distribution_name(root)
    _remove_root_generated_dir(root / "build", root)
    _remove_root_generated_dir(root / f"{normalized_name}.egg-info", root)


def expected_wheel_files(root: Path = ROOT) -> set[str]:
    """Return code and runtime resources expected in the wheel."""
    root = root.resolve()
    expected: set[str] = set()

    for module in TOP_LEVEL_MODULES:
        path = root / module
        if path.is_file():
            expected.add(module)

    for package_dir in PACKAGE_DIRS:
        source = root / package_dir
        if source.is_dir():
            expected.update(
                path.relative_to(root).as_posix()
                for path in source.rglob("*.py")
                if path.is_file()
            )

    for source_package in SOURCE_PACKAGES:
        source = root / source_package
        if source.is_dir():
            expected.update(
                path.relative_to(root / "src").as_posix()
                for path in source.rglob("*.py")
                if path.is_file()
            )

    mem_source = root / MEM_PACKAGE
    if mem_source.is_dir():
        expected.update(
            path.relative_to(root / MEM_SOURCE_ROOT).as_posix()
            for path in mem_source.rglob("*.py")
            if path.is_file()
        )
        expected.update(
            path.relative_to(root / MEM_SOURCE_ROOT).as_posix()
            for path in (mem_source / "prompts").rglob("*.txt")
            if path.is_file()
        )
        expected.update(
            path.relative_to(root / MEM_SOURCE_ROOT).as_posix()
            for path in (mem_source / "identity").rglob("*")
            if path.is_file() and path.suffix in {".json", ".md"}
        )

    locales = root / "VoidCube_cli" / "locales"
    if locales.is_dir():
        expected.update(
            path.relative_to(root).as_posix()
            for path in locales.rglob("*.json")
            if path.is_file()
        )

    supervisor_ui = root / SUPERVISOR_UI_RESOURCE
    if supervisor_ui.is_file():
        expected.add(SUPERVISOR_UI_RESOURCE.as_posix())
    canonical_supervisor_ui = root / "src" / CANONICAL_SUPERVISOR_UI_RESOURCE
    if canonical_supervisor_ui.is_file():
        expected.add(CANONICAL_SUPERVISOR_UI_RESOURCE.as_posix())

    preset_resources = root / "src" / "voidcube" / "extensions" / "tools" / "presets"
    if preset_resources.is_dir():
        expected.update(
            path.relative_to(root / "src").as_posix()
            for path in preset_resources.rglob("*.yaml")
            if path.is_file()
        )

    dependency_manifest = root / "src" / "voidcube" / "extensions" / "tools" / "dependency_manifest.yaml"
    if dependency_manifest.is_file():
        expected.add(dependency_manifest.relative_to(root / "src").as_posix())

    podman_containerfile = root / PODMAN_CONTAINERFILE_RESOURCE
    if podman_containerfile.is_file():
        expected.add(PODMAN_CONTAINERFILE_RESOURCE.as_posix())

    expected.update(
        path.relative_to(root).as_posix()
        for path in root.glob(PLUGIN_MANIFEST_GLOB)
        if path.is_file()
    )

    return expected


def mem_ownership_errors(root: Path = ROOT) -> list[str]:
    """Return source violations of Mem's canonical runtime ownership."""
    root = root.resolve()
    errors: list[str] = []
    retired_root = root / "systems" / "memory"
    retired_sources = sorted(
        path.relative_to(root).as_posix()
        for path in retired_root.rglob("*.py")
        if path.is_file()
    )
    if retired_sources:
        errors.append(
            "retired systems/memory contains Python sources: "
            + ", ".join(retired_sources)
        )

    violations: list[str] = []
    mem_root = root / MEM_PACKAGE
    for source in sorted(mem_root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            imported_roots: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.append(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Import):
                imported_roots.extend(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            if MEM_FORBIDDEN_IMPORT_ROOTS.intersection(imported_roots):
                violations.append(
                    f"{source.relative_to(root).as_posix()}:{node.lineno}"
                )
    if violations:
        errors.append(
            "Mem runtime imports host-owned packages: " + ", ".join(violations)
        )
    return errors


def wheel_contract_errors(wheel_path: Path, root: Path = ROOT) -> list[str]:
    """Return source-to-wheel parity errors for packaged code and resources."""
    expected = expected_wheel_files(root)
    with ZipFile(wheel_path) as archive:
        archive_names = archive.namelist()
        packaged = {
            name
            for name in archive_names
            if name.endswith(".py") or (
                name.startswith("memai/prompts/") and name.endswith(".txt")
            ) or (
                name.startswith("memai/identity/")
                and Path(name).suffix in {".json", ".md"}
            ) or (
                name.startswith("VoidCube_cli/locales/") and name.endswith(".json")
            ) or name in {
                SUPERVISOR_UI_RESOURCE.as_posix(),
                CANONICAL_SUPERVISOR_UI_RESOURCE.as_posix(),
            } or (
                name.startswith("voidcube/extensions/tools/presets/")
                and name.endswith(".yaml")
            ) or name == "voidcube/extensions/tools/dependency_manifest.yaml"
            or name == PODMAN_CONTAINERFILE_RESOURCE.as_posix()
            or (name.startswith("plugins/") and name.endswith("/plugin.json"))
        }
        retired_files = sorted(
            name
            for name in packaged
            if matching_retired_integrations(name)
            or matching_retired_integrations(
                archive.read(name).decode("utf-8", errors="ignore")
            )
        )
        retired_memory_files = sorted(
            name
            for name in packaged
            if name.startswith(RETIRED_MEMORY_PACKAGE_PREFIX)
        )
        metadata_files = [
            name for name in archive_names if name.endswith(".dist-info/METADATA")
        ]
        wheel_metadata = None
        if len(metadata_files) == 1:
            wheel_metadata = Parser().parsestr(
                archive.read(metadata_files[0]).decode("utf-8", errors="replace")
            )

    errors = mem_ownership_errors(root)
    unexpected = sorted(packaged - expected)
    missing = sorted(expected - packaged)
    if unexpected:
        errors.append("wheel contains files without current source: " + ", ".join(unexpected))
    if missing:
        errors.append("wheel is missing current source files: " + ", ".join(missing))
    if retired_files:
        errors.append(
            "wheel contains project-retired integration markers: "
            + ", ".join(retired_files)
        )
    if retired_memory_files:
        errors.append(
            "wheel contains retired systems/memory entries: "
            + ", ".join(retired_memory_files)
        )
    if len(metadata_files) != 1:
        errors.append(
            f"wheel must contain exactly one dist-info/METADATA file; found {len(metadata_files)}"
        )
    elif wheel_metadata is not None:
        with (root / "pyproject.toml").open("rb") as handle:
            requires_python = str(tomllib.load(handle)["project"]["requires-python"])
        if wheel_metadata.get("Version") != __version__:
            errors.append(
                "wheel version does not match VoidCube_cli.__version__: "
                f"{wheel_metadata.get('Version')!r} != {__version__!r}"
            )
        packaged_requires_python = wheel_metadata.get("Requires-Python")
        try:
            python_range_matches = (
                packaged_requires_python is not None
                and SpecifierSet(packaged_requires_python) == SpecifierSet(requires_python)
            )
        except InvalidSpecifier:
            python_range_matches = False
        if not python_range_matches:
            errors.append(
                "wheel Requires-Python does not match pyproject.toml: "
                f"{packaged_requires_python!r} != {requires_python!r}"
            )
    return errors


def verify_wheel(wheel_path: Path, root: Path = ROOT) -> None:
    errors = wheel_contract_errors(wheel_path, root)
    if errors:
        raise WheelContractError("\n".join(errors))


def build_wheel(root: Path = ROOT, outdir: Path | None = None) -> Path:
    """Clean generated state, build one wheel, and verify its contents."""
    root = root.resolve()
    output = (outdir or root / "dist").resolve()
    output.mkdir(parents=True, exist_ok=True)
    clean_build_state(root)
    before = {
        path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in output.glob("*.whl")
    }

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(output)],
        cwd=root,
        check=True,
    )
    built = []
    for path in output.glob("*.whl"):
        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        if before.get(path.resolve()) != signature:
            built.append(path)
    if len(built) != 1:
        raise WheelContractError(
            f"Expected one wheel created or replaced in {output}, found {len(built)}"
        )
    wheel = built[0]
    verify_wheel(wheel, root)
    return wheel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    wheel = build_wheel(ROOT, args.outdir)
    print(f"Verified wheel: {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
