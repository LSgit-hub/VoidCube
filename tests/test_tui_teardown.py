from VoidCube_cli.tui_teardown import TuiTeardownPorts, run_tui_teardown


def test_run_tui_teardown_preserves_cleanup_order() -> None:
    calls: list[str] = []

    def operation(name: str):
        return lambda: calls.append(name)

    run_tui_teardown(
        TuiTeardownPorts(
            stop_autonomous=operation("autonomous"),
            interrupt_agent=operation("agent"),
            shutdown_voice_recorder=operation("voice"),
            close_voice_tts=operation("tts"),
            cleanup_temp_voice_recordings=operation("recordings"),
            unregister_tool_callbacks=operation("callbacks"),
            close_session=operation("session"),
            finish_interrupted_session=operation("plugin"),
            run_global_cleanup=operation("global"),
            print_exit_summary=operation("summary"),
        )
    )

    assert calls == [
        "autonomous",
        "agent",
        "voice",
        "tts",
        "recordings",
        "callbacks",
        "session",
        "plugin",
        "global",
        "summary",
    ]


def test_run_tui_teardown_does_not_hide_owner_failures() -> None:
    calls: list[str] = []

    def fail() -> None:
        calls.append("autonomous")
        raise RuntimeError("stop failed")

    ports = TuiTeardownPorts(
        stop_autonomous=fail,
        interrupt_agent=lambda: calls.append("agent"),
        shutdown_voice_recorder=lambda: calls.append("voice"),
        close_voice_tts=lambda: calls.append("tts"),
        cleanup_temp_voice_recordings=lambda: calls.append("recordings"),
        unregister_tool_callbacks=lambda: calls.append("callbacks"),
        close_session=lambda: calls.append("session"),
        finish_interrupted_session=lambda: calls.append("plugin"),
        run_global_cleanup=lambda: calls.append("global"),
        print_exit_summary=lambda: calls.append("summary"),
    )

    try:
        run_tui_teardown(ports)
    except RuntimeError as error:
        assert str(error) == "stop failed"
    else:
        raise AssertionError("teardown should preserve the owner failure")

    assert calls == ["autonomous"]
