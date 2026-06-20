from types import SimpleNamespace

import pytest

from tools.path_runtime import resolve_runtime_path


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
