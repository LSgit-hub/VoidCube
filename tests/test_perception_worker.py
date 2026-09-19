from __future__ import annotations

import time
from threading import Event, get_ident

from voidcube.systems.perception import PerceptionRuntime, PerceptionWorker


class _Runtime:
    def __init__(self) -> None:
        self.calls = 0
        self.state = "stopped"

    def start(self) -> None:
        self.state = "running"

    def stop(self, *, flush: bool = True) -> None:
        self.state = "stopped"

    def step(self) -> None:
        self.calls += 1

    def status(self):
        return type("Status", (), {"state": self.state})()


def test_worker_starts_only_when_runtime_is_running_and_stops_cleanly() -> None:
    runtime = _Runtime()
    worker = PerceptionWorker(runtime, interval_seconds=0.05)

    assert worker.start() is True
    time.sleep(0.14)
    worker.stop(flush=False)

    assert runtime.calls >= 1
    assert worker.status().running is False
    assert runtime.state == "stopped"


def test_worker_refuses_runtime_without_authorization() -> None:
    class Unauthorized(_Runtime):
        def start(self) -> None:
            self.state = "stopped"

    worker = PerceptionWorker(Unauthorized())

    assert worker.start() is False
    assert worker.status().running is False


def test_stop_timeout_keeps_live_worker_and_defers_flush_until_step_finishes():
    entered, release = Event(), Event()
    events = []

    class SlowRuntime(_Runtime):
        def step(self):
            events.append(("step", get_ident()))
            entered.set()
            assert release.wait(5)
            events.append(("finished", get_ident()))

        def stop(self, *, flush=True):
            events.append(("stop", get_ident()))
            super().stop(flush=flush)

    worker = PerceptionWorker(SlowRuntime())
    assert worker.start()
    try:
        assert entered.wait(3)
        assert worker.stop(timeout_seconds=0) is False
        assert worker.status().running
        assert worker.start() is False
        assert [e[0] for e in events] == ["step"]
    finally:
        release.set()
        assert worker.stop(timeout_seconds=5)
    assert [e[0] for e in events] == ["step", "finished", "stop"]
    assert len({e[1] for e in events}) == 1
