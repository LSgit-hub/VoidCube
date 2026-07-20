from types import SimpleNamespace

import pytest

from tools.path_runtime import resolve_runtime_path


@pytest.mark.unit
def test_memory_setup_module_is_importable():
    from VoidCube_cli.memory_setup import memory_command

    assert callable(memory_command)


@pytest.mark.unit
def test_resolve_runtime_path_maps_host_path_into_docker_workspace():
    env = SimpleNamespace(
        _voidcube_active_backend="docker",
        _voidcube_workspace_host_path="F:/repo/project",
        _voidcube_workspace_backend_path="/workspace",
        _voidcube_home_host_path=None,
        _voidcube_home_backend_path="/root",
        cwd="/workspace",
    )

    runtime_path = resolve_runtime_path("F:/repo/project/src/app.py", env)

    assert runtime_path.backend_path == "/workspace/src/app.py"
    assert runtime_path.host_path is not None
    assert runtime_path.host_path.replace("\\", "/").endswith("F:/repo/project/src/app.py")


@pytest.mark.unit
def test_resolve_runtime_path_maps_backend_workspace_path_back_to_host():
    env = SimpleNamespace(
        _voidcube_active_backend="docker",
        _voidcube_workspace_host_path="F:/repo/project",
        _voidcube_workspace_backend_path="/workspace",
        _voidcube_home_host_path=None,
        _voidcube_home_backend_path="/root",
        cwd="/workspace/subdir",
    )

    runtime_path = resolve_runtime_path("/workspace/src/app.py", env)

    assert runtime_path.backend_path == "/workspace/src/app.py"
    assert runtime_path.host_path is not None
    assert runtime_path.host_path.replace("\\", "/").endswith("F:/repo/project/src/app.py")


@pytest.mark.unit
def test_resolve_runtime_path_expands_relative_path_under_backend_cwd():
    env = SimpleNamespace(
        _voidcube_active_backend="docker",
        _voidcube_workspace_host_path="F:/repo/project",
        _voidcube_workspace_backend_path="/workspace",
        _voidcube_home_host_path=None,
        _voidcube_home_backend_path="/root",
        cwd="/workspace/subdir",
    )

    runtime_path = resolve_runtime_path("nested/file.txt", env)

    assert runtime_path.backend_path == "/workspace/subdir/nested/file.txt"
    assert runtime_path.host_path is not None
    assert runtime_path.host_path.replace("\\", "/").endswith(
        "F:/repo/project/subdir/nested/file.txt"
    )


@pytest.mark.unit
def test_remote_cache_sync_only_reads_canonical_cache_tree(tmp_path, monkeypatch):
    home = tmp_path / ".VoidCube"
    canonical = home / "cache" / "images"
    legacy = home / "image_cache"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    (canonical / "current.png").write_text("current", encoding="utf-8")
    (legacy / "retired.png").write_text("retired", encoding="utf-8")
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    from tools.credential_files import get_cache_directory_mounts, iter_cache_files

    assert get_cache_directory_mounts() == [
        {
            "host_path": str(canonical),
            "container_path": "/root/.VoidCube/cache/images",
        }
    ]
    assert iter_cache_files() == [
        {
            "host_path": str(canonical / "current.png"),
            "container_path": "/root/.VoidCube/cache/images/current.png",
        }
    ]
