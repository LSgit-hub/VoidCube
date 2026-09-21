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
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
STAGING = DESKTOP / "resources" / "voidcube"
ENTRY = ROOT / "scripts" / "voidcube_entry.py"

NIGHTLY_EXE = ROOT / "build" / "nuitka" / "voidcube.exe"

VSWHERE = (
    Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    / "Microsoft Visual Studio"
    / "Installer"
    / "vswhere.exe"
)


def find_vs_dev_cmd() -> str:
    """Locate ``VsDevCmd.bat`` of the newest Visual Studio with C++ tools.

    ``-prerelease`` is required so Insider/Preview channels (e.g. Visual
    Studio 2026 Insiders) are considered; Nuitka's bundled SCons queries
    vswhere without it and therefore cannot see those installations.
    """
    if not VSWHERE.is_file():
        raise FileNotFoundError(f"vswhere not found: {VSWHERE}")
    result = subprocess.run(
        [
            str(VSWHERE),
            "-all",
            "-prerelease",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        install_path = line.strip()
        if not install_path:
            continue
        candidate = Path(install_path) / "Common7" / "Tools" / "VsDevCmd.bat"
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "No Visual Studio with C++ build tools (VC.Tools.x86.x64) found."
    )


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
    run_with_msvc(command)
    exe = output_dir / "voidcube.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Nuitka output missing: {exe}")
    return exe


def run_with_msvc(command: list[str]) -> None:
    """Run ``command`` inside an x64 MSVC build environment.

    Entering ``VsDevCmd.bat -arch=x64 -host_arch=x64`` puts the 64-bit
    ``cl.exe`` on PATH and sets INCLUDE/LIB. With ``cl`` resolvable on PATH,
    Nuitka skips its (prerelease-blind) MSVC version scan and uses that
    compiler directly instead of falling back to the bundled zig backend,
    whose linker fails on this project. Without a usable MSVC environment we
    fall back to running the command in the current environment.
    """
    if sys.platform == "win32":
        try:
            vs_dev_cmd = find_vs_dev_cmd()
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            print(f"MSVC environment not available ({error}); running as-is.", flush=True)
        else:
            inner = subprocess.list2cmdline(
                [vs_dev_cmd, "-arch=x64", "-host_arch=x64"]
            )
            cmdline = (
                f'call "{inner}" >nul && '
                + subprocess.list2cmdline(command)
            )
            print(f"Using MSVC environment: {vs_dev_cmd}", flush=True)
            subprocess.run(["cmd", "/c", cmdline], cwd=ROOT, check=True)
            return
    subprocess.run(command, cwd=ROOT, check=True)


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


def stage_executable(exe: Path) -> Path:
    """Copy the compiled executable into the desktop extraResources tree."""
    STAGING.mkdir(parents=True, exist_ok=True)
    target = STAGING / exe.name
    shutil.copy2(exe, target)
    return target


def build_installer() -> None:
    """Generate icons, bundle the renderer, and produce the NSIS installer."""
    # Use a domestic mirror for electron-builder binaries (winCodeSign, nsis,
    # electron) so the build doesn't stall on GitHub connectivity issues.
    os.environ.setdefault(
        "ELECTRON_BUILDER_BINARIES_MIRROR",
        "https://npmmirror.com/mirrors/electron-builder-binaries/",
    )
    os.environ.setdefault(
        "ELECTRON_MIRROR",
        "https://npmmirror.com/mirrors/electron/",
    )
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    for command in ([npm, "run", "icons"], [npm, "run", "build"]):
        subprocess.run(command, cwd=DESKTOP, check=True)
    subprocess.run([npx, "electron-builder"], cwd=DESKTOP, check=True)


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
