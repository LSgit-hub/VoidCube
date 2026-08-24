import io

import pytest

from voidcube.infrastructure.execution.environments.base import BaseEnvironment


class _TestEnvironment(BaseEnvironment):
    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        raise NotImplementedError

    def cleanup(self):
        pass


class _CompletingProcess:
    def __init__(self):
        self.stdout = io.StringIO("done")
        self.returncode = None
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        self.returncode = 0
        return self.returncode


@pytest.mark.unit
def test_wait_for_process_uses_interruptible_process_wait(monkeypatch):
    env = _TestEnvironment(cwd=".", timeout=1)
    process = _CompletingProcess()

    monkeypatch.setattr(
        "voidcube.infrastructure.execution.environments.base.time.sleep",
        lambda _seconds: pytest.fail("short commands must not use fixed polling sleep"),
    )

    result = env._wait_for_process(process, timeout=1)

    assert result == {"output": "done", "returncode": 0}
    assert len(process.wait_timeouts) == 1
    assert 0 < process.wait_timeouts[0] <= 0.2
