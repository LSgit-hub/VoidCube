"""Build a wheel from clean generated state and verify its source contract."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

from agent.integration_policy import matching_retired_integrations


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIRS = (
    "agent",
    "tools",
    "VoidCube_cli",
    "VoidCube_core",
    "systems",
    "plugins",
)
TOP_LEVEL_MODULES = ("voidcube.py", "cli.py", "run_agent.py")
MEM_SOURCE_ROOT = Path("Mem/src")
MEM_PACKAGE = MEM_SOURCE_ROOT / "memai"


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

    locales = root / "VoidCube_cli" / "locales"
    if locales.is_dir():
        expected.update(
            path.relative_to(root).as_posix()
            for path in locales.rglob("*.json")
            if path.is_file()
        )

    return expected


def wheel_contract_errors(wheel_path: Path, root: Path = ROOT) -> list[str]:
    """Return source-to-wheel parity errors for packaged code and resources."""
    expected = expected_wheel_files(root)
    with ZipFile(wheel_path) as archive:
        packaged = {
            name
            for name in archive.namelist()
            if name.endswith(".py") or (
                name.startswith("memai/prompts/") and name.endswith(".txt")
            ) or (
                name.startswith("VoidCube_cli/locales/") and name.endswith(".json")
            )
        }
        retired_files = sorted(
            name
            for name in packaged
            if matching_retired_integrations(name)
            or matching_retired_integrations(
                archive.read(name).decode("utf-8", errors="ignore")
            )
        )

    errors: list[str] = []
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
