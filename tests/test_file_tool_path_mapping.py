import json
import os
import platform
from pathlib import Path

import pytest

from tools.file_tools import patch_tool, read_file_tool, search_tool, write_file_tool
from tools.terminal_tool import cleanup_vm


def _build_wsl_like_path(path: Path) -> str | None:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":")
    if not drive:
        return None
    suffix = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive.lower()}/{suffix}"


@pytest.mark.unit
def test_read_file_accepts_wsl_style_path_on_windows_local_backend(monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows-specific path mapping test")

    wsl_like = _build_wsl_like_path(Path.cwd() / "README.md")
    if not wsl_like:
        pytest.skip("Current workspace is not on a Windows drive")

    monkeypatch.setenv("TERMINAL_ENV", "local")
    payload = json.loads(read_file_tool(wsl_like, offset=1, limit=2, task_id="wsl-path-read"))

    assert payload.get("content")
    assert payload.get("error") is None


@pytest.mark.unit
def test_write_and_patch_accept_wsl_style_paths_on_windows_local_backend(tmp_path, monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows-specific path mapping test")

    target = tmp_path / "nested" / "notes.txt"
    wsl_like = _build_wsl_like_path(target)
    if not wsl_like:
        pytest.skip("Temporary directory is not on a Windows drive")

    monkeypatch.setenv("TERMINAL_ENV", "local")

    try:
        write_payload = json.loads(
            write_file_tool(wsl_like, "alpha\nbeta\n", task_id="wsl-path-write")
        )
        patch_payload = json.loads(
            patch_tool(
                mode="replace",
                path=wsl_like,
                old_string="beta",
                new_string="gamma",
                task_id="wsl-path-write",
            )
        )
    finally:
        cleanup_vm("wsl-path-write")

    assert write_payload.get("error") is None
    assert patch_payload.get("success") is True
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


@pytest.mark.unit
def test_search_accepts_wsl_style_directory_path_on_windows_local_backend(tmp_path, monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows-specific path mapping test")

    target = tmp_path / "docs"
    target.mkdir(parents=True)
    (target / "app.log").write_text("needle=present\n", encoding="utf-8")
    wsl_like_dir = _build_wsl_like_path(target)
    if not wsl_like_dir:
        pytest.skip("Temporary directory is not on a Windows drive")

    monkeypatch.setenv("TERMINAL_ENV", "local")

    try:
        payload = json.loads(
            search_tool(
                pattern="needle",
                target="content",
                path=wsl_like_dir,
                task_id="wsl-path-search",
            )
        )
    finally:
        cleanup_vm("wsl-path-search")

    assert payload.get("error") is None
    assert payload.get("total_count", 0) >= 1
    assert any("needle=present" in match.get("content", "") for match in payload.get("matches", []))


@pytest.mark.unit
def test_relative_paths_follow_terminal_cwd_for_write_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    try:
        write_payload = json.loads(
            write_file_tool("relative.txt", "workspace marker\n", task_id="relative-path-tools")
        )
        search_payload = json.loads(
            search_tool(
                pattern="workspace",
                target="content",
                path=".",
                task_id="relative-path-tools",
            )
        )
    finally:
        cleanup_vm("relative-path-tools")

    assert write_payload.get("error") is None
    assert (tmp_path / "relative.txt").read_text(encoding="utf-8") == "workspace marker\n"
    assert search_payload.get("error") is None
    assert search_payload.get("total_count", 0) >= 1
