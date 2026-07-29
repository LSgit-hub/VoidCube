from concurrent.futures import ThreadPoolExecutor

import pytest

from tools.environments.local import LocalEnvironment
import tools.environments.local as local_env_module
import tools.terminal_tool as terminal_tool_module


@pytest.fixture
def persistent_env(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path), timeout=5, persistent=True)
    try:
        yield env
    finally:
        env.cleanup()


@pytest.mark.unit
def test_local_persistent_setting_inherits_shared_setting(monkeypatch):
    monkeypatch.delenv("TERMINAL_LOCAL_PERSISTENT", raising=False)
    monkeypatch.setenv("TERMINAL_PERSISTENT_SHELL", "false")
    assert terminal_tool_module._get_env_config()["local_persistent"] is False

    monkeypatch.setenv("TERMINAL_PERSISTENT_SHELL", "true")
    assert terminal_tool_module._get_env_config()["local_persistent"] is True

    monkeypatch.setenv("TERMINAL_LOCAL_PERSISTENT", "false")
    assert terminal_tool_module._get_env_config()["local_persistent"] is False


@pytest.mark.unit
def test_create_local_environment_honors_persistent_config(tmp_path):
    env = terminal_tool_module._create_environment_once(
        "local",
        "",
        str(tmp_path),
        5,
        local_config={"persistent": True},
    )
    try:
        assert env._persistent is True
        assert env._persistent_shell.pid is not None
    finally:
        env.cleanup()


@pytest.mark.unit
def test_persistent_shell_reuses_process_and_preserves_state(persistent_env):
    original_pid = persistent_env._persistent_shell.pid

    first = persistent_env.execute(
        "mkdir child; cd child; export VC_EXPORTED=exported; "
        "VC_LOCAL=local; vc_fn(){ printf function; }; alias vc_alias='printf alias'"
    )
    second = persistent_env.execute(
        'printf "%s:%s:" "$VC_EXPORTED" "$VC_LOCAL"; vc_fn; '
        "printf ':'; vc_alias; printf ':cwd=%s' \"$PWD\""
    )

    assert first == {"output": "", "returncode": 0}
    assert second["returncode"] == 0
    assert second["output"].startswith("exported:local:function:alias:cwd=")
    assert second["output"].replace("\\", "/").endswith("/child")
    assert persistent_env.cwd.replace("\\", "/").endswith("/child")
    assert persistent_env._persistent_shell.pid == original_pid


@pytest.mark.unit
def test_persistent_shell_preserves_exact_output_and_exit_code(persistent_env):
    assert persistent_env.execute("printf exact") == {
        "output": "exact",
        "returncode": 0,
    }
    assert persistent_env.execute("printf 'line1\\nline2\\n'; false") == {
        "output": "line1\nline2\n",
        "returncode": 1,
    }


@pytest.mark.unit
def test_persistent_shell_serializes_concurrent_commands(persistent_env):
    def append_state():
        return persistent_env.execute(
            'VC_SEQUENCE="${VC_SEQUENCE}x"; printf "%s" "$VC_SEQUENCE"'
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: append_state(), range(2)))

    assert sorted(result["output"] for result in results) == ["x", "xx"]
    assert all(result["returncode"] == 0 for result in results)


@pytest.mark.unit
def test_persistent_shell_restarts_after_timeout(persistent_env):
    assert persistent_env.execute("true", timeout=0.1)["returncode"] == 0
    original_pid = persistent_env._persistent_shell.pid

    timed_out = persistent_env.execute(
        "printf before-timeout; while :; do :; done",
        timeout=0.2,
    )
    recovered = persistent_env.execute("printf recovered")

    assert timed_out["returncode"] == 124
    assert timed_out["output"].startswith("before-timeout")
    assert "timed out after 0.2s" in timed_out["output"]
    assert recovered == {"output": "recovered", "returncode": 0}
    assert persistent_env._persistent_shell.pid != original_pid


@pytest.mark.unit
def test_persistent_shell_propagates_exit_and_restarts(persistent_env):
    original_pid = persistent_env._persistent_shell.pid

    exited = persistent_env.execute("printf exiting; exit 7")
    recovered = persistent_env.execute("printf restarted")

    assert exited == {"output": "exiting", "returncode": 7}
    assert recovered == {"output": "restarted", "returncode": 0}
    assert persistent_env._persistent_shell.pid != original_pid


@pytest.mark.unit
def test_persistent_shell_preserves_errexit_semantics(persistent_env):
    original_pid = persistent_env._persistent_shell.pid

    exited = persistent_env.execute("set -e; false; printf unreachable")
    recovered = persistent_env.execute("printf restarted")

    assert exited == {"output": "", "returncode": 1}
    assert recovered == {"output": "restarted", "returncode": 0}
    assert persistent_env._persistent_shell.pid != original_pid


@pytest.mark.unit
def test_persistent_shell_restarts_after_interrupt(persistent_env, monkeypatch):
    assert persistent_env.execute("true")["returncode"] == 0
    original_pid = persistent_env._persistent_shell.pid
    monkeypatch.setattr(local_env_module, "is_interrupted", lambda: True)

    interrupted = persistent_env.execute("while :; do :; done")
    monkeypatch.setattr(local_env_module, "is_interrupted", lambda: False)
    recovered = persistent_env.execute("printf recovered")

    assert interrupted["returncode"] == 130
    assert "Command interrupted" in interrupted["output"]
    assert recovered == {"output": "recovered", "returncode": 0}
    assert persistent_env._persistent_shell.pid != original_pid


@pytest.mark.unit
def test_persistent_shell_isolates_control_stdin(persistent_env):
    assert persistent_env.execute("IFS= read -r value || printf eof") == {
        "output": "eof",
        "returncode": 0,
    }
    assert persistent_env.execute("printf still-alive") == {
        "output": "still-alive",
        "returncode": 0,
    }


@pytest.mark.unit
def test_persistent_shell_preserves_process_for_piped_stdin(persistent_env):
    assert persistent_env.execute("true")["returncode"] == 0
    original_pid = persistent_env._persistent_shell.pid
    stdin_payload = "hello\n世界"

    result = persistent_env.execute(
        "cat; VC_STDIN=kept",
        stdin_data=stdin_payload,
    )
    recovered = persistent_env.execute('printf "%s" "$VC_STDIN"')

    assert result == {"output": stdin_payload, "returncode": 0}
    assert recovered == {"output": "kept", "returncode": 0}
    assert persistent_env._persistent_shell.pid == original_pid


@pytest.mark.unit
def test_persistent_shell_cleanup_stops_process(persistent_env):
    persistent_env.cleanup()
    assert persistent_env._persistent_shell.pid is None


@pytest.mark.unit
def test_environment_info_and_cleanup_handle_persistent_local_env(tmp_path):
    task_id = "persistent-lifecycle-test"
    env = LocalEnvironment(cwd=str(tmp_path), timeout=5, persistent=True)
    with terminal_tool_module._env_lock:
        terminal_tool_module._active_environments[task_id] = env
    try:
        info = terminal_tool_module.get_active_environments_info()

        assert task_id in info["task_ids"]
        assert info["workdirs"][task_id] == str(tmp_path)
        assert terminal_tool_module.is_persistent_env(task_id) is True
        assert terminal_tool_module.cleanup_all_environments() >= 1
        assert env._persistent_shell.pid is None
    finally:
        terminal_tool_module.cleanup_vm(task_id)
