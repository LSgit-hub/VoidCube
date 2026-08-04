"""Terminal output isolation for the embedded autonomous component thread."""

from __future__ import annotations

import contextlib
import io
import sys
import threading
from collections.abc import Callable
from typing import Any


class _ThreadOutputProxy:
    def __init__(self, original: Any, target_thread_id: int, sink: Any) -> None:
        self._original = original
        self._target_thread_id = target_thread_id
        self._sink = sink

    def write(self, data: str) -> Any:
        if threading.get_ident() == self._target_thread_id:
            return self._sink.write(data)
        return self._original.write(data)

    def flush(self) -> Any:
        if threading.get_ident() != self._target_thread_id:
            return self._original.flush()
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def run_component_operation_silently(operation: Callable[[], Any]) -> Any:
    thread_id = threading.get_ident()
    stdout_proxy = _ThreadOutputProxy(sys.stdout, thread_id, io.StringIO())
    stderr_proxy = _ThreadOutputProxy(sys.stderr, thread_id, io.StringIO())
    with contextlib.redirect_stdout(stdout_proxy), contextlib.redirect_stderr(
        stderr_proxy
    ):
        return operation()


__all__ = ["run_component_operation_silently"]
