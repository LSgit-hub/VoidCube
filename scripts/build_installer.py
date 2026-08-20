"""Build the closed-source Windows installer for VoidCube.

Pipeline:
  1. Compile the CLI/control entry into a single ``voidcube.exe`` with Nuitka.
  2. Stage the executable under ``desktop/resources/voidcube/``.
  3. Build the NSIS installer with electron-builder.

The desktop shell locates the staged executable at
``<resources>/voidcube/voidcube.exe`` (see ``desktop/src/main/runtime-locator.ts``)
and the NSIS hook registers that directory on the user PATH, so both the
desktop UI and the ``voidcube`` terminal command come from one installer.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
STAGING = DESKTOP / "resources" / "voidcube"
ENTRY = ROOT / "scripts" / "voidcube_entry.py"

NIGHTLY_EXE = ROOT / "build" / "nuitka" / "voidcube.exe"


def venv_python() -> str:
    """Return the project virtualenv interpreter, required to run Nuitka."""
    if sys.platform == "win32":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Project virtualenv interpreter not found: {candidate}"
        )
    return str(candidate)


def build_cli_executable(output_dir: Path) -> Path:
    """Compile the single-file closed-source CLI/control executable."""
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        venv_python(),
        "-m",
        "nuitka",
        "--standalone",
        "--onefile",
        "--assume-yes-for-downloads",
        "--enable-plugin=anti-bloat",
        "--include-package=voidcube",
        "--include-package=plugins",
        "--include-package=memai",
        "--include-package-data=voidcube",
        "--include-package-data=plugins",
        "--include-package-data=memai",
        "--windows-console-mode=force",
        f"--output-dir={output_dir}",
        "--output-filename=voidcube.exe",
        str(ENTRY),
    ]
    print("Compiling with Nuitka:", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
    exe = output_dir / "voidcube.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Nuitka output missing: {exe}")
    return exe


def stage_executable(exe: Path) -> Path:
    """Copy the compiled executable into the desktop extraResources tree."""
    STAGING.mkdir(parents=True, exist_ok=True)
    target = STAGING / exe.name
    shutil.copy2(exe, target)
    return target


def build_installer() -> None:
    """Generate icons, bundle the renderer, and produce the NSIS installer."""
    for command in (["npm", "run", "icons"], ["npm", "run", "build"]):
        subprocess.run(command, cwd=DESKTOP, check=True)
    subprocess.run(["npx", "electron-builder"], cwd=DESKTOP, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="Reuse an existing staged executable instead of recompiling",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "build" / "nuitka",
        help="Directory for Nuitka build artifacts",
    )
    args = parser.parse_args()

    exe = args.output_dir / "voidcube.exe"
    if args.skip_compile and exe.is_file():
        print(f"Skipping Nuitka compilation; reusing {exe}", flush=True)
    else:
        exe = build_cli_executable(args.output_dir)

    staged = stage_executable(exe)
    print(f"Staged executable at {staged}", flush=True)
    build_installer()
    print("Installer built under desktop/release/.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
