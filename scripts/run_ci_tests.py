"""Run the full repository test gate with a CI-safe minimum timeout."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
MIN_TIMEOUT_SECONDS = 30 * 60
DEFAULT_TIMEOUT_SECONDS = MIN_TIMEOUT_SECONDS
DEFAULT_PYTEST_ARGS = ("tests", "Mem/tests", "-q")


def run_ci_tests(
    pytest_args: Sequence[str] = (),
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    """Run pytest and return its exit code, reserving 124 for gate timeout."""
    timeout = int(timeout_seconds)
    if timeout < MIN_TIMEOUT_SECONDS:
        raise ValueError(
            f"CI test timeout must be at least {MIN_TIMEOUT_SECONDS} seconds"
        )
    command = [
        sys.executable,
        "-m",
        "pytest",
        *(pytest_args or DEFAULT_PYTEST_ARGS),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"Full test gate exceeded {timeout} seconds",
            file=sys.stderr,
        )
        return 124
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    args, pytest_args = parser.parse_known_args()
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    return run_ci_tests(pytest_args, timeout_seconds=args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
