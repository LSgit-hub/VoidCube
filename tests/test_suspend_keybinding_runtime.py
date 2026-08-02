from types import SimpleNamespace

from VoidCube_cli.suspend_keybinding_runtime import (
    SuspendKeybindingPorts,
    SuspendKeybindingRuntime,
)


def _runtime(calls, platform):
    return SuspendKeybindingRuntime(
        SuspendKeybindingPorts(
            platform=lambda: platform,
            emit=lambda value: calls.append(("emit", value)),
            invalidate=lambda _event: calls.append("invalidate"),
            run_in_terminal=lambda operation: (calls.append("terminal"), operation()),
            suspend_process=lambda: calls.append("suspend"),
        )
    )


def test_ctrl_z_reports_unsupported_windows_without_suspending():
    calls = []
    _runtime(calls, "win32").handle(SimpleNamespace())
    assert calls == [
        ("emit", "\nSuspend (Ctrl+Z) is not supported on Windows."),
        "invalidate",
    ]


def test_ctrl_z_runs_suspend_inside_terminal_context_on_unix():
    calls = []
    _runtime(calls, "linux").handle(SimpleNamespace())
    assert calls == ["terminal", "suspend"]
