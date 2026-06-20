import json
import platform
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.model_tools import handle_function_call
from tools.terminal_tool import cleanup_vm
import tools.terminal_tool as terminal_tool_module


def _build_wsl_like_path(path: Path) -> str | None:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":")
    if not drive:
        return None
    suffix = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive.lower()}/{suffix}"


def _podman_integration_ready(image: str) -> tuple[bool, str]:
    podman = shutil.which("podman")
    if not podman:
        return False, "podman executable not found"

    try:
        version = subprocess.run(
            [podman, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return False, f"podman version failed: {exc}"

    if version.returncode != 0:
        detail = version.stderr.strip() or version.stdout.strip() or "unknown podman error"
        return False, f"podman unavailable: {detail}"

    try:
        image_exists = subprocess.run(
            [podman, "image", "exists", image],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return False, f"podman image probe failed: {exc}"

    if image_exists.returncode != 0:
        return False, f"required image missing: {image}"

    return True, ""


@pytest.mark.unit
def test_handle_function_call_runs_file_tool_chain_with_relative_paths(tmp_path, monkeypatch):
    task_id = "model-tool-relative"
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    try:
        write_payload = json.loads(
            handle_function_call(
                "write_file",
                {"path": "docs/note.txt", "content": "hello\n"},
                task_id=task_id,
                user_task="update scratch note",
            )
        )
        patch_payload = json.loads(
            handle_function_call(
                "patch",
                {
                    "mode": "replace",
                    "path": "docs/note.txt",
                    "old_string": "hello",
                    "new_string": "hello world",
                },
                task_id=task_id,
                user_task="update scratch note",
            )
        )
        search_payload = json.loads(
            handle_function_call(
                "search_files",
                {
                    "pattern": "hello world",
                    "target": "content",
                    "path": ".",
                },
                task_id=task_id,
                user_task="verify scratch note",
            )
        )
        read_payload = json.loads(
            handle_function_call(
                "read_file",
                {"path": "docs/note.txt", "offset": "1", "limit": "5"},
                task_id=task_id,
                user_task="verify scratch note",
            )
        )
    finally:
        cleanup_vm(task_id)

    assert write_payload.get("error") is None
    assert patch_payload.get("success") is True
    assert search_payload.get("error") is None
    assert search_payload.get("total_count", 0) >= 1
    assert any("hello world" in match.get("content", "") for match in search_payload.get("matches", []))
    assert read_payload.get("error") is None
    assert "1|hello world" in read_payload.get("content", "")
    assert (tmp_path / "docs" / "note.txt").read_text(encoding="utf-8") == "hello world\n"


@pytest.mark.unit
def test_handle_function_call_accepts_windows_absolute_path_for_read_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    target = tmp_path / "absolute.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    try:
        payload = json.loads(
            handle_function_call(
                "read_file",
                {"path": str(target), "offset": 1, "limit": 2},
                task_id="model-tool-absolute",
                user_task="inspect absolute path file",
            )
        )
    finally:
        cleanup_vm("model-tool-absolute")

    assert payload.get("error") is None
    assert "1|alpha" in payload.get("content", "")


@pytest.mark.unit
def test_handle_function_call_accepts_wsl_style_paths_for_file_tools(tmp_path, monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows-specific path mapping test")

    target = tmp_path / "nested" / "from_wsl.txt"
    wsl_like = _build_wsl_like_path(target)
    if not wsl_like:
        pytest.skip("Temporary directory is not on a Windows drive")

    task_id = "model-tool-wsl"
    monkeypatch.setenv("TERMINAL_ENV", "local")

    try:
        write_payload = json.loads(
            handle_function_call(
                "write_file",
                {"path": wsl_like, "content": "bridge path\n"},
                task_id=task_id,
                user_task="write through wsl path",
            )
        )
        read_payload = json.loads(
            handle_function_call(
                "read_file",
                {"path": wsl_like, "offset": 1, "limit": 2},
                task_id=task_id,
                user_task="read through wsl path",
            )
        )
    finally:
        cleanup_vm(task_id)

    assert write_payload.get("error") is None
    assert read_payload.get("error") is None
    assert "1|bridge path" in read_payload.get("content", "")
    assert target.read_text(encoding="utf-8") == "bridge path\n"


@pytest.mark.unit
def test_handle_function_call_terminal_surfaces_backend_fallback(monkeypatch):
    task_id = "model-tool-terminal-fallback"

    def fake_get_env_config():
        return {
            "env_type": "docker",
            "fallback_to_local": True,
            "docker_image": "ignored",
            "singularity_image": "ignored",
            "modal_image": "ignored",
            "daytona_image": "ignored",
            "cwd": "/root",
            "host_cwd": None,
            "timeout": 30,
            "container_cpu": 1,
            "container_memory": 5120,
            "container_disk": 51200,
            "container_persistent": True,
            "modal_mode": "auto",
            "docker_volumes": [],
            "docker_mount_cwd_to_workspace": False,
            "local_persistent": False,
        }

    def fake_check_all_guards(command, env_type):
        return {"approved": True, "user_approved": False, "smart_approved": False}

    def fake_create_once(env_type, *args, **kwargs):
        if env_type == "docker":
            raise RuntimeError("docker unavailable")
        return SimpleNamespace(
            cwd=".",
            execute=lambda command, **kw: {"output": "ok\n", "returncode": 0},
        )

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", fake_get_env_config)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", fake_check_all_guards)
    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)

    try:
        payload = json.loads(
            handle_function_call(
                "terminal",
                {"command": "echo ok"},
                task_id=task_id,
                user_task="sanity check terminal fallback",
            )
        )
    finally:
        cleanup_vm(task_id)

    assert payload["output"] == "ok"
    assert payload["requested_backend"] == "docker"
    assert payload["active_backend"] == "local"
    assert "docker unavailable" in payload["_warning"]


@pytest.mark.unit
def test_handle_function_call_terminal_surfaces_podman_backend_fallback(monkeypatch):
    task_id = "model-tool-terminal-podman-fallback"

    def fake_get_env_config():
        return {
            "env_type": "podman",
            "fallback_to_local": True,
            "docker_image": "ignored",
            "podman_image": "ignored",
            "singularity_image": "ignored",
            "modal_image": "ignored",
            "daytona_image": "ignored",
            "cwd": "/root",
            "host_cwd": None,
            "timeout": 30,
            "container_cpu": 1,
            "container_memory": 5120,
            "container_disk": 51200,
            "container_persistent": True,
            "modal_mode": "auto",
            "docker_volumes": [],
            "docker_mount_cwd_to_workspace": False,
            "local_persistent": False,
        }

    def fake_check_all_guards(command, env_type):
        return {"approved": True, "user_approved": False, "smart_approved": False}

    def fake_create_once(env_type, *args, **kwargs):
        if env_type == "podman":
            raise RuntimeError("podman unavailable")
        return SimpleNamespace(
            cwd=".",
            execute=lambda command, **kw: {"output": "ok\n", "returncode": 0},
        )

    monkeypatch.setattr(terminal_tool_module, "_get_env_config", fake_get_env_config)
    monkeypatch.setattr(terminal_tool_module, "_check_all_guards", fake_check_all_guards)
    monkeypatch.setattr(terminal_tool_module, "_create_environment_once", fake_create_once)
    monkeypatch.setattr(terminal_tool_module, "_start_cleanup_thread", lambda: None)

    try:
        payload = json.loads(
            handle_function_call(
                "terminal",
                {"command": "echo ok"},
                task_id=task_id,
                user_task="sanity check podman terminal fallback",
            )
        )
    finally:
        cleanup_vm(task_id)

    assert payload["output"] == "ok"
    assert payload["requested_backend"] == "podman"
    assert payload["active_backend"] == "local"
    assert "podman unavailable" in payload["_warning"]


@pytest.mark.integration
def test_handle_function_call_execute_code_rejects_unsupported_language():
    payload = json.loads(
        handle_function_call(
            "execute_code",
            {"code": "puts 'hi'", "language": "ruby"},
            task_id="model-tool-execute-unsupported",
            user_task="reject unsupported execute_code language",
        )
    )

    assert payload["success"] is False
    assert "Unsupported language" in payload["error"]


@pytest.mark.unit
def test_handle_function_call_execute_code_requires_sandbox_backend(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")

    payload = json.loads(
        handle_function_call(
            "execute_code",
            {"code": "print('hi')", "language": "python"},
            task_id="model-tool-execute-local",
            user_task="reject local execute_code backend",
        )
    )

    assert payload["success"] is False
    assert "requires a sandbox backend" in payload["error"]
    assert payload["backend"] == "local"


@pytest.mark.integration
def test_handle_function_call_runs_execute_code_in_podman(monkeypatch):
    image = "localhost/voidcube-podman-local:latest"
    ready, reason = _podman_integration_ready(image)
    if not ready:
        pytest.skip(reason)

    task_id = "model-tool-execute-podman"
    monkeypatch.setenv("TERMINAL_ENV", "podman")
    monkeypatch.setenv("TERMINAL_PODMAN_IMAGE", image)
    monkeypatch.setenv("TERMINAL_FALLBACK_TO_LOCAL", "false")
    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "false")

    payload = json.loads(
        handle_function_call(
            "execute_code",
            {
                "language": "python",
                "code": "print('sandbox ok')\nimport sys\nprint(sys.version_info.major)",
            },
            task_id=task_id,
            user_task="verify execute_code in podman",
        )
    )

    assert payload.get("success") is True
    assert payload.get("backend") == "podman"
    assert payload.get("exit_code") == 0
    assert "sandbox ok" in payload.get("stdout", "")
    assert "\n3" in payload.get("stdout", "") or payload.get("stdout", "").endswith("3")


@pytest.mark.integration
def test_handle_function_call_runs_tool_chain_in_podman_workspace(tmp_path, monkeypatch):
    image = "localhost/voidcube-podman-local:latest"
    ready, reason = _podman_integration_ready(image)
    if not ready:
        pytest.skip(reason)

    task_id = "model-tool-podman-integration"
    monkeypatch.setenv("TERMINAL_ENV", "podman")
    monkeypatch.setenv("TERMINAL_PODMAN_IMAGE", image)
    monkeypatch.setenv("TERMINAL_FALLBACK_TO_LOCAL", "false")
    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "true")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    try:
        terminal_payload = json.loads(
            handle_function_call(
                "terminal",
                {"command": "pwd && python3 --version && node --version && rg --version | head -n 1"},
                task_id=task_id,
                user_task="verify podman terminal runtime",
            )
        )
        write_payload = json.loads(
            handle_function_call(
                "write_file",
                {"path": "agent_smoke/note.txt", "content": "alpha\n"},
                task_id=task_id,
                user_task="write smoke file in podman workspace",
            )
        )
        patch_payload = json.loads(
            handle_function_call(
                "patch",
                {
                    "mode": "replace",
                    "path": "agent_smoke/note.txt",
                    "old_string": "alpha",
                    "new_string": "alpha beta",
                },
                task_id=task_id,
                user_task="patch smoke file in podman workspace",
            )
        )
        search_payload = json.loads(
            handle_function_call(
                "search_files",
                {"pattern": "alpha beta", "target": "content", "path": "agent_smoke"},
                task_id=task_id,
                user_task="search smoke file in podman workspace",
            )
        )
        read_payload = json.loads(
            handle_function_call(
                "read_file",
                {"path": "agent_smoke/note.txt", "offset": 1, "limit": 5},
                task_id=task_id,
                user_task="read smoke file in podman workspace",
            )
        )
    finally:
        cleanup_vm(task_id)

    assert terminal_payload.get("error") is None
    assert terminal_payload.get("exit_code") == 0
    assert terminal_payload.get("output", "").splitlines()[0] == "/workspace"
    assert "Python " in terminal_payload.get("output", "")
    assert "ripgrep " in terminal_payload.get("output", "")

    assert write_payload.get("bytes_written") == 7
    assert patch_payload.get("success") is True
    assert "/workspace/agent_smoke/note.txt" in patch_payload.get("files_modified", [])
    assert search_payload.get("total_count", 0) >= 1
    assert any(
        match.get("path") == "/workspace/agent_smoke/note.txt"
        and "alpha beta" in match.get("content", "")
        for match in search_payload.get("matches", [])
    )
    assert read_payload.get("error") is None
    assert "1|alpha beta" in read_payload.get("content", "")
    assert (tmp_path / "agent_smoke" / "note.txt").read_text(encoding="utf-8") == "alpha beta\n"
