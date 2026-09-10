from __future__ import annotations

from voidcube.infrastructure.execution.terminal_tool import _validate_workdir


def test_validate_workdir_accepts_windows_drive_paths() -> None:
    assert _validate_workdir(r"F:\My_code\VScode_py\VoidCube") is None
    assert _validate_workdir("F:/My_code/VScode_py/VoidCube") is None
    assert _validate_workdir(r"C:\work space\repo") is None


def test_validate_workdir_accepts_unc_and_posix_paths() -> None:
    assert _validate_workdir(r"\\server\share\repo") is None
    assert _validate_workdir("/workspace/project") is None
    assert _validate_workdir("repo/subdir") is None


def test_validate_workdir_rejects_shell_and_control_characters() -> None:
    for path in (
        r"F:\repo;whoami",
        r"F:\repo|whoami",
        r"F:\repo&whoami",
        r"F:\repo$HOME",
        r"F:\repo`whoami`",
        "F:\\repo\nnext",
        "F:\\repo\x00",
    ):
        assert _validate_workdir(path) is not None
