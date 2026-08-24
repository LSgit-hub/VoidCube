from __future__ import annotations

from voidcube.interfaces.cli.lifecycle.refresh_loop import run_tui_refresh_loop, start_tui_refresh_loop


def test_refresh_loop_waits_for_application_before_doing_work() -> None:
    sleeps: list[float] = []

    run_tui_refresh_loop(
        stop_requested=lambda: bool(sleeps),
        application_ready=lambda: False,
        presence_refresh_needed=lambda: True,
        refresh_presence=lambda: (_ for _ in ()).throw(AssertionError("must not refresh")),
        command_running=lambda: False,
        invalidate=lambda _interval: (_ for _ in ()).throw(AssertionError("must not invalidate")),
        monotonic_time=lambda: 0.0,
        sleep=sleeps.append,
    )

    assert sleeps == [0.1]


def test_refresh_loop_preserves_presence_command_and_idle_cadence() -> None:
    current_time = [5.0, 5.0, 5.2]
    sleeps: list[float] = []
    presence: list[None] = []
    invalidations: list[float] = []
    commands = [True, False]

    run_tui_refresh_loop(
        stop_requested=lambda: len(sleeps) >= 2,
        application_ready=lambda: True,
        presence_refresh_needed=lambda: True,
        refresh_presence=lambda: presence.append(None),
        command_running=lambda: commands.pop(0),
        invalidate=invalidations.append,
        monotonic_time=lambda: current_time.pop(0),
        sleep=sleeps.append,
    )

    assert presence == [None]
    assert invalidations == [0.1, 1.0]
    assert sleeps == [0.1, 0.2]


def test_start_refresh_loop_creates_and_starts_a_daemon_thread() -> None:
    captured: dict[str, object] = {}

    class _Thread:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.started = False

        def start(self) -> None:
            self.started = True

    thread = start_tui_refresh_loop(
        stop_requested=lambda: True,
        application_ready=lambda: True,
        presence_refresh_needed=lambda: False,
        refresh_presence=lambda: None,
        command_running=lambda: False,
        invalidate=lambda _interval: None,
        monotonic_time=lambda: 0.0,
        sleep=lambda _seconds: None,
        thread_factory=_Thread,  # type: ignore[arg-type]
    )

    assert thread.started is True
    assert captured["daemon"] is True


def test_refresh_failures_do_not_terminate_the_loop() -> None:
    sleeps: list[float] = []

    run_tui_refresh_loop(
        stop_requested=lambda: len(sleeps) >= 1,
        application_ready=lambda: True,
        presence_refresh_needed=lambda: True,
        refresh_presence=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        command_running=lambda: False,
        invalidate=lambda _interval: None,
        monotonic_time=lambda: 5.0,
        sleep=sleeps.append,
    )

    assert sleeps == [0.2]
