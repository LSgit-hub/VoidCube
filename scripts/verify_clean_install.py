"""Verify a fresh editable install and smoke tests in an isolated virtualenv."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_wheel import clean_build_state


def venv_python(venv_dir: Path) -> Path:
    """Return the Python executable path for a virtualenv on this platform."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def isolated_environment(base: Mapping[str, str], home: Path) -> dict[str, str]:
    """Build a subprocess environment that cannot read user-site packages."""
    env = dict(base)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["VOIDCUBE_HOME"] = str(home)
    return env


def verification_commands(
    python: Path,
    root: Path,
    *,
    extras: str,
    run_smoke: bool,
) -> list[list[str]]:
    """Return the commands executed inside the fresh virtualenv."""
    commands = [
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-e",
            f"{root.resolve()}[{extras}]",
        ],
        [str(python), "-m", "pip", "check"],
    ]
    if run_smoke:
        commands.append([str(python), "-m", "pytest", "-m", "smoke", "-q"])
    return commands


def verify_clean_install(
    root: Path = ROOT,
    *,
    extras: str = "all,dev",
    run_smoke: bool = True,
    keep_temp: bool = False,
) -> Path | None:
    """Create a clean venv, install the project, and run dependency checks."""
    root = root.resolve()
    clean_build_state(root)
    temp_dir = Path(tempfile.mkdtemp(prefix="voidcube-clean-install-"))
    venv_dir = temp_dir / "venv"
    home = temp_dir / "home"
    env = isolated_environment(os.environ, home)

    try:
        print(f"Creating isolated environment: {venv_dir}", flush=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=root,
            env=env,
            check=True,
        )
        python = venv_python(venv_dir)
        for command in verification_commands(
            python,
            root,
            extras=extras,
            run_smoke=run_smoke,
        ):
            print("Running:", subprocess.list2cmdline(command), flush=True)
            subprocess.run(command, cwd=root, env=env, check=True)
        print("Clean install verification passed.", flush=True)
        return temp_dir if keep_temp else None
    finally:
        clean_build_state(root)
        if keep_temp:
            print(f"Kept isolated environment: {temp_dir}", flush=True)
        else:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extras",
        default="all,dev",
        help="Comma-separated project extras to install (default: all,dev)",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Install and run pip check without executing the smoke suite",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary virtualenv for debugging",
    )
    args = parser.parse_args()
    verify_clean_install(
        ROOT,
        extras=args.extras,
        run_smoke=not args.skip_smoke,
        keep_temp=args.keep_temp,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
