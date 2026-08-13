from __future__ import annotations

import multiprocessing
import threading
import time

from VoidCube_core.utils import interprocess_file_lock


def _hold_lock(path: str, acquired, release) -> None:
    with interprocess_file_lock(path):
        acquired.set()
        release.wait(5)


def test_interprocess_file_lock_waits_for_another_process(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_lock,
        args=(str(tmp_path / "shared.lock"), acquired, release),
    )
    process.start()
    assert acquired.wait(5)

    entered = threading.Event()

    def wait_for_lock() -> None:
        with interprocess_file_lock(tmp_path / "shared.lock"):
            entered.set()

    waiter = threading.Thread(target=wait_for_lock)
    waiter.start()
    try:
        time.sleep(0.1)
        assert not entered.is_set()
        release.set()
        waiter.join(timeout=5)
        assert entered.is_set()
    finally:
        release.set()
        waiter.join(timeout=5)
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
