"""Run provider-neutral verification suites for an external scheduler."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = ROOT / "desktop"
DEFAULT_MEMORY_DURATION_SECONDS = 180.0


@dataclass(frozen=True)
class VerificationStep:
    name: str
    command: tuple[str, ...]
    cwd: Path


def _npm_executable() -> str:
    return shutil.which("npm") or ("npm.cmd" if sys.platform == "win32" else "npm")


def build_suite_steps(
    suite: str,
    *,
    memory_duration_seconds: float = DEFAULT_MEMORY_DURATION_SECONDS,
) -> tuple[VerificationStep, ...]:
    """Build one suite without executing or registering any scheduled task."""
    if suite == "memory":
        if not math.isfinite(memory_duration_seconds) or memory_duration_seconds < 0:
            raise ValueError("memory duration must be finite and not negative")
        return (
            VerificationStep(
                name="memory outbox smoke and recovery soak",
                command=(
                    sys.executable,
                    str(ROOT / "scripts" / "smoke_memory_outbox.py"),
                    "--mode",
                    "all",
                    "--duration-seconds",
                    str(memory_duration_seconds),
                ),
                cwd=ROOT,
            ),
            VerificationStep(
                name="memory outbox operational tests",
                command=(
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_memory_outbox_operational.py",
                    "-q",
                ),
                cwd=ROOT,
            ),
        )
    if suite == "full":
        return (
            VerificationStep(
                name="full Python test gate",
                command=(
                    sys.executable,
                    str(ROOT / "scripts" / "run_ci_tests.py"),
                ),
                cwd=ROOT,
            ),
        )
    if suite == "supervisor-e2e":
        return (
            VerificationStep(
                name="Supervisor desktop end-to-end tests",
                command=(_npm_executable(), "run", "test:e2e"),
                cwd=DESKTOP_ROOT,
            ),
        )
    raise ValueError(f"unknown verification suite: {suite}")


def run_periodic_verification(
    suite: str,
    *,
    memory_duration_seconds: float = DEFAULT_MEMORY_DURATION_SECONDS,
) -> int:
    """Run a suite and return its first non-zero child exit code."""
    for step in build_suite_steps(
        suite,
        memory_duration_seconds=memory_duration_seconds,
    ):
        print(f"[periodic-verification] running: {step.name}", flush=True)
        try:
            completed = subprocess.run(
                list(step.command),
                cwd=step.cwd,
                check=False,
            )
        except OSError as exc:
            print(
                f"[periodic-verification] could not start {step.name}: {exc}",
                file=sys.stderr,
            )
            return 127
        if completed.returncode != 0:
            return int(completed.returncode)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "suite",
        choices=("memory", "full", "supervisor-e2e"),
        help="verification suite to run",
    )
    parser.add_argument(
        "--memory-duration-seconds",
        type=float,
        default=DEFAULT_MEMORY_DURATION_SECONDS,
        help="recovery soak duration used by the memory suite",
    )
    args = parser.parse_args()
    return run_periodic_verification(
        args.suite,
        memory_duration_seconds=args.memory_duration_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
