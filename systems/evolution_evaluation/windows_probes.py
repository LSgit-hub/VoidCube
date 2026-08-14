"""Executable probes for Windows-native evolution validation."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


def _python_import() -> dict[str, object]:
    import systems.evolution_evaluation  # noqa: F401
    import tools.terminal_tool  # noqa: F401

    return {"probe": "python-import", "python": sys.version.split()[0]}


def _path_semantics() -> dict[str, object]:
    root = Path.cwd().resolve()
    if not root.is_absolute() or not root.drive:
        raise RuntimeError(f"Windows worktree path is not drive-qualified: {root}")
    with tempfile.TemporaryDirectory(prefix="voidcube path ", dir=root) as temporary:
        nested = Path(temporary).resolve() / "nested path"
        nested.mkdir()
        if nested.parent.parent != root:
            raise RuntimeError("Windows path with spaces did not resolve inside the worktree")
    return {"probe": "windows-path", "cwd": str(root), "drive": root.drive}


def _readonly_semantics() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="voidcube-readonly-", dir=Path.cwd()) as temporary:
        path = Path(temporary) / "readonly.txt"
        path.write_text("voidcube\n", encoding="utf-8")
        os.chmod(path, stat.S_IREAD)
        try:
            mode = os.stat(path).st_mode
            if mode & stat.S_IWRITE:
                raise RuntimeError("Windows read-only attribute was not applied")
        finally:
            os.chmod(path, stat.S_IWRITE)
    return {"probe": "windows-readonly", "readonly_observed": True}


def _subprocess_exit() -> dict[str, object]:
    command = (
        sys.executable,
        "-c",
        "import sys; print('voidcube-child-out'); "
        "sys.stderr.write('voidcube-child-error\\n'); sys.exit(23)",
    )
    result = subprocess.run(
        command,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if (
        result.returncode != 23
        or "voidcube-child-out" not in result.stdout
        or "voidcube-child-error" not in result.stderr
    ):
        raise RuntimeError("Windows child exit code or captured streams were incorrect")
    return {"probe": "windows-subprocess", "child_exit_code": result.returncode}


def _file_lock() -> dict[str, object]:
    import msvcrt

    child_code = (
        "import msvcrt,sys; "
        "f=open(sys.argv[1],'r+b',buffering=0); f.seek(0); "
        "\ntry: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)\n"
        "except OSError: sys.exit(0)\n"
        "else: msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1); sys.exit(9)"
    )
    with tempfile.TemporaryDirectory(prefix="voidcube-lock-", dir=Path.cwd()) as temporary:
        path = Path(temporary) / "locked.bin"
        with path.open("w+b", buffering=0) as handle:
            handle.write(b"x")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                child = subprocess.run(
                    (sys.executable, "-c", child_code, str(path)),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        if child.returncode != 0:
            raise RuntimeError(
                "Windows file lock was not enforced across processes: "
                f"exit={child.returncode}, stderr={child.stderr.strip()}"
            )
    return {"probe": "windows-file-lock", "contending_exit_code": child.returncode}


def _node_native_module() -> dict[str, object]:
    root = Path.cwd()
    lock_path = root / "desktop" / "package-lock.json"
    module_path = root / "desktop" / "node_modules" / "node-pty" / "package.json"
    if not lock_path.is_file() or not module_path.is_file():
        raise RuntimeError("trusted desktop/node_modules dependency tree is unavailable")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    installed = json.loads(module_path.read_text(encoding="utf-8"))
    locked = lock.get("packages", {}).get("node_modules/node-pty", {})
    if not locked or locked.get("version") != installed.get("version"):
        raise RuntimeError("installed node-pty does not match the candidate package lock")

    script = """
const pty = require('./desktop/node_modules/node-pty');
const terminal = pty.spawn(process.env.ComSpec || 'cmd.exe',
  ['/d', '/s', '/c', 'echo voidcube-native'],
  {cwd: process.cwd(), env: process.env, cols: 80, rows: 24});
let output = '';
terminal.onData(data => output += data);
terminal.onExit(event => {
  const evidence = {
    exitCode: event.exitCode,
    outputSeen: output.includes('voidcube-native'),
    platform: process.platform,
    arch: process.arch,
    abi: process.versions.modules,
    resolved: require.resolve('./desktop/node_modules/node-pty')
  };
  console.log(JSON.stringify(evidence));
  process.exit(event.exitCode === 0 && evidence.outputSeen ? 0 : 7);
});
"""
    result = subprocess.run(
        ("node", "-e", script),
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "node-pty Windows native probe failed: "
            f"exit={result.returncode}, stderr={result.stderr.strip()[:1000]}"
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("node-pty Windows native probe returned no evidence")
    evidence = json.loads(lines[-1])
    return {
        "probe": "windows-node-native",
        "node_pty_version": installed.get("version"),
        **evidence,
    }


_PROBES: dict[str, Callable[[], dict[str, object]]] = {
    "python-import": _python_import,
    "windows-path": _path_semantics,
    "windows-readonly": _readonly_semantics,
    "windows-subprocess": _subprocess_exit,
    "windows-file-lock": _file_lock,
    "windows-node-native": _node_native_module,
}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0] not in _PROBES:
        print("usage: python -m systems.evolution_evaluation.windows_probes PROBE", file=sys.stderr)
        return 2
    if os.name != "nt" and arguments[0].startswith("windows-"):
        print("Windows probe requires a Windows host", file=sys.stderr)
        return 3
    try:
        evidence = _PROBES[arguments[0]]()
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
