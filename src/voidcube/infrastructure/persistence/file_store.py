"""Atomic file writes and inter-process file locking.

These operations are infrastructure concerns shared by repositories, config
adapters, and plugins. Keeping them here prevents storage mechanics leaking
into the legacy core utility module.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Union

import yaml

_INTERPROCESS_LOCKS: dict[str, threading.RLock] = {}
_INTERPROCESS_LOCKS_GUARD = threading.Lock()


@contextmanager
def interprocess_file_lock(path: Union[str, Path]):
    """Serialize a file-backed read/modify/write section across processes."""
    lock_path = Path(path).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(lock_path))
    with _INTERPROCESS_LOCKS_GUARD:
        local_lock = _INTERPROCESS_LOCKS.setdefault(key, threading.RLock())

    with local_lock, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json_write(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    **dump_kwargs: Any,
) -> None:
    """Write JSON with fsync and atomic replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent, ensure_ascii=False, **dump_kwargs)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_yaml_write(
    path: Union[str, Path],
    data: Any,
    *,
    default_flow_style: bool = False,
    sort_keys: bool = False,
    extra_content: str | None = None,
) -> None:
    """Write YAML with fsync and atomic replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.dump(data, handle, default_flow_style=default_flow_style, sort_keys=sort_keys)
            if extra_content:
                handle.write(extra_content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


__all__ = ["atomic_json_write", "atomic_yaml_write", "interprocess_file_lock"]
