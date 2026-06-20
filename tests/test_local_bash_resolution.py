import os

import pytest

import tools.environments.local as local_env


@pytest.mark.unit
def test_find_bash_prefers_git_bash_over_wsl_launcher(monkeypatch):
    git_bash = r"C:\Program Files\Git\bin\bash.exe"
    system_bash = r"C:\Windows\System32\bash.exe"

    monkeypatch.setattr(local_env, "_IS_WINDOWS", True)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    monkeypatch.setattr(local_env.shutil, "which", lambda name: system_bash)
    monkeypatch.setattr(
        local_env.os.path,
        "isfile",
        lambda path: os.path.normcase(path) == os.path.normcase(git_bash),
    )

    assert local_env._find_bash() == git_bash


@pytest.mark.unit
def test_find_bash_rejects_wsl_launcher_when_only_which_matches(monkeypatch):
    system_bash = r"C:\Windows\System32\bash.exe"

    monkeypatch.setattr(local_env, "_IS_WINDOWS", True)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    monkeypatch.delenv("VOIDCUBE_GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(local_env.shutil, "which", lambda name: system_bash)
    monkeypatch.setattr(local_env.os.path, "isfile", lambda path: False)

    with pytest.raises(RuntimeError, match="Git Bash not found"):
        local_env._find_bash()
