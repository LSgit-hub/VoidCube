"""Detached local-command wrapper with bounded output and an atomic result marker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _write_marker(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run(command: list[str], *, spool_path: Path, marker_path: Path, max_bytes: int) -> int:
    spool_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    truncated = False
    exit_code: int | None = None
    error = ""
    try:
        with spool_path.open("wb") as spool:
            process = subprocess.Popen(
                command,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                remaining = max(0, max_bytes - written)
                if remaining:
                    accepted = chunk[:remaining]
                    spool.write(accepted)
                    spool.flush()
                    written += len(accepted)
                if len(chunk) > remaining:
                    truncated = True
            exit_code = process.wait()
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"[:1000]
        exit_code = 127
    _write_marker(
        marker_path,
        {
            "exit_code": exit_code,
            "error": error,
            "output_bytes": written,
            "output_truncated": truncated,
        },
    )
    return int(exit_code if exit_code is not None else 127)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    return run(
        command,
        spool_path=args.spool,
        marker_path=args.marker,
        max_bytes=max(1, args.max_bytes),
    )


if __name__ == "__main__":
    raise SystemExit(main())
