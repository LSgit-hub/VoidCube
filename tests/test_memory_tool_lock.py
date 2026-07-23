from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import memory_tool


pytestmark = [pytest.mark.unit]


def test_windows_file_lock_unlocks_when_mutation_raises(tmp_path, monkeypatch):
    calls: list[int] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=2,
        locking=lambda _fd, mode, _count: calls.append(mode),
    )
    monkeypatch.setattr(memory_tool, "fcntl", None)
    monkeypatch.setattr(memory_tool, "msvcrt", fake_msvcrt)

    with pytest.raises(RuntimeError, match="mutation failed"):
        with memory_tool.MemoryStore._file_lock(tmp_path / "MEMORY.md"):
            raise RuntimeError("mutation failed")

    assert calls == [fake_msvcrt.LK_LOCK, fake_msvcrt.LK_UNLCK]


def test_windows_file_lock_does_not_unlock_after_failed_acquire(
    tmp_path,
    monkeypatch,
):
    calls: list[int] = []
    fake_msvcrt = SimpleNamespace(LK_LOCK=1, LK_UNLCK=2)

    def fail_acquire(_fd, mode, _count):
        calls.append(mode)
        raise OSError("lock unavailable")

    fake_msvcrt.locking = fail_acquire
    monkeypatch.setattr(memory_tool, "fcntl", None)
    monkeypatch.setattr(memory_tool, "msvcrt", fake_msvcrt)

    with pytest.raises(OSError, match="lock unavailable"):
        with memory_tool.MemoryStore._file_lock(tmp_path / "MEMORY.md"):
            pass

    assert calls == [fake_msvcrt.LK_LOCK]
