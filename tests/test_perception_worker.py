from __future__ import annotations

import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
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


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class _FakeEvent:
    # 由等待推进虚拟时钟，测试不依赖墙钟或真实退避时间。
    def __init__(self, clock, on_wait):
        self.clock = clock
        self.on_wait = on_wait
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def clear(self):
        self.stopped = False

    def wait(self, delay):
        self.waits.append(delay)
        self.on_wait(delay)
        if not self.stopped:
            self.clock.now += delay
        return self.stopped


def test_backoff_grows_caps_and_resets_with_history_preserved():
    clock = _Clock()
    snapshots = []

    class Runtime(_Runtime):
        def step(self):
            self.calls += 1
            if self.calls != 5:
                raise ValueError('private screenshot / model response')

    worker = PerceptionWorker(Runtime(), interval_seconds=0.5,
                              backoff_initial_seconds=1, backoff_max_seconds=4,
                              clock=clock, wall_clock=clock)

    def wait(delay):
        snapshots.append(worker.status())
        if len(snapshots) == 6:
            worker.stop(timeout_seconds=0)

    event = _FakeEvent(clock, wait)
    worker._stop = event
    worker._run()
    assert event.waits == [1, 2, 4, 4, 0.5, 1]
    assert [s.consecutive_failures for s in snapshots] == [1, 2, 3, 4, 0, 1]
    assert [s.total_failures for s in snapshots] == [1, 2, 3, 4, 4, 5]
    assert [s.retry_after_seconds for s in snapshots] == [1, 2, 4, 4, 0, 1]
    recovered = snapshots[4]
    assert recovered.health == 'healthy'
    assert recovered.active_error is recovered.last_error is None
    assert recovered.last_failure == 'ValueError'
    assert recovered.last_success_at == 111
    assert recovered.last_error_at == 107
    assert recovered.iterations == 1
    assert not recovered.backoff
    assert snapshots[0].health == 'degraded'
    assert snapshots[0].backoff
    assert snapshots[0].iterations == 0
    with pytest.raises(FrozenInstanceError):
        recovered.iterations = 99
    assert not worker.status().backoff


def test_stop_interrupts_backoff_without_advancing_clock():
    clock = _Clock()

    class Runtime(_Runtime):
        def step(self):
            self.calls += 1
            raise RuntimeError('secret')

    runtime = Runtime()
    worker = PerceptionWorker(runtime, clock=clock, backoff_initial_seconds=30)
    event = _FakeEvent(clock, lambda delay: worker.stop(flush=False, timeout_seconds=0))
    worker._stop = event
    worker._run()
    assert event.waits == [30]
    assert clock.now == 100
    assert runtime.calls == 1
    assert runtime.state == 'stopped'
    assert worker.status().retry_after_seconds == 0


def test_callback_flush_and_close_errors_are_sanitized_and_cleanup_continues(caplog):
    clock = _Clock()
    calls = []

    class Source:
        def close(self):
            calls.append('close')
            raise OSError('PRIVATE_CLOSE_IMAGE')

    class Runtime(_Runtime):
        loop = SimpleNamespace(frame_source=Source())

        def step(self):
            raise ValueError('PRIVATE_MODEL_RESPONSE')

        def stop(self, *, flush=True):
            calls.append(('stop', flush))
            raise RuntimeError('PRIVATE_FLUSH_IMAGE')

    def callback(exc):
        calls.append(type(exc).__name__)
        raise LookupError('PRIVATE_CALLBACK_IMAGE')

    worker = PerceptionWorker(Runtime(), on_error=callback, clock=clock, wall_clock=clock)
    worker._stop = _FakeEvent(clock, lambda delay: worker.stop(timeout_seconds=0))
    worker._run()
    assert calls == ['ValueError', ('stop', True), 'close']
    assert worker.status().total_failures == 3
    assert worker.status().last_failure == 'OSError'
    assert worker.status().health == 'degraded'
    assert 'PRIVATE_' not in caplog.text
    for stage, kind in [('step', 'ValueError'), ('on_error', 'LookupError'),
                        ('flush', 'RuntimeError'), ('close', 'OSError')]:
        assert f'stage={stage} type={kind}' in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), -float('inf')])
@pytest.mark.parametrize('parameter', ['interval_seconds', 'backoff_initial_seconds', 'backoff_max_seconds'])
def test_worker_rejects_invalid_timing_parameters(parameter, value):
    with pytest.raises(ValueError):
        PerceptionWorker(_Runtime(), **{parameter: value})


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf'), -float('inf')])
def test_stop_rejects_invalid_timeout_without_stopping(value):
    worker = PerceptionWorker(_Runtime())
    with pytest.raises(ValueError):
        worker.stop(timeout_seconds=value)
    assert not worker._stop.is_set()


def test_worker_rejects_inverted_backoff_bounds():
    with pytest.raises(ValueError):
        PerceptionWorker(_Runtime(), backoff_initial_seconds=5, backoff_max_seconds=1)


def test_real_runtime_without_consent_never_samples():
    from voidcube.systems.perception import LocalPerceptionLoop

    class Source:
        def capture(self):
            pytest.fail('未授权时不能截图')

    worker = PerceptionWorker(PerceptionRuntime(LocalPerceptionLoop(Source(), None)))
    assert worker.start() is False
    status = worker.status()
    assert not status.running
    assert status.iterations == status.total_failures == 0
    assert status.health == 'idle'


def test_real_thread_stop_wakes_event_during_long_backoff():
    waiting = Event()

    class ObservableEvent(Event):
        def wait(self, timeout=None):
            waiting.set()
            return super().wait(timeout)

    class Runtime(_Runtime):
        def step(self):
            self.calls += 1
            raise RuntimeError('private')

    runtime = Runtime()
    worker = PerceptionWorker(runtime, backoff_initial_seconds=300, backoff_max_seconds=300)
    worker._stop = ObservableEvent()
    assert worker.start()
    try:
        assert waiting.wait(3)
        assert worker.status().backoff
        assert worker.status().running
        assert worker.stop(timeout_seconds=3)
    finally:
        worker.stop(timeout_seconds=3)
    assert runtime.calls == 1
    assert not worker.status().running
    assert not worker.status().backoff


def test_retry_countdown_uses_monotonic_clock_and_snapshots_stay_unchanged():
    clock = _Clock()
    snapshots = []

    class Runtime(_Runtime):
        def step(self):
            raise RuntimeError('private')

    worker = PerceptionWorker(Runtime(), clock=clock, wall_clock=lambda: 500,
                              backoff_initial_seconds=4)

    def wait(delay):
        snapshots.append(worker.status())
        clock.now += 1
        snapshots.append(worker.status())
        worker.stop(timeout_seconds=0)

    worker._stop = _FakeEvent(clock, wait)
    worker._run()
    assert [s.retry_after_seconds for s in snapshots] == [4, 3]
    assert [s.last_error_at for s in snapshots] == [500, 500]
